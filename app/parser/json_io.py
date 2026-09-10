# -*- coding: utf-8 -*-
"""
json_io.py - JSON 数据导入导出（完整备份/还原 / 每日变更包）
创建日期：2026-08-12（阶段六创建；阶段八重构为全局分类+领域关联 v2）

导出格式（version 4，2026-09-08 V1.7.0：变更包 summary 元信息；仍兼容 v2/v3 导入）：
{
  "version": 4,
  "type": "full" | "change",          // full=完整备份；change=某日变更包（每日备份）
  "exported_at": "…",
  "computer_code": "PC-HOME",         // 变更包来源电脑代号（普通导出可为空）
  "day": "2026-09-08",                // 变更包日期（普通导出可为空）
  "summary": {                        // 元信息：让文件"一眼可读"（导入预览/导出披露用）
      "add_entries": n, "add_categories": n, "add_domains": n,   // 新增/修改的对象数
      "del_entries": n, "del_categories": n, "del_domains": n    // 删除的对象数
  },
  "projects":      [{"name": "日常学习记录", "sort_order": 0}, …],
  "domain_projects": {"视频": "日常学习记录", …},
  "domains":      [{"name": "视频", "sort_order": 0}, …],
  "domain_links": {"视频": ["第一维度：…", …]},
  "categories":   [{"parent": null, "name": "第一维度：…", "sort_order": 0}, …],
  "entries":      [{"path": ["第一维度：…", "写实影像类"], 9字段…, "is_favorite": 0}, …],
  "deleted_entries": […], "deleted_categories": […], "deleted_domains": […]  // 仅变更包有
}
说明：分类为全局共享树；path 不含领域；path 为空表示"未分类"；
version 2/3 旧文件仍可导入（无 summary/无 deleted_* 字段按 0/空处理）。
"""
import json
from datetime import datetime
from typing import Optional

from ..database import Database  # 2026-08-18（P1-1）：内容判重键 content_key
from ..models import Entry

JSON_VERSION = 4  # 2026-09-08（V1.7.0）：summary 元信息 + type 字段；v2/v3 文件仍兼容导入


# ---------------------------------------------------------------------- #
# 分类树收集（供本模块与 excel/html 导出共用）
# ---------------------------------------------------------------------- #
def _gather_categories(db, domain_id=None, parent_id=None):
    """收集分类：按领域(domain_id)/子树根(parent_id)收集；两者皆空收集全部一级分类及后代"""
    if parent_id is not None:
        roots = [db.get_category(parent_id)]
    elif domain_id is not None:
        roots = db.list_categories(domain_id=domain_id, parent_id=None)
    else:
        roots = db.list_categories(parent_id=None)
    cats = []
    stack = list(roots)
    while stack:
        c = stack.pop()
        if not c:
            continue
        cats.append(c)
        stack.extend(db.list_categories(parent_id=c["id"]))
    return cats


def _chain_names(db, category_id) -> list:
    """分类名称链（不含领域）：[L1名, L2名…]"""
    names = []
    c = db.get_category(category_id)
    while c:
        names.append(c["name"])
        c = db.get_category(c["parent_id"]) if c["parent_id"] else None
    return names[::-1]


def _ancestor_chain_cats(db, category_id) -> list:
    """从一级分类到自身（含自身）的分类列表（子树导出时保留路径上下文）"""
    chain = []
    c = db.get_category(category_id)
    while c:
        chain.append(c)
        c = db.get_category(c["parent_id"]) if c["parent_id"] else None
    chain.reverse()
    return chain


def _domain_links(db, cat_ids) -> dict:
    """{领域名: [一级分类名, …]}：仅统计给定分类集合中的一级分类"""
    links = {}
    for cid in cat_ids:
        c = db.get_category(cid)
        if c and c["parent_id"] is None:
            for d in db.linked_domains(cid):
                links.setdefault(d["name"], []).append(c["name"])
    return links


def _entry_payload(db, e) -> dict:
    payload = {k: e[k] for k in ("name", "intro", "origin", "features", "scenes", "works",
                                 "image_desc", "prompt_cn", "prompt_en", "image_plan",
                                 "is_favorite")}
    payload["path"] = _chain_names(db, e["category_id"]) if e["category_id"] else []
    return payload


