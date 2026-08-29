# -*- coding: utf-8 -*-
"""
main_window.py - 主窗口：界面布局与交互逻辑
创建日期：2026-08-12（阶段一创建；阶段三~六完善）

布局（五行结构）：
  ┌ 顶部工具栏：🧩PromptSprite | 🔒锁定 | 📂未分类 | ⭐常用 | [搜索框] | ⇩导入 | ⇧导出 | ✚快速新建 ┐
  ├ L0根目录列 | L1一级分类列 | L2二级分类列 | 条目区(卡片/列表) | 详情区(9字段可编辑)         ┤
  └ 底部状态栏（备份失败黄点警告）                                                              ┘

交互逻辑：
  - 点击根目录 → 刷新一级列；点击一级 → 刷新二级列；点击二级 → 刷新条目区
  - 条目卡片/列表切换；点击条目 → 详情区：9 字段可编辑 + 图片关联预览 + 复制(全部/中文/英文) + 收藏 + 删除
  - 切换条目有未保存修改时弹窗：保存/放弃/取消
  - 条目右键：移动到分类(树形选择器)/收藏/复制/删除；分类右键：新增子分类/重命名/删除
  - 🔒锁定：开启后所有删除功能置灰；删除一律二次确认；删除分类其下条目自动转入"未分类"
  - 搜索框实时过滤；清空恢复当前视图
  - 导入：JSON 备份 / Excel / Markdown 手册（均带进度条）；导出：全部或当前分类的 JSON/Excel/HTML
  - ESC 隐藏（托盘恢复）；全局热键呼出时自动聚焦搜索框
"""
import os
import re  # 2026-08-18（第020条，P2-B2 修复）：import re 由 _open_image_plan 函数内上移至模块顶部
import shutil
import tkinter as tk
import webbrowser  # 2026-08-18（第015条）：详情"⑩图像获取方案"打开链接按钮
from datetime import datetime  # 2026-08-29（M4）：增量备份文件名日期
from tkinter import filedialog, messagebox, simpledialog
from typing import Optional

import customtkinter as ctk
import pyperclip

from .. import config
from ..models import Entry
from ..parser import excel_io, html_export, json_io, md_parser
from .copy_move_dialog import CopyMoveDialog  # 2026-08-21（第004条）：各级目录"复制到/移动到"
from .move_selector import MoveSelector
from .progress_dialog import ProgressDialog
from .quick_add import QuickAddWindow
from .settings_dialog import SettingsDialog  # 2026-08-18："设置"入口
from ..incremental_backup import (get_computer_code, incr_dir,  # 2026-08-29（M4）：增量备份
                                  write_incremental, export_incremental_to)


# 详情区字段展示配置：(显示名, 数据库字段键, 文本框高度行数)
# 注：⑧/⑨ 提示词字段的实际高度由 _build_collapsible_field 按像素控制（默认 120px=6 行可见），
#     此处 24 仅为占位值、不参与渲染（2026-08-18 修正：CTkTextbox.height 单位是像素）。
_FIELDS = [
    ("② 介绍", "intro", 3),
    ("③ 溯源", "origin", 3),
    ("④ 核心特征", "features", 3),
    ("⑤ 应用场景", "scenes", 3),
    ("⑥ 代表作", "works", 2),
    ("⑦ 代表高清配图", "image_desc", 3),
    ("⑧ 中文版提示词", "prompt_cn", 24),
    ("⑨ 英文版提示词", "prompt_en", 24),
    ("⑩ 图像获取方案", "image_plan", 3),
]

_COPY_ALL = 0
_COPY_CN = 1
_COPY_EN = 2

_HOVER_SELECT_MS = 300  # 悬停选中延迟（毫秒）：导航与条目区采用"鼠标悬浮即选择"

# 2026-08-18：提示词字段可折叠。注意：CTkTextbox 的 height 单位是【像素】而非行数！
# 实测（默认字体）行高约 20px：
#   - 有内容默认 _COLLAPSED_H=120px（6 行文字完整可见，不被遮盖）
#   - 点击"展开"→ 高度自适应为内容实际显示行数（有多少行就显示多少行，避免空白行）
#   - 无内容时只显示 1 行空行（_EMPTY_H=24px），且不显示"展开"按钮
_COLLAPSIBLE_KEYS = {"prompt_cn", "prompt_en"}
_EMPTY_H = 24          # 无内容：1 行空行（≈20px 行高 + 少量余量）
_COLLAPSED_H = 120     # 有内容默认：6 行完整可见（实测 120px 时约 6.5 行可见）

# "新增/返回"辅助按钮样式：浅色底 + 深色字，保证标签文字清晰可读
_ADD_BTN = dict(fg_color="#e8ecf1", hover_color="#d5dce5", text_color="#1f2937")

# 导航选中态：比默认按钮颜色稍稍加深，便于识别选中的 根目录→一级→二级 链路
_SEL_BTN = dict(fg_color="#25639c", hover_color="#1d4f7c", text_color="white")


class _FieldTooltip:
    """字段悬停提示：鼠标移到字段上显示完整内容（不受滚动框裁剪影响）"""

    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self._tip = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event=None):
        if self._tip is not None:
            return
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        tk.Label(self._tip, text=self.text, justify="left", bg="#ffffe0",
                 relief="solid", borderwidth=1, wraplength=460, padx=8, pady=6,
                 font=("Microsoft YaHei", 10)).pack()
        self._tip.update_idletasks()
        w, h = self._tip.winfo_reqwidth(), self._tip.winfo_reqheight()
        sw, sh = self.widget.winfo_screenwidth(), self.widget.winfo_screenheight()
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        # 2026-08-18（P2-2 修复）：屏幕边界校正——右侧越界左移、底部越界上翻，避免提示框出屏
        if x + w > sw:
            x = max(sw - w - 8, 0)
        if y + h > sh:
            y = max(self.widget.winfo_rooty() - h - 4, 0)
        self._tip.wm_geometry(f"+{x}+{y}")

    def _hide(self, _event=None):
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


