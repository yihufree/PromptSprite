# -*- coding: utf-8 -*-
"""
move_selector.py - "移动到分类"树形选择器
创建日期：2026-08-12（阶段三：条目管理）

模态窗口：领域→一级→二级 树形选择；提供"移入未分类"快捷按钮。
关闭后通过 result/selected_cat_id 读取选择结果。
"""
import tkinter as tk
from tkinter import ttk

import customtkinter as ctk


class MoveSelector(ctk.CTkToplevel):
    def __init__(self, master, db):
        super().__init__(master)
        self.db = db
        self.result = "cancel"        # 'ok' / 'uncategorized' / 'cancel'
        self.selected_cat_id = None

        self.title("移动到分类")
        self.geometry("420x520")
        self.resizable(True, True)
        self.transient(master)
        self.grab_set()
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # 树形控件（ttk.Treeview 嵌在 CTk 容器中）
        tree_frame = ctk.CTkFrame(self)
        tree_frame.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        self._iid_to_cat = {}
        self.tree = ttk.Treeview(tree_frame, show="tree")
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self._build_tree()

        # 底部按钮
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        ctk.CTkButton(btn_row, text="📂 移入未分类", width=120, fg_color="#8a94a6",
                      command=self._pick_uncategorized).pack(side="left")
        ctk.CTkButton(btn_row, text="确定", width=88,
                      command=self._confirm).pack(side="right", padx=(4, 0))
        ctk.CTkButton(btn_row, text="取消", width=88,
                      command=self._cancel).pack(side="right", padx=4)

        # 居中于主窗口
        self.update_idletasks()
        x = master.winfo_x() + (master.winfo_width() - 420) // 2
        y = master.winfo_y() + (master.winfo_height() - 520) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.lift()

    def _build_tree(self) -> None:
        # 2026-08-29（M3）：树形目标按 项目类别 → 根目录 → 一级 → 二级 分组
        for p in self.db.list_projects():
            pid = self.tree.insert("", "end", text=p["name"], open=True)
            for d in self.db.list_domains(project_id=p["id"]):
                self._insert_domain(pid, d)
        # 未分配根目录（无归属）
        for d in self.db.list_unassigned_domains():
            self._insert_domain("", d, suffix="（未分配）")

    def _insert_domain(self, parent, d: dict, suffix: str = "") -> None:
        did = self.tree.insert(parent, "end", text=f"{d['name']}{suffix}", open=True)
        for l1 in self.db.list_categories(domain_id=d["id"], parent_id=None):
            l1_id = self.tree.insert(did, "end", text=l1["name"], open=True)
            self._iid_to_cat[l1_id] = l1["id"]
            for l2 in self.db.list_categories(parent_id=l1["id"]):
                l2_id = self.tree.insert(l1_id, "end", text=l2["name"])
                self._iid_to_cat[l2_id] = l2["id"]

    def _pick_uncategorized(self):
        self.result = "uncategorized"
        self.selected_cat_id = None
        self._close()

    def _confirm(self):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid in self._iid_to_cat:  # 仅分类节点可选，领域节点忽略
            self.result = "ok"
            self.selected_cat_id = self._iid_to_cat[iid]
            self._close()

    def _cancel(self):
        self.result = "cancel"
        self._close()

    def _close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