# ---------------------------------------------------------------------- #
# 导出
# ---------------------------------------------------------------------- #
def export_json(db, path, category_id=None, computer_code=None, day=None) -> int:
    """导出全部（或指定分类子树）为 JSON（v3，含项目类别归属）；返回导出的条目数。

    computer_code/day：增量备份场景补充来源信息（电脑代号/日期）；普通导出可省略。
    """
    if category_id is None:
        export_cats = _gather_categories(db)
        export_entries = db.list_all_entries()
    else:
        chain = _ancestor_chain_cats(db, category_id)
        subtree = _gather_categories(db, parent_id=category_id)
        seen, export_cats = set(), []
        for c in chain + subtree:  # 路径上下文(链) + 子树，按 id 去重
            if c["id"] not in seen:
                seen.add(c["id"])
                export_cats.append(c)
        cat_ids = [c["id"] for c in subtree]
        export_entries = [e for cid in cat_ids for e in db.list_entries(cid)]

    projects = db.list_projects()
    p_by_id = {p["id"]: p for p in projects}
    domain_projects = {}
    for d in db.list_domains():
        if d.get("project_id") and d["project_id"] in p_by_id:
            domain_projects[d["name"]] = p_by_id[d["project_id"]]["name"]
    data = {
        "version": JSON_VERSION,
        "type": "full",
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "add_entries": len(export_entries),
            "add_categories": len(export_cats),
            "add_domains": len(db.list_domains()),
            "del_entries": 0,
            "del_categories": 0,
            "del_domains": 0,
        },
        "projects": [{"name": p["name"], "sort_order": p["sort_order"]} for p in projects],
        "domain_projects": domain_projects,
        "domains": [{k: d[k] for k in ("name", "sort_order")} for d in db.list_domains()],
        "domain_links": _domain_links(db, [c["id"] for c in export_cats]),
        "categories": [{"parent": (db.get_category(c["parent_id"])["name"]
                                   if c["parent_id"] else None),
                        "name": c["name"], "sort_order": c["sort_order"]}
                       for c in export_cats],
        "entries": [_entry_payload(db, e) for e in export_entries],
    }
    if computer_code:
        data["computer_code"] = computer_code
    if day:
        data["day"] = day
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return len(export_entries)


# ---------------------------------------------------------------------- #
# 导入
# ---------------------------------------------------------------------- #
def count_json_entries(path) -> int:
    """预览：读取 JSON 中的条目数（用于进度条总量）"""
    with open(path, encoding="utf-8") as f:
        return len(json.load(f).get("entries", []))


