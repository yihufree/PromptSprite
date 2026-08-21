# -*- coding: utf-8 -*-
"""
database.py - PromptSprite SQLite 数据访问层
创建日期：2026-08-12（阶段一：项目初始化与数据地基）

职责：
  - 建表（domains / categories / entries / meta）与索引
  - 根目录(Domain)、分类(Category)、条目(Entry) 的增删改查
  - 删除安全机制：删除分类时其下条目自动转入"未分类"(category_id=NULL)
  - 收藏、搜索、统计

自测：python -m app.database
"""
import os
import re
import sqlite3
from datetime import datetime
from typing import List, Optional

from .config import data_dir, PRESET_DOMAINS
from .models import Entry  # 2026-08-18（P2-5）：Domain/Category 冗余数据类已删除，仅保留 Entry


def _now() -> str:
    """当前时间字符串（用于 created_at / updated_at）"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 建表 SQL（schema v2）：
#  - 分类为全局共享树（不再归属单一领域），通过 domain_category 实现 领域↔一级分类 多对一关联
#  - 外键：分类删除级联子分类；条目删除分类置 NULL(转入未分类)；领域删除仅解除关联（共享数据保留）
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS domains (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    sort_order  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id   INTEGER DEFAULT NULL REFERENCES categories(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    sort_order  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS domain_category (
    domain_id   INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    PRIMARY KEY (domain_id, category_id)
);

CREATE TABLE IF NOT EXISTS entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    name        TEXT NOT NULL,
    intro       TEXT DEFAULT '',
    origin      TEXT DEFAULT '',
    features    TEXT DEFAULT '',
    scenes      TEXT DEFAULT '',
    works       TEXT DEFAULT '',
    image_desc  TEXT DEFAULT '',
    prompt_cn   TEXT DEFAULT '',
    prompt_en   TEXT DEFAULT '',
    image_plan  TEXT DEFAULT '',
    image_path  TEXT DEFAULT '',
    is_favorite INTEGER DEFAULT 0,
    created_at  TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_categories_parent  ON categories(parent_id);
CREATE INDEX IF NOT EXISTS idx_dc_domain          ON domain_category(domain_id);
CREATE INDEX IF NOT EXISTS idx_dc_category        ON domain_category(category_id);
CREATE INDEX IF NOT EXISTS idx_entries_category   ON entries(category_id);
CREATE INDEX IF NOT EXISTS idx_entries_favorite   ON entries(is_favorite);
"""

# 数据库结构版本（meta 键 schema_version）；v1=旧版按领域归属分类，v2=全局分类+领域关联
SCHEMA_VERSION = "2"


