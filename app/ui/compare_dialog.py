# -*- coding: utf-8 -*-
"""
compare_dialog.py - 数据比对（2026-09-08 V1.7.0 新增，只读诊断）

与一个备份库（*.db，含自动备份/手动快照）对比当前库，直观显示差异：
  - 分类差异（备份独有 = 当前已删；当前新增）
  - 根目录/项目类别差异
  - 条目差异（按"详情内容键 content_key"：
      备份独有 = 当前库中已不存在；当前独有 = 备份后新增）
全程只读：备份库先复制到临时文件再打开，绝不写当前主库。
可"导出差异报告（HTML）"另存。
"""
import os
import shutil
import tempfile
import webbrowser
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from ..database import Database


# ---------------------------------------------------------------------- #
# 差异计算（只读）
# ---------------------------------------------------------------------- #
def _category_chain_map(db) -> dict:
    """全部分类：分类 id → 完整名称链文本（[一级…自身]；2026-09-09 P2-15：改以 id 为键，
    避免不同父级下的同名分类互相覆盖导致比对失真）"""
    rows = db.conn.execute("SELECT * FROM categories").fetchall()
    rows = {r["id"]: dict(r) for r in rows}
    out = {}
    for cid, c in rows.items():
        chain = []
        cur = c
        while cur:
            chain.append(cur["name"])
            cur = rows.get(cur["parent_id"])
        out[cid] = " / ".join(chain[::-1])
    return out


def _chain_text(db, category_id) -> str:
    if not category_id:
        return ""
    return " / ".join(db._category_name_chain(category_id))


def _entry_rows(db) -> dict:
    """条目：content_key → (名称, 分类链文本)；未分类 → '未分类'"""
    out = {}
    for e in db.list_all_entries():
        path = _chain_text(db, e.get("category_id")) or "未分类"
        out[Database.content_key(e)] = (e["name"], path)
    return out


def compare_databases(db_current, db_backup) -> dict:
    """对比当前库与备份库，返回结构化差异。"""
    # 分类（按名称链唯一标识；不同层级同名的少见，忽略差异仅在显示层）
    cur_cats = set(" / ".join(v) for v in _category_chain_map(db_current).values())
    bak_cats = set(" / ".join(v) for v in _category_chain_map(db_backup).values())
    # 根目录/项目类别
    cur_doms = {d["name"] for d in db_current.list_domains()}
    bak_doms = {d["name"] for d in db_backup.list_domains()}
    cur_prj = {p["name"] for p in db_current.list_projects()}
    bak_prj = {p["name"] for p in db_backup.list_projects()}

    cur_entries = _entry_rows(db_current)
    bak_entries = _entry_rows(db_backup)
    key_cur = set(cur_entries)
    key_bak = set(bak_entries)

    def _row(kind, name, extra):
        return (kind, name, extra)

    rows = []
    for name in sorted(bak_cats - cur_cats):
        rows.append(_row("分类（备份有/当前无）", name.split(" / ")[-1], name))
    for name in sorted(cur_cats - bak_cats):
        rows.append(_row("分类（当前新增）", name.split(" / ")[-1], name))
    for name in sorted(bak_doms - cur_doms):
        rows.append(_row("根目录（备份有/当前无）", name, ""))
    for name in sorted(cur_doms - bak_doms):
        rows.append(_row("根目录（当前新增）", name, ""))
    for name in sorted(bak_prj - cur_prj):
        rows.append(_row("项目类别（备份有/当前无）", name, ""))
    for name in sorted(cur_prj - bak_prj):
        rows.append(_row("项目类别（当前新增）", name, ""))
    for key in sorted(bak_entries.keys() - cur_entries.keys()):
        nm, path = bak_entries[key]
        rows.append(_row("条目（备份有/当前无）", nm, path))
    for key in sorted(cur_entries.keys() - bak_entries.keys()):
        nm, path = cur_entries[key]
        rows.append(_row("条目（当前新增）", nm, path))

    return {
        "rows": rows,
        "total_current_entries": len(cur_entries),
        "total_backup_entries": len(bak_entries),
        "cat_del": len(bak_cats - cur_cats),
        "cat_add": len(cur_cats - bak_cats),
        "dom_del": len(bak_doms - cur_doms),
        "dom_add": len(cur_doms - bak_doms),
        "entry_del": len(bak_entries.keys() - cur_entries.keys()),
        "entry_add": len(cur_entries.keys() - bak_entries.keys()),
    }