def read_pack_summary(path) -> dict:
    """解析 JSON 备份/变更包摘要（v2/v3/v4 兼容；导入预览与导出披露用，不改数据）。

    返回 summary 各项：有 summary 字段用字段值，否则按区块长度推算。
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    add_entries = len(data.get("entries", []))
    add_categories = len(data.get("categories", []))
    add_domains = len(data.get("domains", []))
    del_entries = data.get("deleted_entries", [])
    del_categories = len(data.get("deleted_categories", []))
    del_domains = len(data.get("deleted_domains", []))
    # 携带被删快照数量（⑤逆向恢复的前提）
    deleted_snapshots = sum(1 for x in del_entries
                            if isinstance(x, dict) and x.get("snapshot"))
    s = data.get("summary") or {}
    out = {
        "version": data.get("version"),
        "type": data.get("type", "full"),
        "computer_code": data.get("computer_code", "") or "",
        "day": data.get("day", "") or "",
        "exported_at": data.get("exported_at", "") or "",
        "add_entries": s.get("add_entries", add_entries),
        "add_categories": s.get("add_categories", add_categories),
        "add_domains": s.get("add_domains", add_domains),
        "del_entries": s.get("del_entries", len(del_entries)),
        "del_categories": s.get("del_categories", del_categories),
        "del_domains": s.get("del_domains", del_domains),
        "deleted_snapshots": int(s.get("deleted_snapshots", deleted_snapshots)),
    }
    out["add_total"] = (out["add_entries"] + out["add_categories"]
                        + out["add_domains"])
    out["del_total"] = (out["del_entries"] + out["del_categories"]
                        + out["del_domains"])
    out["has_snapshots"] = out["deleted_snapshots"] > 0
    return out


def _ensure_chain_categories(db, names, top_links=None) -> Optional[int]:
    """按名称链逐级查找，缺失则创建，返回末级分类 id（2026-09-08 V1.7.0：⑤逆向恢复用）。

    top_links（2026-09-09 P1-6）：{一级分类名: [根目录 id, ...]}——新建的一级分类若在
    变更包 domain_links 中有原归属，则同时建立 根目录↔一级分类 关联，避免恢复成"孤儿分类"。
    names 为空返回 None。
    """
    pid = None
    for idx, name in enumerate(names or []):
        row = db.conn.execute(
            "SELECT id FROM categories WHERE parent_id IS ? AND name = ?",
            (pid, name)).fetchone()
        if row:
            pid = row["id"]
        else:
            pid = db.add_category(name, parent_id=pid)
            if idx == 0 and top_links:
                for did in top_links.get(name, ()):
                    db.link_domain_category(did, pid)
    return pid


def import_json(db, path, progress_cb=None, deletion_mode="apply",
                apply_additions=True) -> dict:
    """导入 JSON 备份/变更包（v2/v3/v4 兼容）：重建项目类别/全局分类树/领域关联/条目；返回统计。

    deletion_mode（2026-09-08 V1.7.0）：
      - "apply"：应用文件内 deleted_* 删除清单（同步删除，先进回收站可恢复）；
      - "skip" ：忽略删除清单（仅合并新增/修改，最安全）；
      - "reverse"：逆向恢复——把带快照的删除清单反向转为新增导入（不执行删除）。
    删除清单为空时各模式等价；reverse 仅在包内 deleted_entries 携带 snapshot 时才有实际效果。
    apply_additions（2026-09-08 V1.7.0）：False 时仅应用删除清单，不新增/修改任何数据
    （对应导入向导"② 仅应用删除"）。
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    ver = data.get("version", 1)
    if ver not in (2, 3, 4):
        raise ValueError("JSON 版本不兼容，请使用本软件导出的备份文件")

    add_entries = data.get("entries", [])
    del_entries = data.get("deleted_entries", [])
    del_categories = data.get("deleted_categories", [])
    del_domains = data.get("deleted_domains", [])
    if not apply_additions:
        # "仅应用删除"：清空所有新增/修改区段（项目/领域/分类/关联/条目），保留删除清单
        add_entries = []
        data = {**data, "projects": [], "domains": [],
                "categories": [], "domain_links": {}}
    # 两阶段进度总量 = 新增/修改 +（apply）删除对象数 /（reverse）逐条扫描的删除清单数
    del_units = (len(del_entries) + len(del_categories) + len(del_domains)
                 if deletion_mode == "apply" else 0)
    snap_units = len(del_entries) if deletion_mode == "reverse" else 0
    total = len(add_entries) + max(del_units, snap_units)

    # 1. 项目类别（v3+：恢复 根目录→项目类别 归属；v2 无则跳过）
    project_map = {}
    for pr in data.get("projects", []):
        name = pr["name"]
        existing = db.get_project_by_name(name)
        if existing:
            project_map[name] = existing["id"]
        else:
            try:
                project_map[name] = db.add_project(name)
            except Exception:
                project_map[name] = db.ensure_project(name)
    domain_projects = data.get("domain_projects", {})

    # 2. 领域（按名称复用或新建；v3+ 恢复项目归属）
    domain_map = {}
    for d in data.get("domains", []):
        name = d["name"]
        existing = next((x for x in db.list_domains() if x["name"] == name), None)
        did = existing["id"] if existing else db.add_domain(name)
        pname = domain_projects.get(name)
        if pname and pname in project_map:
            db.move_domain_to_project(did, project_map[pname])
        domain_map[name] = did

    # 3. 全局分类（父先子后；key=(父名或None, 名称)）
    # 2026-08-18（P1-3 修复）：分类按名称复用（与 Excel 导入一致），重复导入不再产生重复分类
    cat_map = {}
    for c in data.get("categories", []):
        pid = None
        if c.get("parent"):
            pid = cat_map.get((None, c["parent"]))
        if c.get("parent") and pid is None:
            continue  # 父级缺失，跳过
        existing = next((x for x in db.list_categories(parent_id=pid)
                         if x["name"] == c["name"]), None)
        if existing:
            cid = existing["id"]
        else:
            cid = db.add_category(c["name"], parent_id=pid)
        cat_map[(c.get("parent"), c["name"])] = cid

    # 4. 领域 ↔ 一级分类 关联（多对一共享）
    for dom_name, l1_names in data.get("domain_links", {}).items():
        did = domain_map.get(dom_name)
        if did is None:
            continue
        for l1 in l1_names:
            cid = cat_map.get((None, l1))
            if cid:
                db.link_domain_category(did, cid)

    # 5. 条目（2026-08-18：P2-4 批量插入；P1-1 详情内容去重——内容相同才跳过）
    done, skipped, processed = 0, 0, 0
    pending = []
    seen_by_cat = {}  # category_id(None=未分类) -> 现有条目"详情内容"键集合
    for ep in add_entries:
        p = ep.get("path") or []
        cid = None
        if len(p) >= 2:
            cid = cat_map.get((p[0], p[1]))          # 挂二级分类
        if cid is None and len(p) >= 1:
            cid = cat_map.get((None, p[0]))          # 挂一级分类
        e = Entry(category_id=cid, name=ep.get("name", ""),
                  intro=ep.get("intro", ""), origin=ep.get("origin", ""),
                  features=ep.get("features", ""), scenes=ep.get("scenes", ""),
                  works=ep.get("works", ""), image_desc=ep.get("image_desc", ""),
                  prompt_cn=ep.get("prompt_cn", ""), prompt_en=ep.get("prompt_en", ""),
                  image_plan=ep.get("image_plan", ""),
                  is_favorite=int(ep.get("is_favorite", 0)))
        keys = seen_by_cat.setdefault(cid, set())
        if not keys:
            existing = db.list_uncategorized() if cid is None else db.list_entries(cid)
            keys.update(Database.content_key(x) for x in existing)
        key = Database.content_key(e)
        processed += 1
        if key in keys:
            skipped += 1
            if progress_cb:
                progress_cb(processed, total, f"{e.name}（重复跳过）")
            continue
        keys.add(key)
        pending.append(e)
        done += 1
        if progress_cb:
            progress_cb(processed, total, e.name)
    # 5.5 逆向恢复（2026-09-08 V1.7.0）：deletion_mode="reverse" 时把带快照的删除清单
    #     反向转为新增导入（不执行删除）；与新增共用判重与批量插入。
    recovered = 0
    if deletion_mode == "reverse":
        base = len(add_entries)
        # P1-6：从包内 domain_links/domain_map 收集"一级分类名 → 根目录 id"，
        # 逆向恢复新建一级分类时恢复其原根目录归属，避免"孤儿分类"。
        top_links = {}
        if domain_map:
            for dname, l1_names in data.get("domain_links", {}).items():
                did = domain_map.get(dname)
                if did is None:
                    continue
                for n in l1_names:
                    top_links.setdefault(n, []).append(did)
        for i, de in enumerate(del_entries):
            sn = de.get("snapshot")
            if not isinstance(sn, dict):
                if progress_cb:
                    progress_cb(base + i + 1, total,
                                f"（本包未携带快照，无法恢复）{de.get('name','')}")
                continue
            cid = _ensure_chain_categories(db, de.get("chain") or [],
                                           top_links=top_links)
            e = Entry(category_id=cid, name=sn.get("name") or de.get("name", ""),
                      intro=sn.get("intro", ""), origin=sn.get("origin", ""),
                      features=sn.get("features", ""), scenes=sn.get("scenes", ""),
                      works=sn.get("works", ""), image_desc=sn.get("image_desc", ""),
                      prompt_cn=sn.get("prompt_cn", ""), prompt_en=sn.get("prompt_en", ""),
                      image_plan=sn.get("image_plan", ""),
                      is_favorite=int(sn.get("is_favorite", 0)))
            keys = seen_by_cat.setdefault(cid, set())
            if not keys:
                existing = db.list_uncategorized() if cid is None else db.list_entries(cid)
                keys.update(Database.content_key(x) for x in existing)
            key = Database.content_key(e)
            if key in keys:  # 内容已存在（可能是当初同步删除后留在回收站/未删干净），跳过
                if progress_cb:
                    progress_cb(base + i + 1, total, f"{e.name}（已存在，跳过）")
                continue
            keys.add(key)
            pending.append(e)
            recovered += 1
            if progress_cb:
                progress_cb(base + i + 1, total, f"逆向恢复条目：{e.name}")
    if pending:
        db.add_entries_batch(pending)

    # 6. 删除同步（2026-08-29 增量备份增强 / 2026-09-08 V1.7.0 加固）：
    #    - 仅 deletion_mode="apply" 时执行；
    #    - 分类：按名称链定位后删除（其子条目保留并转"未分类"，与原库语义一致）；
    #    - 条目：按"详情内容"判重键，一次性建索引后批量移入回收站（可恢复、保留图片）；
    #    - 根目录：按名称删除（仅解除关联，共享分类数据保留）。
    deleted = {"entries": 0, "categories": 0, "domains": 0}
    if deletion_mode == "apply" and del_units:
        base = len(add_entries)
        # 6a 分类
        for i, dc in enumerate(del_categories):
            chain = dc.get("chain") or []
            if chain:
                cid = db.find_category_by_chain(chain)
                if cid is not None:
                    db.delete_category(cid)
                    deleted["categories"] += 1
            if progress_cb:
                progress_cb(base + i + 1, total, f"同步删除分类：{dc.get('name','')}")
        base += len(del_categories)
        # 6b 条目（2026-09-09 审核 P1-3 修复：默认按包内 chain 收敛到分类子树匹配，
        # 链为空或定位失败才全库兜底，减少"同内容多行/多位置"的跨分类误删；
        # 仍为一次建索引 O(N) + 批量单事务移入回收站可恢复）
        if del_entries:
            rows = db.list_all_entries()
            key_index = {}
            cat_of = {}
            for e in rows:
                key_index.setdefault(Database.content_key(e), []).append(e["id"])
                cat_of[e["id"]] = e.get("category_id")

            def _sub_ids(root_cid):
                out = {root_cid}
                stack = [root_cid]
                while stack:
                    cur = stack.pop()
                    for ch in db.list_categories(parent_id=cur):
                        out.add(ch["id"])
                        stack.append(ch["id"])
                return out

            subtree_cache = {}
            hit_ids = []
            for i, de in enumerate(del_entries):
                chain = de.get("chain") or []
                cid = db.find_category_by_chain(chain) if chain else None
                cand = key_index.get(de.get("content_key", ""), ())
                if cid is not None:
                    sub = subtree_cache.get(cid)
                    if sub is None:
                        sub = _sub_ids(cid)
                        subtree_cache[cid] = sub
                    ids = [x for x in cand if cat_of.get(x) in sub]
                else:
                    ids = list(cand)  # 链为空/定位失败 → 全库兜底
                hit_ids.extend(ids)
                if progress_cb:
                    progress_cb(base + i + 1, total, f"同步删除条目：{de.get('name','')}")
            if hit_ids:
                deleted["entries"] += db.trash_entries_batch(
                    list(dict.fromkeys(hit_ids)), reason="变更包删除同步",
                    log_deletion=False)  # P2-13：接收端不写本机删除日志，避免回声广播
        base += len(del_entries)
        # 6c 根目录
        for i, dd in enumerate(del_domains):
            dom = next((x for x in db.list_domains() if x["name"] == dd.get("name")), None)
            if dom is not None:
                db.delete_domain(dom["id"])
                deleted["domains"] += 1
            if progress_cb:
                progress_cb(base + i + 1, total, f"同步删除根目录：{dd.get('name','')}")

    return {"entries": done, "skipped": skipped,
            "categories": len(data.get("categories", [])),
            "deleted": deleted, "mode": deletion_mode,
            "recovered": recovered}