class MainWindow(ctk.CTk):
    def __init__(self, db, startup_warning: str = ""):
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        super().__init__()
        self.db = db
        self.startup_warning = startup_warning

        self.title("PromptSprite（提示精灵）")
        self.geometry("1480x780")  # 2026-08-29（M2）：四列导航，默认宽度 1360→1480
        self.minsize(1360, 660)

        # 交互状态
        self._lock_on = False
        self._cur_domain_id = None
        self._cur_cat_id = None
        self._view = None          # (kind, ref)：kind ∈ domain/cat/uncat/fav/search
        self._view_mode = "card"   # 卡片/列表
        self._detail_mode = config.DETAIL_MODE_AUTO  # 2026-08-18：详情字段策略（自动/全部/精简）
        self._detail_show_all_override = False       # 2026-08-18：精简模式下临时"显示全部字段"
        self._remember_size = True                   # 2026-08-18：是否记住窗口大小
        self._detail_entry_id = None
        self._detail_boxes = {}
        self._detail_dirty = False
        self._detail_hidden = set()   # 2026-08-18：当前根目录下详情区隐藏的字段（③-⑦）
        self._select_timer = None   # 悬停选中防抖定时器
        self._l0_btns = {}          # 根目录列按钮引用（用于原地更新高亮）
        self._l1_btns = {}          # 一级分类列按钮引用
        self._l2_btns = {}          # 二级分类列按钮引用
        self._l0_styles = {}        # 根目录列按钮原始配色（恢复高亮用）
        self._l1_styles = {}
        self._l2_styles = {}
        self._p_btns = {}           # 项目类别列按钮引用（四级分类最高层级，2026-08-29 新增）
        self._p_styles = {}         # 项目类别列按钮原始配色
        self._cur_project_id = None  # 当前项目类别 id（None=未分配视图）
        self._nav_initialized = False  # 首次导航默认选择是否已确定
        self._project_col_hidden = False  # 2026-08-29：项目类别列是否折叠
        self._toast_label = None

        self._load_settings()   # 2026-08-18：应用持久化设置（窗口大小/视图模式/详情策略）
        self._build_toolbar()
        self._build_body()
        self._build_statusbar()
        self.refresh_domains()

        self.bind("<Escape>", self._on_escape)
        self._center_window()  # 2026-08-18（第022条）：窗口居中（左右居中、纵向略偏上）

    def _center_window(self) -> None:
        """主窗口定位：左右居中、纵向固定上边距（2026-08-19 00:30，第025条按用户方案简化）。

        方案：屏幕宽度作为变量 sw，窗口中心 = sw 的一半（左右水平居中）；
        纵向上窗口上边距屏幕上边固定 60px（用户指定 50~100px 区间）。
        触发机制（2026-08-19 00:30，第025条重构）：以 <Map> 事件为准——窗口真正显示（映射）
        瞬间触发定位，天然适应 EXE 慢启动（onefile 解压可达数十秒），不依赖固定重试
        时长；每次触发仅带 5 次短重试（共 1 秒），避免并行重试链膨胀；定位成功即置
        _centered 标志，后续 <Map>（如托盘恢复窗口）不再重复移动，不干扰用户手动拖动。
        边框修正：winfo_width 为内容区宽度，用 winfo_rootx - winfo_x 取得装饰框左
        边框宽度，使窗口装饰框真正水平居中（EXE 真机验证：中心偏差 ≤1px）。
        """
        _centered = False  # 2026-08-19 00:30（第025条）：已成功定位过则不再重复移动窗口
        def _do_center(attempt: int = 0):
            nonlocal _centered
            if _centered:                    # 已定位成功 → 不再重复移动窗口
                return
            try:
                self.update_idletasks()                  # 强制完成布局，取得真实尺寸
                w, h = self.winfo_width(), self.winfo_height()
                if not self.winfo_viewable() or w <= 1 or h <= 1:  # 未就绪 → 短重试
                    if attempt < 5:
                        self.after(200, lambda: _do_center(attempt + 1))
                    return
                sw = self.winfo_screenwidth()            # 屏幕宽度变量
                # 边框修正（2026-08-19 00:30，第025条）：winfo_width 是内容区宽度，装饰框
                # 多出左右边框，用 winfo_rootx - winfo_x 取得左边框宽，使装饰框真正居中
                border = self.winfo_rootx() - self.winfo_x()
                frame_w = w + 2 * border if border > 0 else w
                x = max((sw - frame_w) // 2, 0)          # 窗口中心 = 屏幕宽度的一半（左右居中）
                y = 60                                   # 上边距固定 60px（50~100px 区间）
                if y + h > self.winfo_screenheight():    # 极小屏保护：避免窗口底部超出屏幕
                    y = max(self.winfo_screenheight() - h - 20, 0)
                self.geometry(f"+{x}+{y}")
                _centered = True
            except Exception:
                pass

        # 2026-08-19 00:30（第025条）：窗口映射（显示）瞬间触发定位，覆盖 EXE 慢启动场景
        self.bind("<Map>", lambda _e: self.after(150, _do_center), add="+")
        self.after(100, _do_center)  # 首次尝试（窗口若已显示则立即定位）

    # ------------------------------------------------------------------ #
    # 布局构建
    # ------------------------------------------------------------------ #
    def _build_toolbar(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        bar = ctk.CTkFrame(self)
        bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        bar.grid_columnconfigure(4, weight=1)

        ctk.CTkLabel(bar, text="🧩 PromptSprite", font=("Microsoft YaHei", 18, "bold")
                     ).grid(row=0, column=0, padx=(14, 26), pady=8)
        self.lock_btn = ctk.CTkButton(bar, text="🔒 锁定", width=96,
                                      command=self._toggle_lock)
        self.lock_btn.grid(row=0, column=1, padx=4)
        # 2026-08-22（第007条）：记录锁定按钮默认配色——customtkinter 6.0.0 中
        # configure(fg_color=None) 会抛 ValueError，解锁时须恢复为记录的默认色
        self._lock_btn_default_fg = self.lock_btn.cget("fg_color")
        self._lock_btn_default_hover = self.lock_btn.cget("hover_color")
        ctk.CTkButton(bar, text="📂 未分类", width=96, command=self._show_uncategorized
                      ).grid(row=0, column=2, padx=4)
        ctk.CTkButton(bar, text="⭐ 常用", width=96, command=self._show_favorites
                      ).grid(row=0, column=3, padx=4)
        self.search_entry = ctk.CTkEntry(bar, placeholder_text="搜索提示词（匹配全部字段）",
                                         width=340)
        self.search_entry.grid(row=0, column=4, padx=4, sticky="ew")
        self.search_entry.bind("<KeyRelease>", self._on_search_key)

        # 导入/导出下拉菜单
        self.import_menu = tk.Menu(bar, tearoff=0)
        self.import_menu.add_command(label="数据迁移向导…", command=self._open_migrate_wizard)  # 2026-08-29（M5）
        self.import_menu.add_separator()
        self.import_menu.add_command(label="导入 JSON 备份…", command=self._import_json)
        self.import_menu.add_command(label="导入增量备份…", command=self._import_incremental)  # 2026-08-29（M4）：增量合并恢复
        self.import_menu.add_command(label="导入 Excel…", command=self._import_excel)
        self.import_menu.add_command(label="导入 Markdown 手册…", command=self._import_md)
        self.export_menu = tk.Menu(bar, tearoff=0)
        self.export_menu.add_command(label="导出全部 JSON…",
                                     command=lambda: self._export_json(current_only=False))
        self.export_menu.add_command(label="导出当前分类 JSON…",
                                     command=lambda: self._export_json(current_only=True))
        self.export_menu.add_separator()
        self.export_menu.add_command(label="导出全部 Excel…",
                                     command=lambda: self._export_excel(current_only=False))
        self.export_menu.add_command(label="导出当前分类 Excel…",
                                     command=lambda: self._export_excel(current_only=True))
        self.export_menu.add_separator()
        self.export_menu.add_command(label="导出全部 HTML…",
                                     command=lambda: self._export_html(current_only=False))
        self.export_menu.add_command(label="导出当前分类 HTML…",
                                     command=lambda: self._export_html(current_only=True))
        self.export_menu.add_separator()  # 2026-08-29（M4）：增量数据导出
        self.export_menu.add_command(label="增量数据导出 Excel…",
                                     command=lambda: self._export_incremental_browse("excel"))
        self.export_menu.add_command(label="增量数据导出 HTML…",
                                     command=lambda: self._export_incremental_browse("html"))
        self.export_menu.add_command(label="导出增量备份文件到…",
                                     command=self._export_incremental_file_to)

        self.import_btn = ctk.CTkButton(bar, text="⇩ 导入", width=88)
        self.import_btn.grid(row=0, column=5, padx=4)
        self.import_btn.bind("<Button-1>",
                             lambda e: self.import_menu.tk_popup(e.x_root, e.y_root))
        self.export_btn = ctk.CTkButton(bar, text="⇧ 导出", width=88)
        self.export_btn.grid(row=0, column=6, padx=4)
        self.export_btn.bind("<Button-1>",
                             lambda e: self.export_menu.tk_popup(e.x_root, e.y_root))

        self.quick_add_btn = ctk.CTkButton(bar, text="✚ 快速新建", width=110,
                                           command=self._quick_add)
        self.quick_add_btn.grid(row=0, column=7, padx=(4, 4))
        ctk.CTkButton(bar, text="⚙ 设置", width=80, command=self._open_settings  # 2026-08-18：设置入口
                      ).grid(row=0, column=8, padx=(4, 14))
        self.btn_project_toggle = ctk.CTkButton(  # 2026-08-29：项目类别列折叠/展开
            bar, text="🗂 项目列", width=84, command=self._toggle_project_column)
        self.btn_project_toggle.grid(row=0, column=9, padx=(4, 14))

    def _build_body(self) -> None:
        body = ctk.CTkFrame(self)
        body.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 4))
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(4, weight=0)   # 条目区：固定宽度（≤12汉字，2026-08-29 用户要求）
        body.grid_columnconfigure(5, weight=1)   # 详情区：占据剩余空间

        # 2026-08-29 用户要求列宽：1汉字≈14px；项目类别/根目录 ≤8汉字(112px)，一级/二级/条目 ≤12汉字(168px)
        self.project_frame = ctk.CTkScrollableFrame(body, width=112, label_text="项目类别")
        self.l0_frame = ctk.CTkScrollableFrame(body, width=112, label_text="根目录")
        self.l1_frame = ctk.CTkScrollableFrame(body, width=168, label_text="一级分类")
        self.l2_frame = ctk.CTkScrollableFrame(body, width=168, label_text="二级分类")
        self.entry_frame = ctk.CTkScrollableFrame(body, width=168, label_text="条目")

        self.project_frame.grid(row=0, column=0, sticky="nsew")
        self.l0_frame.grid(row=0, column=1, sticky="nsew")
        self.l1_frame.grid(row=0, column=2, sticky="nsew")
        self.l2_frame.grid(row=0, column=3, sticky="nsew")
        self.entry_frame.grid(row=0, column=4, sticky="nsew")

        # 详情区：固定头部（名称/收藏/删除/复制/保存按钮）+ 滚动内容区
        self.detail_root = ctk.CTkFrame(body)
        self.detail_root.grid(row=0, column=5, sticky="nsew")
        self.detail_root.grid_rowconfigure(1, weight=1)
        self.detail_root.grid_columnconfigure(0, weight=1)

        self.detail_head = ctk.CTkFrame(self.detail_root)
        self.detail_head.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 0))

        row1 = ctk.CTkFrame(self.detail_head, fg_color="transparent")
        row1.pack(fill="x", padx=6, pady=(4, 0))
        self._name_entry = ctk.CTkEntry(row1, height=36, font=("Microsoft YaHei", 14, "bold"))
        self._name_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._name_entry.bind("<KeyRelease>", self._mark_dirty)
        self.fav_btn = ctk.CTkButton(row1, text="☆ 收藏", width=92)
        self.fav_btn.pack(side="right", padx=2)
        self.del_btn = ctk.CTkButton(row1, text="🗑 删除", width=80, fg_color="#D9534F")
        self.del_btn.pack(side="right", padx=2)

        row2 = ctk.CTkFrame(self.detail_head, fg_color="transparent")
        row2.pack(fill="x", padx=6, pady=(2, 6))
        ctk.CTkLabel(row2, text="⑧/⑨ 提示词：",
                     font=("Microsoft YaHei", 13, "bold")).pack(side="left")
        self.copy_all_btn = ctk.CTkButton(row2, text="📋 复制全部", width=86, fg_color="#2E8B57")
        self.copy_all_btn.pack(side="left", padx=4)
        self.copy_cn_btn = ctk.CTkButton(row2, text="复制中文", width=74)
        self.copy_cn_btn.pack(side="left", padx=2)
        self.copy_en_btn = ctk.CTkButton(row2, text="复制英文", width=74)
        self.copy_en_btn.pack(side="left", padx=2)
        self.save_btn = ctk.CTkButton(row2, text="💾 保存", width=92, fg_color="#2E8B57")
        self.save_btn.pack(side="right", padx=2)
        self.reset_btn = ctk.CTkButton(row2, text="重置", width=72)
        self.reset_btn.pack(side="right", padx=2)

        self.detail_scroll = ctk.CTkScrollableFrame(self.detail_root, label_text="详情")
        self.detail_scroll.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))

    def _build_statusbar(self) -> None:
        self.status_label = ctk.CTkLabel(self, text="", anchor="w", height=24)
        self.status_label.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 6))
        self._status_default()  # 2026-08-18（第017条）：状态栏默认显示统计（总提示词数/一级目录数/二级目录数/条目数）
        if self.startup_warning:
            self.status_label.configure(text=f"● 备份失败：{self.startup_warning}")

    # ------------------------------------------------------------------ #
    # 状态栏统计（2026-08-18 第017条：按鼠标悬停层级动态显示）
    # ------------------------------------------------------------------ #
    def _cat_entry_count(self, cat_id: int) -> int:
        """分类及其全部子分类下的条目总数（直挂 + 子树递归；2026-08-29 复审优化：COUNT 不加载行）"""
        total = self.db.count_entries(cat_id)
        for sub in self.db.list_categories(parent_id=cat_id):
            total += self._cat_entry_count(sub["id"])
        return total

    def _domain_entry_count(self, domain_id: int) -> int:
        """根目录下全部提示词总数（其关联的所有一级分类子树）"""
        total = 0
        for c in self.db.list_categories(domain_id=domain_id, parent_id=None):
            total += self._cat_entry_count(c["id"])
        return total

    def _status_default(self) -> None:
        """无选择状态：总提示词数 / 项目类别 / 根目录 / 一级 / 二级目录数"""
        total = self.db.stats()["entries"]  # 2026-08-29（B5 修复）：COUNT 取代全表加载
        np_ = len(self.db.list_projects())
        nd = len(self.db.list_domains())
        l1 = len(self.db.list_categories(parent_id=None))
        l2 = 0
        for c in self.db.list_categories(parent_id=None):
            l2 += len(self.db.list_categories(parent_id=c["id"]))
        self.status_label.configure(
            text=f"总提示词数 {total}｜项目类别 {np_}｜根目录 {nd}｜一级目录 {l1}｜二级目录 {l2}")

    def _status_hover_project(self, project_id: Optional[int]) -> None:
        """悬停项目类别：项目名 + 根目录数 + 提示词总数（"未分配"显示无归属统计）"""
        total = self.db.stats()["entries"]  # 2026-08-29（B5 修复）：COUNT 取代全表加载
        if project_id is None:
            domains = self.db.list_unassigned_domains()
            name = "未分配"
        else:
            p = self.db.get_project(project_id)
            if not p:
                return
            name = p["name"]
            domains = self.db.list_domains(project_id=project_id)
        n = sum(self._domain_entry_count(d["id"]) for d in domains)
        self.status_label.configure(
            text=f"总提示词数 {total}｜项目【{name}】根目录 {len(domains)} 个｜提示词 {n}")

    def _status_hover_domain(self, domain_id: int) -> None:
        """悬停根目录：总提示词数 + 当前根目录名称和该项下提示词总数 + 所属项目"""
        d = self.db.get_domain(domain_id)
        if not d:
            return
        total = self.db.stats()["entries"]  # 2026-08-29（B5 修复）：COUNT 取代全表加载
        n = self._domain_entry_count(domain_id)
        pname = ""
        if d.get("project_id"):
            p = self.db.get_project(d["project_id"])
            pname = f"｜项目【{p['name']}】" if p else ""
        self.status_label.configure(
            text=f"总提示词数 {total}｜根目录【{d['name']}】提示词 {n}{pname}")

    def _status_hover_cat(self, cat_id: int) -> None:
        """悬停一级/二级分类：所属根目录 + 一级（+二级）统计"""
        cat = self.db.get_category(cat_id)
        if not cat:
            return
        total = self.db.stats()["entries"]  # 2026-08-29（B5 修复）：COUNT 取代全表加载
        root = self.db.category_root(cat_id)          # 一级分类 id（category_root 返回 id）
        doms = self.db.linked_domains(root) if root else []
        dom_name = doms[0]["name"] if doms else ""
        dom_n = self._domain_entry_count(doms[0]["id"]) if doms else 0
        l1_cat = self.db.get_category(root) if root else None
        l1_name = l1_cat["name"] if l1_cat else ""
        l1_n = self._cat_entry_count(root) if root else 0
        text = f"总提示词数 {total}｜根目录【{dom_name}】提示词 {dom_n}｜一级【{l1_name}】提示词 {l1_n}"
        if cat["parent_id"] is not None:              # 二级分类
            l2_n = self._cat_entry_count(cat_id)
            text += f"｜二级【{cat['name']}】提示词 {l2_n}"
        self.status_label.configure(text=text)

    def _status_hover_entry(self, e: dict) -> None:
        """悬停条目：链路统计（根目录/一级/二级）+ 本条目的名称"""
        total = self.db.stats()["entries"]  # 2026-08-29（B5 修复）：COUNT 取代全表加载
        cat_id = e.get("category_id")
        name = (e.get("name") or "").strip()
        if not cat_id:                                 # 未分类条目
            self.status_label.configure(
                text=f"总提示词数 {total}｜未分类条目【{name}】")
            return
        root = self.db.category_root(cat_id)          # 一级分类 id
        doms = self.db.linked_domains(root) if root else []
        dom_name = doms[0]["name"] if doms else ""
        dom_n = self._domain_entry_count(doms[0]["id"]) if doms else 0
        l1_cat = self.db.get_category(root) if root else None
        l1_name = l1_cat["name"] if l1_cat else ""
        l1_n = self._cat_entry_count(root) if root else 0
        text = f"总提示词数 {total}｜根目录【{dom_name}】提示词 {dom_n}｜一级【{l1_name}】提示词 {l1_n}"
        cat = self.db.get_category(cat_id)
        if cat and cat["parent_id"] is not None:       # 条目挂在二级分类下
            l2_n = self._cat_entry_count(cat_id)
            text += f"｜二级【{cat['name']}】提示词 {l2_n}"
        text += f"｜条目【{name}】"
        self.status_label.configure(text=text)

    @staticmethod
    def _clear_frame(frame) -> None:
        for child in frame.winfo_children():
            child.destroy()

    # ------------------------------------------------------------------ #
    # 悬停选中（主界面导航/条目采用"鼠标悬浮即选择"，与快捷新建一致）
    # ------------------------------------------------------------------ #
    def _schedule_select(self, ms: int, fn) -> None:
        self._cancel_select()
        self._select_timer = self.after(ms, fn)

    def _cancel_select(self) -> None:
        if self._select_timer is not None:
            try:
                self.after_cancel(self._select_timer)
            except Exception:
                pass
            self._select_timer = None

    # ------------------------------------------------------------------ #
    # 根目录 / 分类导航
    # ------------------------------------------------------------------ #
    def refresh_domains(self, silent: bool = False) -> None:
        """（重）渲染四列导航并复位。

        silent=True（2026-08-18，P1-2 修复）：跳过"未保存修改"检查、保留详情区当前状态，
        供快捷新建保存后调用，避免弹出未保存确认框打断连续录入。
        """
        if not silent and not self._confirm_unsaved():
            return
        self._ensure_default_project()
        self._refresh_projects()
        self._refresh_l0()
        self._refresh_l1()
        self._clear_frame(self.l2_frame)
        self._clear_nav_btns("l2")
        self._render_entries([], "条目")
        if not silent:
            self._show_detail(None)

    def _ensure_default_project(self) -> None:
        """首次导航默认选择：存在未分配根目录则停留"未分配"视图，否则选中第一个项目类别"""
        if self._nav_initialized:
            return
        self._nav_initialized = True
        if self.db.list_unassigned_domains():
            self._cur_project_id = None
            self._view = ("project", None)
        else:
            projects = self.db.list_projects()
            if projects:
                self._cur_project_id = projects[0]["id"]
                self._view = ("project", self._cur_project_id)

    # ---- 导航按钮引用（选中高亮原地更新，不销毁重建，杜绝悬停闪烁） ----
    def _clear_nav_btns(self, col: str) -> None:
        if col == "p":
            self._p_btns, self._p_styles = {}, {}
        elif col == "l0":
            self._l0_btns, self._l0_styles = {}, {}
        elif col == "l1":
            self._l1_btns, self._l1_styles = {}, {}
        else:
            self._l2_btns, self._l2_styles = {}, {}

    @staticmethod
    def _style_nav_btn(btn, selected: bool, orig) -> None:
        """选中态：深蓝底白字；未选中：恢复创建时的原始配色（不能传 None）"""
        if selected:
            btn.configure(fg_color=_SEL_BTN["fg_color"],
                          hover_color=_SEL_BTN["hover_color"],
                          text_color=_SEL_BTN["text_color"])
        else:
            if orig is None:
                # 2026-08-18（P1-4 修复）：样式字典缺失该按钮时保持当前样式，避免解包 None 崩溃
                return
            fg, hover, text = orig
            btn.configure(fg_color=fg, hover_color=hover, text_color=text)

    def _apply_nav_highlight(self) -> None:
        """原地更新四列选中高亮"""
        l1_sel = self._l1_highlight_id()
        l2_sel = self._l2_highlight_id()
        for pid, btn in self._p_btns.items():
            self._style_nav_btn(btn, pid == self._cur_project_id, self._p_styles.get(pid))
        for cid, btn in self._l0_btns.items():
            self._style_nav_btn(btn, cid == self._cur_domain_id, self._l0_styles.get(cid))
        for cid, btn in self._l1_btns.items():
            self._style_nav_btn(btn, cid == l1_sel, self._l1_styles.get(cid))
        for cid, btn in self._l2_btns.items():
            self._style_nav_btn(btn, cid == l2_sel, self._l2_styles.get(cid))

    @staticmethod
    def _attach_nav_tooltip(btn, name: str, budget: int) -> None:
        """长名称悬停提示：名称长度超过列宽预算时附加 tooltip（2026-08-29 UI 优化）。
        budget：该列可显示的大致汉字数（项目/根目录≈8，一级/二级/条目≈12）。
        """
        if len(name) > budget:
            _FieldTooltip(btn, name)

    # ------------------------------------------------------------------ #
    # 项目类别（四级分类最高层级，2026-08-29 M2 新增）
    # ------------------------------------------------------------------ #
    def _refresh_projects(self) -> None:
        """渲染项目类别列（含"新增"按钮、"未分配"虚拟项）"""
        self._clear_frame(self.project_frame)
        self._clear_nav_btns("p")
        ctk.CTkButton(self.project_frame, text="➕ 新增项目类别", height=30, **_ADD_BTN,
                      state="disabled" if self._lock_on else "normal",
                      command=self._add_project).pack(fill="x", padx=6, pady=3)
        for p in self.db.list_projects():
            btn = ctk.CTkButton(self.project_frame, text=p["name"], anchor="w", height=32,
                                command=lambda pid=p["id"]: self._select_project(pid))
            btn.pack(fill="x", padx=6, pady=2)
            self._p_btns[p["id"]] = btn
            self._p_styles[p["id"]] = (btn.cget("fg_color"), btn.cget("hover_color"),
                                        btn.cget("text_color"))
            btn.bind("<Button-3>",
                     lambda e, pid=p["id"], n=p["name"]: self._project_menu(e, pid, n))
            btn.bind("<Enter>",
                     lambda _e, pid=p["id"]: (self._schedule_select(
                         _HOVER_SELECT_MS, lambda: self._select_project(pid)),
                         self._status_hover_project(pid)))
            btn.bind("<Leave>", lambda _e: (self._cancel_select(),
                                            self._status_default()))
            self._attach_nav_tooltip(btn, p["name"], 6)  # 2026-08-29：长名称悬停提示
        # "未分配"虚拟项（存在无归属根目录时显示）
        if self.db.list_unassigned_domains():
            btn = ctk.CTkButton(self.project_frame, text="🗂 未分配", anchor="w", height=32,
                                command=lambda: self._select_project(None))
            btn.pack(fill="x", padx=6, pady=2)
            self._p_btns[None] = btn
            self._p_styles[None] = (btn.cget("fg_color"), btn.cget("hover_color"),
                                    btn.cget("text_color"))
            btn.bind("<Button-3>", lambda e: self._project_menu(e, None, "未分配"))
            btn.bind("<Enter>", lambda _e: (self._schedule_select(
                _HOVER_SELECT_MS, lambda: self._select_project(None)),
                self._status_hover_project(None)))
            btn.bind("<Leave>", lambda _e: (self._cancel_select(),
                                            self._status_default()))
        self._apply_nav_highlight()

    def _select_project(self, project_id: Optional[int]) -> None:
        """选择项目类别（None=未分配视图）：重建根目录/分类/条目列"""
        if self._cur_project_id == project_id and self._cur_domain_id is None:
            return
        self._cur_project_id = project_id
        self._cur_domain_id = None
        self._cur_cat_id = None
        self._view = ("project", project_id)
        self._refresh_projects()   # 原地高亮
        self._refresh_l0()
        self._refresh_l1()
        self._clear_frame(self.l2_frame)
        self._clear_nav_btns("l2")
        self._render_entries([], "条目")

    def _toggle_project_column(self) -> None:
        """折叠/展开项目类别列（2026-08-29 UI 优化）"""
        if self._project_col_hidden:
            self.project_frame.grid(row=0, column=0, sticky="nsew")
            self._project_col_hidden = False
            self.btn_project_toggle.configure(text="🗂 项目列")
        else:
            self.project_frame.grid_remove()
            self._project_col_hidden = True
            self.btn_project_toggle.configure(text="📄 展开项目")

    def _project_menu(self, event, project_id: Optional[int], name: str) -> None:
        lock_state = "disabled" if self._lock_on else "normal"
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="新增项目类别", state=lock_state, command=self._add_project)
        if project_id is not None:
            m.add_command(label="重命名", state=lock_state,
                          command=lambda: self._rename_project(project_id))
            m.add_command(label="删除", state=lock_state,
                          command=lambda: self._delete_project(project_id))
        m.tk_popup(event.x_root, event.y_root)

    def _add_project(self) -> None:
        if self._lock_on:
            return
        name = simpledialog.askstring("新增项目类别", "请输入项目类别名称：", parent=self)
        if name and name.strip():
            pid = self.db.add_project(name.strip())
            self._select_project(pid)
            self.toast("✅ 已新增项目类别")

    def _rename_project(self, project_id: int) -> None:
        if self._lock_on:
            return
        p = self.db.get_project(project_id)
        if not p:
            return
        name = simpledialog.askstring("重命名项目类别", "请输入新名称：",
                                      initialvalue=p["name"], parent=self)
        if name and name.strip() and name.strip() != p["name"]:
            self.db.rename_project(project_id, name.strip())
            self._refresh_projects()
            self.toast("✅ 已重命名")

    def _delete_project(self, project_id: int) -> None:
        if self._lock_on:
            return
        p = self.db.get_project(project_id)
        if not p:
            return
        n = self.db.count_project_domains(project_id)
        if not messagebox.askyesno("删除确认",
                                   f"⚠️ 确定要删除项目类别【{p['name']}】吗？"):
            return
        fallback = self.db.ensure_project(config.PROJECT_FALLBACK)
        if n:
            if not messagebox.askyesno(
                    "根目录迁移",
                    f"该项目下 {n} 个根目录将移动到【{config.PROJECT_FALLBACK}】，继续？"):
                return
        self.db.delete_project(project_id, fallback_project_id=fallback)
        if self._cur_project_id == project_id:
            self._cur_project_id = None
        self._refresh_projects()
        self._refresh_l0()
        self.toast("已删除项目类别")

    def _choose_project(self, title: str, message: str,
                        include_unassigned: bool = False):
        """弹窗选择一个项目类别；选中返回 (True, pid)，取消返回 None。
        include_unassigned=True 时提供"🗂 未分配"选项（pid 为 None 表示清除归属）。
        2026-08-29：抽取到公共模块 project_chooser 复用（冗余优化）。
        """
        if not self.db.list_projects():
            self.toast("暂无项目类别", color="#D9534F")
            return None
        from .project_chooser import choose_project
        return choose_project(self, self.db, title, message, include_unassigned)

    def _move_domain_to_project(self, domain_id: int) -> None:
        """根目录 → 其他项目类别（快捷移动；"复制到"走 M3 的复制/移动对话框）"""
        if self._lock_on:
            return
        d = self.db.get_domain(domain_id)
        if not d:
            return
        r = self._choose_project("移动到项目类别", f"【{d['name']}】移动到：",
                                 include_unassigned=True)
        if r is None:
            return
        self.db.move_domain_to_project(domain_id, r[1])
        self._refresh_projects()
        self._refresh_l0()
        self.toast("✅ 已移动")

    def _refresh_l0(self) -> None:
        """渲染根目录列（当前项目类别下；"未分配"视图显示无归属根目录）"""
        self._clear_frame(self.l0_frame)
        self._clear_nav_btns("l0")
        ctk.CTkButton(self.l0_frame, text="➕ 新增根目录", height=30, **_ADD_BTN,
                      state="disabled" if self._lock_on else "normal",  # 2026-08-21（第005条）：锁定时禁用新增
                      command=self._add_domain).pack(fill="x", padx=6, pady=3)
        domains = (self.db.list_unassigned_domains() if self._cur_project_id is None
                   else self.db.list_domains(project_id=self._cur_project_id))
        for d in domains:
            btn = ctk.CTkButton(self.l0_frame, text=d["name"], anchor="w", height=32,
                                command=lambda did=d["id"]: self._select_domain(did))
            btn.pack(fill="x", padx=6, pady=2)
            self._l0_btns[d["id"]] = btn
            self._l0_styles[d["id"]] = (btn.cget("fg_color"), btn.cget("hover_color"),
                                         btn.cget("text_color"))
            btn.bind("<Button-3>",
                     lambda e, did=d["id"], n=d["name"]: self._domain_menu(e, did, n))
            btn.bind("<Enter>",
                     lambda _e, did=d["id"]: (self._schedule_select(
                         _HOVER_SELECT_MS, lambda: self._select_domain(did)),
                         self._status_hover_domain(did)))  # 2026-08-18：状态栏显示根目录统计
            btn.bind("<Leave>", lambda _e: (self._cancel_select(),
                                            self._status_default()))  # 2026-08-18：离开恢复默认统计
            self._attach_nav_tooltip(btn, d["name"], 6)  # 2026-08-29：长名称悬停提示
        self._apply_nav_highlight()

    def _select_domain(self, domain_id: int) -> None:
        # 已选中同一根目录且未深入子级时直接返回
        if self._cur_domain_id == domain_id and self._cur_cat_id is None:
            return
        domain_changed = self._cur_domain_id != domain_id
        self._cur_domain_id = domain_id
        self._cur_cat_id = None
        self._view = ("domain", domain_id)
        if domain_changed:
            self._refresh_l0()   # 领域切换：重建（含高亮）
            self._refresh_l1()   # 一级内容随领域变化
        else:
            self._apply_nav_highlight()  # 返回根视图：原地更新高亮
        self._clear_frame(self.l2_frame)
        self._clear_nav_btns("l2")
        self._render_entries([], "条目")

    # ---- 选中链路辅助（用于三列高亮） ----
    def _l1_highlight_id(self):
        """当前浏览链路的一级分类 id（用于一级列高亮）"""
        if not self._cur_cat_id:
            return None
        return self.db.category_root(self._cur_cat_id)

    def _l2_highlight_id(self):
        """当前选中的二级分类 id（一级分类自身不在此列高亮）"""
        if not self._cur_cat_id:
            return None
        cat = self.db.get_category(self._cur_cat_id)
        return self._cur_cat_id if (cat and cat["parent_id"] is not None) else None

    def _l2_parent_id(self):
        """二级列当前应展示的父分类 id：一级分类展示其子级；二级叶子展示其兄弟"""
        if not self._cur_cat_id:
            return None
        cat = self.db.get_category(self._cur_cat_id)
        if cat is None:
            return None
        return self._cur_cat_id if cat["parent_id"] is None else cat["parent_id"]

    def _add_l1(self) -> None:
        """一级分类新增按钮：在当前领域下新建一级分类并建立领域关联"""
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止新增
            return
        if self._cur_domain_id is None:
            self.toast("请先选择根目录", color="#D9534F")
            return
        name = simpledialog.askstring("新增一级分类", "请输入一级分类名称：", parent=self)
        if name and name.strip():
            self.db.add_category(name.strip(), domain_id=self._cur_domain_id)
            self._refresh_l1()

    def _refresh_l1(self) -> None:
        self._clear_frame(self.l1_frame)
        self._clear_nav_btns("l1")
        ctk.CTkButton(self.l1_frame, text="➕ 新增一级分类", height=28, **_ADD_BTN,
                      state="disabled" if self._lock_on else "normal",  # 2026-08-21（第005条）：锁定时禁用新增
                      command=self._add_l1).pack(fill="x", padx=6, pady=2)
        for c in self.db.list_categories(domain_id=self._cur_domain_id, parent_id=None):
            btn = ctk.CTkButton(self.l1_frame, text=c["name"], anchor="w", height=30,
                                command=lambda cid=c["id"]: self._select_category(cid))
            btn.pack(fill="x", padx=6, pady=2)
            self._l1_btns[c["id"]] = btn
            self._l1_styles[c["id"]] = (btn.cget("fg_color"), btn.cget("hover_color"),
                                         btn.cget("text_color"))
            btn.bind("<Button-3>",
                     lambda e, cid=c["id"], n=c["name"]: self._category_menu(e, cid, n))
            btn.bind("<Enter>",
                     lambda _e, cid=c["id"]: (self._schedule_select(
                         _HOVER_SELECT_MS, lambda: self._select_category(cid)),
                         self._status_hover_cat(cid)))  # 2026-08-18：状态栏显示一级分类统计
            btn.bind("<Leave>", lambda _e: (self._cancel_select(),
                                            self._status_default()))  # 2026-08-18：离开恢复默认统计
            self._attach_nav_tooltip(btn, c["name"], 10)  # 2026-08-29：长名称悬停提示
        self._apply_nav_highlight()

    def _add_l2(self) -> None:
        """二级分类新增按钮：在当前分类下新建子分类"""
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止新增
            return
        if self._cur_cat_id is None:
            self.toast("请先在左侧选中分类", color="#D9534F")
            return
        name = simpledialog.askstring("新增二级分类", "请输入二级分类名称：", parent=self)
        if name and name.strip():
            self.db.add_category(name.strip(), parent_id=self._cur_cat_id)
            self._refresh_l2()

    def _refresh_l2(self) -> None:
        self._clear_frame(self.l2_frame)
        self._clear_nav_btns("l2")
        parent_id = self._l2_parent_id()
        ctk.CTkButton(self.l2_frame, text="➕ 新增二级分类", height=28, **_ADD_BTN,
                      state="disabled" if self._lock_on else "normal",  # 2026-08-21（第005条）：锁定时禁用新增
                      command=self._add_l2).pack(fill="x", padx=6, pady=2)
        if parent_id is not None:
            for c in self.db.list_categories(parent_id=parent_id):
                btn = ctk.CTkButton(self.l2_frame, text=c["name"], anchor="w", height=30,
                                    command=lambda cid=c["id"]: self._select_category(cid))
                btn.pack(fill="x", padx=6, pady=2)
                self._l2_btns[c["id"]] = btn
                self._l2_styles[c["id"]] = (btn.cget("fg_color"), btn.cget("hover_color"),
                                             btn.cget("text_color"))
                btn.bind("<Button-3>",
                         lambda e, cid=c["id"], n=c["name"]: self._category_menu(e, cid, n))
                btn.bind("<Enter>",
                         lambda _e, cid=c["id"]: (self._schedule_select(
                             _HOVER_SELECT_MS, lambda: self._select_category(cid)),
                             self._status_hover_cat(cid)))  # 2026-08-18：状态栏显示二级分类统计
                btn.bind("<Leave>", lambda _e: (self._cancel_select(),
                                                self._status_default()))  # 2026-08-18：离开恢复默认统计
                self._attach_nav_tooltip(btn, c["name"], 10)  # 2026-08-29：长名称悬停提示
        self._apply_nav_highlight()

    def _select_category(self, cat_id: int) -> None:
        """点击/悬停分类：高亮原地更新；一级展开时重建二级列；二级叶子显示条目"""
        if self._cur_cat_id == cat_id:
            return
        if not self._confirm_unsaved():
            return
        self._cur_cat_id = cat_id
        if self.db.category_has_children(cat_id):
            self._refresh_l2()          # 一级展开：二级列内容变化，重建（内部应用高亮）
            self._render_entries([], "条目")
        else:
            cat = self.db.get_category(cat_id)
            if cat is not None and cat["parent_id"] is None:
                # 2026-08-18 13:37：修复残留——无子分类的一级分类（如"按年代"维度）也须重建二级列，
                # 否则从其他一级分类移入时，上一分类的二级按钮不会消失、造成误认。
                self._refresh_l2()
            self._view = ("cat", cat_id)
            self._render_entries(self.db.list_entries(cat_id), "条目")
            self._apply_nav_highlight()  # 原地更新高亮，不重建按钮，杜绝闪烁

    # ------------------------------------------------------------------ #
    # 未分类 / 收藏 / 搜索
    # ------------------------------------------------------------------ #
    def _show_uncategorized(self) -> None:
        if not self._confirm_unsaved():
            return
        self._view = ("uncat", None)
        self._render_entries(self.db.list_uncategorized(), "📂 未分类")

    def _show_favorites(self) -> None:
        if not self._confirm_unsaved():
            return
        self._view = ("fav", None)
        self._render_entries(self.db.list_favorites(), "⭐ 常用")

    def _on_search_key(self, _event=None) -> None:
        kw = self.search_entry.get().strip()
        if not kw:
            self._restore_view()
            return
        self._view = ("search", kw)
        self._render_entries(self.db.search(kw), f"搜索结果（{kw}）")

    def _restore_view(self) -> None:
        """重新渲染当前浏览视图（搜索清空/切换视图/保存后刷新）"""
        if self._view is None:
            return
        kind, ref = self._view
        if kind == "project":
            self._cur_project_id = ref
            self._refresh_projects()
            self._refresh_l0()
            self._refresh_l1()
            self._render_entries([], "条目")
        elif kind == "domain":
            self._refresh_l1()
            self._render_entries([], "条目")
        elif kind == "cat":
            self._render_entries(self.db.list_entries(ref), "条目")
        elif kind == "uncat":
            self._render_entries(self.db.list_uncategorized(), "📂 未分类")
        elif kind == "fav":
            self._render_entries(self.db.list_favorites(), "⭐ 常用")
        elif kind == "search":
            self._render_entries(self.db.search(ref), f"搜索结果（{ref}）")

    # ------------------------------------------------------------------ #
    # 条目区（卡片/列表视图切换）
    # ------------------------------------------------------------------ #
    def _set_view_mode(self, value: str) -> None:
        self._view_mode = "card" if value == "卡片" else "list"
        self._restore_view()

    def _render_entries(self, entries, title: str) -> None:
        self.entry_frame.configure(label_text=title)
        self._clear_frame(self.entry_frame)

        toggle = ctk.CTkFrame(self.entry_frame, fg_color="transparent")
        toggle.pack(fill="x", padx=6, pady=(4, 2))
        ctk.CTkLabel(toggle, text=f"共 {len(entries)} 条",
                     text_color="gray").pack(side="left")
        switch = ctk.CTkSegmentedButton(toggle, values=["卡片", "列表"], width=150,
                                        command=self._set_view_mode)
        switch.set("卡片" if self._view_mode == "card" else "列表")
        switch.pack(side="right")

        if not entries:
            ctk.CTkLabel(self.entry_frame, text="（暂无条目）",
                         text_color="gray").pack(pady=30)
            return
        if self._view_mode == "card":
            for e in entries:
                self._add_card(e)
        else:
            for e in entries:
                self._add_row(e)

    def _add_card(self, e: dict) -> None:
        card = ctk.CTkFrame(self.entry_frame, corner_radius=8)
        card.pack(fill="x", padx=6, pady=3)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(6, 0))
        star = "★ " if e["is_favorite"] else ""
        ctk.CTkLabel(top, text=f"{star}{e['name']}",
                     font=("Microsoft YaHei", 13, "bold"), anchor="w").pack(side="left")
        summary = (e["intro"] or "").strip() or "（无介绍）"
        ctk.CTkLabel(card, text=summary, wraplength=330, justify="left",
                     text_color="gray", anchor="w").pack(fill="x", padx=8, pady=(2, 6))
        self._bind_card_events(card, e)

    def _add_row(self, e: dict) -> None:
        row = ctk.CTkFrame(self.entry_frame, corner_radius=6)
        row.pack(fill="x", padx=6, pady=1)
        star = "★ " if e["is_favorite"] else ""
        ctk.CTkLabel(row, text=f"{star}{e['name']}", font=("Microsoft YaHei", 12, "bold"),
                     anchor="w").pack(side="left", padx=8, pady=4)
        summary = ((e["intro"] or "").replace("\n", " ")[:36]) or "（无介绍）"
        ctk.CTkLabel(row, text=summary, text_color="gray", anchor="e",
                     wraplength=240).pack(side="right", padx=8)
        self._bind_card_events(row, e)

    def _bind_card_events(self, widget, e: dict) -> None:
        entry_id = e["id"]

        def _click(_ev, eid=entry_id):
            self._select_entry(eid)

        def _menu(_ev, eid=entry_id):
            self._entry_menu(_ev, eid)

        def _hover(_ev, ent=e):
            self._schedule_select(_HOVER_SELECT_MS, lambda: self._select_entry(ent["id"]))
            self._status_hover_entry(ent)  # 2026-08-18：状态栏显示条目链路统计

        def _leave(_ev):
            self._cancel_select()
            self._status_default()  # 2026-08-18：离开恢复默认统计

        for w in [widget] + widget.winfo_children():
            w.bind("<Button-1>", _click)
            w.bind("<Button-3>", _menu)
            w.bind("<Enter>", _hover)
            w.bind("<Leave>", _leave)

    def _select_entry(self, entry_id: int) -> None:
        if not self._confirm_unsaved():
            return
        e = self.db.get_entry(entry_id)
        if e:
            self._show_detail(e)

    # ------------------------------------------------------------------ #
    # 详情区（9 字段可编辑 + 图片预览 + 复制/收藏/删除）
    # ------------------------------------------------------------------ #
    def _show_detail(self, e: Optional[dict]) -> None:
        """展示/刷新详情：固定头部更新 + 滚动内容区重建

        2026-08-18：按当前查看的根目录决定字段显示模式——根目录为
        config.DETAIL_FULL_FIELDS_DOMAINS（视觉风格分类/视频/图像）时 9 字段全部显示；
        其余根目录隐藏 ③-⑦（config.DETAIL_HIDDEN_KEYS），突出提示词内容。
        """
        self._clear_frame(self.detail_scroll)
        self._detail_boxes = {}
        self._detail_dirty = False
        if e is None:
            self._detail_entry_id = None
            self._detail_hidden = set()  # 2026-08-18：无条目时无隐藏字段
            self.detail_scroll.configure(label_text="详情")  # 2026-08-18：恢复默认标题
            self._name_entry.delete(0, "end")
            self._name_entry.configure(state="normal")
            self.fav_btn.configure(command=lambda: None)
            self.del_btn.configure(command=lambda: None)
            self.copy_all_btn.configure(command=lambda: None)
            self.copy_cn_btn.configure(command=lambda: None)
            self.copy_en_btn.configure(command=lambda: None)
            self.save_btn.configure(command=lambda: None)
            self.reset_btn.configure(command=lambda: None)
            self._apply_lock_state()
            ctk.CTkLabel(self.detail_scroll, text="请选择条目查看详情",
                         text_color="gray").pack(pady=40)
            return
        self._detail_entry_id = e["id"]
        # 2026-08-18：根据当前根目录/策略计算需隐藏的字段（③-⑦）；未选根目录时全部显示
        self._detail_hidden = self._detail_hidden_keys()
        # 2026-08-18：精简模式轻提示（详情区标题后缀）+ "显示全部字段/精简显示"切换按钮
        if self._default_hidden_keys():
            self.detail_scroll.configure(
                label_text="详情 · 已展开全部字段" if self._detail_show_all_override else "详情 · 精简模式")
            self._build_detail_mode_switch()
        else:
            self.detail_scroll.configure(label_text="详情")

        # 固定头部：名称 / 收藏 / 删除 / 复制 / 保存 / 重置（不随滚动消失）
        self._name_entry.configure(state="normal")
        self._name_entry.delete(0, "end")
        self._name_entry.insert(0, e["name"])
        star = "★ 已收藏" if e["is_favorite"] else "☆ 收藏"
        self.fav_btn.configure(text=star,
                               command=lambda: self._toggle_favorite(e["id"]))
        self.del_btn.configure(command=lambda: self._delete_entry(e["id"]))
        self.copy_all_btn.configure(command=lambda: self._copy_entry(e["id"], _COPY_ALL))
        self.copy_cn_btn.configure(command=lambda: self._copy_entry(e["id"], _COPY_CN))
        self.copy_en_btn.configure(command=lambda: self._copy_entry(e["id"], _COPY_EN))
        self.save_btn.configure(command=self._save_detail)
        self.reset_btn.configure(command=self._reset_detail)
        self._apply_lock_state()

        # 9 字段（可编辑文本框 + 悬停完整内容提示；提示词字段可折叠；③-⑦ 按根目录可隐藏）
        for label, key, height in _FIELDS:
            if key in self._detail_hidden:  # 2026-08-18：当前根目录下隐藏 ③-⑦
                continue
            if key in _COLLAPSIBLE_KEYS:  # ⑧/⑨ 提示词：有内容默认 36 行、可展开 72 行，无内容 1 行
                self._build_collapsible_field(e, label, key)
                continue
            lbl = ctk.CTkLabel(self.detail_scroll, text=label,
                               font=("Microsoft YaHei", 12, "bold"), anchor="w")
            lbl.pack(fill="x", padx=8, pady=(6, 0))
            # 2026-08-18（第015条）：⑩图像获取方案 若为链接 → 文本框右侧加"打开"按钮，既能复制网址（左）、又能打开网址（右）
            # 按钮实时读取文本框内容（_open_image_plan），输入链接保存后即可直接打开
            if key == "image_plan":
                row = ctk.CTkFrame(self.detail_scroll, fg_color="transparent")
                row.pack(fill="x", padx=8, pady=(0, 2))
                box = ctk.CTkTextbox(row, height=height)
                box.pack(side="left", fill="x", expand=True)
                url = (e[key] or "").strip()
                if url.startswith(("http://", "https://")):
                    open_btn = ctk.CTkButton(
                        row, text="打开", width=52, height=28,
                        command=lambda b=box: self._open_image_plan(b))
                    open_btn.pack(side="right", padx=(6, 0))
            else:
                box = ctk.CTkTextbox(self.detail_scroll, height=height)
                box.pack(fill="x", padx=8, pady=(0, 2))
            box.insert("1.0", e[key] or "")
            box.bind("<KeyRelease>", self._mark_dirty)
            self._detail_boxes[key] = box
            full = e[key] or "（无内容）"
            _FieldTooltip(lbl, full)
            _FieldTooltip(box, full)
            if key == "image_desc":  # ⑦ 字段下方紧跟图片预览区
                self._build_image_area()

    def _build_collapsible_field(self, e, label: str, key: str) -> None:
        """可折叠字段：标签行 + 展开/收起按钮 + 文本框（height 单位为像素）。

        2026-08-18：有内容默认 _COLLAPSED_H=120px（6 行文字完整可见）；点击"展开"时
        高度自适应为内容实际显示行数（_content_fit_height，有多少行显示多少行）；
        无内容时只显示 _EMPTY_H=24px（1 行空行），且不显示"展开"按钮。
        """
        has_content = bool((e[key] or "").strip())
        base_h = _COLLAPSED_H if has_content else _EMPTY_H

        head = ctk.CTkFrame(self.detail_scroll, fg_color="transparent")
        head.pack(fill="x", padx=8, pady=(6, 0))
        lbl = ctk.CTkLabel(head, text=label,
                           font=("Microsoft YaHei", 12, "bold"), anchor="w")
        lbl.pack(side="left")
        toggle = None
        if has_content:
            toggle = ctk.CTkButton(head, text="展开", width=52, height=22, **_ADD_BTN)
            toggle.pack(side="right")

        box = ctk.CTkTextbox(self.detail_scroll, height=base_h)
        box.pack(fill="x", padx=8, pady=(0, 2))
        box.insert("1.0", e[key] or "")
        box.bind("<KeyRelease>", self._mark_dirty)
        self._detail_boxes[key] = box
        full = e[key] or "（无内容）"
        _FieldTooltip(lbl, full)
        _FieldTooltip(box, full)

        if toggle is not None:
            def _toggle():
                expanded = toggle.cget("text") == "展开"
                # 2026-08-18：展开时高度自适应为内容实际显示行数（有多少行显示多少行，避免空白行）
                box.configure(height=self._content_fit_height(box) if expanded else base_h)
                toggle.configure(text="收起" if expanded else "展开")

            toggle.configure(command=_toggle)

    @staticmethod
    def _content_fit_height(box) -> int:
        """文本框恰好显示全部内容的像素高度（含自动换行；最少 1 行）。

        2026-08-18 新增：展开提示词时按"有多少行就显示多少行"自适应高度，
        避免内容不多时出现大量空白行。用字体测量估算 wrap 后的实际显示行数，
        不依赖控件布局时机（displaylines 在未布局时不可靠）。
        """
        try:
            import tkinter.font as tkfont
            font = tkfont.Font(root=box._textbox, font=box._textbox.cget("font"))
            line_h = font.metrics("linespace") or 20
            text = box._textbox.get("1.0", "end-1c")
            # 文本可用宽度：控件宽扣除内边距/边框/右侧滚动条余量（取偏小值→行数略多，保证不遮挡）
            avail = max(box._textbox.winfo_width() - 14, 80)
            lines = 0
            for para in text.split("\n"):
                w = font.measure(para)
                lines += max(1, -(-w // avail))  # 向上取整：该段落自动换行后的显示行数
            n = max(int(lines), 1)
        except Exception:
            try:  # 兜底：按逻辑行数估算
                n = max(box._textbox.get("1.0", "end-1c").count("\n") + 1, 1)
            except Exception:
                n = 6
            line_h = 20
        return n * line_h + 8  # 8px 余量：上下内边距与边框，确保最后一行完整可见

    def _build_image_area(self) -> None:
        img_row = ctk.CTkFrame(self.detail_scroll, fg_color="transparent")
        img_row.pack(fill="x", padx=8, pady=(2, 6))
        # 无图时占位符为紧凑尺寸（高度与右侧两按钮一致），有图时动态放大
        self._img_view = ctk.CTkLabel(img_row, text="（无关联图片）", text_color="gray",
                                      width=180, height=64, corner_radius=8,
                                      fg_color="#eceff4")
        self._img_view.pack(side="left", padx=(0, 8))
        btn_col = ctk.CTkFrame(img_row, fg_color="transparent")
        btn_col.pack(side="left", fill="y")
        ctk.CTkButton(btn_col, text="选择图片…", width=100,
                      command=self._pick_image).pack(pady=2)
        ctk.CTkButton(btn_col, text="移除图片", width=100, fg_color="#8a94a6",
                      command=self._remove_image).pack(pady=2)
        self._render_image_preview()

    def _mark_dirty(self, _event=None) -> None:
        self._detail_dirty = True

    def _open_image_plan(self, box) -> None:
        """打开"⑩图像获取方案"文本框中的链接（2026-08-18 第015条扩展）。

        实时读取文本框当前内容，提取第一个 http(s) 链接并用默认浏览器打开；
        未找到链接时给出轻提示。这样使用者输入链接后（无论是否保存）点击
        "打开"按钮即可直达对应图片/网址。
        """
        text = box.get("1.0", "end").strip() if box else ""
        m = re.search(r"https?://[^\s\"'<>]+", text)
        if m:
            webbrowser.open(m.group(0))
        else:
            self.toast("未找到链接（请输入 http:// 或 https:// 开头网址）", color="#D9534F")

    def _box_text(self, key) -> str:
        box = self._detail_boxes.get(key)
        return box.get("1.0", "end").strip() if box else ""

    def _default_hidden_keys(self) -> set:
        """按详情字段策略（auto/full/compact）计算应隐藏的字段集合（③-⑦）。

        2026-08-18 重构：策略来自设置（settings_detail_mode）——
          auto（默认）：视觉风格分类/视频/图像 全显示，其余根目录隐藏 ③-⑦；
          full：始终全部显示；compact：始终隐藏 ③-⑦。
        """
        mode = self._detail_mode
        if mode == config.DETAIL_MODE_FULL:
            return set()
        if mode == config.DETAIL_MODE_COMPACT:
            return set(config.DETAIL_HIDDEN_KEYS)
        if self._cur_domain_id is None:
            return set()
        d = self.db.get_domain(self._cur_domain_id)
        name = d["name"] if d else None
        if name in config.DETAIL_FULL_FIELDS_DOMAINS:
            return set()
        return set(config.DETAIL_HIDDEN_KEYS)

    def _detail_hidden_keys(self) -> set:
        """实际生效的隐藏字段：默认隐藏减去"显示全部字段"临时展开。"""
        if self._detail_show_all_override:
            return set()
        return self._default_hidden_keys()

    def _build_detail_mode_switch(self) -> None:
        """精简模式下"显示全部字段 / 精简显示"切换按钮（详情区顶部）。

        2026-08-18 新增：字段被隐藏时，可临时展开全部字段（本次会话内有效）。
        """
        head = ctk.CTkFrame(self.detail_scroll, fg_color="transparent")
        head.pack(fill="x", padx=8, pady=(6, 0))
        if self._detail_show_all_override:
            ctk.CTkLabel(head, text="已展开全部字段", text_color="gray",
                         font=("Microsoft YaHei", 11)).pack(side="left")
            btn_text = "⏸ 精简显示"
        else:
            btn_text = "⏵ 显示全部字段"
        ctk.CTkButton(head, text=btn_text, width=130, height=24, **_ADD_BTN,
                      command=self._toggle_detail_show_all).pack(side="right")

    def _toggle_detail_show_all(self) -> None:
        """切换"显示全部字段 / 精简显示"，并重建详情区。"""
        self._detail_show_all_override = not self._detail_show_all_override
        e = self.db.get_entry(self._detail_entry_id) if self._detail_entry_id else None
        if e is not None:
            self._show_detail(e)

    def _save_detail(self) -> None:
        if self._detail_entry_id is None:
            return
        cur = self.db.get_entry(self._detail_entry_id)
        if cur is None:
            return
        hidden = getattr(self, "_detail_hidden", set())

        def _field(key: str) -> str:
            # 2026-08-18：隐藏未显示的字段（③-⑦）保留数据库原值，避免保存时被清空
            return cur[key] if key in hidden else self._box_text(key)

        e = Entry(id=self._detail_entry_id, category_id=cur["category_id"],
                  name=self._name_entry.get().strip() or cur["name"],
                  intro=self._box_text("intro"),
                  origin=_field("origin"), features=_field("features"),
                  scenes=_field("scenes"), works=_field("works"),
                  image_desc=_field("image_desc"),
                  prompt_cn=self._box_text("prompt_cn"), prompt_en=self._box_text("prompt_en"),
                  image_plan=self._box_text("image_plan"),
                  image_path=cur["image_path"], is_favorite=cur["is_favorite"])
        self.db.update_entry(e)
        self._detail_dirty = False
        self.toast("✅ 已保存")
        self._restore_view()
        # 2026-08-18（第015条）：保存后重建详情区，使"⑩图像获取方案"的"打开"按钮与最新链接对应
        # （使用者输入链接点击保存后，按钮立即指向该链接，点击即可打开）
        e2 = self.db.get_entry(self._detail_entry_id)
        if e2 is not None:
            self._show_detail(e2)

    def _reset_detail(self) -> None:
        e = self.db.get_entry(self._detail_entry_id) if self._detail_entry_id else None
        self._detail_dirty = False
        self._show_detail(e)

    def _confirm_unsaved(self) -> bool:
        """切换前检查未保存修改；返回是否继续切换"""
        if self._detail_entry_id is None or not self._detail_dirty:
            return True
        r = messagebox.askyesnocancel(
            "未保存的修改",
            "当前条目有未保存的修改。\n\n【是】保存修改　【否】放弃修改　【取消】返回")
        if r is None:
            return False
        if r:
            self._save_detail()
        else:
            self._detail_dirty = False
        return True

    # ------------------------------------------------------------------ #
    # 图片关联 / 预览 / 移除
    # ------------------------------------------------------------------ #
    def _render_image_preview(self) -> None:
        if not hasattr(self, "_img_view") or self._detail_entry_id is None:
            return
        e = self.db.get_entry(self._detail_entry_id)
        p = (e or {}).get("image_path") or ""
        full = os.path.join(config.data_dir(), p) if p else ""
        if p and os.path.isfile(full):
            try:
                from PIL import Image
                pil = Image.open(full)
                pil.thumbnail((320, 200))
                img = ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
                self._img_view.configure(image=img, text="",
                                         width=pil.size[0], height=pil.size[1])
            except Exception:
                self._img_view.configure(image=None, text="（图片加载失败）",
                                         width=180, height=64)
        else:
            # 无图：紧凑占位（高度与右侧"选择/移除图片"两按钮一致）
            self._img_view.configure(image=None, text="（无关联图片）",
                                     width=180, height=64)

    def _pick_image(self) -> None:
        if self._detail_entry_id is None:
            return
        path = filedialog.askopenfilename(
            title="选择图片", parent=self,
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.gif *.webp *.bmp"),
                       ("所有文件", "*.*")])
        if not path:
            return
        try:
            ext = os.path.splitext(path)[1].lower() or ".png"
            img_dir = os.path.join(config.data_dir(), config.IMAGES_DIR_NAME)
            os.makedirs(img_dir, exist_ok=True)
            dest = os.path.join(img_dir, f"entry_{self._detail_entry_id}{ext}")
            shutil.copyfile(path, dest)
            rel = os.path.join(config.IMAGES_DIR_NAME, f"entry_{self._detail_entry_id}{ext}")
            # 2026-08-18（P2-1 修复）：替换图片时删除旧图片文件，避免换扩展名后旧文件残留
            old = (self.db.get_entry(self._detail_entry_id) or {}).get("image_path") or ""
            if old and old != rel:
                try:
                    full = os.path.join(config.data_dir(), old)
                    if os.path.isfile(full):
                        os.remove(full)
                except OSError:
                    pass
            self.db.set_entry_image(self._detail_entry_id, rel)
            self._render_image_preview()
            self.toast("✅ 图片已关联")
        except Exception as exc:
            self.toast(f"图片关联失败：{exc}", color="#D9534F")

    def _remove_image(self) -> None:
        if self._detail_entry_id is None:
            return
        e = self.db.get_entry(self._detail_entry_id)
        if e and e.get("image_path"):
            try:
                full = os.path.join(config.data_dir(), e["image_path"])
                if os.path.isfile(full):
                    os.remove(full)
            except OSError:
                pass
            self.db.set_entry_image(self._detail_entry_id, "")
            self._render_image_preview()
            self.toast("图片已移除")

    # ------------------------------------------------------------------ #
    # 条目操作：复制 / 收藏 / 移动 / 删除
    # ------------------------------------------------------------------ #
    def _copy_entry(self, entry_id: int, mode: int) -> None:
        e = self.db.get_entry(entry_id)
        if not e:
            return
        if mode == _COPY_ALL:
            text = f"{e['prompt_cn']}\n\n{e['prompt_en']}".strip()
        elif mode == _COPY_CN:
            text = e["prompt_cn"].strip()
        else:
            text = e["prompt_en"].strip()
        if not text:
            self.toast("内容为空，未复制")
            return
        try:
            pyperclip.copy(text)
            self.toast("✅ 已复制")
        except Exception:
            self.toast("复制失败，请检查剪贴板", color="#D9534F")

    def _toggle_favorite(self, entry_id: int) -> None:
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止修改收藏状态
            return
        self.db.toggle_favorite(entry_id)
        self._restore_view()              # 刷新列表中的星标
        if self._detail_entry_id == entry_id:
            self._show_detail(self.db.get_entry(entry_id))

    def _entry_menu(self, event, entry_id: int) -> None:
        lock_state = "disabled" if self._lock_on else "normal"  # 2026-08-21（第005条）：锁定时仅"复制提示词"可用
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="移动到分类…", state=lock_state,
                      command=lambda: self._move_entry(entry_id))
        m.add_command(label="收藏 / 取消收藏", state=lock_state,
                      command=lambda: self._toggle_favorite(entry_id))
        m.add_command(label="复制提示词（全部）",
                      command=lambda: self._copy_entry(entry_id, _COPY_ALL))
        m.add_separator()
        m.add_command(label="删除", state=lock_state,
                      command=lambda: self._delete_entry(entry_id))
        m.tk_popup(event.x_root, event.y_root)

    def _move_entry(self, entry_id: int) -> None:
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止条目移动
            return
        sel = MoveSelector(self, self.db)
        self.wait_window(sel)
        if sel.result in ("ok", "uncategorized"):
            self.db.move_entry(entry_id, sel.selected_cat_id)
            self._restore_view()
            self.toast("✅ 已移动")

    def _delete_entry(self, entry_id: int) -> None:
        if self._lock_on:
            return
        e = self.db.get_entry(entry_id)
        if not e:
            return
        if not messagebox.askyesno("删除确认", f"⚠️ 确定要删除【{e['name']}】吗？"):
            return
        if not messagebox.askyesno("再次确认", "🚨 删除后不可恢复！请再次点击确定。"):
            return
        self.db.delete_entry(entry_id)
        self._restore_view()
        if self._detail_entry_id == entry_id:
            self._show_detail(None)
        self.toast("已删除")

    # ------------------------------------------------------------------ #
    # 锁定开关
    # ------------------------------------------------------------------ #
    def _toggle_lock(self) -> None:
        self._lock_on = not self._lock_on
        # 2026-08-22（第007条）：解锁时恢复记录的默认配色（customtkinter 6.0.0
        # 不接受 fg_color=None，会抛 ValueError 中断本方法导致按钮显示卡死）
        if self._lock_on:
            self.lock_btn.configure(text="🔓 已锁定", fg_color="#D9534F")
        else:
            self.lock_btn.configure(
                text="🔒 锁定",
                fg_color=self._lock_btn_default_fg,
                hover_color=self._lock_btn_default_hover)
        self._apply_lock_state()
        self.toast("已开启全局锁定，删除功能已禁用" if self._lock_on else "已解除锁定")

    def _set_child_states(self, widget, state: str) -> None:
        """递归设置某容器下所有控件的 state（锁定状态切换用，2026-08-21 第005条新增）"""
        for child in widget.winfo_children():
            try:
                child.configure(state=state)
            except Exception:
                pass
            self._set_child_states(child, state)

    def _apply_lock_state(self) -> None:
        """锁定：只能查询和复制，禁止移动/删除/新增/重命名/导入/编辑保存等（2026-08-21 第005条扩展）"""
        locked = self._lock_on
        state = "disabled" if locked else "normal"
        for attr in ("del_btn", "fav_btn", "save_btn", "reset_btn",
                     "import_btn", "quick_add_btn"):
            w = getattr(self, attr, None)
            if w is not None and w.winfo_exists():
                w.configure(state=state)
        # 详情名称输入框 + 详情滚动区编辑控件（复制按钮不受影响）
        if hasattr(self, "_name_entry") and self._name_entry.winfo_exists():
            self._name_entry.configure(state=state)
        if hasattr(self, "detail_scroll") and self.detail_scroll.winfo_exists():
            self._set_child_states(self.detail_scroll, state)
        # 导入菜单项（导入=新增数据，锁定时禁用）
        if hasattr(self, "import_menu"):
            try:
                last = self.import_menu.index("end")
            except Exception:
                last = None
            if last is not None:
                for i in range(last + 1):
                    try:
                        self.import_menu.entryconfigure(i, state=state)
                    except tk.TclError:
                        pass  # 分隔符等不支持 state 的项跳过（2026-08-29 M5：菜单新增分隔符后修复）
        # 重建新增按钮（p/l0/l1/l2 的"➕新增…"按锁定置灰）
        self._refresh_projects()
        self._refresh_l0()
        self._refresh_l1()
        self._refresh_l2()

    # ------------------------------------------------------------------ #
    # 根目录 / 分类 右键菜单
    # ------------------------------------------------------------------ #
    def _domain_menu(self, event, domain_id: int, name: str) -> None:
        lock_state = "disabled" if self._lock_on else "normal"  # 2026-08-21（第006条）：锁定时"复制到"同样禁用
        m = tk.Menu(self, tearoff=0)
        # 2026-08-21（第004条）：根目录"复制到/移动到"
        m.add_command(label="复制到…", state=lock_state,
                      command=lambda: self._copy_move_domain(domain_id, "copy"))
        m.add_command(label="移动到…", state=lock_state,
                      command=lambda: self._copy_move_domain(domain_id, "move"))
        m.add_command(label="移动到其他项目类别…", state=lock_state,  # 2026-08-29（M2）
                      command=lambda: self._move_domain_to_project(domain_id))
        m.add_separator()
        m.add_command(label="重命名", state=lock_state,
                      command=lambda: self._rename_domain(domain_id))
        m.add_command(label="删除", state=lock_state,
                      command=lambda: self._delete_domain(domain_id))
        m.tk_popup(event.x_root, event.y_root)

    def _category_menu(self, event, cat_id: int, name: str) -> None:
        cat = self.db.get_category(cat_id)
        src_type = "l1" if (cat and cat["parent_id"] is None) else "l2"
        lock_state = "disabled" if self._lock_on else "normal"  # 2026-08-21（第006条）：锁定时"复制到"同样禁用
        m = tk.Menu(self, tearoff=0)
        # 2026-08-21（第004条）：一级/二级分类"复制到/移动到"
        m.add_command(label="复制到…", state=lock_state,
                      command=lambda: self._copy_move_category(src_type, cat_id, "copy"))
        m.add_command(label="移动到…", state=lock_state,
                      command=lambda: self._copy_move_category(src_type, cat_id, "move"))
        m.add_separator()
        m.add_command(label="新增子分类", state=lock_state,
                      command=lambda: self._add_subcategory(cat_id))
        m.add_command(label="重命名", state=lock_state,
                      command=lambda: self._rename_category(cat_id))
        m.add_command(label="删除", state=lock_state,
                      command=lambda: self._delete_category(cat_id))
        m.tk_popup(event.x_root, event.y_root)

    # ------------------------------------------------------------------ #
    # 复制到 / 移动到（2026-08-21 第004条新增）
    # ------------------------------------------------------------------ #
    def _copy_move_domain(self, domain_id: int, action: str) -> None:
        """根目录"复制到/移动到"：目标为另一根目录下（作其一级分类）"""
        if self._lock_on:  # 2026-08-21（第006条修正）：锁定时仅可查询和复制详情内容，"复制到"同样禁止
            return
        d = self.db.get_domain(domain_id)
        if not d:
            return
        dlg = CopyMoveDialog(self, self.db, "domain", domain_id, d["name"], action)
        self.wait_window(dlg)
        if dlg.result != "ok":
            return
        try:
            if dlg.target_kind == "domain_to_project":  # 2026-08-29（M3）：根目录 → 项目类别
                if action == "copy":
                    new_name = self.db.unique_domain_name(d["name"])
                    self.db.copy_domain_to_project(domain_id, dlg.target_id, new_name)
                else:
                    self.db.move_domain_to_project(domain_id, dlg.target_id)
            elif action == "copy":
                self.db.copy_domain_to_domain(domain_id, dlg.target_id)
            else:
                self.db.move_domain_to_domain(domain_id, dlg.target_id)
        except ValueError as e:
            messagebox.showwarning("无法操作", str(e), parent=self)
            return
        self._after_copy_move()
        self.toast("✅ 已复制" if action == "copy" else "✅ 已移动")

    def _copy_move_category(self, src_type: str, cat_id: int, action: str) -> None:
        """一级/二级分类"复制到/移动到"（目标：新建根目录项/某根目录下/某一级分类下）"""
        if self._lock_on:  # 2026-08-21（第006条修正）：锁定时仅可查询和复制详情内容，"复制到"同样禁止
            return
        cat = self.db.get_category(cat_id)
        if not cat:
            return
        dlg = CopyMoveDialog(self, self.db, src_type, cat_id, cat["name"], action,
                             from_domain_id=self._cur_domain_id)
        self.wait_window(dlg)
        if dlg.result != "ok":
            return
        try:
            if dlg.target_kind == "l1_to_domain":
                if action == "copy":
                    self.db.copy_l1_to_domain(cat_id, dlg.target_id, dlg.new_name)
                else:
                    self.db.move_l1_to_domain(cat_id, self._cur_domain_id,
                                              dlg.target_id, dlg.new_name)
            elif dlg.target_kind == "l1_to_l2":
                if action == "copy":
                    self.db.copy_l1_to_l2(cat_id, dlg.target_id)
                else:
                    self.db.move_l1_to_l2(cat_id, dlg.target_id)
            elif dlg.target_kind == "l2_to_domain":
                if action == "copy":
                    self.db.copy_l2_to_domain(cat_id, dlg.target_id, dlg.new_name)
                else:
                    self.db.move_l2_to_domain(cat_id, dlg.target_id, dlg.new_name)
            elif dlg.target_kind == "l2_to_l2":
                if action == "copy":
                    self.db.copy_l2_to_l2(cat_id, dlg.target_id)
                else:
                    self.db.move_l2_to_l2(cat_id, dlg.target_id)
        except ValueError as e:
            messagebox.showwarning("无法操作", str(e), parent=self)
            return
        self._after_copy_move()
        self.toast("✅ 已复制" if action == "copy" else "✅ 已移动")

    def _after_copy_move(self) -> None:
        """复制/移动完成后刷新导航与详情（结构性变化，静默重建并复位，避免未保存确认中断）"""
        self.refresh_domains(silent=True)
        self._show_detail(None)
        self._cur_cat_id = None
        if self._cur_domain_id:
            self._view = ("domain", self._cur_domain_id)

    def _add_domain(self) -> None:
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止新增
            return
        name = simpledialog.askstring("新增根目录", "请输入根目录名称：", parent=self)
        if not (name and name.strip()):
            return
        # 决策 5：新增根目录弹窗选择所属项目类别；未选择则默认归入"未明确分类"
        r = self._choose_project("选择项目类别", f"【{name.strip()}】归属项目类别：")
        if r is None:
            project_id = self.db.ensure_project(config.PROJECT_FALLBACK)
        else:
            project_id = r[1]
        self.db.add_domain(name.strip(), project_id=project_id)
        self.refresh_domains()

    def _rename_domain(self, domain_id: int) -> None:
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止重命名
            return
        cur = self.db.get_domain(domain_id)
        name = simpledialog.askstring("重命名根目录", "请输入新名称：",
                                      initialvalue=cur["name"], parent=self)
        if name and name.strip():
            self.db.rename_domain(domain_id, name.strip())
            self.refresh_domains()

    def _delete_domain(self, domain_id: int) -> None:
        if self._lock_on:
            return
        d = self.db.get_domain(domain_id)
        stat = self.db.count_domain_items(domain_id)
        if not messagebox.askyesno(
                "删除确认",
                f"⚠️ 确定要删除根目录【{d['name']}】吗？\n其下关联 {stat['categories']} 个分类、"
                f"{stat['entries']} 个条目。\n\n删除仅解除关联，分类与条目数据将保留"
                f"（其他关联该分类的根目录仍可正常访问）。"):
            return
        if not messagebox.askyesno("再次确认", "🚨 删除后不可恢复！请再次点击确定。"):
            return
        self.db.delete_domain(domain_id)
        self.refresh_domains()

    def _add_subcategory(self, parent_id: int) -> None:
        """右键"新增子分类"：在当前分类下新建子分类（分类为全局共享树，无需领域）"""
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止新增
            return
        name = simpledialog.askstring("新增子分类", "请输入分类名称：", parent=self)
        if not (name and name.strip()):
            return
        self.db.add_category(name.strip(), parent_id=parent_id)
        self._refresh_l2()

    def _rename_category(self, cat_id: int) -> None:
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止重命名
            return
        cur = self.db.get_category(cat_id)
        name = simpledialog.askstring("重命名分类", "请输入新名称：",
                                      initialvalue=cur["name"], parent=self)
        if name and name.strip():
            self.db.rename_category(cat_id, name.strip())
            if cur["parent_id"] is None:
                self._refresh_l1()
            else:
                self._refresh_l2()

    def _delete_category(self, cat_id: int) -> None:
        if self._lock_on:
            return
        cat = self.db.get_category(cat_id)
        stat = self.db.count_descendants(cat_id)
        if not messagebox.askyesno(
                "删除确认",
                f"⚠️ 确定要删除分类【{cat['name']}】吗？\n这将同时删除其下 "
                f"{stat['categories']} 个子分类，{stat['entries']} 个条目将转入「未分类」。"):
            return
        if not messagebox.askyesno("再次确认", "🚨 删除后不可恢复！请再次点击确定。"):
            return
        self.db.delete_category(cat_id)
        if cat["parent_id"] is None:
            self._refresh_l1()
        else:
            self._refresh_l2()
        self._render_entries([], "条目")

    # ------------------------------------------------------------------ #
    # 数据导入导出（阶段六）
    # ------------------------------------------------------------------ #
    def _current_subtree_cat_id(self) -> Optional[int]:
        """当前分类子树根 id（未选中分类则返回 None）"""
        return self._cur_cat_id

    def _import_json(self) -> None:
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止导入（导入=新增数据）
            return
        path = filedialog.askopenfilename(title="选择 JSON 备份", parent=self,
                                          filetypes=[("JSON", "*.json")])
        if not path:
            return
        if not self._confirm_unsaved():
            return
        try:
            total = json_io.count_json_entries(path)
        except Exception as exc:
            messagebox.showerror("导入失败", f"文件无法读取：{exc}", parent=self)
            return
        dlg = ProgressDialog(self, total=total, message="正在导入 JSON…")
        try:
            result = json_io.import_json(self.db, path, progress_cb=dlg.update_progress)
            self.refresh_domains()
            self.toast(self._import_done_msg(result))
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc), parent=self)
        finally:
            dlg.finish()

    def _import_excel(self) -> None:
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止导入（导入=新增数据）
            return
        path = filedialog.askopenfilename(title="选择 Excel 文件", parent=self,
                                          filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        if not self._confirm_unsaved():
            return
        # 2026-08-29（P2 修复）：大文件限制（≤20MB），避免内存风险
        try:
            if os.path.getsize(path) > 20 * 1024 * 1024:
                messagebox.showwarning(
                    "文件过大",
                    "所选 Excel 文件超过 20MB，请拆分后导入，或改用 JSON 导入。",
                    parent=self)
                return
        except OSError as exc:
            messagebox.showerror("导入失败", f"文件无法读取：{exc}", parent=self)
            return
        try:
            total = excel_io.count_excel_rows(path)
        except Exception as exc:
            messagebox.showerror("导入失败", f"文件无法读取：{exc}", parent=self)
            return
        dlg = ProgressDialog(self, total=total, message="正在导入 Excel…")
        try:
            result = excel_io.import_excel(self.db, path, progress_cb=dlg.update_progress)
            self.refresh_domains()
            self.toast(self._import_done_msg(result))
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc), parent=self)
        finally:
            dlg.finish()

    @staticmethod
    def _import_done_msg(result: dict) -> str:
        """导入完成提示：新增条数 +（跳过重复条数）+（删除同步统计）。

        2026-08-18（P1-1）：新增"跳过重复"统计（详情内容去重）。
        2026-08-29（增量备份增强）：新增删除同步统计。
        """
        msg = f"✅ 导入完成：新增 {result.get('entries', 0)} 条"
        if result.get("skipped"):
            msg += f"，跳过重复 {result['skipped']} 条"
        d = result.get("deleted")
        if d and (d.get("entries") or d.get("categories") or d.get("domains")):
            msg += (f"，同步删除 条目{d.get('entries', 0)}/"
                    f"分类{d.get('categories', 0)}/根目录{d.get('domains', 0)}")
        return msg

    def _import_md(self) -> None:
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止导入（导入=新增数据）
            return
        path = filedialog.askopenfilename(
            title="选择 Markdown 手册", parent=self,
            filetypes=[("Markdown", "*.md *.markdown"), ("所有文件", "*.*")])
        if not path:
            return
        if not self._confirm_unsaved():
            return
        domain = simpledialog.askstring("导入 MD", "请输入目标根目录名称（不存在将自动创建）：",
                                        initialvalue="视觉风格分类", parent=self)  # 2026-08-18（P1-1）：默认目标由"视频"改为"视觉风格分类"
        if not domain or not domain.strip():
            return
        dlg = None  # 2026-08-29（B1 修复）：提前初始化，避免 parse 异常时 finally 触发 NameError
        try:
            manual = md_parser.parse_file(path)
            total = manual.count_entries()
            if total == 0:
                messagebox.showinfo("导入结果",
                                    "未解析到可导入的条目（请确认文件符合手册格式）。",
                                    parent=self)
                return
            dlg = ProgressDialog(self, total=total, message="正在导入 Markdown…")
            result = md_parser.import_manual(self.db, manual, domain.strip(),
                                             progress_cb=dlg.update_progress)
            self.refresh_domains()
            self.toast(f"✅ 导入完成：{result['entries']} 条")
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc), parent=self)
        finally:
            if dlg is not None:  # 2026-08-29（B1 修复）：dlg 可能未创建
                dlg.finish()

    # ------------------------------------------------------------------ #
    # 增量备份：导入合并 / 导出浏览（2026-08-29 M4）
    # ------------------------------------------------------------------ #
    def _open_migrate_wizard(self) -> None:
        """打开老版本数据迁移向导（未归属根目录 → 项目类别）"""
        from .migrate_dialog import MigrateDialog
        dlg = MigrateDialog(self, self.db)
        self.wait_window(dlg)
        if dlg.result == "ok":
            self.refresh_domains()
            self.toast("✅ 迁移完成")
        else:
            self.db.set_meta(config.META_MIGRATE_WIZARD_DISMISSED, "1")
            self.refresh_domains(silent=True)

    def _import_incremental(self) -> None:
        """导入增量备份 JSON（v3）合并到当前库（复用 JSON 导入内容判重）"""
        self._import_json()

    def _export_incremental_browse(self, fmt: str) -> None:
        """把选中的增量 JSON 导出为 Excel/HTML 浏览文件（导入临时库后导出，不改主库）"""
        if self._lock_on:
            return
        src = filedialog.askopenfilename(
            title="选择增量备份 JSON 文件", parent=self,
            filetypes=[("增量备份 JSON", "*.json"), ("JSON", "*.json")])
        if not src:
            return
        if fmt == "excel":
            out = filedialog.asksaveasfilename(
                title="另存为 Excel", parent=self, defaultextension=".xlsx",
                initialfile="incremental_export.xlsx",
                filetypes=[("Excel 工作簿", "*.xlsx")])
        else:
            out = filedialog.asksaveasfilename(
                title="另存为 HTML", parent=self, defaultextension=".html",
                initialfile="incremental_export.html",
                filetypes=[("HTML 页面", "*.html")])
        if not out:
            return
        try:
            n = export_incremental_to(self.db, src, out, fmt)
        except Exception as exc:
            messagebox.showerror("导出失败", f"无法导出：{exc}", parent=self)
            return
        self.toast(f"✅ 已导出 {n} 条供浏览")

    def _export_incremental_file_to(self) -> None:
        """定位/复制当日增量备份文件到指定位置"""
        code = get_computer_code(self.db)
        day = datetime.now().strftime("%Y-%m-%d")
        src = os.path.join(incr_dir(self.db),
                           f"{config.INCR_FILE_PREFIX}_{code}_{day}.json")
        if not os.path.isfile(src):
            messagebox.showinfo("提示", "今日暂无增量备份文件"
                                       "（无当日新增数据时不会生成）。", parent=self)
            return
        out = filedialog.asksaveasfilename(
            title="导出增量备份文件到", parent=self,
            initialfile=os.path.basename(src), defaultextension=".json",
            filetypes=[("JSON", "*.json")])
        if not out:
            return
        try:
            shutil.copy2(src, out)
            self.toast("✅ 已导出增量备份文件")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)

    def _export_json(self, current_only: bool = False) -> None:
        cat = self._current_subtree_cat_id() if current_only else None
        if current_only and cat is None:
            self.toast("请先在左侧选中分类", color="#D9534F")
            return
        path = filedialog.asksaveasfilename(title="导出 JSON", parent=self,
                                            defaultextension=".json",
                                            initialfile="prompts_backup.json",
                                            filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            n = json_io.export_json(self.db, path, category_id=cat)
            self.toast(f"✅ 已导出 {n} 条")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)

    def _export_excel(self, current_only: bool = False) -> None:
        cat = self._current_subtree_cat_id() if current_only else None
        if current_only and cat is None:
            self.toast("请先在左侧选中分类", color="#D9534F")
            return
        path = filedialog.asksaveasfilename(title="导出 Excel", parent=self,
                                            defaultextension=".xlsx",
                                            initialfile="prompts.xlsx",
                                            filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        try:
            n = excel_io.export_excel(self.db, path, category_id=cat)
            self.toast(f"✅ 已导出 {n} 条")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)

    def _export_html(self, current_only: bool = False) -> None:
        cat = self._current_subtree_cat_id() if current_only else None
        if current_only and cat is None:
            self.toast("请先在左侧选中分类", color="#D9534F")
            return
        path = filedialog.asksaveasfilename(title="导出 HTML", parent=self,
                                            defaultextension=".html",
                                            initialfile="index.html",
                                            filetypes=[("HTML", "*.html")])
        if not path:
            return
        try:
            n = html_export.export_html(self.db, path, category_id=cat)
            self.toast(f"✅ 已导出 {n} 条")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)

    # ------------------------------------------------------------------ #
    # 其他：快捷新建、轻提示、热键
    # ------------------------------------------------------------------ #
    def _quick_add(self) -> None:
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止快速新建（新增条目）
            return
        QuickAddWindow(self, self.db)

    def toast(self, msg: str, color: str = "#2E8B57") -> None:
        if self._toast_label is not None:
            self._hide_toast(self._toast_label)
        lbl = ctk.CTkLabel(self, text=msg, fg_color=color, text_color="white",
                           corner_radius=8, font=("Microsoft YaHei", 13))
        lbl.place(relx=0.5, rely=0.93, anchor="center")
        self._toast_label = lbl
        self.after(1600, lambda: self._hide_toast(lbl))

    @staticmethod
    def _hide_toast(lbl) -> None:
        try:
            lbl.destroy()
        except Exception:
            pass

    def _on_escape(self, _event):
        self.withdraw()
        return "break"

    # ------------------------------------------------------------------ #
    # 设置（2026-08-18："⚙ 设置"入口；持久化到数据库 meta 表）
    # ------------------------------------------------------------------ #
    def destroy(self) -> None:
        """销毁前保存设置 + 生成当日增量备份（失败仅控制台提示，不阻塞退出）。"""
        try:
            self._save_settings()
        except Exception:
            pass
        try:  # 2026-08-29（M4）：每次关闭软件时执行每日增量备份
            r = write_incremental(self.db)
            if not r["ok"] and r.get("error"):
                print(f"[增量备份] 失败（不阻塞退出）：{r['error']}")
        except Exception as exc:
            print(f"[增量备份] 失败（不阻塞退出）：{exc}")
        super().destroy()

    def _current_size(self) -> str:
        """当前窗口大小 "WxH"（去掉位置偏移）"""
        try:
            return self.geometry().split("+")[0]
        except Exception:
            return "1360x780"

    def _load_settings(self) -> None:
        """启动时应用持久化设置：记住窗口大小 / 视图模式 / 详情字段策略。"""
        self._remember_size = self.db.get_meta(config.META_REMEMBER_SIZE) != "0"
        vm = self.db.get_meta(config.META_VIEW_MODE)
        if vm in ("card", "list"):
            self._view_mode = vm
        dm = self.db.get_meta(config.META_DETAIL_MODE)
        if dm in (config.DETAIL_MODE_AUTO, config.DETAIL_MODE_FULL, config.DETAIL_MODE_COMPACT):
            self._detail_mode = dm
        if self._remember_size:
            size = self.db.get_meta(config.META_WINDOW_SIZE)
            if size and "x" in size:
                try:
                    self.geometry(size)
                except Exception:
                    pass

    def _save_settings(self) -> None:
        """保存当前设置（关闭/退出时调用）。"""
        if self._remember_size:
            self.db.set_meta(config.META_WINDOW_SIZE, self._current_size())
        self.db.set_meta(config.META_VIEW_MODE, self._view_mode)
        self.db.set_meta(config.META_DETAIL_MODE, self._detail_mode)

    def _open_settings(self) -> None:
        """打开设置对话框。"""
        SettingsDialog(self, self.db)

    def apply_settings(self) -> None:
        """设置对话框确定后应用：视图模式 / 详情策略 / 窗口大小记忆。"""
        self._remember_size = self.db.get_meta(config.META_REMEMBER_SIZE) != "0"
        vm = self.db.get_meta(config.META_VIEW_MODE) or "card"
        self._view_mode = vm if vm in ("card", "list") else "card"
        dm = self.db.get_meta(config.META_DETAIL_MODE) or config.DETAIL_MODE_AUTO
        self._detail_mode = dm if dm in (config.DETAIL_MODE_AUTO, config.DETAIL_MODE_FULL,
                                         config.DETAIL_MODE_COMPACT) else config.DETAIL_MODE_AUTO
        self._restore_view()  # 视图模式重建条目区
        if self._detail_entry_id is not None:
            self._show_detail(self.db.get_entry(self._detail_entry_id))  # 详情策略重建
        self.toast("✅ 设置已保存")

    def show_and_focus_search(self) -> None:
        """全局热键/托盘回调：恢复窗口并聚焦搜索框"""
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.attributes("-topmost", False)
        self.after(60, self.search_entry.focus_set)