# ---------------------------------------------------------------------- #
# 对话框
# ---------------------------------------------------------------------- #
class CompareDialog(ctk.CTkToplevel):
    """数据比对对话框：选备份库 → 显示差异 → 可导出 HTML 报告。"""

    def __init__(self, master, db):
        super().__init__(master)
        self.master = master
        self.db = db
        self.result = None
        self._tmp_dir = None
        self._rows = []
        self._report_meta = {}

        self.title("数据比对（与备份 *.db）")
        self.geometry("900x560")
        self.transient(master)
        self.grab_set()

        self._build()
        self._choose_file()

    def _build(self) -> None:
        pad = 14
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        head = ctk.CTkLabel(self, text="数据比对（只读，不修改任何数据）",
                            font=("Microsoft YaHei", 15, "bold"))
        head.grid(row=0, column=0, padx=pad, pady=(14, 4), sticky="w")
        self.lbl_summary = ctk.CTkLabel(self, text="请选择要对比的备份库文件…",
                                        font=("Microsoft YaHei", 13), justify="left",
                                        anchor="w", text_color="#1f2937")
        self.lbl_summary.grid(row=1, column=0, padx=pad, pady=(0, 6), sticky="ew")

        # 明细表
        cols = ("kind", "name", "path")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=16)
        self.tree.heading("kind", text="差异类型")
        self.tree.heading("name", text="名称")
        self.tree.heading("path", text="位置 / 备注")
        self.tree.column("kind", width=210, anchor="w")
        self.tree.column("name", width=220, anchor="w")
        self.tree.column("path", width=340, anchor="w")
        self.tree.grid(row=2, column=0, padx=pad, pady=(0, 8), sticky="nsew")
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        vsb.grid(row=2, column=1, sticky="ns", pady=(0, 8))
        self.tree.configure(yscrollcommand=vsb.set)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.grid(row=3, column=0, columnspan=2, sticky="e", padx=pad, pady=(0, 14))
        ctk.CTkButton(btns, text="导出差异报告(HTML)", width=150,
                      command=self._export_html).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="重新选择备份…", width=120,
                      fg_color="#8a94a6", command=self._choose_file).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="关闭", width=90,
                      command=self._close).pack(side="left", padx=6)

    # ------------------------------------------------------------------ #
    def _choose_file(self) -> None:
        path = filedialog.askopenfilename(title="选择要对比的备份库（*.db）", parent=self,
                                          filetypes=[("SQLite 数据库", "*.db"), ("所有文件", "*.*")])
        if not path:
            if not self._rows:
                self._close()
            return
        self._run_compare(path)

    def _run_compare(self, backup_path: str) -> None:
        # 复制到临时文件再只读打开，避免占用冲突与意外写入
        try:
            if self._tmp_dir:
                shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = tempfile.mkdtemp(prefix="ps_compare_")
            tmp = os.path.join(self._tmp_dir, "backup_copy.db")
            shutil.copy2(backup_path, tmp)
            db2 = Database(tmp)
            try:
                diff = compare_databases(self.db, db2)
            finally:
                db2.close()
        except Exception as exc:
            messagebox.showerror("比对失败", f"无法读取备份库：{exc}", parent=self)
            return
        self._rows = diff["rows"]
        self._report_meta = {
            "backup": os.path.basename(backup_path),
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "current": diff["total_current_entries"],
            "backup": diff["total_backup_entries"],
            "cat_del": diff["cat_del"], "cat_add": diff["cat_add"],
            "dom_del": diff["dom_del"], "dom_add": diff["dom_add"],
            "entry_del": diff["entry_del"], "entry_add": diff["entry_add"],
        }
        m = self._report_meta
        self.lbl_summary.configure(text=(
            f"对比备份库：{m['backup']}\n"
            f"条目总数 当前 {m['current']} / 备份 {m['backup']}   "
            f"差异：新增条目 {m['entry_add']}、删除条目 {m['entry_del']}；"
            f"分类 新增 {m['cat_add']} / 删除 {m['cat_del']}；"
            f"根目录 新增 {m['dom_add']} / 删除 {m['dom_del']}"))
        for item in self.tree.get_children():
            self.tree.delete(item)
        for kind, name, path in self._rows:
            self.tree.insert("", "end", values=(kind, name, path))

    def _export_html(self) -> None:
        if not self._rows:
            messagebox.showinfo("提示", "当前没有可导出的差异。", parent=self)
            return
        out = filedialog.asksaveasfilename(
            title="另存为差异报告", parent=self, defaultextension=".html",
            initialfile="data_diff_report.html", filetypes=[("HTML", "*.html")])
        if not out:
            return
        m = self._report_meta
        tr = "".join(
            f"<tr><td>{a}</td><td>{b}</td><td>{c}</td></tr>"
            for a, b, c in self._rows)
        html = (
            "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
            "<title>数据比对报告</title>"
            "<style>body{font-family:'Microsoft YaHei';margin:24px}"
            "table{border-collapse:collapse;width:100%}"
            "th,td{border:1px solid #ccc;padding:4px 8px;font-size:13px;text-align:left}"
            "th{background:#eef2f7}</style></head><body>"
            f"<h2>PromptSprite 数据比对报告</h2>"
            f"<p>生成时间：{m['at']}<br>对比备份库：{m['backup']}"
            f"<br>条目总数：当前 {m['current']} / 备份 {m['backup']}"
            f"<br>差异汇总：新增条目 {m['entry_add']}、删除条目 {m['entry_del']}；"
            f"分类 新增 {m['cat_add']} / 删除 {m['cat_del']}；"
            f"根目录 新增 {m['dom_add']} / 删除 {m['dom_del']}（共 {len(self._rows)} 行）</p>"
            "<table><tr><th>差异类型</th><th>名称</th><th>位置/备注</th></tr>"
            f"{tr}</table></body></html>")
        try:
            with open(out, "w", encoding="utf-8") as f:
                f.write(html)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self.toast_msg = messagebox.askyesno(
            "导出成功", f"差异报告已保存：{out}\n是否立即打开查看？", parent=self)
        if self.toast_msg:
            try:
                webbrowser.open(out)
            except Exception:
                pass

    def _close(self) -> None:
        if self._tmp_dir:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
