# -*- coding: utf-8 -*-
"""
json_io.py - JSON 数据导入导出（完整备份/还原）
创建日期：2026-08-12（阶段六创建；阶段八重构为全局分类+领域关联 v2）

导出格式（version 3，2026-08-29 四级分类施工）：
{
  "version": 3,
  "exported_at": "…",
  "computer_code": "PC-HOME",          // 增量备份来源电脑代号（普通导出可为空）
  "day": "2026-08-29",                 // 增量备份日期（普通导出可为空）
  "projects":      [{"name": "日常学习记录", "sort_order": 0}, …],   // 项目类别（最高层级）
  "domain_projects": {"视频": "日常学习记录", …},                    // 根目录 → 项目类别
  "domains":      [{"name": "视频", "sort_order": 0}, …],
  "domain_links": {"视频": ["第一维度：…", …]},          // 领域 ↔ 一级分类（多对一）
  "categories":   [{"parent": null, "name": "第一维度：…", "sort_order": 0}, …],
  "entries":      [{"path": ["第一维度：…", "写实影像类"], 9字段…, "is_favorite": 0}, …]
}
说明：分类为全局共享树；path 不含领域；path 为空表示"未分类"；
version 2 旧文件仍可导入（无项目归属 → 根目录落"未分配"）。
"""
import json
from datetime import datetime

from ..database import Database  # 2026-08-18（P1-1）：内容判重键 content_key
from ..models import Entry

JSON_VERSION = 3


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
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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


def import_json(db, path, progress_cb=None) -> dict:
    """导入 JSON 备份（v2/v3 兼容）：重建项目类别/全局分类树/领域关联/条目；返回统计"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    ver = data.get("version", 1)
    if ver not in (2, 3):
        raise ValueError("JSON 版本不兼容，请使用本软件导出的备份文件")

    # 1. 项目类别（v3：恢复 根目录→项目类别 归属；v2 无则跳过）
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

    # 2. 领域（按名称复用或新建；v3 恢复项目归属）
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
    total = len(data.get("entries", []))
    done, skipped, processed = 0, 0, 0
    pending = []
    seen_by_cat = {}  # category_id(None=未分类) -> 现有条目"详情内容"键集合
    for ep in data.get("entries", []):
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
    if pending:
        db.add_entries_batch(pending)

    # 6. 删除同步（2026-08-29 增量备份增强）：导入"当日删除"到目标库（尽力而为）
    #    - 分类：按名称链定位后删除（级联其子分类）；条目随之转"未分类"（与原库语义一致）
    #    - 条目：按"详情内容"判重键删除（目标库内容相同视为同一逻辑条目）
    #    - 根目录：按名称删除（仅解除关联）
    deleted = {"entries": 0, "categories": 0, "domains": 0}
    for dc in data.get("deleted_categories", []):
        chain = dc.get("chain") or []
        if not chain:
            continue
        cid = db.find_category_by_chain(chain)
        if cid is not None:
            db.delete_category(cid)
            deleted["categories"] += 1
    for de in data.get("deleted_entries", []):
        cid = None
        chain = de.get("chain") or []
        if chain:
            cid = db.find_category_by_chain(chain)
        deleted["entries"] += db.delete_entries_by_content_key(de.get("content_key", ""),
                                                                cat_id=cid)
    for dd in data.get("deleted_domains", []):
        dom = next((x for x in db.list_domains() if x["name"] == dd.get("name")), None)
        if dom is not None:
            db.delete_domain(dom["id"])
            deleted["domains"] += 1

    return {"entries": done, "skipped": skipped, "categories": len(data.get("categories", [])),
            "deleted": deleted}
