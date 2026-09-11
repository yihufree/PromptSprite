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
import json  # 2026-08-29（增量备份增强）：删除日志名称链序列化
import os
import re
import shutil  # 2026-09-07：条目"复制到"独立副本时复制关联图片文件
import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional

from .config import (data_dir, IMAGES_DIR_NAME, PRESET_DOMAINS, PROJECT_PRESETS,
                     PROJECT_FALLBACK, PROJECT_DOMAIN_MAPPING)
from .models import Entry  # 2026-08-18（P2-5）：Domain/Category 冗余数据类已删除，仅保留 Entry


def _now() -> str:
    """当前时间字符串（用于 created_at / updated_at）"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 建表 SQL（schema v3，2026-08-29 四级分类施工）：
#  - 新增 projects 表：项目类别（最高层级），domains.project_id 归属项目类别
#  - 分类为全局共享树（不再归属单一领域），通过 domain_category 实现 领域↔一级分类 多对一关联
#  - 外键：分类删除级联子分类；条目删除分类置 NULL(转入未分类)；领域删除仅解除关联（共享数据保留）
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    sort_order  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS domains (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    sort_order  INTEGER DEFAULT 0,
    project_id  INTEGER REFERENCES projects(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id   INTEGER DEFAULT NULL REFERENCES categories(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    sort_order  INTEGER DEFAULT 0,
    created_at  TEXT,      -- 2026-08-29（增量备份增强）：分类创建时间（支持"新增空分类"增量）
    updated_at  TEXT       -- 分类最后修改时间（改名/移动等）
);

CREATE TABLE IF NOT EXISTS domain_category (
    domain_id   INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    PRIMARY KEY (domain_id, category_id)
);

-- 2026-08-29（增量备份增强）：删除日志——记录被删除的条目/分类/根目录，
-- 供每日增量备份同步"删除操作"到其他电脑（导入时按名称链/内容键应用删除）。
CREATE TABLE IF NOT EXISTS deletion_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,          -- 'entry' | 'category' | 'domain'
    name        TEXT DEFAULT '',        -- 被删对象名称
    chain       TEXT DEFAULT '',        -- JSON 名称链（一级/二级…），entry/category 用
    content_key TEXT DEFAULT '',        -- 条目"详情内容"判重键（entry 用）
    payload     TEXT DEFAULT '',        -- 2026-09-08（V1.7.0）：被删条目完整快照(JSON)，支持"携带被删快照"导出/⑤逆向恢复
    deleted_at  TEXT
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

-- 2026-09-07（条目多位置施工）：条目与分类的"额外关联"（entry_links）。
-- 主挂靠仍存 entries.category_id；本表只存"额外位置"，两表取并集即条目全部可见位置。
-- 任一表被删除行时级联清理（避免悬空关联）。
CREATE TABLE IF NOT EXISTS entry_links (
    entry_id    INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    created_at  TEXT,
    PRIMARY KEY (entry_id, category_id)
);

CREATE INDEX IF NOT EXISTS idx_categories_parent  ON categories(parent_id);
CREATE INDEX IF NOT EXISTS idx_dc_domain          ON domain_category(domain_id);
CREATE INDEX IF NOT EXISTS idx_dc_category        ON domain_category(category_id);
CREATE INDEX IF NOT EXISTS idx_entries_category   ON entries(category_id);
CREATE INDEX IF NOT EXISTS idx_entries_favorite   ON entries(is_favorite);
CREATE INDEX IF NOT EXISTS idx_entry_links_cat    ON entry_links(category_id);

-- 2026-09-07（第2条改进）：回收站/删除历史——保存被删条目的完整快照，支持恢复
CREATE TABLE IF NOT EXISTS trash (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    payload     TEXT NOT NULL,          -- JSON：完整条目字段 + locations 位置列表 + chain 名称链 + 原 id
    deleted_at  TEXT,
    reason      TEXT DEFAULT ''         -- 删除来源：手动删除 / 级联删除
);
CREATE INDEX IF NOT EXISTS idx_trash_deleted ON trash(deleted_at);
"""

