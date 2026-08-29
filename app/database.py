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
import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional

from .config import (data_dir, PRESET_DOMAINS, PROJECT_PRESETS, PROJECT_FALLBACK,
                     PROJECT_DOMAIN_MAPPING)
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

CREATE INDEX IF NOT EXISTS idx_categories_parent  ON categories(parent_id);
CREATE INDEX IF NOT EXISTS idx_dc_domain          ON domain_category(domain_id);
CREATE INDEX IF NOT EXISTS idx_dc_category        ON domain_category(category_id);
CREATE INDEX IF NOT EXISTS idx_entries_category   ON entries(category_id);
CREATE INDEX IF NOT EXISTS idx_entries_favorite   ON entries(is_favorite);
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
        """v3 增量备份增强（幂等）：categories 时间戳列 + deletion_log 表。
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
                      content_key: str = "") -> None:
        """写入删除日志（kind: entry/category/domain）"""
        self.conn.execute(
            "INSERT INTO deletion_log(kind, name, chain, content_key, deleted_at) "
            "VALUES(?, ?, ?, ?, ?)",
            (kind, name, json.dumps(chain or [], ensure_ascii=False),
             content_key, _now()),
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

    def delete_entry(self, entry_id: int) -> None:
        entry = self.get_entry(entry_id)
        if entry and entry.get("image_path"):
            self._remove_image_file(entry["image_path"])  # 同步删除关联图片（尽力而为）
        # 2026-08-29（增量备份增强）：记录删除日志，供换机同步删除
        if entry:
            self._log_deletion(
                "entry", entry["name"],
                chain=self._category_name_chain(entry["category_id"]) if entry.get("category_id") else [],
                content_key=self.content_key(entry),
            )
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

    def count_entries(self, category_id: Optional[int] = None,
                      include_descendants: bool = False) -> int:
        """统计条目数（COUNT，不加载行；2026-08-29 复审优化：状态栏悬停高频场景用）"""
        if category_id is None:
            return self.conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        if include_descendants:
            ids = self._collect_category_ids(category_id)
            if not ids:
                return 0
            ph = ",".join("?" * len(ids))
            return self.conn.execute(
                f"SELECT COUNT(*) FROM entries WHERE category_id IN ({ph})", ids
            ).fetchone()[0]
        return self.conn.execute(
            "SELECT COUNT(*) FROM entries WHERE category_id = ?", (category_id,)
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
