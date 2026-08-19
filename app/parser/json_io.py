# -*- coding: utf-8 -*-
"""
json_io.py - JSON 数据导入导出（完整备份/还原）
创建日期：2026-08-12（阶段六创建；阶段八重构为全局分类+领域关联 v2）

导出格式（version 2）：
{
  "version": 2,
  "exported_at": "…",
  "domains":      [{"name": "视频", "sort_order": 0}, …],
  "domain_links": {"视频": ["第一维度：…", …]},          // 领域 ↔ 一级分类（多对一）
  "categories":   [{"parent": null, "name": "第一维度：…", "sort_order": 0}, …],
  "entries":      [{"path": ["第一维度：…", "写实影像类"], 9字段…, "is_favorite": 0}, …]
}
说明：分类为全局共享树；path 不含领域；path 为空表示"未分类"。
"""
import json
from datetime import datetime

from ..database import Database  # 2026-08-18（P1-1）：内容判重键 content_key
from ..models import Entry

JSON_VERSION = 2


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
def export_json(db, path, category_id=None) -> int:
    """导出全部（或指定分类子树）为 JSON；返回导出的条目数"""
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

    data = {
        "version": JSON_VERSION,
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "domains": [{k: d[k] for k in ("name", "sort_order")} for d in db.list_domains()],
        "domain_links": _domain_links(db, [c["id"] for c in export_cats]),
        "categories": [{"parent": (db.get_category(c["parent_id"])["name"]
                                   if c["parent_id"] else None),
                        "name": c["name"], "sort_order": c["sort_order"]}
                       for c in export_cats],
        "entries": [_entry_payload(db, e) for e in export_entries],
    }
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
    """导入 JSON 备份：重建全局分类树 + 领域关联 + 条目；返回统计"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("version", 1) != JSON_VERSION:
        raise ValueError("JSON 版本不兼容，请使用本软件导出的备份文件")

    # 1. 领域（按名称复用或新建）
    domain_map = {}
    for d in data.get("domains", []):
        name = d["name"]
        existing = next((x for x in db.list_domains() if x["name"] == name), None)
        domain_map[name] = existing["id"] if existing else db.add_domain(name)

    # 2. 全局分类（父先子后；key=(父名或None, 名称)）
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

    # 3. 领域 ↔ 一级分类 关联（多对一共享）
    for dom_name, l1_names in data.get("domain_links", {}).items():
        did = domain_map.get(dom_name)
        if did is None:
            continue
        for l1 in l1_names:
            cid = cat_map.get((None, l1))
            if cid:
                db.link_domain_category(did, cid)

    # 4. 条目（2026-08-18：P2-4 批量插入；P1-1 详情内容去重——内容相同才跳过）
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
    return {"entries": done, "skipped": skipped, "categories": len(data.get("categories", []))}