# 数据库结构版本（meta 键 schema_version）；v1=旧版按领域归属分类，v2=全局分类+领域关联，v3=四级分类（项目类别）
SCHEMA_VERSION = "3"


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
        """结构迁移：v1 → v2 → v3 按序执行（幂等）＋ v3 增量增强补列。

        - v1→v2：旧版分类按 domain_id 归属单一领域 → 全局分类 + domain_category 多对一关联；
        - v2→v3：新增 projects 表 + domains.project_id 列 + 预置项目类别（四级分类最高层级）；
        - v3 增强（2026-08-29）：categories 加 created_at/updated_at 列（历史数据回填为旧时间戳，
          避免首次增量误把存量分类当"今日新增"）、新建 deletion_log 删除日志表。
        注：v2→v3 仅做"结构"升级（建表/加列/预置），不移动任何数据；
        根目录→项目类别的"归属分配"由 assign_domains_to_projects() 执行（迁移向导/自动迁移）。
        """
        self._migrate_v1_to_v2()
        self._migrate_v2_to_v3()
        self._ensure_v3_enhancements()

    def _ensure_v3_enhancements(self) -> None:
        """v3 增量备份增强（幂等）：categories 时间戳列 + deletion_log 表 + entry_links 表。
        历史分类回填为固定旧时间戳（1970-01-01），保证首次增量不误报存量分类。
        """
        if not self._has_column("categories", "created_at"):
            self.conn.execute(
                "ALTER TABLE categories ADD COLUMN created_at TEXT")
        if not self._has_column("categories", "updated_at"):
            self.conn.execute(
                "ALTER TABLE categories ADD COLUMN updated_at TEXT")
        self.conn.executescript(
            "CREATE TABLE IF NOT EXISTS deletion_log ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " kind TEXT NOT NULL, name TEXT DEFAULT '', chain TEXT DEFAULT '',"
            " content_key TEXT DEFAULT '', deleted_at TEXT)")
        # 2026-09-08（V1.7.0）：删除日志补 payload 列（被删条目完整快照，⑤逆向恢复用）
        if not self._has_column("deletion_log", "payload"):
            self.conn.execute(
                "ALTER TABLE deletion_log ADD COLUMN payload TEXT DEFAULT ''")
        # 2026-09-07（条目多位置施工）：存量库幂等补建 entry_links 表 + 索引
        self.conn.executescript(
            "CREATE TABLE IF NOT EXISTS entry_links ("
            " entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,"
            " category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,"
            " created_at TEXT,"
            " PRIMARY KEY (entry_id, category_id))")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entry_links_cat ON entry_links(category_id)")
        # 2026-09-07（第2条改进）：存量库幂等补建 trash 回收站表 + 索引
        self.conn.executescript(
            "CREATE TABLE IF NOT EXISTS trash ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " name TEXT NOT NULL, payload TEXT NOT NULL,"
            " deleted_at TEXT, reason TEXT DEFAULT '')")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trash_deleted ON trash(deleted_at)")
        # 回填：旧数据无时间戳 → 设为固定旧时间（不在任何"今日"范围内）；
        # 2026-08-29（复审优化）：仅当存在空值时执行，避免每次打开全表 UPDATE
        if self.conn.execute(
            "SELECT COUNT(*) FROM categories WHERE created_at IS NULL OR updated_at IS NULL"
        ).fetchone()[0] > 0:
            self.conn.execute(
                "UPDATE categories SET created_at = COALESCE(created_at, '1970-01-01 00:00:00'),"
                " updated_at = COALESCE(updated_at, '1970-01-01 00:00:00')"
            )
        self.conn.commit()

    def _migrate_v1_to_v2(self) -> None:
        """v1 → v2：旧版一级分类(parent_id IS NULL)生成领域关联，再移除 domain_id 遗留列。"""
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
        self.set_meta("schema_version", "2")

    def _migrate_v2_to_v3(self) -> None:
        """v2 → v3（结构升级，幂等）：projects 表 + domains.project_id 列 + 预置项目类别。"""
        if self.get_meta("schema_version") == SCHEMA_VERSION:
            return
        # 1. projects 表（_SCHEMA_SQL 已含 CREATE IF NOT EXISTS，确保旧库也有）
        self.conn.executescript(_SCHEMA_SQL)
        # 2. domains 加列（幂等）
        if not self._has_column("domains", "project_id"):
            self.conn.execute(
                "ALTER TABLE domains ADD COLUMN project_id INTEGER "
                "REFERENCES projects(id) ON DELETE SET NULL"
            )
        # 3. 预置项目类别
        self.seed_preset_projects()
        # 4. 版本号
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
                    "UPDATE categories SET name = ?, updated_at = ? WHERE id = ?",
                    (new_name, _now(), r["id"]),  # 2026-08-29：改名同步 updated_at
                )
                changed = True
        if changed:
            self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def seed_preset_domains(self) -> None:
        """写入预置根目录（仅当表为空时）；随后按映射自动归入项目类别（新库开箱即用四级）。"""
        if self.list_domains():
            return
        for name in PRESET_DOMAINS:
            self.add_domain(name)
        self.assign_domains_to_projects(PROJECT_DOMAIN_MAPPING)

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
    # 排序（2026-09-11 08:52 用户要求 2）：分类列"上移/下移"
    # ------------------------------------------------------------------ #
    _ORDER_TABLES = ("projects", "domains", "categories")  # 允许重排的表白名单（防拼接外部输入）

    def _order_value(self, table: str, row_id: int) -> int:
        """取某行的 sort_order（不存在时返回 0）"""
        row = self.conn.execute(
            f"SELECT sort_order FROM {table} WHERE id = ?", (row_id,)).fetchone()
        return int(row["sort_order"] or 0) if row else 0

    def swap_order(self, table: str, ordered_ids: List[int], item_id: int,
                   delta: int) -> bool:
        """把 item_id 在其所在列内与相邻项对调排序号（-1=上移，+1=下移）。

        ordered_ids 为该列当前的可见顺序（与界面渲染顺序一致）。
        - 常规：只对调两项的 sort_order 值，不动其它行（避免影响共享分类在其它根目录中的顺序）；
        - 两项 sort_order 相同（历史并列数据）：先把可见列表按当前顺序物化为 0..n-1 再对调，
          保证"上移/下移"确实生效；
        - item_id 不在列表内或已到边界 → 返回 False，不写库。
        """
        if table not in self._ORDER_TABLES:
            raise ValueError(f"不支持重排的表：{table}")
        if item_id not in ordered_ids:
            return False
        i = ordered_ids.index(item_id)
        j = i + delta
        if j < 0 or j >= len(ordered_ids):
            return False
        a, b = ordered_ids[i], ordered_ids[j]
        va, vb = self._order_value(table, a), self._order_value(table, b)
        if va == vb:
            for k, rid in enumerate(ordered_ids):
                self.conn.execute(
                    f"UPDATE {table} SET sort_order = ? WHERE id = ?", (k, rid))
            va, vb = i, j
        self.conn.execute(
            f"UPDATE {table} SET sort_order = ? WHERE id = ?", (vb, a))
        self.conn.execute(
            f"UPDATE {table} SET sort_order = ? WHERE id = ?", (va, b))
        self.conn.commit()
        return True

    # ------------------------------------------------------------------ #
    # 根目录 Domain
    # ------------------------------------------------------------------ #
    def add_domain(self, name: str, project_id: Optional[int] = None) -> int:
        """新建根目录；project_id 指定其所属项目类别（四级分类，可为空=未分配）"""
        order = self.conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM domains").fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO domains(name, sort_order, project_id) VALUES(?, ?, ?)",
            (name, order, project_id),
        )
        self.conn.commit()
        return cur.lastrowid

    def rename_domain(self, domain_id: int, new_name: str) -> None:
        self.conn.execute("UPDATE domains SET name = ? WHERE id = ?", (new_name, domain_id))
        self.conn.commit()

    def get_domain(self, domain_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM domains WHERE id = ?", (domain_id,)).fetchone()
        return dict(row) if row else None

    def list_domains(self, project_id: Optional[int] = None) -> List[dict]:
        """列出根目录；project_id 非空时仅返回该项目的根目录（四级分类过滤）"""
        if project_id is not None:
            rows = self.conn.execute(
                "SELECT * FROM domains WHERE project_id = ? ORDER BY sort_order, id",
                (project_id,),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM domains ORDER BY sort_order, id").fetchall()
        return [dict(r) for r in rows]

    def list_unassigned_domains(self) -> List[dict]:
        """project_id 为空的根目录（迁移分配前的存量 / 新建未指定项目的）"""
        rows = self.conn.execute(
            "SELECT * FROM domains WHERE project_id IS NULL ORDER BY sort_order, id"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_domain(self, domain_id: int) -> dict:
        """删除根目录：仅解除 领域↔一级分类 关联（分类与条目为共享数据，不删除）。
        返回受影响统计 {'categories': n, 'entries': m}（n/m 为该领域视角下的数量）
        """
        stat = self.count_domain_items(domain_id)
        d = self.get_domain(domain_id)
        if d:
            self._log_deletion("domain", d["name"])  # 2026-08-29：记录根目录删除日志
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
    # 项目类别 Project（2026-08-29 四级分类施工新增）
    # ------------------------------------------------------------------ #
    def seed_preset_projects(self) -> None:
        """写入预置项目类别（仅当表为空时）"""
        if self.list_projects():
            return
        for name in PROJECT_PRESETS:
            self.add_project(name)

    def add_project(self, name: str) -> int:
        order = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM projects"
        ).fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO projects(name, sort_order) VALUES(?, ?)", (name, order)
        )
        self.conn.commit()
        return cur.lastrowid

    def get_project(self, project_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return dict(row) if row else None

    def get_project_by_name(self, name: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM projects WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None

    def list_projects(self) -> List[dict]:
        rows = self.conn.execute("SELECT * FROM projects ORDER BY sort_order, id").fetchall()
        return [dict(r) for r in rows]

    def rename_project(self, project_id: int, new_name: str) -> None:
        self.conn.execute("UPDATE projects SET name = ? WHERE id = ?", (new_name, project_id))
        self.conn.commit()

    def _project_id_tx(self, name: str) -> int:
        """事务内：按名查找项目类别，不存在则插入（不 commit，供批量/迁移事务使用）"""
        row = self.conn.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        order = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM projects"
        ).fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO projects(name, sort_order) VALUES(?, ?)", (name, order)
        )
        return cur.lastrowid

    def ensure_project(self, name: str) -> int:
        """按名查找或新建项目类别（惰性创建，如"未明确分类"兜底）"""
        pid = self._project_id_tx(name)
        self.conn.commit()
        return pid

    def count_project_domains(self, project_id: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM domains WHERE project_id = ?", (project_id,)
        ).fetchone()[0]

    def delete_project(self, project_id: int,
                       fallback_project_id: Optional[int] = None) -> dict:
        """删除项目类别：其下根目录移至 fallback_project_id（无则置空=未分配）。
        返回受影响统计 {'domains': n}（UI 层负责确认弹窗与兜底选择）"""
        stat = {"domains": self.count_project_domains(project_id)}
        if fallback_project_id is not None:
            self.conn.execute(
                "UPDATE domains SET project_id = ? WHERE project_id = ?",
                (fallback_project_id, project_id),
            )
        else:
            self.conn.execute(
                "UPDATE domains SET project_id = NULL WHERE project_id = ?", (project_id,)
            )
        self.conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        self.conn.commit()
        return stat

    def move_domain_to_project(self, domain_id: int,
                               project_id: Optional[int]) -> None:
        """移动根目录到其他项目类别（仅更新归属，分类树/条目不动）"""
        self.conn.execute(
            "UPDATE domains SET project_id = ? WHERE id = ?", (project_id, domain_id)
        )
        self.conn.commit()

    def copy_domain_to_project(self, domain_id: int, project_id: int,
                               new_name: str) -> int:
        """复制根目录到其他项目类别：建改名副本挂目标项目（重名加序号由调用方保证），源保留"""
        src = self.get_domain(domain_id)
        if not src:
            raise ValueError("根目录不存在")
        try:
            with self.conn:
                cur = self.conn.execute(
                    "INSERT INTO domains(name, sort_order, project_id) VALUES(?, ?, ?)",
                    (new_name, self._next_domain_order_tx(), project_id),
                )
                new_domain_id = cur.lastrowid
                for l1 in self.list_categories(domain_id=domain_id, parent_id=None):
                    self._copy_subtree_tx(l1["id"], None, l1["name"], new_domain_id)
            return new_domain_id
        except Exception:
            self.conn.rollback()
            raise

    def assign_domains_to_projects(self, mapping: dict, on_unmatched=None) -> dict:
        """按名称把根目录分配到项目类别（迁移/向导核心，单事务、幂等可重跑）。

        - mapping: {项目类别名: [根目录名, ...]}（按库中实际名称精确匹配）；
        - on_unmatched: 回调 fn(domain_name, [项目名...]) -> 选中的项目名；返回 None 表示未回答，
          该根目录归入兜底项目 PROJECT_FALLBACK（"未明确分类"，惰性创建）。
        返回 {'matched': n, 'unmatched': m, 'fallback': k}
        """
        stats = {"matched": 0, "unmatched": 0, "fallback": 0}
        try:
            with self.conn:
                # 1) 预置项目类别确保存在
                for pname in PROJECT_PRESETS:
                    self._project_id_tx(pname)
                # 2) 匹配项：按映射精确名称更新归属
                for pname, dom_names in mapping.items():
                    pid = self._project_id_tx(pname)
                    for dname in dom_names:
                        row = self.conn.execute(
                            "SELECT id FROM domains WHERE name = ?", (dname,)
                        ).fetchone()
                        if row:
                            self.conn.execute(
                                "UPDATE domains SET project_id = ? WHERE id = ?",
                                (pid, row["id"]),
                            )
                            stats["matched"] += 1
                # 3) 未匹配项：仍无归属的根目录 → 弹窗选择 / 兜底
                projects = [p["name"] for p in self.list_projects()]
                for d in self.conn.execute(
                    "SELECT * FROM domains WHERE project_id IS NULL ORDER BY sort_order, id"
                ).fetchall():
                    chosen = None
                    if on_unmatched is not None:
                        try:
                            chosen = on_unmatched(d["name"], list(projects))
                        except Exception:
                            chosen = None  # 回调异常视为未回答，安全兜底
                    if chosen:
                        pid = self._project_id_tx(chosen)
                        self.conn.execute(
                            "UPDATE domains SET project_id = ? WHERE id = ?", (pid, d["id"])
                        )
                        stats["unmatched"] += 1
                    else:
                        pid = self._project_id_tx(PROJECT_FALLBACK)
                        self.conn.execute(
                            "UPDATE domains SET project_id = ? WHERE id = ?", (pid, d["id"])
                        )
                        stats["fallback"] += 1
            return stats
        except Exception:
            self.conn.rollback()
            raise

    # ------------------------------------------------------------------ #
    # 删除日志 DeletionLog（2026-08-29 增量备份增强：同步"删除"到其他电脑）
    # ------------------------------------------------------------------ #
    def _category_name_chain(self, category_id: int) -> List[str]:
        """分类名称链（从一级到自身）；无父级返回 [自身]"""
        names = []
        cid = category_id
        while cid:
            c = self.get_category(cid)
            if not c:
                break
            names.append(c["name"])
            cid = c["parent_id"]
        return names[::-1]

    def _log_deletion(self, kind: str, name: str = "",
                      chain: Optional[list] = None,
                      content_key: str = "",
                      payload: str = "") -> None:
        """写入删除日志（kind: entry/category/domain）。

        payload（2026-09-08 V1.7.0）：条目被删时写入完整快照(JSON)，供"携带被删快照"
        变更包导出与⑤逆向恢复；分类/根目录等非条目删除传空。
        """
        self.conn.execute(
            "INSERT INTO deletion_log(kind, name, chain, content_key, payload, deleted_at) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (kind, name, json.dumps(chain or [], ensure_ascii=False),
             content_key, payload or "", _now()),
        )

    def list_deletions_since(self, since: str) -> List[dict]:
        """自 since（含）以来的删除日志"""
        rows = self.conn.execute(
            "SELECT * FROM deletion_log WHERE deleted_at >= ? ORDER BY id", (since,)
        ).fetchall()
        return [dict(r) for r in rows]

    def prune_deletion_log(self, keep_days: int = 7) -> None:
        """清理超过保留天数的删除日志（已被增量文件捕获后的历史清理）"""
        self.conn.execute(
            "DELETE FROM deletion_log WHERE deleted_at < ?",
            ((datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d %H:%M:%S"),),
        )
        self.conn.commit()

    def find_category_by_chain(self, chain: List[str]) -> Optional[int]:
        """按名称链（一级/二级…）定位分类 id；任一级不存在返回 None"""
        cid = None
        for name in chain:
            row = self.conn.execute(
                "SELECT id FROM categories WHERE parent_id IS ? AND name = ?",
                (cid, name),
            ).fetchone()
            if not row:
                return None
            cid = row["id"]
        return cid

    def delete_entries_by_content_key(self, content_key: str,
                                      cat_id: Optional[int] = None) -> int:
        """按"详情内容"判重键删除条目（增量删除同步用，尽力而为）。
        cat_id 指定则仅在该分类及其子树匹配；否则全库匹配。返回删除条数。
        """
        ids = []
        if cat_id is not None:
            for e in self.list_entries(cat_id, include_descendants=True):
                if self.content_key(e) == content_key:
                    ids.append(e["id"])
        else:
            for e in self.list_all_entries():
                if self.content_key(e) == content_key:
                    ids.append(e["id"])
        for eid in ids:
            self.delete_entry(eid)  # 复用删除（含图片清理与删除日志）
        return len(ids)

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
        ts = _now()  # 2026-08-29（增量备份增强）：记录分类创建/修改时间
        cur = self.conn.execute(
            "INSERT INTO categories(parent_id, name, sort_order, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?)",
            (parent_id, name, order, ts, ts),
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
        # 2026-08-29（增量备份增强）：改名更新 updated_at
        self.conn.execute(
            "UPDATE categories SET name = ?, updated_at = ? WHERE id = ?",
            (new_name, _now(), category_id),
        )
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
        # 2026-08-29（增量备份增强）：记录被删分类及其全部子分类的删除日志（含各自名称链）
        for cid in self._collect_category_ids(category_id):
            c = self.get_category(cid)
            if c:
                self._log_deletion("category", c["name"],
                                   chain=self._category_name_chain(cid))
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

    def delete_entry(self, entry_id: int, purge_image: bool = True) -> None:
        """物理删除条目（硬删除）。

        purge_image（2026-09-07 第2条改进）：是否同步删除关联图片文件。
        手动删除/级联删除先经 trash_entry() 写入回收站并保留图片（恢复可用），
        只有"回收站彻底删除/清空"时才 purge_image=True 释放图片。
        """
        entry = self.get_entry(entry_id)
        if entry and entry.get("image_path") and purge_image:
            self._remove_image_file(entry["image_path"])  # 同步删除关联图片（尽力而为）
        # 2026-08-29（增量备份增强）：记录删除日志，供换机同步删除
        if entry:
            self._log_deletion(
                "entry", entry["name"],
                chain=self._category_name_chain(entry["category_id"]) if entry.get("category_id") else [],
                content_key=self.content_key(entry),
                payload=json.dumps(dict(entry), ensure_ascii=False),  # V1.7.0：完整快照(⑤逆向恢复)
            )
        self.conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        self.conn.commit()

    # ------------------------------------------------------------------ #
    # 回收站 / 删除历史（2026-09-07 第2条改进：可恢复删除的条目）
    # 说明：deletion_log 只记"名字+内容键"用于换机增量同步；
    #       trash 保存完整内容快照，用于本机"恢复删除的条目"。
    # ------------------------------------------------------------------ #
    def trash_entry(self, entry_id: int, reason: str = "手动删除") -> bool:
        """把条目移入回收站：完整快照入 trash → 硬删除（保留图片文件，便于恢复）。

        返回是否成功（条目不存在返回 False）。
        """
        e = self.get_entry(entry_id)
        if not e:
            return False
        payload = dict(e)
        payload["locations"] = self._entry_location_ids(entry_id)  # 全部位置（含主挂靠）
        payload["chain"] = (self._category_name_chain(e["category_id"])
                            if e.get("category_id") else [])
        self.conn.execute(
            "INSERT INTO trash(name, payload, deleted_at, reason) VALUES(?, ?, ?, ?)",
            (e["name"], json.dumps(payload, ensure_ascii=False), _now(), reason),
        )
        self.conn.commit()
        self.delete_entry(entry_id, purge_image=False)  # 保留图片，供恢复后继续显示
        return True

    def list_trash(self) -> List[dict]:
        """回收站列表（最新删除在前）；payload 解析为 dict，供 UI 展示/恢复"""
        rows = self.conn.execute(
            "SELECT * FROM trash ORDER BY deleted_at DESC, id DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(r["payload"])
            except (TypeError, ValueError):
                d["payload"] = {}
            out.append(d)
        return out

    def count_trash(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM trash").fetchone()[0]

    def restore_from_trash(self, trash_id: int) -> Optional[int]:
        """从回收站恢复条目（重建为新 id，保留原内容/收藏/位置与创建时间）。

        分类已被删除的位置自动剔除；无任何现存位置 → 恢复为「未分类」。
        返回新条目 id；trash_id 不存在返回 None。
        """
        row = self.conn.execute("SELECT * FROM trash WHERE id = ?", (trash_id,)).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            payload = {}
        now = _now()
        # 现存位置：原主挂靠优先，其次按 id 升序
        locs = [c for c in (payload.get("locations") or []) if self.get_category(c)]
        main = payload.get("category_id")
        if main not in locs:
            main = locs[0] if locs else None
        others = [c for c in locs if c != main]
        try:
            with self.conn:
                cur = self.conn.execute(
                    "INSERT INTO entries(category_id, name, intro, origin, features, scenes, "
                    "works, image_desc, prompt_cn, prompt_en, image_plan, image_path, "
                    "is_favorite, created_at, updated_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (main,
                     payload.get("name", row["name"]),
                     payload.get("intro", ""), payload.get("origin", ""),
                     payload.get("features", ""), payload.get("scenes", ""),
                     payload.get("works", ""), payload.get("image_desc", ""),
                     payload.get("prompt_cn", ""), payload.get("prompt_en", ""),
                     payload.get("image_plan", ""), payload.get("image_path", ""),
                     1 if payload.get("is_favorite") else 0,
                     payload.get("created_at") or now, now))
                new_id = cur.lastrowid
                for cid in others:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO entry_links(entry_id, category_id, created_at) "
                        "VALUES(?, ?, ?)", (new_id, cid, now))
                self.conn.execute("DELETE FROM trash WHERE id = ?", (trash_id,))
            return new_id
        except Exception:
            self.conn.rollback()
            raise

    def purge_trash(self, trash_id: int) -> None:
        """彻底删除回收站中的一条（连同其关联图片文件释放）。

        2026-09-09（审核 P1-7 修复）：同时清空 deletion_log 中同内容的 payload 快照，
        缩短"以为已彻底删除、实则可被变更包快照导出"的隐私窗口（保留名称/链/指纹以便同步）。
        """
        row = self.conn.execute("SELECT * FROM trash WHERE id = ?", (trash_id,)).fetchone()
        if not row:
            return
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            payload = {}
        img = payload.get("image_path") if isinstance(payload, dict) else ""
        if img:
            self._remove_image_file(img)
        if isinstance(payload, dict) and payload:
            try:
                key = self.content_key(payload)
                if key:
                    self.conn.execute(
                        "UPDATE deletion_log SET payload = '' "
                        "WHERE kind = 'entry' AND content_key = ?", (key,))
            except Exception:
                pass
        self.conn.execute("DELETE FROM trash WHERE id = ?", (trash_id,))
        self.conn.commit()

    def clear_trash(self) -> int:
        """清空回收站（返回清理条数）；2026-09-09（P1-7）：同步清除 deletion_log 快照 payload"""
        items = self.conn.execute("SELECT id, payload FROM trash").fetchall()
        for it in items:
            try:
                payload = json.loads(it["payload"])
                img = payload.get("image_path") if isinstance(payload, dict) else ""
                if img:
                    self._remove_image_file(img)
                if isinstance(payload, dict) and payload:
                    try:
                        key = self.content_key(payload)
                        if key:
                            self.conn.execute(
                                "UPDATE deletion_log SET payload = '' "
                                "WHERE kind = 'entry' AND content_key = ?", (key,))
                    except Exception:
                        pass
            except (TypeError, ValueError):
                pass
        n = self.conn.execute("SELECT COUNT(*) FROM trash").fetchone()[0]
        self.conn.execute("DELETE FROM trash")
        self.conn.commit()
        return n

    def trash_entries_batch(self, entry_ids: list, reason: str = "变更包删除同步",
                            log_deletion: bool = True) -> int:
        """批量把条目移入回收站（2026-09-08 V1.7.0：变更包删除同步用）。

        与 trash_entry 语义一致（完整快照入 trash、保留图片、写删除日志），
        但全部操作在**单事务**内完成，避免逐条 commit 的性能开销。
        log_deletion（2026-09-09 P2-13）：本机删除/级联删除传 True（需广播）；
        "变更包删除同步"传 False——接收端不把同步删除再写成"本机删除"，
        避免本机当日变更包出现"回声删除"（负增量噪音）。
        已不存在/重复 id 自动忽略；返回实际移入条数。
        """
        seen, del_ids = set(), []
        for eid in entry_ids:
            if eid is None or eid in seen:
                continue
            seen.add(eid)
            del_ids.append(eid)
        if not del_ids:
            return 0
        now = _now()
        trash_rows, log_rows, alive = [], [], []
        for eid in del_ids:
            e = self.get_entry(eid)
            if not e:
                continue
            payload = dict(e)
            payload["locations"] = self._entry_location_ids(eid)
            payload["chain"] = (self._category_name_chain(e["category_id"])
                                if e.get("category_id") else [])
            payload_json = json.dumps(payload, ensure_ascii=False)
            trash_rows.append((e["name"], payload_json, now, reason))
            if log_deletion:
                log_rows.append(("entry", e["name"],
                                 json.dumps(payload["chain"], ensure_ascii=False),
                                 self.content_key(e), payload_json, now))
            alive.append(eid)
        if not alive:
            return 0
        ph = ",".join("?" * len(alive))
        try:
            with self.conn:
                self.conn.executemany(
                    "INSERT INTO trash(name, payload, deleted_at, reason) VALUES(?, ?, ?, ?)",
                    trash_rows)
                if log_deletion and log_rows:
                    self.conn.executemany(
                        "INSERT INTO deletion_log(kind, name, chain, content_key, payload, "
                        "deleted_at) VALUES(?, ?, ?, ?, ?, ?)", log_rows)
                self.conn.execute(
                    f"DELETE FROM entries WHERE id IN ({ph})", alive)
            return len(alive)
        except Exception:
            self.conn.rollback()
            raise

    def list_entries_added_since(self, since: str) -> List[dict]:
        """按 created_at >= since 列出最近新增的条目（第3条改进：查看添加历史）"""
        rows = self.conn.execute(
            "SELECT * FROM entries WHERE created_at >= ? "
            "ORDER BY created_at DESC, id DESC", (since,)).fetchall()
        return [dict(r) for r in rows]

    def category_path(self, category_id: Optional[int]) -> str:
        """分类路径显示文本（如"视觉风格分类 › 某分类"；无分类返回「未分类」）"""
        if not category_id:
            return "未分类"
        return " › ".join(self._category_name_chain(category_id))

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
        """列出某分类可见条目（主挂靠=该分类 ∪ 关联表含该分类，去重）。

        include_descendants=True 时含所有子分类子树内的可见条目。
        2026-09-07（条目多位置施工）：单分类列举由"只看 category_id"改为两路并集，
        使"关联到"的条目也能在对应分类下列出。
        """
        if include_descendants:
            ids = self._collect_category_ids(category_id)
            if not ids:
                return []
            ph = ",".join("?" * len(ids))
            rows = self.conn.execute(
                "SELECT * FROM ("
                f" SELECT e.* FROM entries e WHERE e.category_id IN ({ph})"
                " UNION "
                f" SELECT e.* FROM entries e JOIN entry_links l ON l.entry_id = e.id"
                f"  WHERE l.category_id IN ({ph})"
                ") ORDER BY updated_at DESC, id",
                ids + ids,
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM ("
                " SELECT e.* FROM entries e WHERE e.category_id = ?"
                " UNION "
                " SELECT e.* FROM entries e JOIN entry_links l ON l.entry_id = e.id"
                "  WHERE l.category_id = ?"
                ") ORDER BY updated_at DESC, id",
                (category_id, category_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_entries(self, category_id: Optional[int] = None,
                      include_descendants: bool = False) -> int:
        """统计某分类可见条目数（COUNT，不加载行）。

        2026-09-07（条目多位置施工）：改为按并集统计"位置可见条目数"；
        category_id=None 仍返回全局物理条目总数（不重复计关联）。
        """
        if category_id is None:
            return self.conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        if include_descendants:
            ids = self._collect_category_ids(category_id)
            if not ids:
                return 0
            ph = ",".join("?" * len(ids))
            return self.conn.execute(
                "SELECT COUNT(*) FROM ("
                f" SELECT e.id FROM entries e WHERE e.category_id IN ({ph})"
                " UNION "
                f" SELECT e.id FROM entries e JOIN entry_links l ON l.entry_id = e.id"
                f"  WHERE l.category_id IN ({ph})"
                ")",
                ids + ids,
            ).fetchone()[0]
        return self.conn.execute(
            "SELECT COUNT(*) FROM ("
            " SELECT e.id FROM entries e WHERE e.category_id = ?"
            " UNION "
            " SELECT e.id FROM entries e JOIN entry_links l ON l.entry_id = e.id"
            "  WHERE l.category_id = ?"
            ")",
            (category_id, category_id),
        ).fetchone()[0]

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
        """列出未分类条目：主挂靠为空 **且** 无任何关联位置。

        2026-09-07（条目多位置施工）：若条目经 entry_links 关联到某分类，
        即使 category_id 为空也不再视为"未分类"。
        """
        rows = self.conn.execute(
            "SELECT * FROM entries WHERE category_id IS NULL "
            "AND id NOT IN (SELECT entry_id FROM entry_links) "
            "ORDER BY updated_at DESC, id"
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

    def list_entries_updated_since(self, since: str) -> List[dict]:
        """按 updated_at >= since 列出条目（增量备份收集用，2026-08-29 新增）"""
        rows = self.conn.execute(
            "SELECT * FROM entries WHERE updated_at >= ? ORDER BY id", (since,)
        ).fetchall()
        return [dict(r) for r in rows]

    def list_categories_changed_since(self, since: str) -> List[dict]:
        """按 created_at/updated_at >= since 列出分类（含"新增空分类"，2026-08-29 新增）"""
        rows = self.conn.execute(
            "SELECT * FROM categories WHERE created_at >= ? OR updated_at >= ? "
            "ORDER BY id", (since, since)
        ).fetchall()
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

    # ------------------------------------------------------------------ #
    # 条目多位置：关联 / 复制 / 移动（2026-09-07 施工）
    # 统一原语 set_entry_locations：关联=add；解除=remove；移动=remove(全部)+add(目标)
    # 不变量：条目只要有位置，主挂靠(category_id)恰为其一；位置清空则 category_id=NULL。
    # ------------------------------------------------------------------ #
    def _entry_location_ids(self, entry_id: int) -> List[int]:
        """条目当前全部位置分类 id（主挂靠 + 关联，去重、升序）"""
        e = self.get_entry(entry_id)
        ids = []
        if e and e.get("category_id") is not None:
            ids.append(e["category_id"])
        rows = self.conn.execute(
            "SELECT category_id FROM entry_links WHERE entry_id = ? "
            "ORDER BY created_at, rowid", (entry_id,)
        ).fetchall()
        for r in rows:
            if r["category_id"] not in ids:
                ids.append(r["category_id"])
        return ids

    def list_entry_locations(self, entry_id: int) -> List[int]:
        """条目全部位置分类 id（含主挂靠；供 UI 位置提示/移动对话框用）"""
        return self._entry_location_ids(entry_id)

    def _entry_location_apply_tx(self, entry_id: int, remove: List[int],
                                 add: List[int]) -> None:
        """位置编辑核心（须在调用方事务内执行，不自行 commit）。

        规则：
        1) add 的目标若已在任一位置则忽略；
        2) remove 同时作用于 主挂靠 与 关联表；
        3) 主挂靠被移除后，若仍有剩余关联位置则自动提升其一为主挂靠；
        4) 全部位置清空 → category_id=NULL（回落未分类）。
        """
        e = self.get_entry(entry_id)
        if not e:
            raise ValueError("条目不存在")
        main = e["category_id"]
        now = set(self._entry_location_ids(entry_id))
        for cid in dict.fromkeys(remove or []):
            if cid is None or cid not in now:
                continue
            now.discard(cid)
            self.conn.execute(
                "DELETE FROM entry_links WHERE entry_id = ? AND category_id = ?",
                (entry_id, cid))
        for cid in dict.fromkeys(add or []):
            if cid is None or cid in now:
                continue
            now.add(cid)
            self.conn.execute(
                "INSERT INTO entry_links(entry_id, category_id, created_at) "
                "VALUES(?, ?, ?)", (entry_id, cid, _now()))
        # 主挂靠维护：main 是否还在剩余位置中
        if main is not None and main not in now:
            main = None
        if main is None and now:
            main = min(now)  # 提升最小 id 的关联位置为主挂靠
            self.conn.execute(
                "DELETE FROM entry_links WHERE entry_id = ? AND category_id = ?",
                (entry_id, main))
        self.conn.execute(
            "UPDATE entries SET category_id = ?, updated_at = ? WHERE id = ?",
            (main, _now(), entry_id))

    def set_entry_locations(self, entry_id: int, remove: Optional[list] = None,
                            add: Optional[list] = None) -> None:
        """统一位置编辑原语（原子）。remove/add 为分类 id 列表。"""
        try:
            with self.conn:
                self._entry_location_apply_tx(entry_id, remove or [], add or [])
        except Exception:
            self.conn.rollback()
            raise

    def link_entry(self, entry_id: int, category_id: int) -> None:
        """把条目关联到某分类（若该分类已是主挂靠则忽略）"""
        self.set_entry_locations(entry_id, remove=[], add=[category_id])

    def unlink_entry(self, entry_id: int, category_id: int) -> None:
        """解除条目在某分类的关联（含解除主挂靠；仅剩位置会自动提升）"""
        self.set_entry_locations(entry_id, remove=[category_id], add=[])

    def move_entry(self, entry_id: int, category_id: Optional[int]) -> None:
        """把条目整体转移：仅保留目标位置（清空其它全部关联）。

        category_id=None 表示移入未分类。兼容旧调用（单位置对象行为不变）；
        2026-09-07：多位置下"移动到"＝ remove(全部位置) + add(目标)。
        """
        self.set_entry_locations(entry_id,
                                 remove=self._entry_location_ids(entry_id),
                                 add=[] if category_id is None else [category_id])

    def copy_entry_to(self, entry_id: int, target_cat_id: int,
                      new_name: Optional[str] = None) -> int:
        """"复制到（独立副本）"：将条目内容拷贝到目标分类成为独立新条目。

        - 内容 9 字段全拷贝、收藏清零、时间戳新建；
        - 关联图片复制为新文件（entry_{新id}.{ext}），避免删除一份误删另一份；
        - 新条目不带任何额外关联（只有目标主挂靠）。
        - 名称：目标分类下同名自动加"（副本）/（副本2）…"（2026-09-07，便于区分）。
        返回新条目 id。
        """
        e = self.get_entry(entry_id)
        if not e:
            raise ValueError("条目不存在")
        ts = _now()
        try:
            with self.conn:
                name = new_name or self.unique_entry_name(e["name"], target_cat_id)
                cur = self.conn.execute(
                    "INSERT INTO entries(category_id, name, intro, origin, features, scenes, "
                    "works, image_desc, prompt_cn, prompt_en, image_plan, image_path, "
                    "is_favorite, created_at, updated_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (target_cat_id, name, e["intro"], e["origin"], e["features"],
                     e["scenes"], e["works"], e["image_desc"], e["prompt_cn"],
                     e["prompt_en"], e["image_plan"], e["image_path"], 0, ts, ts))
                new_id = cur.lastrowid
                if e.get("image_path"):
                    new_rel = self._copy_image_file(e["image_path"], new_id)
                    if new_rel:
                        self.conn.execute(
                            "UPDATE entries SET image_path = ? WHERE id = ?",
                            (new_rel, new_id))
            return new_id
        except Exception:
            self.conn.rollback()
            raise

    def _copy_image_file(self, old_rel: str, new_entry_id: int) -> Optional[str]:
        """复制条目图片到新条目名下（相对 data/ 路径安全校验；失败返回 None）"""
        try:
            root = os.path.abspath(data_dir())
            src = os.path.abspath(os.path.join(root, old_rel))
            if os.path.commonpath([root, src]) != root or not os.path.isfile(src):
                return None
            ext = os.path.splitext(old_rel)[1] or ""
            img_dir = os.path.join(root, IMAGES_DIR_NAME)
            os.makedirs(img_dir, exist_ok=True)
            dest_rel = os.path.join(IMAGES_DIR_NAME, f"entry_{new_entry_id}{ext}")
            shutil.copy2(src, os.path.join(root, dest_rel))
            return dest_rel
        except (OSError, ValueError):
            return None

    # ---- 保存一致性提示 / 副本命名辅助（2026-09-07） ----
    def find_content_duplicates(self, key: str,
                                exclude_entry_id: Optional[int] = None,
                                limit: int = 5) -> List[dict]:
        """按"详情内容键"查找同内容条目（用于保存前轻提示；可排除自身）。

        返回最多 limit 条 {"id", "name"}；全库逐条比对（保存频率低，量级可接受）。
        """
        hits = []
        for row in self.conn.execute("SELECT * FROM entries").fetchall():
            e = dict(row)
            if exclude_entry_id is not None and e["id"] == exclude_entry_id:
                continue
            if self.content_key(e) == key:
                hits.append({"id": e["id"], "name": e["name"]})
                if len(hits) >= limit:
                    break
        return hits

    def _entry_name_exists(self, name: str, category_id: int) -> bool:
        """某分类（主挂靠或关联）下是否已存在同名条目"""
        row = self.conn.execute(
            "SELECT 1 FROM entries WHERE category_id = ? AND name = ? LIMIT 1",
            (category_id, name),
        ).fetchone()
        if row:
            return True
        row = self.conn.execute(
            "SELECT 1 FROM entry_links l JOIN entries e ON e.id = l.entry_id "
            "WHERE l.category_id = ? AND e.name = ? LIMIT 1",
            (category_id, name),
        ).fetchone()
        return row is not None

    def unique_entry_name(self, name: str, category_id: int) -> str:
        """目标分类下条目名去重：重名 → name（副本）→ name（副本2）…"""
        if not self._entry_name_exists(name, category_id):
            return name
        base = f"{name}（副本）"
        if not self._entry_name_exists(base, category_id):
            return base
        i = 2
        while self._entry_name_exists(f"{name}（副本{i}）", category_id):
            i += 1
        return f"{name}（副本{i}）"

    # ------------------------------------------------------------------ #
    # 分类删除保护（2026-09-07 施工，DB 层；UI 短语输入在阶段 3 接入）
    # ------------------------------------------------------------------ #
    _CASCADE_PHRASE = "删除全部下级内容"

    def delete_category_safe(self, category_id: int) -> dict:
        """安全删除单个分类：拒绝有子分类；其直挂条目仅解除本分类位置，
        无其它位置的条目转未分类，不真删任何条目。返回 {'categories','entries'}。
        """
        if self.category_has_children(category_id):
            raise ValueError("该分类仍有下级分类，请先处理下级（或改用级联删除）")
        entries = [e["id"] for e in self.list_entries(category_id)]
        try:
            with self.conn:
                for eid in entries:
                    self._entry_location_apply_tx(eid, remove=[category_id], add=[])
                c = self.get_category(category_id)
                if c:
                    self._log_deletion("category", c["name"],
                                       chain=self._category_name_chain(category_id))
                self.conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
            return {"categories": 1, "entries": len(entries)}
        except Exception:
            self.conn.rollback()
            raise

    def delete_category_cascade(self, category_id: int, confirm_phrase: str) -> dict:
        """级联删除整棵（该分类 + 全部下级分类 + 其全部直挂条目真删）。

        必须传入确认短语并等于 _CASCADE_PHRASE 才执行（DB 层兜底防误删）。
        返回 {'categories','entries'}（entries 为物理删除数）。
        """
        if confirm_phrase != self._CASCADE_PHRASE:
            raise ValueError("确认短语不正确，已取消级联删除")
        ids = self._collect_category_ids(category_id)
        if not ids:
            return {"categories": 0, "entries": 0}
        ph = ",".join("?" * len(ids))
        affected = [r["id"] for r in self.conn.execute(
            "SELECT e.id FROM entries e WHERE e.category_id IN (" + ph + ")"
            " UNION "
            "SELECT l.entry_id AS id FROM entry_links l WHERE l.category_id IN (" + ph + ")",
            ids + ids,
        ).fetchall()]
        try:
            # 条目先全部移入回收站（完整快照 + 保留图片），再从主表硬删除；
            # 分类结构与图片资源在用户"彻底删除/清空回收站"时才最终释放
            for eid in affected:
                self.trash_entry(eid, reason="级联删除")
            # 记录分类删除日志
            for cid in ids:
                c = self.get_category(cid)
                if c:
                    self._log_deletion("category", c["name"],
                                       chain=self._category_name_chain(cid))
            self.conn.execute(
                "DELETE FROM categories WHERE id IN (" + ph + ")", ids)
            self.conn.commit()
            return {"categories": len(ids), "entries": len(affected)}
        except Exception:
            self.conn.rollback()
            raise

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
        ts = _now()  # 2026-08-29（增量备份增强）：复制等新建分类记录时间戳
        cur = self.conn.execute(
            "INSERT INTO categories(parent_id, name, sort_order, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?)",
            (parent_id, name, order, ts, ts),
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
                        self.conn.execute(
                            "UPDATE categories SET name = ?, updated_at = ? WHERE id = ?",
                            (new_name, _now(), l1["id"]))  # 2026-08-29：改名同步 updated_at
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
                    self.conn.execute(
                        "UPDATE categories SET name = ?, parent_id = ?, updated_at = ? WHERE id = ?",
                        (new_name, target_l1_id, _now(), child["id"]))  # 2026-08-29：移动同步 updated_at
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
                self.conn.execute(
                    "UPDATE categories SET parent_id = NULL, updated_at = ? WHERE id = ?",
                    (_now(), category_id))  # 2026-08-29：提升一级同步 updated_at
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
                self.conn.execute(
                    "UPDATE categories SET parent_id = ?, updated_at = ? WHERE id = ?",
                    (target_l1_id, _now(), category_id))  # 2026-08-29：移动同步 updated_at
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
            "projects": self.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
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

        # ---- 21~24：四级分类（2026-08-29 M1 新增）----
        # 21. 项目类别预置 + 预置根目录自动归属 + 过滤
        projects = db.list_projects()
        assert len(projects) == 5
        pnames = [p["name"] for p in projects]
        assert pnames == ["日常学习记录", "网上资源收集", "个人梳理资源", "本人创作作品", "个人经验总结"]
        p_map = {p["name"]: p["id"] for p in projects}
        dom_by_name = {d["name"]: d for d in db.list_domains()}
        preset_names = {"计算机编程", "视频", "图像", "音频", "文学", "学术", "专业报告", "视觉风格分类"}
        existing = set(dom_by_name)
        # 说明：测试[10]已删除"图像"等根目录，此处仅校验仍存在的预置根目录均已自动归属
        assert all(dom_by_name[n].get("project_id") is not None
                   for n in preset_names & existing), "预置根目录应已自动归入项目"
        assert dom_by_name["视频"]["project_id"] == p_map["日常学习记录"]
        assert dom_by_name["计算机编程"]["project_id"] == p_map["个人经验总结"]
        assert dom_by_name["视觉风格分类"]["project_id"] == p_map["个人梳理资源"]
        # 图像已在[10]删除，仅校验现存预置
        assert {d["name"] for d in db.list_domains(project_id=p_map["日常学习记录"])} == \
            {"视频", "音频", "文学", "学术", "专业报告"}
        print("[21] 项目类别预置/自动归属/过滤 通过")

        # 22. 项目类别增删改 + 删除兜底
        p_extra = db.add_project("测试项目")
        assert db.get_project(p_extra)["name"] == "测试项目"
        db.rename_project(p_extra, "测试项目2")
        assert db.get_project(p_extra)["name"] == "测试项目2"
        dom_tmp = db.add_domain("临时域", project_id=p_extra)
        assert db.get_domain(dom_tmp)["project_id"] == p_extra
        fallback_id = db.ensure_project("未明确分类")
        stat = db.delete_project(p_extra, fallback_project_id=fallback_id)
        assert stat == {"domains": 1}
        assert db.get_domain(dom_tmp)["project_id"] == fallback_id
        assert db.get_project(p_extra) is None
        print("[22] 项目类别增删改/删除兜底 通过")

        # 23. 移动/复制根目录到项目类别
        p_src = p_map["个人经验总结"]
        p_dst = p_map["网上资源收集"]
        dom_m = db.add_domain("移动域", project_id=p_src)
        db.move_domain_to_project(dom_m, p_dst)
        assert db.get_domain(dom_m)["project_id"] == p_dst
        dom_c = db.add_domain("复制域", project_id=p_src)
        c_l1 = db.add_category("复制一级", domain_id=dom_c)
        c_l2 = db.add_category("复制二级", parent_id=c_l1)
        db.add_entry(Entry(name="复制条目", category_id=c_l2))
        new_id = db.copy_domain_to_project(dom_c, p_dst, db.unique_domain_name("复制域"))
        assert db.get_domain(new_id)["project_id"] == p_dst
        assert db.get_domain(dom_c)["project_id"] == p_src  # 源保留
        new_l1 = db.list_categories(domain_id=new_id, parent_id=None)
        assert len(new_l1) == 1 and new_l1[0]["name"] == "复制一级"
        new_l2 = db.list_categories(parent_id=new_l1[0]["id"])
        assert len(new_l2) == 1 and new_l2[0]["name"] == "复制二级"
        assert len(db.list_entries(new_l2[0]["id"])) == 1
        print("[23] 移动/复制根目录到项目类别 通过")

        # 24. assign_domains_to_projects：未命中 → 选择 / 兜底 / 幂等
        for d in db.list_unassigned_domains():   # 先把测试遗留的无归属根目录隔离
            db.move_domain_to_project(d["id"], p_src)
        dom_x = db.add_domain("未知根目录X")
        dom_y = db.add_domain("未知根目录Y")

        def _choose(name, projects):
            return "日常学习记录" if name == "未知根目录X" else None

        st = db.assign_domains_to_projects({}, _choose)
        assert st == {"matched": 0, "unmatched": 1, "fallback": 1}, st
        fb = db.get_project_by_name("未明确分类")["id"]
        assert db.get_domain(dom_x)["project_id"] == p_map["日常学习记录"]
        assert db.get_domain(dom_y)["project_id"] == fb
        st2 = db.assign_domains_to_projects({}, _choose)   # 幂等：无新变化
        assert st2 == {"matched": 0, "unmatched": 0, "fallback": 0}
        print("[24] 归属分配/未命中兜底/幂等 通过")

        # ---- 25~27：增量备份增强（2026-08-29）----
        # 25. 分类时间戳：新建/改名/移动会写入 created_at/updated_at
        ts_cat = db.add_category("时间戳分类", domain_id=p_src)
        c_row = db.get_category(ts_cat)
        assert c_row["created_at"] and c_row["updated_at"], "新建分类应带时间戳"
        db.rename_category(ts_cat, "时间戳分类2")
        assert db.get_category(ts_cat)["updated_at"] >= c_row["updated_at"]
        today_start = datetime.now().strftime("%Y-%m-%d 00:00:00")
        changed = db.list_categories_changed_since(today_start)
        assert any(x["id"] == ts_cat for x in changed), "当日新建分类应被 list_categories_changed_since 命中"
        print("[25] 分类时间戳/当日变更查询 通过")

        # 26. 删除日志：条目/分类(级联)/根目录
        e_del = db.add_entry(Entry(name="待删条目", category_id=c_l2))
        db.delete_entry(e_del)
        logs = db.list_deletions_since(today_start)
        entry_log = [x for x in logs if x["kind"] == "entry" and x["name"] == "待删条目"]
        assert entry_log and entry_log[0]["content_key"], "删除条目应记录内容键"
        # 删除分类（含子分类级联日志）
        cat_del = db.add_category("待删一级", domain_id=p_src)
        cat_del2 = db.add_category("待删二级", parent_id=cat_del)
        db.delete_category(cat_del)
        cat_logs = [x for x in db.list_deletions_since(today_start)
                    if x["kind"] == "category"]
        assert any(x["name"] == "待删一级" and json.loads(x["chain"]) == ["待删一级"]
                   for x in cat_logs)
        assert any(x["name"] == "待删二级" and json.loads(x["chain"]) == ["待删一级", "待删二级"]
                   for x in cat_logs)
        # 删除根目录日志
        dom_del = db.add_domain("待删根目录")
        db.delete_domain(dom_del)
        assert any(x["kind"] == "domain" and x["name"] == "待删根目录"
                   for x in db.list_deletions_since(today_start))
        print("[26] 删除日志（条目/分类级联/根目录）通过")

        # 27. find_category_by_chain + delete_entries_by_content_key
        ch_l1 = db.add_category("链一级", domain_id=p_src)
        ch_l2 = db.add_category("链二级", parent_id=ch_l1)
        e1 = db.add_entry(Entry(name="同内容", intro="X", category_id=ch_l2))
        e2 = db.add_entry(Entry(name="同内容", intro="X", category_id=ch_l2))
        assert db.find_category_by_chain(["链一级", "链二级"]) == ch_l2
        assert db.find_category_by_chain(["链一级", "不存在"]) is None
        k = Database.content_key(db.get_entry(e1))
        n = db.delete_entries_by_content_key(k, cat_id=ch_l2)
        assert n == 2, n   # 内容键相同的两条都被删除
        assert db.get_entry(e1) is None and db.get_entry(e2) is None
        print("[27] 按名称链查找/按内容键删除 通过")

        # ---- 28~30：条目多位置 / 复制到 / 删除保护（2026-09-07 施工）----
        # 28. 关联/解除/统一原语/整体转移/未分类回落
        pj_id = db.list_projects()[0]["id"]
        dom_m = db.add_domain("多位置域", project_id=pj_id)
        la = db.add_category("AA", domain_id=dom_m)
        lb = db.add_category("BB", domain_id=dom_m)
        lc = db.add_category("CC", parent_id=la)
        ld = db.add_category("DD", parent_id=lb)
        m1 = db.add_entry(Entry(name="多位置条目", category_id=lc, prompt_cn="P1"))
        db.link_entry(m1, ld)
        assert set(db.list_entry_locations(m1)) == {lc, ld}
        assert len(db.list_entries(ld)) == 1 and db.count_entries(ld) == 1
        assert all(x["id"] != m1 for x in db.list_uncategorized())
        db.unlink_entry(m1, ld)
        assert db.list_entry_locations(m1) == [lc] and db.count_entries(ld) == 0
        # 统一原语：add 两处 + remove 主挂靠 lc → 提升剩余其一为主挂靠
        db.set_entry_locations(m1, remove=[lc], add=[lb, ld])
        assert set(db.list_entry_locations(m1)) == {lb, ld}
        assert db.get_entry(m1)["category_id"] == min(lb, ld)
        # 整体转移 move_entry → 仅保留目标
        db.move_entry(m1, lb)
        assert db.list_entry_locations(m1) == [lb]
        assert db.count_entries(lb) == 1 and db.count_entries(ld) == 0
        # 唯一位置解除 → 未分类
        db.unlink_entry(m1, lb)
        assert db.list_entry_locations(m1) == []
        assert db.get_entry(m1)["category_id"] is None
        assert any(x["id"] == m1 for x in db.list_uncategorized())
        print("[28] 条目多位置 关联/解除/统一原语/整体转移 通过")

        # 29. 复制到（独立副本）：内容拷贝、收藏清零、独立演化
        m2 = db.add_entry(Entry(name="源条目", category_id=lc, intro="I1",
                                prompt_cn="CP", is_favorite=1))
        m2_new = db.copy_entry_to(m2, lb)
        c2 = db.get_entry(m2_new)
        assert m2_new != m2 and c2["category_id"] == lb
        assert c2["name"] == "源条目" and c2["prompt_cn"] == "CP"
        assert c2["is_favorite"] == 0 and db.list_entry_locations(m2_new) == [lb]
        db.update_entry(Entry(id=m2_new, category_id=lb, name="源条目2", prompt_cn="CP2"))
        assert db.get_entry(m2)["prompt_cn"] == "CP"  # 原条目不受副本影响
        print("[29] 条目复制到（独立副本）通过")

        # 30. 分类删除保护：安全删除 / 级联删除短语守卫
        dom_d = db.add_domain("删除域", project_id=pj_id)
        g1 = db.add_category("GA", domain_id=dom_d)
        h1 = db.add_category("HA", parent_id=g1)
        e_in = db.add_entry(Entry(name="子条目", category_id=h1))
        e_share = db.add_entry(Entry(name="共享条目", category_id=h1))
        db.link_entry(e_share, lc)  # 另有其它位置
        try:  # 有子分类 → 安全删除被拒
            db.delete_category_safe(g1)
            raise SystemExit("应拒绝删除含下级分类")
        except ValueError:
            pass
        st = db.delete_category_safe(h1)
        assert st == {"categories": 1, "entries": 2}, st
        assert db.get_category(h1) is None
        assert db.get_entry(e_in)["category_id"] is None           # 无其它位置 → 未分类
        assert any(x["id"] == e_in for x in db.list_uncategorized())
        assert set(db.list_entry_locations(e_share)) == {lc}        # 其它位置保留
        try:  # 短语不符 → 拒绝
            db.delete_category_cascade(g1, "错误短语")
            raise SystemExit("应拒绝错误确认短语")
        except ValueError:
            pass
        h2 = db.add_category("HB", parent_id=g1)
        e_in2 = db.add_entry(Entry(name="子条目2", category_id=h2))
        st2 = db.delete_category_cascade(g1, Database._CASCADE_PHRASE)
        assert st2 == {"categories": 2, "entries": 1}, st2          # g1+h2、条目真删
        assert db.get_category(g1) is None and db.get_category(h2) is None
        assert db.get_entry(e_in2) is None
        print("[30] 分类删除保护：安全删除/级联短语 通过")

        print(f"[统计] {db.stats()}")
        print("=== 数据库层全部自测通过 ===")
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def _migrate_selftest() -> None:
    """v2 → v3 迁移自测：结构升级 + 归属分配 + 兜底 + 幂等（在临时 v2 库上进行）"""
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="promptsprite_migrate_")
    try:
        v2 = os.path.join(tmp, "v2.db")
        conn = sqlite3.connect(v2)
        conn.executescript("""
            CREATE TABLE domains (id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE, sort_order INTEGER DEFAULT 0);
            CREATE TABLE categories (id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id INTEGER DEFAULT NULL, name TEXT NOT NULL, sort_order INTEGER DEFAULT 0);
            CREATE TABLE domain_category (domain_id INTEGER NOT NULL,
                category_id INTEGER NOT NULL, PRIMARY KEY (domain_id, category_id));
            CREATE TABLE entries (id INTEGER PRIMARY KEY AUTOINCREMENT, category_id INTEGER,
                name TEXT NOT NULL, intro TEXT DEFAULT '', origin TEXT DEFAULT '',
                features TEXT DEFAULT '', scenes TEXT DEFAULT '', works TEXT DEFAULT '',
                image_desc TEXT DEFAULT '', prompt_cn TEXT DEFAULT '', prompt_en TEXT DEFAULT '',
                image_plan TEXT DEFAULT '', image_path TEXT DEFAULT '',
                is_favorite INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT);
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """)
        conn.execute("INSERT INTO meta(key,value) VALUES('schema_version','2')")
        conn.executemany("INSERT INTO domains(name, sort_order) VALUES(?,?)",
                         [("视频", 0), ("计算机编程", 1), ("未知根目录", 2)])
        conn.execute("INSERT INTO categories(parent_id,name,sort_order) VALUES(NULL,'一级A',0)")
        conn.execute("INSERT INTO categories(parent_id,name,sort_order) VALUES(1,'二级B',0)")
        conn.execute("INSERT INTO entries(category_id,name) VALUES(2,'迁移条目')")
        conn.commit()
        conn.close()

        db = Database(v2)  # 触发结构迁移 v2→v3
        try:
            assert db.get_meta("schema_version") == "3"
            assert db._has_column("domains", "project_id")
            assert len(db.list_projects()) == 5
            assert db.get_entry(1)["name"] == "迁移条目"  # 数据无损
            # 归属分配：全命中 + 未知根目录未回答 → 兜底"未明确分类"
            st = db.assign_domains_to_projects(PROJECT_DOMAIN_MAPPING)
            assert st["matched"] == 2, st
            assert st["unmatched"] == 0 and st["fallback"] == 1
            p_map = {p["name"]: p["id"] for p in db.list_projects()}
            assert db.get_domain(1)["project_id"] == p_map["日常学习记录"]   # 视频
            assert db.get_domain(2)["project_id"] == p_map["个人经验总结"]   # 计算机编程
            assert db.get_domain(3)["project_id"] == db.get_project_by_name("未明确分类")["id"]
            # 幂等：重跑不再产生变化
            st2 = db.assign_domains_to_projects({}, None)
            assert st2 == {"matched": 0, "unmatched": 0, "fallback": 0}, st2
            print("[迁移] v2→v3 结构升级/归属分配/兜底/幂等 通过")
        finally:
            db.close()
        # 幂等：重开库不再重复迁移
        db2 = Database(v2)
        try:
            assert db2.get_meta("schema_version") == "3"
            assert len(db2.list_projects()) == 6  # 5 预置 + 未明确分类
            print("[迁移] 重开幂等 通过")
        finally:
            db2.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    _selftest()
    _migrate_selftest()