class Database:
    """SQLite 数据访问封装（线程内单连接使用）"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.path.join(data_dir(), "prompts.db")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")  # 启用外键约束（每连接需单独开启）
        self.init_schema()
        self._migrate_if_needed()
        self._normalize_dimension_prefixes()

    # ------------------------------------------------------------------ #
    # 基础
    # ------------------------------------------------------------------ #
    def init_schema(self) -> None:
        """建表 + 索引"""
        self.conn.executescript(_SCHEMA_SQL)
        self.conn.commit()

    def _has_column(self, table: str, column: str) -> bool:
        """检查表是否包含某列（用于识别旧版数据库结构）"""
        cols = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r["name"] == column for r in cols)

    def _migrate_if_needed(self) -> None:
        """v1 → v2 迁移：
        旧版分类按 domain_id 归属单一领域；v2 改为全局分类 + domain_category 多对一关联。
        迁移：把旧版一级分类(parent_id IS NULL)生成领域关联，再移除 domain_id 遗留列。
        """
        if self.get_meta("schema_version") is not None:
            return
        if self._has_column("categories", "domain_id"):
            self.conn.execute(
                "INSERT OR IGNORE INTO domain_category(domain_id, category_id) "
                "SELECT domain_id, id FROM categories WHERE parent_id IS NULL"
            )
            try:
                self.conn.execute("ALTER TABLE categories DROP COLUMN domain_id")
            except sqlite3.OperationalError:
                pass  # 新库无该列时忽略
        self.set_meta("schema_version", SCHEMA_VERSION)

    def _normalize_dimension_prefixes(self) -> None:
        """归一化一级分类名称：移除"第X维度："前缀（幂等，兼容已按旧名导入的库）"""
        pat = re.compile(r"^第[一二三四五六七八九十百\d]+维度\s*[:：]?\s*")
        rows = self.conn.execute(
            "SELECT id, name FROM categories WHERE parent_id IS NULL"
        ).fetchall()
        changed = False
        for r in rows:
            new_name = pat.sub("", r["name"])
            if new_name != r["name"]:
                self.conn.execute(
                    "UPDATE categories SET name = ? WHERE id = ?", (new_name, r["id"])
                )
                changed = True
        if changed:
            self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def seed_preset_domains(self) -> None:
        """写入预置根目录（仅当表为空时）"""
        if self.list_domains():
            return
        for name in PRESET_DOMAINS:
            self.add_domain(name)

    def reset_content(self) -> None:
        """清除全部分类与条目（保留根目录与元信息）。

        2026-08-18（第020条，P2-R1 标注）：自 P0-2 起内置手册升级已改为"非破坏性合并"
        （见 md_parser.import_manual），本方法已无任何调用方，标记为【已废弃】，仅供
        需要时手动全量重建使用；常规流程不得调用（会清空用户数据）。
        """
        self.conn.execute("DELETE FROM categories")  # 关联表 domain_category 级联清理
        self.conn.execute("DELETE FROM entries")
        self.conn.commit()

    # ------------------------------------------------------------------ #
    # 元信息
    # ------------------------------------------------------------------ #
    def get_meta(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ #
    # 根目录 Domain
    # ------------------------------------------------------------------ #
    def add_domain(self, name: str) -> int:
        order = self.conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM domains").fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO domains(name, sort_order) VALUES(?, ?)", (name, order)
        )
        self.conn.commit()
        return cur.lastrowid

    def rename_domain(self, domain_id: int, new_name: str) -> None:
        self.conn.execute("UPDATE domains SET name = ? WHERE id = ?", (new_name, domain_id))
        self.conn.commit()

    def get_domain(self, domain_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM domains WHERE id = ?", (domain_id,)).fetchone()
        return dict(row) if row else None

    def list_domains(self) -> List[dict]:
        rows = self.conn.execute("SELECT * FROM domains ORDER BY sort_order, id").fetchall()
        return [dict(r) for r in rows]

    def delete_domain(self, domain_id: int) -> dict:
        """删除根目录：仅解除 领域↔一级分类 关联（分类与条目为共享数据，不删除）。
        返回受影响统计 {'categories': n, 'entries': m}（n/m 为该领域视角下的数量）
        """
        stat = self.count_domain_items(domain_id)
        self.conn.execute("DELETE FROM domains WHERE id = ?", (domain_id,))
        self.conn.commit()
        return stat

    def count_domain_items(self, domain_id: int) -> dict:
        """统计某根目录关联的分类数（一级+子分类）与条目数（用于删除确认弹窗提示）"""
        cat_ids = self._domain_category_ids(domain_id)
        entries = 0
        if cat_ids:
            ph = ",".join("?" * len(cat_ids))
            entries = self.conn.execute(
                f"SELECT COUNT(*) FROM entries WHERE category_id IN ({ph})", cat_ids
            ).fetchone()[0]
        return {"categories": len(cat_ids), "entries": entries}

    def _domain_category_ids(self, domain_id: int) -> List[int]:
        """某领域关联的一级分类及其全部子分类 id 集合"""
        l1_rows = self.conn.execute(
            "SELECT c.id FROM categories c JOIN domain_category dc ON dc.category_id = c.id "
            "WHERE dc.domain_id = ? AND c.parent_id IS NULL", (domain_id,)
        ).fetchall()
        ids = []
        for row in l1_rows:
            ids.extend(self._collect_category_ids(row["id"]))
        return ids

    # ------------------------------------------------------------------ #
    # 分类 Category
    # ------------------------------------------------------------------ #
    def add_category(self, name: str, parent_id: Optional[int] = None,
                     domain_id: Optional[int] = None) -> int:
        """新建分类（全局共享树）：
        - parent_id=None 表示一级分类，同时建立 领域↔分类 关联（支持多对一共享）
        - parent_id 指定则为子分类，无需 domain_id
        """
        order = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM categories WHERE parent_id IS ?",
            (parent_id,),
        ).fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO categories(parent_id, name, sort_order) VALUES(?, ?, ?)",
            (parent_id, name, order),
        )
        cid = cur.lastrowid
        if parent_id is None and domain_id is not None:
            self.link_domain_category(domain_id, cid)
        self.conn.commit()
        return cid

    def link_domain_category(self, domain_id: int, category_id: int) -> None:
        """将一级分类关联到某领域（多对一共享）；重复关联自动忽略"""
        self.conn.execute(
            "INSERT OR IGNORE INTO domain_category(domain_id, category_id) VALUES(?, ?)",
            (domain_id, category_id),
        )
        self.conn.commit()

    def linked_domains(self, category_id: int) -> List[dict]:
        """返回关联到该分类（一级）的领域列表"""
        rows = self.conn.execute(
            "SELECT d.* FROM domains d JOIN domain_category dc ON dc.domain_id = d.id "
            "WHERE dc.category_id = ? ORDER BY d.sort_order, d.id", (category_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def category_root(self, category_id: int) -> Optional[int]:
        """返回分类所属的顶级（一级）分类 id；无父级返回自身"""
        cid = category_id
        while True:
            c = self.get_category(cid)
            if not c or c["parent_id"] is None:
                return cid if c else None
            cid = c["parent_id"]

    def rename_category(self, category_id: int, new_name: str) -> None:
        self.conn.execute("UPDATE categories SET name = ? WHERE id = ?", (new_name, category_id))
        self.conn.commit()

    def get_category(self, category_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()
        return dict(row) if row else None

    def list_categories(self, domain_id: Optional[int] = None,
                        parent_id: Optional[int] = None) -> List[dict]:
        """列出分类：
        - parent_id 为 None：一级分类；若指定 domain_id 则仅返回该领域关联的一级分类
        - parent_id 指定：返回该分类的子分类
        """
        if parent_id is not None:
            rows = self.conn.execute(
                "SELECT * FROM categories WHERE parent_id = ? ORDER BY sort_order, id",
                (parent_id,),
            ).fetchall()
        elif domain_id is not None:
            rows = self.conn.execute(
                "SELECT c.* FROM categories c JOIN domain_category dc ON dc.category_id = c.id "
                "WHERE dc.domain_id = ? AND c.parent_id IS NULL "
                "ORDER BY c.sort_order, c.id",
                (domain_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM categories WHERE parent_id IS NULL ORDER BY sort_order, id"
            ).fetchall()
        return [dict(r) for r in rows]

    def count_descendants(self, category_id: int) -> dict:
        """递归统计某分类下的子分类数与条目数（用于删除确认弹窗提示）"""
        cats = 0
        entries = 0
        stack = [category_id]
        while stack:
            cid = stack.pop()
            children = self.conn.execute(
                "SELECT id FROM categories WHERE parent_id = ?", (cid,)
            ).fetchall()
            for child in children:
                cats += 1
                stack.append(child["id"])
            entries += self.conn.execute(
                "SELECT COUNT(*) FROM entries WHERE category_id = ?", (cid,)
            ).fetchone()[0]
        return {"categories": cats, "entries": entries}

    def category_has_children(self, category_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM categories WHERE parent_id = ? LIMIT 1", (category_id,)
        ).fetchone()
        return row is not None

    def delete_category(self, category_id: int) -> dict:
        """删除分类：级联删除子分类；其下条目经外键 SET NULL 自动转入未分类。
        返回受影响统计 {'categories': n, 'entries': m}
        """
        stat = self.count_descendants(category_id)
        self.conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        self.conn.commit()
        return stat

    # ------------------------------------------------------------------ #
    # 条目 Entry
    # ------------------------------------------------------------------ #
    @staticmethod
    def _entry_params(entry: Entry) -> tuple:
        return (
            entry.category_id, entry.name, entry.intro, entry.origin, entry.features,
            entry.scenes, entry.works, entry.image_desc, entry.prompt_cn, entry.prompt_en,
            entry.image_plan, entry.image_path, entry.is_favorite,
        )

    def add_entry(self, entry: Entry) -> int:
        ts = _now()
        cur = self.conn.execute(
            "INSERT INTO entries(category_id, name, intro, origin, features, scenes, works, "
            "image_desc, prompt_cn, prompt_en, image_plan, image_path, is_favorite, "
            "created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*self._entry_params(entry), ts, ts),
        )
        self.conn.commit()
        return cur.lastrowid

    def add_entries_batch(self, entries: list) -> int:
        """批量插入条目（单事务提交，比逐条 add_entry 快；JSON/Excel 大文件导入用）。

        2026-08-18（P2-4 新增）：避免大文件导入时逐条 commit 的性能与碎片化开销。
        """
        ts = _now()
        cols = ("category_id", "name", "intro", "origin", "features", "scenes", "works",
                "image_desc", "prompt_cn", "prompt_en", "image_plan", "image_path",
                "is_favorite", "created_at", "updated_at")
        ph = ",".join("?" * len(cols))
        params = [(*self._entry_params(e), ts, ts) for e in entries]
        self.conn.executemany(
            f"INSERT INTO entries({', '.join(cols)}) VALUES({ph})", params)
        self.conn.commit()
        return len(entries)

    # 参与"详情内容"判重的字段（不含收藏/图片/时间戳等附加属性）
    _CONTENT_FIELDS = ("name", "intro", "origin", "features", "scenes", "works",
                       "image_desc", "prompt_cn", "prompt_en", "image_plan")

    @staticmethod
    def content_key(row) -> str:
        """条目"详情内容"判重键：名称 + ②~⑩ 九个内容字段拼接。

        2026-08-18（P1-1 新增）：Excel/JSON 导入去重——仅当详情内容完全相同时视为重复，
        名称相同但内容不同仍会新增。兼容 dict（查询行/JSON 条目）与 Entry 对象。
        """
        parts = []
        for k in Database._CONTENT_FIELDS:
            if isinstance(row, dict):
                v = row.get(k) or ""
            else:
                v = getattr(row, k, "") or ""
            parts.append(str(v))
        return "\x1f".join(parts)

    def update_entry(self, entry: Entry) -> None:
        self.conn.execute(
            "UPDATE entries SET category_id = ?, name = ?, intro = ?, origin = ?, features = ?, "
            "scenes = ?, works = ?, image_desc = ?, prompt_cn = ?, prompt_en = ?, "
            "image_plan = ?, image_path = ?, is_favorite = ?, updated_at = ? WHERE id = ?",
            (*self._entry_params(entry), _now(), entry.id),
        )
        self.conn.commit()

    def get_entry(self, entry_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return dict(row) if row else None

    def delete_entry(self, entry_id: int) -> None:
        entry = self.get_entry(entry_id)
        if entry and entry.get("image_path"):
            self._remove_image_file(entry["image_path"])  # 同步删除关联图片（尽力而为）
        self.conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        self.conn.commit()

    def _remove_image_file(self, image_path: str) -> None:
        """删除条目关联的本地图片文件（相对 data/ 的路径，失败静默）。

        2026-08-18（P2-1 修复）：校验最终绝对路径仍在 data/ 目录内，防止越界读写。
        """
        try:
            root = os.path.abspath(data_dir())
            full = os.path.abspath(os.path.join(root, image_path))
            try:
                in_root = os.path.commonpath([root, full]) == root
            except ValueError:
                in_root = False  # 不同盘符等情况：视为越界，拒绝
            if not in_root:
                return  # 越出 data/ 目录（如被篡改为 ../、绝对路径或异盘路径），拒绝删除
            if os.path.isfile(full):
                os.remove(full)
        except OSError:
            pass

    def list_entries(self, category_id: int, include_descendants: bool = False) -> List[dict]:
        """列出某分类下的条目；include_descendants=True 时含所有子分类条目"""
        if include_descendants:
            ids = self._collect_category_ids(category_id)
            if not ids:
                return []
            ph = ",".join("?" * len(ids))
            rows = self.conn.execute(
                f"SELECT * FROM entries WHERE category_id IN ({ph}) ORDER BY updated_at DESC, id",
                ids,
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM entries WHERE category_id = ? ORDER BY updated_at DESC, id",
                (category_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def _collect_category_ids(self, category_id: int) -> List[int]:
        ids = []
        stack = [category_id]
        while stack:
            cid = stack.pop()
            ids.append(cid)
            children = self.conn.execute(
                "SELECT id FROM categories WHERE parent_id = ?", (cid,)
            ).fetchall()
            stack.extend(child["id"] for child in children)
        return ids

    def list_uncategorized(self) -> List[dict]:
        rows = self.conn.execute(
            "SELECT * FROM entries WHERE category_id IS NULL ORDER BY updated_at DESC, id"
        ).fetchall()
        return [dict(r) for r in rows]

    def list_favorites(self) -> List[dict]:
        rows = self.conn.execute(
            "SELECT * FROM entries WHERE is_favorite = 1 ORDER BY updated_at DESC, id"
        ).fetchall()
        return [dict(r) for r in rows]

    def list_all_entries(self) -> List[dict]:
        rows = self.conn.execute("SELECT * FROM entries ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def search(self, keyword: str) -> List[dict]:
        """全局搜索：匹配全部文本字段（名称/介绍/溯源/特征/场景/代表作/配图/中英提示词/图像方案）。

        2026-08-18（第020条，P2-B1 修复）：对 LIKE 通配符 % / _ 做转义（ESCAPE '\\'），
        使搜索含 % 或 _ 的关键词按字面匹配，避免意外通配匹配到多余结果。
        """
        # 转义顺序：先转义反斜杠自身，再转义 % 与 _（ESCAPE 字符为反斜杠）
        escaped = keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        kw = f"%{escaped}%"
        esc = " ESCAPE '\\' "
        rows = self.conn.execute(
            "SELECT * FROM entries WHERE name LIKE ?" + esc + "OR intro LIKE ?" + esc +
            "OR origin LIKE ?" + esc + "OR features LIKE ?" + esc + "OR scenes LIKE ?" + esc +
            "OR works LIKE ?" + esc + "OR image_desc LIKE ?" + esc + "OR prompt_cn LIKE ?" + esc +
            "OR prompt_en LIKE ?" + esc + "OR image_plan LIKE ?" + esc +
            "ORDER BY updated_at DESC, id",
            (kw,) * 10,
        ).fetchall()
        return [dict(r) for r in rows]

    def move_entry(self, entry_id: int, category_id: Optional[int]) -> None:
        """移动条目到指定分类；category_id=None 表示移入未分类"""
        self.conn.execute(
            "UPDATE entries SET category_id = ?, updated_at = ? WHERE id = ?",
            (category_id, _now(), entry_id),
        )
        self.conn.commit()

    def toggle_favorite(self, entry_id: int) -> int:
        """切换收藏状态，返回新状态(0/1)"""
        self.conn.execute(
            "UPDATE entries SET is_favorite = 1 - is_favorite, updated_at = ? WHERE id = ?",
            (_now(), entry_id),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT is_favorite FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return row["is_favorite"] if row else 0

    def set_entry_image(self, entry_id: int, image_path: str) -> None:
        """更新条目的关联图片路径（阶段三：图片预览）"""
        self.conn.execute(
            "UPDATE entries SET image_path = ?, updated_at = ? WHERE id = ?",
            (image_path, _now(), entry_id),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ #
    # 复制/移动分类子树（2026-08-21 第004条新增：各级目录"复制到/移动到"）
    # 语义（经用户两轮确认，见 20260821_PromptSprite_04 开发工作记录 第003条）：
    #   - 复制 = 多重关联 / 新建改名副本（源保留）；移动 = 调整关联 / 改名重挂载 + 删除源
    #   - 级别永不改变（平铺子级保持原级别，不产生三级）；下移加前缀、上移去前缀、重名加序号
    # ------------------------------------------------------------------ #
    def unlink_domain_category(self, domain_id: int, category_id: int) -> None:
        """解除 根目录↔一级分类 关联（移动=调整关联关系）"""
        self.conn.execute(
            "DELETE FROM domain_category WHERE domain_id = ? AND category_id = ?",
            (domain_id, category_id),
        )
        self.conn.commit()

    @staticmethod
    def strip_prefix(name: str) -> str:
        """上移去前缀：去掉最左侧 'xxx.' 前缀段（对话框默认值，用户可编辑）"""
        return name.split(".", 1)[1] if "." in name else name

    def _next_domain_order_tx(self) -> int:
        """事务内：取下一个根目录排序号（不 commit）"""
        return self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM domains"
        ).fetchone()[0]

    def _domain_name_exists(self, name: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM domains WHERE name = ? LIMIT 1", (name,)).fetchone()
        return row is not None

    def unique_domain_name(self, name: str) -> str:
        """新建根目录名去重（name → name(2) → ...；domains.name 有 UNIQUE 约束）"""
        if not self._domain_name_exists(name):
            return name
        i = 2
        while self._domain_name_exists(f"{name}({i})"):
            i += 1
        return f"{name}({i})"

    def category_name_exists(self, name: str, parent_id: Optional[int] = None,
                             domain_id: Optional[int] = None) -> bool:
        """检测分类名在目标位置是否已存在：
        - parent_id 非空 → 该父级下的子分类；
        - parent_id 为空且 domain_id 非空 → 该根目录关联的一级分类。
        """
        if parent_id is not None:
            row = self.conn.execute(
                "SELECT 1 FROM categories WHERE parent_id = ? AND name = ? LIMIT 1",
                (parent_id, name),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT 1 FROM categories c JOIN domain_category dc ON dc.category_id = c.id "
                "WHERE dc.domain_id = ? AND c.parent_id IS NULL AND c.name = ? LIMIT 1",
                (domain_id, name),
            ).fetchone()
        return row is not None

    def unique_category_name(self, name: str, parent_id: Optional[int] = None,
                             domain_id: Optional[int] = None) -> str:
        """目标位置下分类名自动加序号：name → name(2) → name(3) ..."""
        if not self.category_name_exists(name, parent_id=parent_id, domain_id=domain_id):
            return name
        i = 2
        while self.category_name_exists(f"{name}({i})", parent_id=parent_id, domain_id=domain_id):
            i += 1
        return f"{name}({i})"

    def _insert_category_tx(self, parent_id: Optional[int], name: str,
                            new_domain_id: Optional[int] = None) -> int:
        """事务内新建分类（不 commit）；parent_id=None 且给 new_domain_id 时建立根目录关联"""
        order = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM categories WHERE parent_id IS ?",
            (parent_id,),
        ).fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO categories(parent_id, name, sort_order) VALUES(?, ?, ?)",
            (parent_id, name, order),
        )
        cid = cur.lastrowid
        if parent_id is None and new_domain_id is not None:
            self.conn.execute(
                "INSERT OR IGNORE INTO domain_category(domain_id, category_id) VALUES(?, ?)",
                (new_domain_id, cid),
            )
        return cid

    def _copy_subtree_tx(self, src_id: int, new_parent_id: Optional[int], new_name: str,
                         new_domain_id: Optional[int] = None) -> int:
        """事务内深拷贝分类子树（含条目；条目图片引用同一文件，不 commit）。返回新分类 id"""
        cid = self._insert_category_tx(new_parent_id, new_name, new_domain_id)
        ts = _now()
        for e in self.list_entries(src_id):
            entry = Entry(**{**e, "id": None, "category_id": cid})
            self.conn.execute(
                "INSERT INTO entries(category_id, name, intro, origin, features, scenes, works, "
                "image_desc, prompt_cn, prompt_en, image_plan, image_path, is_favorite, "
                "created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*self._entry_params(entry), ts, ts),
            )
        for child in self.list_categories(parent_id=src_id):
            self._copy_subtree_tx(child["id"], cid, child["name"])
        return cid

    # ---- 根目录 A → 根目录 B（作 B 的一级分类） ----
    def move_domain_to_domain(self, domain_id: int, target_domain_id: int) -> dict:
        """移动根目录 A 到根目录 B 下作为一级分类：
        1) A 的每个一级分类 C：仅被 A 关联 → 改名 'A.C' 并建立与 B 的关联；
           被多根目录共享 → 复制改名副本 'A.C' 挂 B 下（原共享分类保留原名）；
        2) 删除根目录 A。E、F 等二级分类级别与名称不变。
        """
        if domain_id == target_domain_id:
            raise ValueError("不能移动到自身")
        src = self.get_domain(domain_id)
        target = self.get_domain(target_domain_id)
        if not src or not target:
            raise ValueError("根目录不存在")
        prefix = src["name"] + "."
        stats = {"renamed": 0, "copied": 0}
        try:
            with self.conn:
                for l1 in self.list_categories(domain_id=domain_id, parent_id=None):
                    new_name = self.unique_category_name(prefix + l1["name"],
                                                         domain_id=target_domain_id)
                    if len(self.linked_domains(l1["id"])) <= 1:
                        # 仅被 A 关联：改名 + 建立与 B 的关联
                        self.conn.execute("UPDATE categories SET name = ? WHERE id = ?",
                                          (new_name, l1["id"]))
                        self.conn.execute(
                            "INSERT OR IGNORE INTO domain_category(domain_id, category_id) "
                            "VALUES(?, ?)", (target_domain_id, l1["id"]),
                        )
                        stats["renamed"] += 1
                    else:
                        # 被多根目录共享：复制改名副本，原共享分类保留原名
                        self._copy_subtree_tx(l1["id"], None, new_name, target_domain_id)
                        stats["copied"] += 1
                self.conn.execute("DELETE FROM domains WHERE id = ?", (domain_id,))
            return stats
        except Exception:
            self.conn.rollback()
            raise

    def copy_domain_to_domain(self, domain_id: int, target_domain_id: int) -> dict:
        """复制根目录 A 到根目录 B 下作为一级分类：为 A 的每个一级分类建改名副本 'A.C' 挂 B 下，A 保留"""
        if domain_id == target_domain_id:
            raise ValueError("不能复制到自身")
        src = self.get_domain(domain_id)
        target = self.get_domain(target_domain_id)
        if not src or not target:
            raise ValueError("根目录不存在")
        prefix = src["name"] + "."
        stats = {"copied": 0}
        try:
            with self.conn:
                for l1 in self.list_categories(domain_id=domain_id, parent_id=None):
                    new_name = self.unique_category_name(prefix + l1["name"],
                                                         domain_id=target_domain_id)
                    self._copy_subtree_tx(l1["id"], None, new_name, target_domain_id)
                    stats["copied"] += 1
            return stats
        except Exception:
            self.conn.rollback()
            raise

    # ---- 一级分类 C ----
    def move_l1_to_domain(self, category_id: int, from_domain_id: int,
                          target_domain_id: Optional[int] = None,
                          new_name: Optional[str] = None) -> dict:
        """移动一级分类 C：将 C 的关联关系从 from_domain 调整为 target_domain（同级平移，不改名）；
        target_domain_id=None → 新建根目录项（默认名去前缀，可传 new_name 指定）。"""
        cat = self.get_category(category_id)
        if not cat or cat["parent_id"] is not None:
            raise ValueError("仅支持一级分类")
        if target_domain_id is None:
            new_dom = self.unique_domain_name(new_name or self.strip_prefix(cat["name"]))
            try:
                with self.conn:
                    cur = self.conn.execute(
                        "INSERT INTO domains(name, sort_order) VALUES(?, ?)",
                        (new_dom, self._next_domain_order_tx()),
                    )
                    target_domain_id = cur.lastrowid
            except Exception:
                self.conn.rollback()
                raise
        if from_domain_id == target_domain_id:
            raise ValueError("目标根目录与来源相同")
        try:
            with self.conn:
                self.conn.execute(
                    "DELETE FROM domain_category WHERE domain_id = ? AND category_id = ?",
                    (from_domain_id, category_id),
                )
                self.conn.execute(
                    "INSERT OR IGNORE INTO domain_category(domain_id, category_id) VALUES(?, ?)",
                    (target_domain_id, category_id),
                )
            return {"domain_id": target_domain_id}
        except Exception:
            self.conn.rollback()
            raise

    def copy_l1_to_domain(self, category_id: int,
                          target_domain_id: Optional[int] = None,
                          new_name: Optional[str] = None) -> dict:
        """复制一级分类 C 到根目录：多重关联（目标根目录建立关联，保留原关联）；
        target_domain_id=None → 新建根目录项（默认名去前缀，可传 new_name 指定）。"""
        cat = self.get_category(category_id)
        if not cat or cat["parent_id"] is not None:
            raise ValueError("仅支持一级分类")
        if target_domain_id is None:
            new_dom = self.unique_domain_name(new_name or self.strip_prefix(cat["name"]))
            try:
                with self.conn:
                    cur = self.conn.execute(
                        "INSERT INTO domains(name, sort_order) VALUES(?, ?)",
                        (new_dom, self._next_domain_order_tx()),
                    )
                    target_domain_id = cur.lastrowid
            except Exception:
                self.conn.rollback()
                raise
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT OR IGNORE INTO domain_category(domain_id, category_id) VALUES(?, ?)",
                    (target_domain_id, category_id),
                )
            return {"domain_id": target_domain_id}
        except Exception:
            self.conn.rollback()
            raise

    def move_l1_to_l2(self, category_id: int, target_l1_id: int) -> dict:
        """移动一级分类 C 到一级分类 D 下作二级：C 的直接子级改名加前缀(C.)并挂到 D 下（级别不变），删除 C"""
        src = self.get_category(category_id)
        target = self.get_category(target_l1_id)
        if not src or src["parent_id"] is not None:
            raise ValueError("仅支持一级分类")
        if not target or target["parent_id"] is not None:
            raise ValueError("目标必须是一级分类")
        if category_id == target_l1_id:
            raise ValueError("不能移动到自身")
        prefix = src["name"] + "."
        stats = {"children": 0}
        try:
            with self.conn:
                for child in self.list_categories(parent_id=category_id):
                    new_name = self.unique_category_name(prefix + child["name"],
                                                         parent_id=target_l1_id)
                    self.conn.execute("UPDATE categories SET name = ?, parent_id = ? WHERE id = ?",
                                      (new_name, target_l1_id, child["id"]))
                    stats["children"] += 1
                # 2026-08-21（第005条修复）：源一级分类直接挂载的条目先迁移到目标一级分类下，
                # 避免删除源分类时因外键 ON DELETE SET NULL 转入未分类（复制/移动不删除条目）
                self.conn.execute(
                    "UPDATE entries SET category_id = ?, updated_at = ? WHERE category_id = ?",
                    (target_l1_id, _now(), category_id),
                )
                # 源一级分类已无子级，安全删除（其根目录关联级联清理）
                self.conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
            return stats
        except Exception:
            self.conn.rollback()
            raise

    def copy_l1_to_l2(self, category_id: int, target_l1_id: int) -> dict:
        """复制一级分类 C 到一级分类 D 下作二级：C 的直接子级复制改名副本(C.)挂 D 下，C 保留"""
        src = self.get_category(category_id)
        target = self.get_category(target_l1_id)
        if not src or src["parent_id"] is not None:
            raise ValueError("仅支持一级分类")
        if not target or target["parent_id"] is not None:
            raise ValueError("目标必须是一级分类")
        if category_id == target_l1_id:
            raise ValueError("不能复制到自身")
        prefix = src["name"] + "."
        stats = {"children": 0}
        try:
            with self.conn:
                for child in self.list_categories(parent_id=category_id):
                    new_name = self.unique_category_name(prefix + child["name"],
                                                         parent_id=target_l1_id)
                    self._copy_subtree_tx(child["id"], target_l1_id, new_name)
                    stats["children"] += 1
            return stats
        except Exception:
            self.conn.rollback()
            raise

    # ---- 二级分类 E ----
    def move_l2_to_domain(self, category_id: int,
                          target_domain_id: Optional[int] = None) -> dict:
        """移动二级分类 E 到根目录下作一级：E 提升为一级（parent 置空）并关联目标根目录；
        target_domain_id=None → 新建根目录项（默认名去前缀）。"""
        cat = self.get_category(category_id)
        if not cat or cat["parent_id"] is None:
            raise ValueError("仅支持二级分类")
        if target_domain_id is None:
            new_dom = self.unique_domain_name(self.strip_prefix(cat["name"]))
            try:
                with self.conn:
                    cur = self.conn.execute(
                        "INSERT INTO domains(name, sort_order) VALUES(?, ?)",
                        (new_dom, self._next_domain_order_tx()),
                    )
                    target_domain_id = cur.lastrowid
            except Exception:
                self.conn.rollback()
                raise
        try:
            with self.conn:
                self.conn.execute("UPDATE categories SET parent_id = NULL WHERE id = ?",
                                  (category_id,))
                self.conn.execute(
                    "INSERT OR IGNORE INTO domain_category(domain_id, category_id) VALUES(?, ?)",
                    (target_domain_id, category_id),
                )
            return {"domain_id": target_domain_id}
        except Exception:
            self.conn.rollback()
            raise

    def copy_l2_to_domain(self, category_id: int,
                          target_domain_id: Optional[int] = None,
                          new_name: Optional[str] = None) -> dict:
        """复制二级分类 E 到根目录下作一级：建改名副本（默认去前缀）提升为一级并关联目标根目录；E 保留"""
        cat = self.get_category(category_id)
        if not cat or cat["parent_id"] is None:
            raise ValueError("仅支持二级分类")
        base = new_name or self.strip_prefix(cat["name"])
        if target_domain_id is not None:
            new_name = self.unique_category_name(base, domain_id=target_domain_id)
        else:
            new_name = base
        if target_domain_id is None:
            new_dom = self.unique_domain_name(new_name)
            try:
                with self.conn:
                    cur = self.conn.execute(
                        "INSERT INTO domains(name, sort_order) VALUES(?, ?)",
                        (new_dom, self._next_domain_order_tx()),
                    )
                    target_domain_id = cur.lastrowid
            except Exception:
                self.conn.rollback()
                raise
        try:
            with self.conn:
                new_cat_id = self._copy_subtree_tx(category_id, None, new_name, target_domain_id)
            return {"category_id": new_cat_id, "domain_id": target_domain_id}
        except Exception:
            self.conn.rollback()
            raise

    def move_l2_to_l2(self, category_id: int, target_l1_id: int) -> dict:
        """移动二级分类 E 到一级分类 D 下作二级：同级平移（parent 改 D，名称不变）"""
        cat = self.get_category(category_id)
        if not cat or cat["parent_id"] is None:
            raise ValueError("仅支持二级分类")
        target = self.get_category(target_l1_id)
        if not target or target["parent_id"] is not None:
            raise ValueError("目标必须是一级分类")
        if cat["parent_id"] == target_l1_id:
            raise ValueError("目标与当前父级相同")
        try:
            with self.conn:
                self.conn.execute("UPDATE categories SET parent_id = ? WHERE id = ?",
                                  (target_l1_id, category_id))
            return {}
        except Exception:
            self.conn.rollback()
            raise

    def copy_l2_to_l2(self, category_id: int, target_l1_id: int) -> dict:
        """复制二级分类 E 到一级分类 D 下作二级：建副本挂 D 下（重名自动加序号），E 保留"""
        cat = self.get_category(category_id)
        if not cat or cat["parent_id"] is None:
            raise ValueError("仅支持二级分类")
        target = self.get_category(target_l1_id)
        if not target or target["parent_id"] is not None:
            raise ValueError("目标必须是一级分类")
        if cat["parent_id"] == target_l1_id:
            raise ValueError("目标与当前父级相同")
        new_name = self.unique_category_name(cat["name"], parent_id=target_l1_id)
        try:
            with self.conn:
                new_cat_id = self._copy_subtree_tx(category_id, target_l1_id, new_name)
            return {"category_id": new_cat_id}
        except Exception:
            self.conn.rollback()
            raise

    # ------------------------------------------------------------------ #
    # 统计
    # ------------------------------------------------------------------ #
    def stats(self) -> dict:
        return {
            "domains": self.conn.execute("SELECT COUNT(*) FROM domains").fetchone()[0],
            "categories": self.conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0],
            "entries": self.conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0],
            "uncategorized": self.conn.execute(
                "SELECT COUNT(*) FROM entries WHERE category_id IS NULL"
            ).fetchone()[0],
            "favorites": self.conn.execute(
                "SELECT COUNT(*) FROM entries WHERE is_favorite = 1"
            ).fetchone()[0],
        }


# ---------------------------------------------------------------------- #
# 自测
# ---------------------------------------------------------------------- #
def _selftest() -> None:
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="promptsprite_selftest_")
    db = Database(os.path.join(tmp, "test.db"))
    try:
        # 1. 预置根目录
        db.seed_preset_domains()
        domains = db.list_domains()
        # 2026-08-18 15:50：断言由 7 个预置根目录更新为 8 个（新增"视觉风格分类"，P0-1 修复）
        assert len(domains) == 8, f"预置根目录应为8个，实际 {len(domains)}"
        assert [d["name"] for d in domains] == ["计算机编程", "视频", "图像", "音频", "文学", "学术", "专业报告", "视觉风格分类"]
        print("[1] 预置根目录 通过")

        # 2. 根目录增删改查
        vid_id = domains[1]["id"]
        extra_id = db.add_domain("测试域")
        assert db.get_domain(extra_id)["name"] == "测试域"
        db.rename_domain(extra_id, "测试域2")
        assert db.get_domain(extra_id)["name"] == "测试域2"
        print("[2] 根目录增删改查 通过")

        # 3. 分类（L1维度 → L2大类）+ 多对一共享
        l1 = db.add_category("第一维度：按媒介&艺术载体总分类", domain_id=vid_id)
        l2 = db.add_category("写实影像类", parent_id=l1)
        assert db.list_categories(domain_id=vid_id, parent_id=None)[0]["name"] == "第一维度：按媒介&艺术载体总分类"
        assert db.list_categories(domain_id=vid_id, parent_id=l1)[0]["name"] == "写实影像类"
        # 同一 L1 关联到 图像 领域（多对一）
        img_id = domains[2]["id"]
        db.link_domain_category(img_id, l1)
        assert db.list_categories(domain_id=img_id, parent_id=None)[0]["name"] == "第一维度：按媒介&艺术载体总分类"
        assert len(db.linked_domains(l1)) == 2
        print("[3] 二级分类 + 多对一共享 通过")

        # 4. 条目新增 + 9字段回读
        e = Entry(
            category_id=l2, name="35mm电影胶片风",
            intro="好莱坞院线标准商业电影写实基底",
            origin="1960年后好莱坞35mm胶片工业体系",
            features="2.39:1宽遮幅，橙蓝冷暖对冲",
            scenes="都市情感短剧、悬疑犯罪",
            works="《盗梦空间》《流浪地球》",
            image_desc="雨夜城市街道，冷蓝夜色搭配暖橙路灯",
            prompt_cn="4K超高清，2.39:1宽幅遮幅电影画面，35mm胶片实拍…",
            prompt_en="4K ultra HD, 2.39:1 widescreen cinematic frame…",
            image_plan="绘图工具设置比例21:9、4K分辨率",
        )
        eid = db.add_entry(e)
        got = db.get_entry(eid)
        assert got["name"] == "35mm电影胶片风" and got["prompt_cn"].startswith("4K超高清")
        assert got["category_id"] == l2 and got["prompt_en"].startswith("4K ultra HD")
        print("[4] 条目新增/9字段回读 通过")

        # 5. 编辑保存
        e2 = Entry(**{k: got[k] for k in got})
        e2.name = "35mm电影胶片风（新版）"
        e2.features += "；新增特征测试"
        db.update_entry(e2)
        got2 = db.get_entry(eid)
        assert got2["name"] == "35mm电影胶片风（新版）" and "新增特征测试" in got2["features"]
        print("[5] 条目编辑 通过")

        # 6. 搜索
        hits = db.search("胶片")
        assert any(h["id"] == eid for h in hits)
        print("[6] 全局搜索 通过")

        # 7. 收藏
        assert db.toggle_favorite(eid) == 1
        assert any(f["id"] == eid for f in db.list_favorites())
        assert db.toggle_favorite(eid) == 0
        print("[7] 收藏切换 通过")

        # 8. 未分类与移动
        u1 = db.add_entry(Entry(name="未分类测试条目"))
        assert any(u["name"] == "未分类测试条目" for u in db.list_uncategorized())
        db.move_entry(u1, l1)
        assert db.get_entry(u1)["category_id"] == l1
        db.move_entry(u1, None)
        assert db.get_entry(u1)["category_id"] is None
        print("[8] 未分类/移动 通过")

        # 9. 删除分类 → 条目自动转入未分类
        stat = db.count_descendants(l1)
        assert stat == {"categories": 1, "entries": 1}, f"统计异常 {stat}"
        deleted = db.delete_category(l1)
        assert deleted == {"categories": 1, "entries": 1}
        assert db.get_entry(eid)["category_id"] is None
        print("[9] 删除分类转入未分类 通过")

        # 10. 删除根目录 → 仅解除关联，共享分类/条目保留
        # 2026-08-18 15:52：原断言依赖 [9] 已删除的 l1，必然失败；改为重建共享分类后验证
        l1b = db.add_category("共享维度B", domain_id=vid_id)
        db.link_domain_category(img_id, l1b)  # 同一级分类再关联到图像领域（多对一）
        dstat = db.delete_domain(extra_id)
        assert dstat == {"categories": 0, "entries": 0}
        db.delete_domain(img_id)  # 删除图像领域
        assert db.list_categories(domain_id=img_id, parent_id=None) == []  # 图像领域视角为空
        assert db.list_categories(domain_id=vid_id, parent_id=None)[0]["name"] == "共享维度B"  # 视频领域仍可见
        print("[10] 删除根目录仅解除关联 通过")

        # 11. 元信息
        db.set_meta("k", "v")
        assert db.get_meta("k") == "v"
        print("[11] 元信息 通过")

        # 12. 根目录→根目录 移动/复制（2026-08-21 第004条新增）
        da = db.add_domain("A")
        dbx = db.add_domain("B")
        c1 = db.add_category("C", domain_id=da)
        c2 = db.add_category("D", domain_id=da)
        e1 = db.add_category("E", parent_id=c1)
        e2 = db.add_category("F", parent_id=c1)
        en1 = db.add_entry(Entry(name="EF条目", category_id=e1))
        # 12.1 移动根目录 A → B：C/D 改名 A.C/A.D 并关联 B，E/F 不变，A 删除
        db.move_domain_to_domain(da, dbx)
        assert db.get_domain(da) is None
        assert db.get_category(c1)["name"] == "A.C"
        assert db.get_category(c2)["name"] == "A.D"
        assert db.get_category(c1)["parent_id"] is None
        assert db.get_category(e1)["name"] == "E" and db.get_category(e1)["parent_id"] == c1
        assert db.get_category(e2)["name"] == "F"
        assert db.get_entry(en1)["category_id"] == e1
        assert any(x["id"] == dbx for x in db.linked_domains(c1))
        print("[12] 移动根目录→另一根目录(作一级) 通过")
        # 12.2 复制根目录 B → 新根目录 C：副本名 B.A.C/B.A.D 且带子树条目
        dc = db.add_domain("C")
        db.copy_domain_to_domain(dbx, dc)
        copied = db.list_categories(domain_id=dc, parent_id=None)
        assert sorted(c["name"] for c in copied) == ["B.A.C", "B.A.D"]
        b_ac = [c for c in copied if c["name"] == "B.A.C"][0]
        subs = db.list_categories(parent_id=b_ac["id"])
        assert sorted(s["name"] for s in subs) == ["E", "F"]
        assert len(db.list_entries(subs[0]["id"])) == 1
        print("[13] 复制根目录→新根目录(作一级) 通过")
        # 12.3 移动一级分类到"新建根目录项"（默认名去前缀，重名加序号）
        new_d = db.move_l1_to_domain(c1, dbx, None)
        assert db.get_domain(new_d["domain_id"])["name"] == "C(2)"  # 域"C"已存在
        assert not any(x["id"] == dbx for x in db.linked_domains(c1))
        assert any(x["id"] == new_d["domain_id"] for x in db.linked_domains(c1))
        print("[14] 移动一级分类→新建根目录项 通过")
        # 12.4 复制一级分类到根目录（多重关联）
        db.copy_l1_to_domain(c1, dc)
        ids = [x["id"] for x in db.linked_domains(c1)]
        assert new_d["domain_id"] in ids and dc in ids
        print("[15] 复制一级分类→根目录(多重关联) 通过")
        # 12.5 移动一级分类 → 一级分类下作二级（子级改名加前缀；源直挂条目随迁）
        l1g = db.add_category("G", domain_id=dbx)
        l2h = db.add_category("H", parent_id=l1g)
        l2i = db.add_category("I", parent_id=l1g)
        en2 = db.add_entry(Entry(name="HI条目", category_id=l2h))
        en3 = db.add_entry(Entry(name="G直挂条目", category_id=l1g))  # 005 修复验证
        db.move_l1_to_l2(l1g, b_ac["id"])
        assert db.get_category(l1g) is None
        assert db.get_category(l2h)["name"] == "G.H"
        assert db.get_category(l2h)["parent_id"] == b_ac["id"]
        assert db.get_category(l2i)["name"] == "G.I"
        assert db.get_entry(en2)["category_id"] == l2h
        # 005 修复：源一级分类直接挂载的条目迁移到目标一级分类，不转入未分类
        assert db.get_entry(en3)["category_id"] == b_ac["id"]
        assert db.get_entry(en3)["name"] == "G直挂条目"
        print("[16] 移动一级分类→一级分类下作二级 通过")
        # 12.6 复制一级分类 → 一级分类下作二级（源保留）
        l1j = db.add_category("J", domain_id=dc)
        l2k = db.add_category("K", parent_id=l1j)
        db.copy_l1_to_l2(l1j, b_ac["id"])
        assert any(s["name"] == "J.K" for s in db.list_categories(parent_id=b_ac["id"]))
        assert db.get_category(l1j) is not None and db.get_category(l2k)["parent_id"] == l1j
        print("[17] 复制一级分类→一级分类下作二级 通过")
        # 12.7 二级分类 移动/复制
        e_new = db.move_l2_to_domain(e1, None)  # E 提升为一级 + 新建根目录项(名 E)
        assert db.get_category(e1)["parent_id"] is None
        assert db.get_domain(e_new["domain_id"])["name"] == "E"
        assert any(x["id"] == e_new["domain_id"] for x in db.linked_domains(e1))
        db.copy_l2_to_l2(e2, b_ac["id"])
        assert any(s["name"] == "F" for s in db.list_categories(parent_id=b_ac["id"]))
        assert db.get_category(e2)["parent_id"] == c1
        db.move_l2_to_l2(e2, b_ac["id"])
        assert db.get_category(e2)["parent_id"] == b_ac["id"]
        print("[18] 二级分类 移动/复制 通过")
        # 12.8 重名自动加序号
        l2h2 = db.add_category("H", parent_id=l1j)
        db.copy_l2_to_l2(l2h2, b_ac["id"])
        db.copy_l2_to_l2(l2h2, b_ac["id"])
        names = [s["name"] for s in db.list_categories(parent_id=b_ac["id"])]
        assert names.count("H") == 1 and names.count("H(2)") == 1
        print("[19] 重名自动加序号 通过")
        # 12.9 共享一级分类 → 移动根目录时退化为复制副本
        dom_p = db.add_domain("P")
        dom_q = db.add_domain("Q")
        l1m = db.add_category("M", domain_id=dom_p)
        db.link_domain_category(dom_q, l1m)
        dom_r = db.add_domain("R")
        st = db.move_domain_to_domain(dom_p, dom_r)
        assert st == {"renamed": 0, "copied": 1}
        assert db.get_category(l1m)["name"] == "M"
        rm = db.list_categories(domain_id=dom_r, parent_id=None)
        assert len(rm) == 1 and rm[0]["name"] == "P.M"
        assert any(x["id"] == dom_q for x in db.linked_domains(l1m))
        print("[20] 共享分类移动退化为复制 通过")

        print(f"[统计] {db.stats()}")
        print("=== 数据库层全部自测通过 ===")
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    _selftest()
