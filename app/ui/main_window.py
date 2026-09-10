# -*- coding: utf-8 -*-
"""
main_window.py - 主窗口：界面布局与交互逻辑
创建日期：2026-08-12（阶段一创建；阶段三~六完善）

布局（五行结构）：
  ┌ 顶部工具栏：🧩PromptSprite | 🔒锁定 | 🗂目录隐藏 | 📂无类条目 | ⭐常用 | ✚新建 | ⇩导入 | ⇧导出 | ⚙设置 | [搜索框🔍] ┐
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
from .. import backup as backup_mod  # 2026-09-08（V1.7.0）：导入前快照 preimport_snapshot
from ..models import Entry
from ..parser import excel_io, html_export, json_io, md_parser
from .change_import_dialog import ChangeImportDialog  # 2026-09-08（V1.7.0）：变更包导入向导
from .column_visibility_dialog import ColumnVisibilityDialog  # 2026-09-10：目录隐藏/目录显示对话框
from .copy_move_dialog import CopyMoveDialog  # 2026-08-21（第004条）：各级目录"复制到/移动到"
from .move_selector import MoveSelector
from .progress_dialog import ProgressDialog
from .quick_add import QuickAddWindow
from .settings_dialog import SettingsDialog  # 2026-08-18："设置"入口
from ..incremental_backup import (get_computer_code, incr_dir,  # 2026-08-29（M4）：增量备份
                                  write_incremental, export_incremental_to)
from .ui_common import ADD_BTN_STYLE as _ADD_BTN  # 2026-09-09（P2-12）：与快速新建共用公共样式/工具
from .ui_common import SEL_BTN_STYLE as _SEL_BTN
from .ui_common import rows_to_px as _rows_to_px
from .ui_common import install_edit_capability as _enable_text_undo  # 2026-09-09：文本框撤销/重做
from .ui_common import set_boxes_readonly as _set_boxes_readonly  # 2026-09-09：浏览只读（可选中复制）


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

# 2026-09-10（用户要求 3）：左侧四个分类列的列宽（逻辑px，顺序＝主界面从左到右）
#   项目类别 / 根目录 / 一级分类 / 二级分类；既用于 _build_body 建列，也用于"目录隐藏"后
#   动态计算窗口最小宽度（隐藏 n 列即按前 n 列列宽之和减小最小宽度）。
_NAV_COL_WIDTHS = (112, 112, 168, 168)
# 2026-09-10（用户要求）：四列全显示时的窗口最小宽度由 1360 提高到 1420——
# 详情区第 2 行（⑧/⑨ 复制全部/中文/英文 + 保存/重置）在 1360 宽时缺约 60px，
# Tk 的 pack 会把缺口全部压到最后排入的"重置"上导致其文字被裁切；加宽后该行完整显示。
_BASE_MIN_WIDTH = 1420
_BASE_MIN_HEIGHT = 660
# 窗口最小宽度下限：顶部工具栏完整显示所需宽度（1012）+ 工具栏左右边距（16）≈ 1028，
# 低于此值「导入/导出/设置/搜索框」会被挤出可视区，故不再继续减小。
_MIN_WIDTH_FLOOR = 1028

# 2026-09-10（用户要求）：搜索框输入防抖——停止输入后再等这么久才真正查询。
# 作用：避免"输入第一个字符就开始全字段检索 + 条目列表重建"造成的持续刷新与卡顿；
# 回车 或 点击右侧"🔍"图标 仍可立即查询。
_SEARCH_DEBOUNCE_MS = 1500

# 2026-09-07（第5条改进）：详情区 ②~⑩ 字段配色。
# 每个字段独立成"浅色圆角卡片块"：标签用各自主题色文字、块底淡彩、内容框白底同色细边，
# 与① 条目名称的深色卡呼应成统一层次，字段与字段之间有颜色区分、观感更舒服。
_FIELD_STYLE = {
    # key: (标签文字色, 字段块底色, 输入框底色, 输入框描边色)
    "intro":      ("#8a5a00", "#fbf6e9", "#ffffff", "#e8dcc0"),
    "origin":     ("#9c4a1f", "#fcf2e8", "#ffffff", "#ead2ba"),
    "features":   ("#5b46a0", "#f4f0fb", "#ffffff", "#dcd3ee"),
    "scenes":     ("#0f7588", "#eaf5f8", "#ffffff", "#cde3ea"),
    "works":      ("#ad4a63", "#fbf0f3", "#ffffff", "#e9cdd5"),
    "image_desc": ("#a03d3d", "#fbeeee", "#ffffff", "#e7cdcd"),
    "prompt_cn":  ("#1f7a50", "#edf6f0", "#ffffff", "#d0e3d8"),
    "prompt_en":  ("#2565b0", "#ecf3fb", "#ffffff", "#ccdcea"),
    "image_plan": ("#7d46a0", "#f6eefa", "#ffffff", "#e2d0ea"),
}
_FIELD_DEFAULT_STYLE = ("#1f4e79", "#f2f5f9", "#ffffff", "#d7e0ea")


def _field_style(key: str):
    """取某字段的配色；(标签色, 块底色, 输入框底色, 描边色)，未配置字段用统一默认"""
    return _FIELD_STYLE.get(key, _FIELD_DEFAULT_STYLE)

_COPY_ALL = 0
_COPY_CN = 1
_COPY_EN = 2

_HOVER_SELECT_MS = 200  # 悬停选中延迟（毫秒）：导航与条目区采用"鼠标悬浮即选择"
# 2026-09-07（第2条改进）：300ms→200ms，逐级悬浮选择的响应更跟手；
# 配合"列选中改用原地高亮、不再整列重建"，切换列时明显更流畅。

# 2026-08-18：提示词字段可折叠。注意：CTkTextbox 的 height 单位是【像素】而非行数！
# 实测（默认字体）行高约 20px：
#   - 有内容默认 _COLLAPSED_H=120px（6 行文字完整可见，不被遮盖）
#   - 点击"展开"→ 高度自适应为内容实际显示行数（有多少行就显示多少行，避免空白行）
#   - 无内容时只显示 1 行空行（_EMPTY_H=24px），且不显示"展开"按钮
_COLLAPSIBLE_KEYS = {"prompt_cn", "prompt_en"}
_EMPTY_H = 24          # 无内容：1 行空行（≈20px 行高 + 少量余量）
_COLLAPSED_H = 120     # 有内容默认：6 行完整可见（实测 120px 时约 6.5 行可见）

# 2026-09-06：主界面就地"新增条目"——③-⑦ 补充信息（溯源/核心特征/应用场景/代表作/代表高清配图）
# 在新增表单中默认折叠为一组，需要时点"展开"逐条填写（不依赖根目录显隐策略）。
_ADD_FOLD_KEYS = set(config.DETAIL_HIDDEN_KEYS)

# 2026-09-09：详情区 ②~⑦（介绍/溯源/核心特征/应用场景/代表作/代表高清配图）默认折叠组。
# 浏览已存条目与"＋新增条目"两处统一：默认仅显示 1 行标题，点"展开"才显示全部字段，
# 折叠后不再各字段各自占行（此前折叠态 ③-⑦ 仍占较大竖向空间）。
_INFO_GROUP_KEYS = ("intro", "origin", "features", "scenes", "works", "image_desc")


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
        # 2026-09-10（用户要求 3）：最小宽度改为随"目录隐藏"动态计算（见 apply_nav_visibility）
        self.minsize(_BASE_MIN_WIDTH, _BASE_MIN_HEIGHT)

        # 交互状态
        self._lock_on = False
        self._cur_domain_id = None
        self._cur_cat_id = None
        self._view = None          # (kind, ref)：kind ∈ domain/cat/uncat/fav/search
        self._view_mode = "card"   # 卡片/列表
        self._detail_mode = config.DETAIL_MODE_AUTO  # 2026-08-18：详情字段策略（自动/全部/精简）
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
        # 2026-09-10（用户要求 2-（2））：目录隐藏/目录显示——从"项目类别"起连续隐藏的列数（0~4）
        self._nav_hidden = 0  # 0=全部显示（按钮显示"目录隐藏"）；>0=按钮显示"目录显示"
        self._nav_cols = []   # 四个分类列控件（项目类别/根目录/一级分类/二级分类），_build_body 中填充
        self._toast_label = None
        self._name_entry = None  # 2026-09-07：名称输入框移入详情区后初始化占位
        # 2026-09-06：主界面就地"新增条目"状态
        self._adding_new = False     # 详情区是否处于"新增条目"空白态
        self._add_target = None      # 新增目标分类 id（None = 未分类）；仅新增态有效
        self._add_group_open = False # 新增态 ③-⑦ 折叠组是否展开
        self._add_group_pairs = []   # 折叠组内的 (标签, 文本框) 对（折叠/展开显隐用）
        self._add_entry_btn = None   # 条目区"➕ 新增条目"按钮引用

        # 2026-09-09：详情区 ②~⑦ 折叠组（浏览视图）状态
        self._detail_group_open = False   # 是否已展开（浏览/新增切换条目后复位）
        self._detail_group_for = None     # 该展开状态所属条目 id（切条目时复位折叠）
        self._detail_group_toggle = None  # 展开/收起按钮引用
        self._detail_group_widgets = []   # 组内待显隐的字段块（含 ⑦ 后图片区）
        self._browse_mode = False         # 浏览/编辑切换：True=只读浏览（可选中复制）
        # 2026-09-09：条目列悬停"全部条目名"浮层状态
        self._entry_ov_names = []
        self._entry_ov_popup = None
        self._entry_ov_after = None
        self._entry_ov_y = None
        self._entry_ov_listbox = None   # 2026-09-10（用户要求 4）：浮层内的列表控件（滚动同步用）
        self._search_after = None       # 2026-09-10（用户要求）：搜索输入防抖定时器 id

        self._load_settings()   # 2026-08-18：应用持久化设置（窗口大小/视图模式/详情策略）
        self._build_toolbar()
        self._build_body()
        # 2026-09-10（用户要求 4-一）：在条目区滚动滚轮时，浮层"条目名称一览"按比例同步滚动。
        # 注册在 _build_body 之后，保证晚于 CTkScrollableFrame 自身的 bind_all 处理（先滚动条目列、
        # 再按新的滚动位置同步浮层）。add="+" 只追加不覆盖既有绑定。
        self.bind_all("<MouseWheel>", self._entry_ov_wheel, add="+")
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
        # 2026-09-10（用户要求 1-（2））：搜索框移到最右侧（"⚙ 设置"右侧、靠边）；
        # 该列最小宽度 = 搜索输入框 10 汉字(140px) + 右侧放大镜按钮(27px) + 间距(4px) = 171px，
        # 窗口加宽时随 weight=1 自动变宽，缩小时不小于最小宽度（输入框不小于 10 汉字）。
        bar.grid_columnconfigure(9, weight=1, minsize=171)

        ctk.CTkLabel(bar, text="🧩 PromptSprite", font=("Microsoft YaHei", 18, "bold")
                     ).grid(row=0, column=0, padx=(14, 20), pady=8)
        # 2026-09-10（用户要求 附）：两个汉字的命令按钮（锁定/常用/新建/导入/导出/设置）
        # 宽度再各减 ≈2 个英文字符（≈14px）；已到"文字+内边距"下限的（如设置）只能减到该下限。
        self.lock_btn = ctk.CTkButton(bar, text="🔒 锁定", width=68,
                                      command=self._toggle_lock)
        self.lock_btn.grid(row=0, column=1, padx=4)
        # 2026-08-22（第007条）：记录锁定按钮默认配色——customtkinter 6.0.0 中
        # configure(fg_color=None) 会抛 ValueError，解锁时须恢复为记录的默认色
        self._lock_btn_default_fg = self.lock_btn.cget("fg_color")
        self._lock_btn_default_hover = self.lock_btn.cget("hover_color")
        # 2026-09-10（用户要求 1-（1）、2-（2））：原"🗂 项目列"按钮改名"目录隐藏/目录显示"，
        # 位置移到"🔒 锁定"右侧、"📂 无类条目"左侧；功能改为显示/隐藏各分类列（见 _on_dir_toggle）。
        self.btn_project_toggle = ctk.CTkButton(
            bar, text="🗂 目录隐藏", width=86, command=self._on_dir_toggle)
        self.btn_project_toggle.grid(row=0, column=2, padx=4)
        # 2026-09-10（用户要求 附）：按钮名称 "未分类条目" → "无类条目"，宽度按新文案贴合（96→84）
        ctk.CTkButton(bar, text="📂 无类条目", width=84, command=self._show_uncategorized
                      ).grid(row=0, column=3, padx=4)
        ctk.CTkButton(bar, text="⭐ 常用", width=68, command=self._show_favorites
                      ).grid(row=0, column=4, padx=4)

        # 导入/导出下拉菜单
        self.import_menu = tk.Menu(bar, tearoff=0)
        self.import_menu.add_command(label="数据迁移向导…", command=self._open_migrate_wizard)  # 2026-08-29（M5）
        self.import_menu.add_separator()
        self.import_menu.add_command(label="导入 JSON 备份…", command=self._import_json)
        self.import_menu.add_command(label="导入变更包…（新增/删除合并）",  # 2026-09-08（V1.7.0）：原"导入增量备份"
                                     command=self._import_change_pack)
        self.import_menu.add_command(label="导入 Excel…", command=self._import_excel)
        self.import_menu.add_command(label="导入 Markdown 手册…", command=self._import_md)
        self.import_menu.add_separator()  # 2026-09-08（V1.7.0）：数据比对（只读诊断）
        self.import_menu.add_command(label="数据比对…（与备份 *.db）",
                                     command=self._compare_with_backup)
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
        self.export_menu.add_separator()  # 2026-08-29（M4）变更包数据导出
        self.export_menu.add_command(label="变更包数据导出 Excel…",
                                     command=lambda: self._export_incremental_browse("excel"))
        self.export_menu.add_command(label="变更包数据导出 HTML…",
                                     command=lambda: self._export_incremental_browse("html"))
        self.export_menu.add_command(label="导出当日变更包文件到…",
                                     command=self._export_incremental_file_to)

        # 2026-09-10（用户要求 1-（3））：快速新建移到"⇩ 导入"左侧
        # 2026-09-10（用户要求 3 追加）：工具栏各命令按钮宽度统一缩减 ≈2 个英文字符（≈14 逻辑px），
        # 文字仍完整显示（CTkButton 会自动撑到"文字+内边距"的最小宽度，故不会出现文字裁切）。
        # 2026-09-10（用户要求 附）：名称 "快速新建" → "新建"，宽度再减 ≈14px（96→82）。
        self.quick_add_btn = ctk.CTkButton(bar, text="✚ 新建", width=82,
                                           command=self._quick_add)
        self.quick_add_btn.grid(row=0, column=5, padx=4)

        self.import_btn = ctk.CTkButton(bar, text="⇩ 导入", width=60)
        self.import_btn.grid(row=0, column=6, padx=4)
        self.import_btn.bind("<Button-1>",
                             lambda e: self.import_menu.tk_popup(e.x_root, e.y_root))
        self.export_btn = ctk.CTkButton(bar, text="⇧ 导出", width=60)
        self.export_btn.grid(row=0, column=7, padx=4)
        self.export_btn.bind("<Button-1>",
                             lambda e: self.export_menu.tk_popup(e.x_root, e.y_root))

        # ⚙ 设置已到"文字+内边距"下限，只能由 66 再缩到 62（实宽 79→74）
        ctk.CTkButton(bar, text="⚙ 设置", width=62, command=self._open_settings  # 2026-08-18：设置入口
                      ).grid(row=0, column=8, padx=4)
        # 2026-09-10（用户要求 1-（2））：搜索框移到"⚙ 设置"右侧（工具栏最右、靠边），
        # 搜索输入框最小宽度保持 10 汉字 = 140px（20 个英文字符宽），不随按钮缩减而变。
        # 2026-09-10（用户要求 2）：搜索框右侧新增"🔍"小图标按钮，点击即可执行搜索。
        self.search_box = ctk.CTkFrame(bar, fg_color="transparent")
        self.search_box.grid(row=0, column=9, padx=(4, 10), sticky="ew")
        self.search_entry = ctk.CTkEntry(self.search_box, placeholder_text="搜索提示词（匹配全部字段）",
                                         width=140)
        self.search_entry.pack(side="left", fill="x", expand=True)
        # 2026-09-10（用户要求）：输入走防抖（停顿 _SEARCH_DEBOUNCE_MS 才查询）；回车立即查询。
        self.search_entry.bind("<KeyRelease>", self._on_search_typing)
        self.search_entry.bind("<Return>", self._on_search_key)
        self.search_btn = ctk.CTkButton(self.search_box, text="🔍", width=27, height=28,
                                        command=self._on_search_click)
        self.search_btn.pack(side="left", padx=(4, 0))

    def _build_body(self) -> None:
        body = ctk.CTkFrame(self)
        body.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 4))
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(4, weight=0)   # 条目区：固定宽度（≈16汉字，2026-09-09 加宽）
        body.grid_columnconfigure(5, weight=1)   # 详情区：占据剩余空间

        # 2026-08-29 用户要求列宽：1汉字≈14px；项目类别/根目录 ≤8汉字(112px)，一级/二级 ≤12汉字(168px)
        # 2026-09-09：条目区由 168px(≈12汉字) 加宽至 224px(≈16汉字)，便于同屏多看几个条目名
        # 2026-09-10（用户要求 3）：列宽统一取自模块常量 _NAV_COL_WIDTHS（与窗口最小宽度计算同源）
        self.project_frame = ctk.CTkScrollableFrame(body, width=_NAV_COL_WIDTHS[0], label_text="项目类别")
        self.l0_frame = ctk.CTkScrollableFrame(body, width=_NAV_COL_WIDTHS[1], label_text="根目录")
        self.l1_frame = ctk.CTkScrollableFrame(body, width=_NAV_COL_WIDTHS[2], label_text="一级分类")
        self.l2_frame = ctk.CTkScrollableFrame(body, width=_NAV_COL_WIDTHS[3], label_text="二级分类")
        self.entry_frame = ctk.CTkScrollableFrame(body, width=224, label_text="条目")

        self.project_frame.grid(row=0, column=0, sticky="nsew")
        self.l0_frame.grid(row=0, column=1, sticky="nsew")
        self.l1_frame.grid(row=0, column=2, sticky="nsew")
        self.l2_frame.grid(row=0, column=3, sticky="nsew")
        self.entry_frame.grid(row=0, column=4, sticky="nsew")
        # 2026-09-10（用户要求 2-（2））：四个分类列控件按"从左到右"顺序登记，
        # 供"目录隐藏/目录显示"按连续前缀隐藏、连续后缀显示（见 apply_nav_visibility）。
        self._nav_cols = [self.project_frame, self.l0_frame, self.l1_frame, self.l2_frame]
        # 2026-09-09：悬停条目区浮出"全部条目名"（进入/离开各处理一次，避免重复绑定累积）
        self.entry_frame.bind("<Enter>", self._entry_ov_enter, add="+")
        self.entry_frame.bind("<Leave>", self._entry_ov_leave, add="+")

        # 详情区：右侧以"浅灰蓝底 + 白色内容卡片"与左侧导航区分（2026-09-07 美化）
        self.detail_root = ctk.CTkFrame(body, fg_color="#e9eef5")
        self.detail_root.grid(row=0, column=5, sticky="nsew")
        self.detail_root.grid_rowconfigure(1, weight=1)
        self.detail_root.grid_columnconfigure(0, weight=1)

        self.detail_head = ctk.CTkFrame(self.detail_root, fg_color="#e9eef5")
        self.detail_head.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 0))

        row1 = ctk.CTkFrame(self.detail_head, fg_color="transparent")
        row1.pack(fill="x", padx=6, pady=(4, 0))
        # 名称输入框已移入详情区（① 条目名称），row1 只保留命令按钮；
        # 2026-09-10（用户要求）："编辑/浏览"与"☆ 收藏"互换位置 →
        #   显示顺序（左→右）＝ ☆ 收藏 → 移动到 → 关联到 → 复制到 …… 编辑/浏览（最右）。
        # 2026-09-10（用户第2条）："☆ 收藏"文案短、宽度余量大 → 92→74。
        # 2026-09-10（用户要求）：三个按钮文字标签**去掉末尾省略号**（…）。
        self.fav_btn = ctk.CTkButton(row1, text="☆ 收藏", width=86)
        # 2026-09-10（用户要求 附）："移动到/关联到/复制到"宽度缩减到刚好容纳文字标签。
        self.copyto_btn = ctk.CTkButton(row1, text="⧉ 复制到", width=71,
                                        command=lambda: None)
        self.link_btn = ctk.CTkButton(row1, text="↔ 关联到", width=70,
                                      command=lambda: None)
        self.move_btn = ctk.CTkButton(row1, text="➜ 移动到", width=82,
                                      command=lambda: None)
        self.fav_btn.pack(side="left", padx=(4, 2))   # 收藏换到最左端

        # 2026-09-09：浏览/编辑切换——浏览时详情文本只读（可选中复制），避免误改内容
        # 2026-09-10（用户第2条）：CTkSegmentedButton 的 width 参数不生效，整体宽度由各分段按钮
        # "文案+内边距"自适应，此处显式设置每个分段按钮的宽度。
        # 2026-09-10（用户要求 1）：分段按钮宽度改为"刚好容纳最宽标签（✏️ 编辑）"的最小值——
        # 实测 "✏️ 编辑" 文字需 60px、"👁 浏览" 需 54px，宽度参数 62 时每段实宽 74px（含内边距），
        # 两段均不裁切；不能再小于该值，否则文字会被裁切。
        self.edit_mode_toggle = ctk.CTkSegmentedButton(
            row1, values=["✏️ 编辑", "👁 浏览"],
            command=self._on_edit_mode_change, width=200)
        self.edit_mode_toggle.set("✏️ 编辑")
        for _seg_btn in self.edit_mode_toggle._buttons_dict.values():
            _seg_btn.configure(width=62)
        # 2026-09-10（用户要求）：与"☆ 收藏"互换位置 → 编辑/浏览改靠最右端
        self.edit_mode_toggle.pack(side="right", padx=(8, 4))
        # 2026-09-10（用户要求 2）：移动到/关联到/复制到 紧挨"☆ 收藏"依次向右排列
        self.move_btn.pack(side="left", padx=2)
        self.link_btn.pack(side="left", padx=2)
        self.copyto_btn.pack(side="left", padx=2)

        row2 = ctk.CTkFrame(self.detail_head, fg_color="transparent")
        row2.pack(fill="x", padx=6, pady=(2, 6))
        ctk.CTkLabel(row2, text="⑧/⑨ 提示词：",
                     font=("Microsoft YaHei", 13, "bold")).pack(side="left")
        # 2026-09-10（用户要求）："复制中文/复制英文/保存"各减 1 个英文字符（≈7px），
        # 为右侧"重置"腾出空间；保存宽度 78→71（上一条要求已 92→78）；
        # "复制全部"再减 1 个英文字符（86→79），进一步为"重置"腾空间。
        self.copy_all_btn = ctk.CTkButton(row2, text="📋 复制全部", width=79, fg_color="#2E8B57")
        self.copy_all_btn.pack(side="left", padx=4)
        self.copy_cn_btn = ctk.CTkButton(row2, text="复制中文", width=71)
        self.copy_cn_btn.pack(side="left", padx=2)
        self.copy_en_btn = ctk.CTkButton(row2, text="复制英文", width=71)
        self.copy_en_btn.pack(side="left", padx=2)
        # 2026-09-10（用户要求 3）：保存宽度 −2 个英文字符（≈14px）：92→78；
        # 重置宽度 +2 个英文字符（≈14px）：72→86。
        # 2026-09-10（用户要求）："重置"过宽 → 再减 1 个汉字字符（≈14px）：86→72；
        # "保存"过窄 → 加 1 个英文字符（≈7px）：71→78。
        self.save_btn = ctk.CTkButton(row2, text="💾 保存", width=78, fg_color="#2E8B57")
        self.save_btn.pack(side="right", padx=2)
        self.reset_btn = ctk.CTkButton(row2, text="重置", width=72)
        self.reset_btn.pack(side="right", padx=2)

        # 2026-09-10（用户第1条）：详情内容"白色圆角卡片"独立成 detail_card，
        # 状态行移入白卡内部顶部（位于"复制…/保存"行之下、"① 条目名称"之上），
        # 仍固定在白卡内、不随滚动消失；下方为可滚动正文。圆角与边框由白卡承担，
        # 滚动区自身不再画边框，避免出现"双边框"。
        self.detail_card = ctk.CTkFrame(self.detail_root, fg_color="#ffffff",
                                        corner_radius=12, border_width=1,
                                        border_color="#c9d3df")
        self.detail_card.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        self.detail_card.grid_rowconfigure(1, weight=1)
        self.detail_card.grid_columnconfigure(0, weight=1)

        # 2026-09-09：详情区固定状态行（不随滚动消失）——左侧"详情 · 精简模式 /
        # 已展开全部字段"状态标题，右侧"⏵ 显示全部字段 / ⏸ 精简显示"开关，只作用于 ②~⑦，
        # 与 ②~⑦ 标题条内联"展开/收起"联动。任何根目录/任何层级查看时都恒常出现。
        state_row = ctk.CTkFrame(self.detail_card, fg_color="transparent")
        state_row.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        self.detail_state_lbl = ctk.CTkLabel(
            state_row, text="详情 · 精简模式", font=("Microsoft YaHei", 13, "bold"),
            text_color="#25639c", anchor="w")
        self.detail_state_lbl.pack(side="left", fill="x", expand=True, padx=(4, 6))
        self.show_all_btn = ctk.CTkButton(state_row, text="⏵ 显示全部字段",
                                          width=140, height=28, **_ADD_BTN,
                                          command=self._toggle_detail_show_all)
        self.show_all_btn.pack(side="right", padx=(0, 4))
        ctk.CTkLabel(state_row, text="（②~⑦ 默认折叠，展开/收起用本开关或标题条“展开”）",
                     font=("Microsoft YaHei", 9), text_color="#9aa4b1"
                     ).pack(side="right", padx=(0, 8))

        # 2026-09-07：详情内容区做成"白色圆角卡片"，与浅灰蓝底区隔、更聚焦
        # 2026-09-10：改为白卡内部的正文滚动区（白底、无边框、无圆角，随白卡一起呈现）
        self.detail_scroll = ctk.CTkScrollableFrame(self.detail_card, label_text="详情",
                                                    fg_color="#ffffff", corner_radius=0)
        self.detail_scroll.grid(row=1, column=0, sticky="nsew", padx=(2, 0), pady=(0, 2))

        # 2026-09-07（第3条改进）："🗑 删除"移到详情区最下方"底部常驻栏"，
        # 不再占用顶部操作行；无当前条目/锁定态/新增态时自动禁用（见 _apply_lock_state）。
        # 2026-09-10（用户要求 1~3）：三个历史/删除类按钮整体右侧停靠，顺序（左→右）
        # 新增历史 → 删除历史/回收站 → 删除当前条目（最右靠边）；宽度按"刚好容纳文字标签"缩减。
        self.detail_foot = ctk.CTkFrame(self.detail_root, fg_color="#e9eef5")
        self.detail_foot.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 4))
        self.del_btn = ctk.CTkButton(self.detail_foot, text="🗑 删除当前条目",
                                     width=110, height=32, fg_color="#D9534F")
        # 2026-09-10（用户要求 二）："删除当前条目"左侧新增小图标快捷按钮（无文字标签），
        # 一键"全部隐藏/全部显示"各分类区域（与工具栏"目录隐藏/目录显示"按钮等价，见 _toggle_all_dirs）。
        self.dir_toggle_btn = ctk.CTkButton(
            self.detail_foot, text="🗂", width=32, height=32,
            fg_color="#6b7280", hover_color="#575e68", font=("Microsoft YaHei", 15),
            command=self._toggle_all_dirs)
        self.dir_toggle_btn.pack(side="left", padx=(8, 0), pady=4)
        self.recycle_btn = ctk.CTkButton(
            self.detail_foot, text="♻ 删除历史 / 回收站", width=139, height=32,
            fg_color="#6b7280", hover_color="#575e68",
            command=self._open_recycle)
        self.recent_btn = ctk.CTkButton(
            self.detail_foot, text="🕒 新增历史", width=83, height=32,
            fg_color="#1f6f8f", hover_color="#185a73",
            command=self._open_recent_adds)
        # pack(side="right") 先打包者最靠右：删除当前条目 → 删除历史/回收站 → 新增历史
        self.del_btn.pack(side="right", padx=(4, 8), pady=4)
        self.recycle_btn.pack(side="right", padx=4, pady=4)
        self.recent_btn.pack(side="right", padx=4, pady=4)

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

    @staticmethod
    def _scroll_top(frame) -> None:
        """把 CTkScrollableFrame 的垂直滚动复位到顶部（2026-09-07 第4条改进）"""
        try:
            canvas = getattr(frame, "_parent_canvas", None)
            if canvas is not None and canvas.winfo_exists():
                canvas.yview_moveto(0)
        except Exception:
            pass

    @staticmethod
    def _nav_hint(frame, text: str) -> None:
        """在某导航/条目列内放一条居中提示（用于尚无可用内容的空/初始视图）"""
        ctk.CTkLabel(frame, text=text, text_color="#9aa4b1",
                     font=("Microsoft YaHei", 11), justify="center",
                     anchor="center", wraplength=150,
                     ).pack(fill="x", padx=10, pady=(14, 2))

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
        """（重）渲染导航列并复位。

        silent=True（2026-08-18，P1-2 修复）：跳过"未保存修改"检查、保留详情区当前状态，
        供快捷新建保存后调用，避免弹出未保存确认框打断连续录入。

        2026-09-07（第1条改进）：打开软件后默认【不自动选中任何层级】——仅顶级
        "项目类别"列展示选项，其余列给出提示，由用户逐级选择后再逐列展开，
        不再出现"尚未选择就整列灌满全部分类"的旧行为。
        """
        if not silent and not self._confirm_unsaved():
            return
        # 未选择任何层级时（首启/结构刷新），各列刷新函数会自动：只给提示 + “新增”按钮灰显；
        # 用户逐级选择后按钮随可用条件自动点亮、对应列填充内容。
        self._nav_initialized = True
        self._refresh_projects()
        self._refresh_l0()
        self._refresh_l1()
        self._refresh_l2()
        self._render_entries([], "条目")
        if not silent:
            self._show_detail(None)

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
            try:
                btn.configure(fg_color=_SEL_BTN["fg_color"],
                              hover_color=_SEL_BTN["hover_color"],
                              text_color=_SEL_BTN["text_color"])
            except Exception:
                pass  # 2026-09-07：按钮可能已被重建销毁，忽略（避免残留引用崩溃）
        else:
            if orig is None:
                # 2026-08-18（P1-4 修复）：样式字典缺失该按钮时保持当前样式，避免解包 None 崩溃
                return
            fg, hover, text = orig
            try:
                btn.configure(fg_color=fg, hover_color=hover, text_color=text)
            except Exception:
                pass

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
        if not self.db.list_projects() and not self.db.list_unassigned_domains():
            self._nav_hint(self.project_frame, "（暂无项目类别，请点上方新增）")
        self._scroll_top(self.project_frame)

    def _select_project(self, project_id: Optional[int]) -> None:
        """选择项目类别（None=未分配视图）：列出其根目录列，再逐级展开。

        2026-09-07（第1/2条改进）：不整列重建项目类别列——内容未变时仅原地更新高亮，
        悬浮切换更流畅；未选择根目录前，一级分类列只给提示、不再灌入全库分类。
        """
        if (self._view == ("project", project_id)
                and self._cur_project_id == project_id
                and self._cur_domain_id is None):
            return
        self._cur_project_id = project_id
        self._cur_domain_id = None
        self._cur_cat_id = None
        self._view = ("project", project_id)
        self._apply_nav_highlight()   # 2026-09-07：原地高亮，避免整列重建拖慢悬浮
        self._refresh_l0()
        self._refresh_l1()
        self._refresh_l2()            # 未选一级分类 → 显示"请先选一级分类"提示
        self._render_entries([], "条目")

    def _on_dir_toggle(self) -> None:
        """「目录隐藏 / 目录显示」按钮：打开对话框选择隐藏或显示各分类列（2026-09-10）。

        - 当前无隐藏列（_nav_hidden == 0，按钮显示"目录隐藏"）→ 打开"隐藏"对话框；
        - 当前有隐藏列（_nav_hidden > 0，按钮显示"目录显示"）→ 打开"显示"对话框。
        """
        mode = "hide" if self._nav_hidden == 0 else "show"
        ColumnVisibilityDialog(self, self._nav_hidden, mode, self.apply_nav_visibility)

    def _nav_min_width(self) -> int:
        """按"连续隐藏的分类列宽度"计算当前窗口最小宽度（2026-09-10，用户要求 3）。

        隐藏 n 列 → 最小宽度 = 1360 − 前 n 列列宽之和；不低于 _MIN_WIDTH_FLOOR（1028，
        低于该值顶部工具栏的"导入/导出/设置/搜索框"会被挤出可视区）。
        """
        hidden_w = sum(_NAV_COL_WIDTHS[:self._nav_hidden])
        return max(_BASE_MIN_WIDTH - hidden_w, _MIN_WIDTH_FLOOR)

    def apply_nav_visibility(self, hidden_count: int) -> None:
        """按"从项目类别起连续隐藏的列数"显示/隐藏左侧四个分类列并更新按钮文案（2026-09-10）。

        hidden_count：0~4。0 = 四列全显示（按钮"目录隐藏"）；>0 = 按钮显示"目录显示"。
        """
        hidden_count = max(0, min(len(self._nav_cols), int(hidden_count)))
        self._nav_hidden = hidden_count
        for idx, frame in enumerate(self._nav_cols):
            if idx < hidden_count:
                frame.grid_remove()                                  # 隐藏：列宽自动收缩为 0
            else:
                frame.grid(row=0, column=idx, sticky="nsew")         # 显示：恢复原列位
        self.btn_project_toggle.configure(
            text="🗂 目录显示" if hidden_count > 0 else "🗂 目录隐藏")
        # 2026-09-10（用户要求 3）：按被隐藏的列宽同步降低窗口最小宽度，
        # 使用户可把窗口（连同右侧详情区）缩得更窄；恢复显示时自动还原。
        self.minsize(self._nav_min_width(), _BASE_MIN_HEIGHT)

    def _toggle_all_dirs(self) -> None:
        """详情区底部"🗂"小图标快捷按钮：一键全部隐藏 / 全部显示各分类列（2026-09-10，用户要求 二）。

        当前有隐藏列 → 全部显示；四列全显示 → 全部隐藏。与工具栏按钮共用 apply_nav_visibility。
        """
        if self._nav_hidden > 0:
            self.apply_nav_visibility(0)                       # 有隐藏（含部分隐藏）→ 全部显示
        else:
            self.apply_nav_visibility(len(self._nav_cols))     # 四列全显示 → 全部隐藏

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
            self._refresh_projects()  # 2026-09-07：先重建列使新按钮出现（_select_project 现仅原地高亮）
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
        """渲染根目录列（当前项目类别下；"未分配"视图显示无归属根目录）。

        2026-09-07（第1条改进）：尚未选择任何项目类别时只给提示、不预填内容。
        """
        self._clear_frame(self.l0_frame)
        self._clear_nav_btns("l0")
        ctk.CTkButton(self.l0_frame, text="➕ 新增根目录", height=30, **_ADD_BTN,
                      state=("disabled" if (self._lock_on
                                            or (self._view is None and self._cur_project_id is None))
                             else "normal"),
                      # 2026-08-21（第005条）：锁定时禁用新增；2026-09-07：未选任何项目时灰显但保留可见
                      command=self._add_domain).pack(fill="x", padx=6, pady=3)
        if self._view is None and self._cur_project_id is None:
            domains = []                      # 初始态：未选项目类别 → 列留提示
            empty_hint = "先选择上方「项目类别」，\n此处将列出其根目录"
        else:
            domains = (self.db.list_unassigned_domains() if self._cur_project_id is None
                       else self.db.list_domains(project_id=self._cur_project_id))
            empty_hint = "（该视图暂无根目录）"
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
        if not domains:
            self._nav_hint(self.l0_frame, empty_hint)
        self._apply_nav_highlight()
        self._scroll_top(self.l0_frame)

    def _select_domain(self, domain_id: int) -> None:
        # 已选中同一根目录且未深入子级时直接返回
        if self._cur_domain_id == domain_id and self._cur_cat_id is None:
            return
        domain_changed = self._cur_domain_id != domain_id
        self._cur_domain_id = domain_id
        self._cur_cat_id = None
        self._view = ("domain", domain_id)
        self._apply_nav_highlight()  # 2026-09-07：根目录列原地高亮（内容未变，不再整列重建）
        if domain_changed:
            self._refresh_l1()   # 一级内容随领域变化
        self._refresh_l2()       # 未选一级分类 → 显示"请先选一级分类"提示
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
        """渲染一级分类列。

        2026-09-07（第1条改进）：未选择根目录时只显示提示，
        不再把全库一级分类无差别灌入（旧行为会一次性铺满整列）。
        """
        self._clear_frame(self.l1_frame)
        self._clear_nav_btns("l1")
        ctk.CTkButton(self.l1_frame, text="➕ 新增一级分类", height=28, **_ADD_BTN,
                      state="disabled" if (self._lock_on or self._cur_domain_id is None) else "normal",
                      # 2026-08-21（第005条）：锁定时禁用新增；2026-09-07：未选根目录时灰显但保留可见
                      command=self._add_l1).pack(fill="x", padx=6, pady=2)
        if self._cur_domain_id is None:
            self._nav_hint(self.l1_frame, "先在上方选择「根目录」，\n此处将列出一级分类")
            self._apply_nav_highlight()
            self._scroll_top(self.l1_frame)
            return
        cats = self.db.list_categories(domain_id=self._cur_domain_id, parent_id=None)
        for c in cats:
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
        if not cats:
            self._nav_hint(self.l1_frame, "（该根目录暂无一级分类）")
        self._apply_nav_highlight()
        self._scroll_top(self.l1_frame)

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
                      state="disabled" if (self._lock_on or self._cur_cat_id is None) else "normal",
                      # 2026-08-21（第005条）：锁定时禁用新增；2026-09-07：未选分类时灰显但保留可见
                      command=self._add_l2).pack(fill="x", padx=6, pady=2)
        if parent_id is not None:
            subs = self.db.list_categories(parent_id=parent_id)
            for c in subs:
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
            if not subs:
                self._nav_hint(self.l2_frame, "（该分类暂无二级分类）")
        else:
            self._nav_hint(self.l2_frame, "先选择一级分类，\n此处将列出二级分类")
        self._apply_nav_highlight()
        self._scroll_top(self.l2_frame)

    def _select_category(self, cat_id: int) -> None:
        """点击/悬停分类：高亮原地更新；一级展开时重建二级列。

        2026-09-07（阶段2）：任一分级分类都显示其"本级条目"（主挂靠∪关联），
        即使它仍有子分类——子分类照常显示在其二级列。
        """
        if self._cur_cat_id == cat_id:
            return
        if not self._confirm_unsaved():
            return
        self._cur_cat_id = cat_id
        if self.db.category_has_children(cat_id):
            self._refresh_l2()          # 有子级：重建二级列展示子分类
            self._view = ("cat", cat_id)
            self._render_entries(self.db.list_entries(cat_id), "条目")
            self._apply_nav_highlight()
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

    def _cancel_search_timer(self) -> None:
        """取消尚未到点的"搜索防抖"定时器（2026-09-10，用户要求）"""
        if self._search_after is not None:
            try:
                self.after_cancel(self._search_after)
            except Exception:
                pass
            self._search_after = None

    def _on_search_typing(self, _event=None) -> None:
        """搜索框输入中：重置防抖定时器，**停止输入 _SEARCH_DEBOUNCE_MS 后**才真正查询。

        2026-09-10（用户要求）：此前每敲一个字符就触发一次"全字段检索 + 列表重建"，
        输入长句时持续刷新、卡顿；改为停顿后才查询。回车或点击右侧"🔍"仍可立即查询。
        """
        if _event is not None and getattr(_event, "keysym", "") == "Return":
            return  # 回车已由 <Return> 绑定立即查询，无需再排队一次
        self._cancel_search_timer()
        self._search_after = self.after(_SEARCH_DEBOUNCE_MS, self._on_search_key)

    def _on_search_key(self, _event=None) -> None:
        """真正执行搜索（防抖到点 / 回车 / 点击"🔍"三处共用）"""
        self._cancel_search_timer()
        kw = self.search_entry.get().strip()
        if not kw:
            self._restore_view()
            return
        self._view = ("search", kw)
        self._render_entries(self.db.search(kw), f"搜索结果（{kw}）")

    def _on_search_click(self) -> None:
        """点击搜索框右侧"🔍"图标按钮：立即按输入内容执行搜索并保持输入焦点（2026-09-10，用户要求 2）"""
        self._on_search_key()   # 内部已取消待执行的防抖定时器
        try:
            self.search_entry.focus_set()
        except Exception:
            pass

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
    _NO_ADD = -1  # 视图不可新增条目的哨兵值（分类 id 恒为正）

    def _add_context_target(self):
        """当前浏览视图对应的"新增目标"：叶子分类视图→分类 id；未分类视图→None；
        其余视图不可新增，返回 _NO_ADD。供新增态自动退出时判断目标是否已改变。
        """
        if not self._view:
            return self._NO_ADD
        kind = self._view[0]
        if kind == "cat":
            return self._cur_cat_id if self._cur_cat_id is not None else self._NO_ADD
        if kind == "uncat":
            return None
        return self._NO_ADD

    def _add_available(self) -> bool:
        """就地新增是否可用：当前"条目"列表里能新增时即可
        ——未分类视图，或选中任一分级分类（含仍带子分类的一级，本级条目可直接新增）；
        锁定态、项目/根目录/搜索/常用视图禁用。

        2026-09-07（阶段2）：放宽为"选中任意分类均可新增"（各分类都可持有本级条目）。
        """
        if self._lock_on or not self._view:
            return False
        kind = self._view[0]
        if kind == "uncat":
            return True
        if kind != "cat":
            return False
        return self._cur_cat_id is not None

    def _set_view_mode(self, value: str) -> None:
        self._view_mode = "card" if value == "卡片" else "list"
        self._restore_view()

    def _render_entries(self, entries, title: str) -> None:
        self.entry_frame.configure(label_text=title)
        self._hide_entry_overview()  # 2026-09-09：列表重建前收起"全部条目名"浮层
        self._entry_ov_names = []
        self._clear_frame(self.entry_frame)

        # 2026-09-06：浏览视图离开新增目标时，退出"新增条目"态并清空残留空表单
        # （未保存输入已由各切换入口的 _confirm_unsaved 处理，此处只管无脏内容场景）
        if self._adding_new and not self._detail_dirty:
            if self._add_context_target() != self._add_target:
                self._adding_new = False
                self._add_target = None
                self._show_detail(None)

        # 2026-09-07：条目区"新增条目"主按钮——绿色白字加高加粗，突出"要新增就点这里"
        self._add_entry_btn = ctk.CTkButton(
            self.entry_frame, text="＋ 新增条目", height=36,
            fg_color="#2E8B57", hover_color="#256e46", text_color="white",
            font=("Microsoft YaHei", 15, "bold"),
            state="normal" if self._add_available() else "disabled",
            command=self._start_new_entry)
        self._add_entry_btn.pack(fill="x", padx=6, pady=(4, 2))

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
            self._scroll_top(self.entry_frame)  # 2026-09-07（第4条改进）
            return
        if self._view_mode == "card":
            for e in entries:
                self._add_card(e)
        else:
            for e in entries:
                self._add_row(e)
        self._entry_ov_names = [e["name"] for e in entries]  # 2026-09-09：悬停浮层数据
        self._scroll_top(self.entry_frame)  # 2026-09-07（第4条改进）：切换分类后条目列回到顶部

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
            # 2026-09-09：悬停条目卡片同样触发"全部条目名"浮层（add 保留原有悬停选中）
            w.bind("<Enter>", self._entry_ov_enter, add="+")
            w.bind("<Leave>", self._entry_ov_leave, add="+")

    # ------------------------------------------------------------------ #
    # 条目列悬停"全部条目名"浮层（2026-09-09：列宽加宽后仍看不全时的兜底）
    # ------------------------------------------------------------------ #
    def _cancel_entry_overview(self) -> None:
        if self._entry_ov_after is not None:
            try:
                self.after_cancel(self._entry_ov_after)
            except Exception:
                pass
            self._entry_ov_after = None

    def _entry_ov_enter(self, _event=None) -> None:
        if not self._entry_ov_names:
            return
        if _event is not None and getattr(_event, "y_root", None):
            self._entry_ov_y = _event.y_root
        self._cancel_entry_overview()
        # 稍长的延迟：快速扫读条目时不至于频繁弹层
        self._entry_ov_after = self.after(500, self._show_entry_overview)

    def _entry_ov_leave(self, _event=None) -> None:
        # 2026-09-10（用户要求 4-二）：不再直接关闭，改为延时判断光标是否已移到浮层内
        self._cancel_entry_overview()
        self._entry_ov_after = self.after(200, self._entry_ov_maybe_hide)

    def _entry_ov_maybe_hide(self) -> None:
        """延时判断是否关闭浮层（2026-09-10，用户要求 4-二）。

        光标已离开条目列时，若仍停在浮层内（例如正把鼠标移向浮层的滚动条），则**不关闭**，
        改为继续轮询；只有条目列与浮层都不在光标下才真正关闭，使浮层内容可被滚动。
        """
        self._cancel_entry_overview()
        if self._entry_ov_popup is None:
            return
        if self._pointer_in_overview_area():
            self._entry_ov_after = self.after(150, self._entry_ov_maybe_hide)
            return
        self._hide_entry_overview()

    def _pointer_in_overview_area(self) -> bool:
        """光标当前是否位于条目列或浮层窗口内（2026-09-10，用户要求 4-二）"""
        try:
            px, py = self.winfo_pointerxy()
        except Exception:
            return False
        for w in (self._entry_ov_popup, self.entry_frame):
            if w is None:
                continue
            try:
                if not w.winfo_exists() or not w.winfo_ismapped():
                    continue
                x, y = w.winfo_rootx(), w.winfo_rooty()
                if x <= px <= x + w.winfo_width() and y <= py <= y + w.winfo_height():
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _widget_inside(widget, ancestor) -> bool:
        """判断控件是否在指定祖先控件之内（2026-09-10，用户要求 4-一）"""
        w = widget
        while w is not None:
            if w is ancestor:
                return True
            try:
                w = w.master
            except Exception:
                return False
        return False

    def _entry_ov_wheel(self, event=None) -> None:
        """条目区滚动时，浮层"条目名称一览"按相同滚动比例同步滚动（2026-09-10，用户要求 4-一）"""
        if self._entry_ov_popup is None or self._entry_ov_listbox is None:
            return
        if event is None or not self._widget_inside(getattr(event, "widget", None),
                                                   self.entry_frame):
            return
        try:
            lb = self._entry_ov_listbox
            if not lb.winfo_exists():
                return
            canvas = getattr(self.entry_frame, "_parent_canvas", None)
            if canvas is None or not canvas.winfo_exists():
                return
            first, _last = canvas.yview()   # 条目列与浮层列表条目一一对应，按比例同步
            lb.yview_moveto(first)
        except Exception:
            pass

    def _hide_entry_overview(self) -> None:
        self._cancel_entry_overview()
        if self._entry_ov_popup is not None:
            try:
                self._entry_ov_popup.destroy()
            except Exception:
                pass
            self._entry_ov_popup = None
        self._entry_ov_listbox = None

    def _show_entry_overview(self) -> None:
        """在条目列左侧浮出当前分类下全部条目名称（可滚动，超长自动横向滚动）。"""
        if not self._entry_ov_names or self._entry_ov_popup is not None:
            return
        names = self._entry_ov_names
        popup = tk.Toplevel(self.entry_frame)
        popup.wm_overrideredirect(True)
        popup.configure(bg="#ffffff")
        head = tk.Label(popup, text=f"📋 条目名称一览（共 {len(names)} 条）",
                        bg="#25639c", fg="white", padx=8, pady=4,
                        font=("Microsoft YaHei", 10, "bold"))
        head.pack(fill="x")
        body = tk.Frame(popup, bg="#ffffff")
        body.pack(fill="both", expand=True)
        sb = tk.Scrollbar(body)
        sb.pack(side="right", fill="y")
        max_len = max((len(n) for n in names), default=4)
        width = max(min(max_len + 4, 60), 24)
        lb = tk.Listbox(body, font=("Microsoft YaHei", 10), activestyle="none",
                        width=width, height=min(len(names), 16),
                        yscrollcommand=sb.set, borderwidth=0, highlightthickness=0)
        for i, n in enumerate(names, 1):
            lb.insert("end", f"{i}. {n}")
        lb.pack(side="left", fill="both", expand=True)
        sb.configure(command=lb.yview)
        # 2026-09-10（用户要求 4-二）：浮层内各控件绑定"进入取消关闭 / 离开延时关闭"，
        # 使鼠标可移入浮层（含滚动条）滚动内容而不消失。
        for wdg in (popup, head, body, lb, sb):
            wdg.bind("<Enter>", lambda _e: self._cancel_entry_overview(), add="+")
            wdg.bind("<Leave>", self._entry_ov_leave, add="+")
        popup.update_idletasks()
        w, h = popup.winfo_reqwidth(), popup.winfo_reqheight()
        sw, sh = popup.winfo_screenwidth(), popup.winfo_screenheight()
        # 定位：条目列【左侧】（右侧是详情区，浮层不应盖住它）；超出屏幕左侧则改放右侧
        x = self.entry_frame.winfo_rootx() - w - 4
        if x < 8:
            x = self.entry_frame.winfo_rootx() + self.entry_frame.winfo_width() + 4
        if x + w > sw:
            x = max(sw - w - 8, 0)
        y = self._entry_ov_y or self.entry_frame.winfo_rooty()
        if y + h > sh:
            y = max(sh - h - 8, 0)
        popup.wm_geometry(f"+{x}+{y}")
        self._entry_ov_popup = popup
        self._entry_ov_listbox = lb   # 2026-09-10（用户要求 4-一）：滚动同步对象

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
        # 2026-09-06：展示已存条目/清空详情时一律退出"新增条目"态
        self._adding_new = False
        self._add_target = None
        self._add_group_open = False
        self._add_group_pairs = []
        self._clear_frame(self.detail_scroll)
        self._detail_boxes = {}
        self._detail_dirty = False
        if e is None:
            self._detail_entry_id = None
            self._detail_hidden = set()  # 2026-08-18：无条目时无隐藏字段
            self.detail_scroll.configure(label_text="")  # 2026-09-09：固定头部状态行取代内置标题
            self.fav_btn.configure(command=lambda: None)
            self.del_btn.configure(command=lambda: None)
            self.link_btn.configure(state="disabled", command=lambda: None)
            self.copyto_btn.configure(state="disabled", command=lambda: None)
            self.move_btn.configure(state="disabled", command=lambda: None)
            self.copy_all_btn.configure(command=lambda: None)
            self.copy_cn_btn.configure(command=lambda: None)
            self.copy_en_btn.configure(command=lambda: None)
            self.save_btn.configure(command=lambda: None)
            self.reset_btn.configure(command=lambda: None)
            # 2026-09-09：无条目时编辑/浏览切换无意义 → 禁用并回到编辑态
            try:
                self.edit_mode_toggle.set("✏️ 编辑")
                self.edit_mode_toggle.configure(state="disabled")
            except Exception:
                pass
            self._apply_lock_state()
            self._refresh_detail_header()
            ctk.CTkLabel(self.detail_scroll, text="请选择条目查看详情",
                         text_color="gray").pack(pady=40)
            return
        self._detail_entry_id = e["id"]
        # 2026-09-09：固定头部状态行恒常显示"显示全部字段/精简显示"开关（任何根目录/层级），
        # 滚动区内不再按根目录条件显示精简顶栏，也不显示孤立"详情"标题
        self.detail_scroll.configure(label_text="")

        # 命令按钮：收藏 / 删除 / 关联到 / 复制到 / 复制提示词 / 保存 / 重置
        star = "★ 已收藏" if e["is_favorite"] else "☆ 收藏"
        self.fav_btn.configure(text=star,
                               command=lambda: self._toggle_favorite(e["id"]))
        self.del_btn.configure(command=lambda: self._delete_entry(e["id"]))
        # 2026-09-07（阶段2）：详情区"关联到/复制到"作用于当前展示的条目
        self.link_btn.configure(state="normal",
                                command=lambda eid=e["id"]: self._link_entry(eid))
        self.copyto_btn.configure(state="normal",
                                  command=lambda eid=e["id"]: self._copy_entry_to_targets(eid))
        self.move_btn.configure(state="normal",
                                command=lambda eid=e["id"]: self._move_entry(eid))
        self.copy_all_btn.configure(command=lambda: self._copy_entry(e["id"], _COPY_ALL))
        self.copy_cn_btn.configure(command=lambda: self._copy_entry(e["id"], _COPY_CN))
        self.copy_en_btn.configure(command=lambda: self._copy_entry(e["id"], _COPY_EN))
        self.save_btn.configure(command=self._save_detail)
        self.reset_btn.configure(command=self._reset_detail)
        # 2026-09-07：① 条目名称（风格名称）作为详情区第一个字段（醒目大输入框）
        self._build_name_field(initial=e["name"])
        # 2026-09-07（阶段3）：多位置提示行（主/关联位置 + 关联位置"解除"按钮）
        # 先构建再 _apply_lock_state，使锁定态能一并禁用位置解除按钮
        self._build_detail_location_hint(e["id"])
        self._apply_lock_state()

        # 2026-09-09：②~⑦（介绍…代表高清配图）默认折叠为一组（仅 1 行标题），
        # 点"展开"才显示全部字段；⑧/⑨ 提示词与 ⑩ 图像获取方案保持平铺。
        # 2026-09-09（修正）：折叠组始终包含全部六项——不再受"精简模式按根目录隐藏 ③-⑦"
        # 影响；折叠本身即是精简，点"展开"后 ②~⑦ 无论哪个根目录都全部显示。
        self._detail_group_toggle = None
        info_items = [(label, key, height) for label, key, height in _FIELDS
                      if key in _INFO_GROUP_KEYS]
        if info_items:
            if self._detail_group_for != e["id"]:  # 切到别的条目时复位为默认折叠
                self._detail_group_open = False
                self._detail_group_for = e["id"]
            self._build_detail_info_group(e, info_items,
                                          default_open=self._detail_group_open)

        # ⑧/⑨ 提示词（可折叠）+ ⑩ 图像获取方案
        group_trailing = None
        for label, key, height in _FIELDS:
            if key in _INFO_GROUP_KEYS or key in self._detail_hidden:
                continue
            if key in _COLLAPSIBLE_KEYS:  # 有内容默认 6 行可见、可展开；无内容 1 行
                block = self._build_collapsible_field(e, label, key)
                group_trailing = group_trailing or block
                continue
            # 2026-09-07（第5条改进）：每字段成"淡彩圆角卡片块"，标签主题色文字 + 白底同色细边文本框
            block, label_c, box_bg, box_border = self._begin_field_block(key)
            group_trailing = group_trailing or block
            lbl = ctk.CTkLabel(block, text=label, text_color=label_c,
                               font=("Microsoft YaHei", 12, "bold"), anchor="w")
            lbl.pack(fill="x", padx=12, pady=(8, 2))
            # 2026-08-18（第015条）：⑩图像获取方案 若为链接 → 文本框右侧加"打开"按钮，既能复制网址（左）、又能打开网址（右）
            if key == "image_plan":
                row = ctk.CTkFrame(block, fg_color="transparent")
                row.pack(fill="x", padx=6, pady=(0, 6))
                box = ctk.CTkTextbox(row, height=height, fg_color=box_bg,
                                     border_width=1, border_color=box_border,
                                     corner_radius=6)
                box.pack(side="left", fill="x", expand=True)
                url = (e[key] or "").strip()
                if url.startswith(("http://", "https://")):
                    open_btn = ctk.CTkButton(
                        row, text="打开", width=52, height=28,
                        command=lambda b=box: self._open_image_plan(b))
                    open_btn.pack(side="right", padx=(6, 0))
            else:
                box = ctk.CTkTextbox(block, height=_rows_to_px(height), fg_color=box_bg,
                                     border_width=1, border_color=box_border,
                                     corner_radius=6)
                box.pack(fill="x", padx=6, pady=(0, 6))
            box.insert("1.0", e[key] or "")
            box.bind("<KeyRelease>", self._mark_dirty)
            self._detail_boxes[key] = box
            full = e[key] or "（无内容）"
            _FieldTooltip(lbl, full)
            _FieldTooltip(box, full)

        # ②~⑦ 折叠组展开时把卡片插回标题条与这个锚点（第一个 ⑧⑨⑩ 字段块）之间
        if group_trailing is not None:
            self._detail_group_anchor = group_trailing

        # 2026-09-10（用户要求）：详情区文本框内的滚轮统一转给详情区滚动
        self._install_detail_wheel(self.detail_scroll)

        # 2026-09-09：为详情区全部文本框启用撤销/重做，并按当前模式设置只读
        _enable_text_undo(self.detail_scroll)
        self._apply_browse()
        try:
            self.edit_mode_toggle.configure(state="normal")  # 非新增态恢复切换可用
        except Exception:
            pass
        self._refresh_detail_header()
        self._scroll_top(self.detail_scroll)  # 2026-09-07（第4条改进）：打开新条目详情回到顶部

    # ------------------------------------------------------------------ #
    # 详情区文本框滚轮（2026-09-10，用户要求）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _walk_widgets(root_widget):
        """深度遍历控件树（不含自身，含全部子级）"""
        for child in root_widget.winfo_children():
            yield child
            for grand in MainWindow._walk_widgets(child):
                yield grand

    def _install_detail_wheel(self, root_widget) -> None:
        """把详情区文本框上的滚轮统一转交"详情区滚动"（2026-09-10，用户要求）。

        背景根因：tkinter 的 Text 有**类级** `<MouseWheel>` 绑定，会把滚轮"吞"去滚动
        文本框自身；于是鼠标停在文本框上时详情区（页面）几乎不滚动，用户反馈
        "指针在文本框上滚动失效、只有移到文本框以外才有效"。
        处理：为详情区（含"＋新增条目"表单）内每个 CTkTextbox 的内部 tk.Text 挂一个
        **控件级**处理器——统一把滚轮转给详情区画布并 `return "break"`，从而跳过
        Text 类级绑定与 CTkScrollableFrame 的 bind_all 处理，行为与指针在文本框外一致。
        需要阅读超长提示词时：点 ⑧/⑨ 的"展开"放大文本框，或用键盘（PageUp/PageDown、
        方向键、Ctrl+Home/End）浏览。
        """
        canvas = getattr(self.detail_scroll, "_parent_canvas", None)
        if canvas is None:
            return
        for w in self._walk_widgets(root_widget):
            inner = getattr(w, "_textbox", None)          # 仅 CTkTextbox 有内部 Text
            if inner is None or getattr(inner, "_ps_wheel_ok", False):
                continue
            inner._ps_wheel_ok = True
            inner.bind("<MouseWheel>",
                       lambda e, c=canvas: self._detail_wheel_to_page(e, c), add="+")

    @staticmethod
    def _detail_wheel_to_page(event, canvas):
        """把滚轮事件转给详情区画布滚动；返回 "break" 以阻止文本框自身滚动"""
        try:
            delta = int(getattr(event, "delta", 0) or 0)
            if delta and canvas.winfo_exists():
                canvas.yview("scroll", -int(delta / 6), "units")
        except Exception:
            pass
        return "break"

    def _build_detail_info_group(self, e, info_items, default_open: bool = False) -> None:
        """详情区 ②~⑦ 折叠组（2026-09-09 二次修订，采用"兄弟卡片 + 锚点插入"）。

        此前用“外层容器包卡片、运行时再向容器内 pack 子项”的做法存在几何/裁切不稳，
        实测会出现展开后字段显示不全。现改为：标题条与各字段卡片都是详情滚动区的
        兄弟控件——折叠时只创建不 pack（仅标题 1 行可见）；展开时用
        pack(before=锚点) 把卡片按顺序插回“标题条 与 ⑧⑨⑩”之间，与页面其它内容
        一样正常参与滚动布局，不会再出现标题下方空白或只显示部分字段的问题。
        info_items：本根目录/策略下实际可见的 (标签, 字段键, 高度行数)。
        """
        shown = len(info_items)
        bar = ctk.CTkFrame(self.detail_scroll, fg_color="#eef2f7",
                           corner_radius=10, border_width=1, border_color="#cfd9e5")
        bar.pack(fill="x", padx=10, pady=(8, 0))
        cap_text = "②~⑦ 详情信息" if shown > 1 else "② 介绍"
        ctk.CTkLabel(bar, text=f"📋 {cap_text}",
                     font=("Microsoft YaHei", 12, "bold"),
                     text_color="#3b5a78", anchor="w").pack(side="left", padx=(12, 4), pady=5)
        have = sum(1 for _l, _k, _h in info_items if (e.get(_k) or "").strip())
        if have:
            status = ctk.CTkLabel(bar, text=f"● {have}/{shown} 项有内容",
                                  font=("Microsoft YaHei", 10), text_color="#2E8B57")
        else:
            status = ctk.CTkLabel(bar, text="（暂无内容）",
                                  font=("Microsoft YaHei", 10), text_color="#9aa4b1")
        status.pack(side="left", padx=(2, 6))
        toggle = ctk.CTkButton(bar, text="展开", width=56, height=24, **_ADD_BTN,
                               command=self._toggle_detail_info_group)
        toggle.pack(side="right", padx=(6, 8), pady=3)
        self._detail_group_toggle = toggle
        self._detail_group_anchor = None  # ②~⑦ 之后的下一个控件，展开时作 pack 锚点

        widgets = []
        for label, key, height in info_items:
            label_c, block_bg, box_bg, box_border = _field_style(key)
            blk = ctk.CTkFrame(self.detail_scroll, fg_color=block_bg,
                               corner_radius=10, border_width=1, border_color=box_border)
            lbl = ctk.CTkLabel(blk, text=label, text_color=label_c,
                               font=("Microsoft YaHei", 12, "bold"), anchor="w")
            lbl.pack(fill="x", padx=12, pady=(8, 2))
            box = ctk.CTkTextbox(blk, height=_rows_to_px(height), fg_color=box_bg,
                                 border_width=1, border_color=box_border, corner_radius=6)
            box.pack(fill="x", padx=6, pady=(0, 6))
            box.insert("1.0", e[key] or "")
            box.bind("<KeyRelease>", self._mark_dirty)
            self._detail_boxes[key] = box
            full = e[key] or "（无内容）"
            _FieldTooltip(lbl, full)
            _FieldTooltip(box, full)
            widgets.append(blk)
            if key == "image_desc":  # ⑦ 下方紧跟图片预览区，一并随组显隐
                widgets.append(self._build_image_area(parent=self.detail_scroll,
                                                      pack_now=False))
        self._detail_group_widgets = widgets
        if default_open:
            self._detail_group_open = True
            toggle.configure(text="收起")
            self._expand_detail_group_widgets()

    def _expand_detail_group_widgets(self) -> None:
        """把 ②~⑦ 折叠组各字段卡片插入标题条与后续字段之间（标题条始终保留）。"""
        anchor = getattr(self, "_detail_group_anchor", None)
        if anchor is not None and anchor.winfo_exists():
            for w in self._detail_group_widgets:
                w.pack(fill="x", pady=(4, 0), before=anchor)
        else:  # 初始展开（后续字段尚未创建时）直接依次 pack 到末尾即可
            for w in self._detail_group_widgets:
                w.pack(fill="x", pady=(4, 0))

    def _toggle_detail_info_group(self) -> None:
        """展开/收起 ②~⑦ 详情折叠组（内容保留，仅切换整组显隐）。"""
        if not self._detail_group_widgets:
            return
        if not self._detail_group_open:
            self._expand_detail_group_widgets()
            self._detail_group_open = True
        else:
            for w in reversed(self._detail_group_widgets):
                w.pack_forget()
            self._detail_group_open = False
        if self._detail_group_toggle is not None:
            self._detail_group_toggle.configure(
                text="收起" if self._detail_group_open else "展开")
        self._refresh_detail_header()

    def _build_collapsible_field(self, e, label: str, key: str) -> None:
        """可折叠字段：标签行 + 展开/收起按钮 + 文本框（height 单位为像素）。

        2026-08-18：有内容默认 _COLLAPSED_H=120px（6 行文字完整可见）；点击"展开"时
        高度自适应为内容实际显示行数（_content_fit_height，有多少行显示多少行）；
        无内容时只显示 _EMPTY_H=24px（1 行空行），且不显示"展开"按钮。
        """
        has_content = bool((e[key] or "").strip())
        base_h = _COLLAPSED_H if has_content else _EMPTY_H

        # 2026-09-07（第5条改进）：可折叠字段同样用"淡彩卡片块"——标签主题色 + 内容状态小标签
        block, label_c, box_bg, box_border = self._begin_field_block(key)
        head = ctk.CTkFrame(block, fg_color="transparent")
        head.pack(fill="x", padx=10, pady=(8, 0))
        lbl = ctk.CTkLabel(head, text=label, text_color=label_c,
                           font=("Microsoft YaHei", 12, "bold"), anchor="w")
        lbl.pack(side="left")
        status = ctk.CTkLabel(
            head,
            text=("● 有内容" if has_content else "（无内容）"),
            font=("Microsoft YaHei", 10),
            text_color=("#2E8B57" if has_content else "#9aa4b1"))
        status.pack(side="left", padx=(6, 0))
        toggle = None
        if has_content:
            toggle = ctk.CTkButton(head, text="展开", width=52, height=22, **_ADD_BTN)
            toggle.pack(side="right")

        box = ctk.CTkTextbox(block, height=base_h, fg_color=box_bg,
                             border_width=1, border_color=box_border, corner_radius=6)
        box.pack(fill="x", padx=6, pady=(0, 8))
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
                status.configure(text=("⏶ 已展开" if expanded else "● 有内容"),
                                 text_color=("#25639c" if expanded else "#2E8B57"))

            toggle.configure(command=_toggle)
        return block

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

    def _build_image_area(self, parent=None, pack_now: bool = True) -> ctk.CTkFrame:
        """⑦ 代表高清配图 下的图片预览与操作区（返回行容器，随折叠组显隐）。
        parent 缺省为详情滚动区；2026-09-09 起在 ②~⑦ 折叠组内时传入组容器并
        以 pack_now=False 先不打包，由折叠组统一控制展开/收起，保证折叠态仅 1 行。
        """
        parent = parent or self.detail_scroll
        img_row = ctk.CTkFrame(parent, fg_color="transparent")
        if pack_now:
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
        return img_row

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

    def _toggle_detail_show_all(self) -> None:
        """固定头部"显示全部字段/精简显示"开关：只作用于 ②~⑦，与标题条内联"展开"联动。

        查看既有条目 → 展开/收起 ②~⑦ 折叠组；"＋新增条目"态 → 展开/收起 ②~⑦ 补充组。
        """
        if self._adding_new:
            if self._add_group_pairs:
                self._toggle_add_group()
        else:
            self._toggle_detail_info_group()
        self._refresh_detail_header()

    def _refresh_detail_header(self) -> None:
        """同步固定头部状态行：左侧状态标题、右侧开关文案与可用性（与折叠组状态联动）。"""
        lbl = getattr(self, "detail_state_lbl", None)
        btn = getattr(self, "show_all_btn", None)
        if lbl is None or btn is None:
            return
        if self._adding_new:
            opened = bool(self._add_group_open)
            enabled = bool(self._add_group_pairs)
            base = "新增条目"
        else:
            opened = bool(self._detail_group_open)
            enabled = bool(self._detail_entry_id is not None
                           and self._detail_group_widgets)
            base = "详情"
        lbl.configure(text=f"{base} · 已展开全部字段" if opened else f"{base} · 精简模式")
        btn.configure(text="⏸ 精简显示" if opened else "⏵ 显示全部字段",
                      state="normal" if enabled else "disabled")

    def _save_detail(self) -> None:
        if self._detail_entry_id is None:
            return
        cur = self.db.get_entry(self._detail_entry_id)
        if cur is None:
            return

        def _field(key: str) -> str:
            # 2026-09-09：②~⑦ 折叠组内字段始终构建；仅当某字段确实未构建时
            # （理论兜底）才保留数据库原值，避免误清空。
            return self._box_text(key) if key in self._detail_boxes else cur[key]

        e = Entry(id=self._detail_entry_id, category_id=cur["category_id"],
                  name=self._name_entry.get().strip() or cur["name"],
                  intro=self._box_text("intro"),
                  origin=_field("origin"), features=_field("features"),
                  scenes=_field("scenes"), works=_field("works"),
                  image_desc=_field("image_desc"),
                  prompt_cn=self._box_text("prompt_cn"), prompt_en=self._box_text("prompt_en"),
                  image_plan=self._box_text("image_plan"),
                  image_path=cur["image_path"], is_favorite=cur["is_favorite"])
        # 2026-09-07：保存前同内容一致性轻提示（排除自身）
        dup = self.db.find_content_duplicates(self.db.content_key(e),
                                              exclude_entry_id=self._detail_entry_id)
        if dup:
            sample = "、".join(d["name"] for d in dup)
            if not messagebox.askyesno(
                    "内容重复提示",
                    f"检测到 {len(dup)} 个相同内容的其它条目（示例：{sample}）。\n\n仍要保存吗？",
                    parent=self):
                return
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
        """切换前检查未保存修改；返回是否继续切换

        2026-09-06：支持"新增条目"态——有未保存的新增内容时同样提示，
        【是】保存本次新增后继续切换、【否】放弃、【取消】返回。
        """
        if not self._detail_dirty:
            return True
        if self._adding_new:
            r = messagebox.askyesnocancel(
                "未保存的新增内容",
                "当前新增的条目尚未保存。\n\n【是】保存新增　【否】放弃新增　【取消】返回")
            if r is None:
                return False
            if r:
                return self._save_new_entry(exit_mode=True)
            self._detail_dirty = False
            return True
        if self._detail_entry_id is None:
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
    # 主界面就地"新增条目"（2026-09-06）
    # ------------------------------------------------------------------ #
    def _cat_label(self, cat_id: int) -> str:
        """分类全路径显示文本（如"一级 › 二级"）"""
        parts = []
        cid = cat_id
        seen = set()
        while cid is not None and cid not in seen:
            c = self.db.get_category(cid)
            if not c:
                break
            parts.append(c["name"])
            seen.add(cid)
            cid = c["parent_id"]
        return " › ".join(reversed(parts))

    def _full_location_path(self, cat_id: int) -> str:
        """位置"面包屑"完整路径：项目类别 › 根目录 › 一级分类 › …（2026-09-07 第2条改进）。

        任一分级分类都可能挂在某根目录（L0）下；根目录再归属某项目类别（最高层级），
        据此拼出从最高层到自身的完整链；未关联到任何根目录时退化为分类链本身。
        """
        root = self.db.category_root(cat_id)
        segs = []
        if root:
            doms = self.db.linked_domains(root)
            if doms:
                d = doms[0]
                if d.get("project_id"):
                    p = self.db.get_project(d["project_id"])
                    if p:
                        segs.append(p["name"])
                segs.append(d["name"])
        parts = []
        cid = cat_id
        seen = set()
        while cid is not None and cid not in seen:
            c = self.db.get_category(cid)
            if not c:
                break
            parts.append(c["name"])
            seen.add(cid)
            cid = c["parent_id"]
        segs.extend(reversed(parts))
        return " › ".join(segs) if segs else "未分类"

    def _begin_field_block(self, key: str, pady_top: int = 10) -> tuple:
        """为 ②~⑩ 某一字段新建"淡彩圆角卡片块"（2026-09-07 第5条改进）。

        返回 (block, 标签文字色, 输入框底色, 输入框描边色)，调用方在其内部放标签与输入框，
        各字段因此拥有各自的浅彩底色与主题色标签，与 ① 名称深蓝卡形成统一层次。
        """
        label_c, block_bg, box_bg, box_border = _field_style(key)
        block = ctk.CTkFrame(self.detail_scroll, fg_color=block_bg, corner_radius=10,
                             border_width=1, border_color=box_border)
        block.pack(fill="x", padx=10, pady=(pady_top, 2))
        return block, label_c, box_bg, box_border

    def _build_name_field(self, initial: str = "") -> None:
        """详情/新增表单的"① 条目名称（风格名称）"输入字段（2026-09-07）。

        以淡蓝卡片 + 细边框突出"主字段"，与下方 ②~⑩ 淡彩字段块形成统一层次。
        """
        card = ctk.CTkFrame(self.detail_scroll, fg_color="#e2edfa",
                            corner_radius=10, border_width=1, border_color="#a9c6e4")
        card.pack(fill="x", padx=10, pady=(10, 2))
        ctk.CTkLabel(card, text="① 条目名称（风格名称）",
                     font=("Microsoft YaHei", 13, "bold"), anchor="w",
                     text_color="#1d4e89").pack(fill="x", padx=12, pady=(8, 2))
        self._name_entry = ctk.CTkEntry(card, height=44,
                                        font=("Microsoft YaHei", 15, "bold"))
        self._name_entry.pack(fill="x", padx=12, pady=(0, 8))
        self._name_entry.insert(0, initial)
        self._name_entry.bind("<KeyRelease>", self._mark_dirty)

    def _build_detail_location_hint(self, entry_id: int) -> None:
        """详情区顶部"所在位置"提示行（2026-09-07 阶段3）。

        列出条目全部位置（面包屑式完整链）：主位置标"主"，额外关联位置各带"×"解除按钮。
        2026-09-07（第2条改进）：面包屑带出「项目类别 › 根目录 › …」两级，
        并将整个面包屑栏背景改为更淡的浅蓝灰底、轻色小标签。
        """
        locs = self.db.list_entry_locations(entry_id)
        if not locs:
            return
        main_id = (self.db.get_entry(entry_id) or {}).get("category_id")
        head = ctk.CTkFrame(self.detail_scroll, fg_color="#eaf1f9",
                            corner_radius=10, border_width=1,
                            border_color="#d6e2ef")
        head.pack(fill="x", padx=10, pady=(8, 0))
        ctk.CTkLabel(head, text="🧭 位置", font=("Microsoft YaHei", 12, "bold"),
                     text_color="#5a6f88").pack(side="left", padx=(12, 4), pady=6)
        for cid in locs:
            bread = self._full_location_path(cid)
            is_main = cid == main_id
            # 主位置=浅绿、关联=浅灰蓝（浅色背景+深色字），整体观感更轻
            if is_main:
                chip = ctk.CTkFrame(head, fg_color="#e3f2ea", corner_radius=8)
                tag = "主"
                txt_color = "#1f7a50"
            else:
                chip = ctk.CTkFrame(head, fg_color="#e8eef6", corner_radius=8)
                tag = "关联"
                txt_color = "#25639c"
            chip.pack(side="left", padx=(0, 6), pady=6)
            ctk.CTkLabel(chip, text=f"{tag} · {bread}",
                         font=("Microsoft YaHei", 11),
                         text_color=txt_color).pack(side="left", padx=6, pady=2)
            if not is_main:  # 关联位置提供"解除"按钮（主位置用右键菜单移动/解除）
                ctk.CTkButton(
                    chip, text="×", width=22, height=20, fg_color="#9aa9ba",
                    command=lambda c=cid: self._remove_detail_location(entry_id, c)
                ).pack(side="right", padx=(0, 3), pady=2)
        # 提示：多位置条目编辑一处全同步
        if len(locs) > 1:
            ctk.CTkLabel(head, text="（同一条目编辑后各位置同步）",
                         text_color="#8aa0b5", font=("Microsoft YaHei", 10)
                         ).pack(side="left", padx=(2, 0))

    def _remove_detail_location(self, entry_id: int, cat_id: int) -> None:
        """详情位置行"×"：解除该条目在某关联分类的位置"""
        if self._lock_on or self._browse_mode:  # 2026-09-09：浏览态禁止改动
            return
        self.db.unlink_entry(entry_id, cat_id)
        self._restore_view()
        e = self.db.get_entry(entry_id)
        if e is not None:
            self._show_detail(e)
        self.toast("✅ 已解除该位置关联")

    def _start_new_entry(self) -> None:
        """条目区"➕ 新增条目"：详情区切入空白新增态（连续录入用）。

        目标分类取自当前视图：叶子分类视图 → 该分类；未分类视图 → 未分类。
        其他视图按钮已置灰，不会进入本方法。
        """
        if self._lock_on:
            return
        if not self._confirm_unsaved():  # 丢弃/保存当前编辑或新增内容后继续
            return
        kind = self._view[0] if self._view else None
        if kind == "cat":
            target = self._cur_cat_id
        elif kind == "uncat":
            target = None
        else:
            # 连续录入时视图可能已切走（如搜索中）：退出新增态并清空详情，避免残留空表单
            self.toast("请先在分类树/未分类中定位再新增条目", color="#D9534F")
            self._show_detail(None)
            return
        self._adding_new = True
        self._add_target = target
        self._detail_entry_id = None
        self._detail_dirty = False
        self._build_new_entry_editor(target)

    def _build_new_entry_editor(self, target) -> None:
        """新增态表单：空白 9 字段编辑器，②-⑦ 详情信息默认折叠为一组。

        2026-09-06：与详情编辑共用同一批字段控件；不采用根目录显隐策略。
        2026-09-09：② 介绍 一并并入折叠组（此前仅 ③-⑦ 折叠、② 常显占行），
        折叠态只占 1 行标题，点"展开"才显示 ②~⑦ 全部字段（⑧⑨⑩ 常显）。
        """
        self._clear_frame(self.detail_scroll)
        self._detail_boxes = {}
        self._detail_dirty = False
        self._add_group_open = False
        self._add_group_pairs = []
        self._browse_mode = False  # 新增必须录入 → 强制编辑模式
        try:
            self.edit_mode_toggle.set("✏️ 编辑")
            self.edit_mode_toggle.configure(state="disabled")
        except Exception:
            pass

        loc = self._cat_label(target) if target is not None else "未分类"
        self.detail_scroll.configure(label_text="")  # 2026-09-09：固定头部状态行承担顶部状态
        cap = ctk.CTkLabel(self.detail_scroll, text=f"＋ 将新增到：「{loc}」",
                           font=("Microsoft YaHei", 12, "bold"),
                           text_color="#2E8B57", anchor="w")
        cap.pack(fill="x", padx=8, pady=(8, 0))

        # 顶部命令按钮：收藏/删除/复制对新条目无意义 → 禁用（名称框随表单置入详情区）
        self.fav_btn.configure(state="disabled", command=lambda: None)
        self.del_btn.configure(state="disabled", command=lambda: None)
        for b in (self.move_btn, self.link_btn, self.copyto_btn,
                  self.copy_all_btn, self.copy_cn_btn, self.copy_en_btn):
            b.configure(state="disabled", command=lambda: None)
        self.save_btn.configure(state="normal", command=self._save_new_entry)
        self.reset_btn.configure(state="normal", command=self._reset_new_entry)

        # 2026-09-07：名称输入框作为表单第一个字段（醒目大输入框）
        self._build_name_field(initial="")
        # ②-⑦ 补充信息（默认折叠为 1 行，点"展开"逐条填写）
        self._build_add_extra_group()
        # ⑧ 中文版提示词 / ⑨ 英文版提示词（固定约 6 行高，方便直接录入）
        anchor = self._add_field_block("⑧ 中文版提示词", "prompt_cn", prompt=True)
        self._add_field_block("⑨ 英文版提示词", "prompt_en", prompt=True)
        # ⑩ 图像获取方案（右侧"打开"按钮，与详情一致）
        self._add_field_block("⑩ 图像获取方案", "image_plan")
        self._add_group_anchor = anchor  # 折叠组展开时的锚点：⑧ 字段块
        # 2026-09-10（用户要求）：新增表单文本框内的滚轮同样转给详情区滚动
        self._install_detail_wheel(self.detail_scroll)
        # 2026-09-09：新增表单文本框同样启用撤销/重做
        _enable_text_undo(self.detail_scroll)
        self._apply_browse()
        self._refresh_detail_header()
        self._scroll_top(self.detail_scroll)  # 2026-09-07（第4条改进）：打开新增表单回到顶部

        if not self._lock_on:
            self._name_entry.focus_set()

    def _add_field_block(self, label: str, key: str, prompt: bool = False) -> None:
        """在新增表单中渲染单个标签 + 文本框；②-⑦ 折叠组之外的字段使用。

        非折叠框沿用详情编辑的像素高度（_FIELDS）；⑧/⑨ 提示词固定 120px≈6 行，
        便于"内容少时直接填中文提示词"；⑩ 追加"打开"按钮读取网址。
        （2026-09-07 第5条改进：与详情编辑一致的淡彩卡片块样式）
        """
        heights = {k: h for _l, k, h in _FIELDS}
        block, label_c, box_bg, box_border = self._begin_field_block(key)
        ctk.CTkLabel(block, text=label, text_color=label_c,
                     font=("Microsoft YaHei", 12, "bold"), anchor="w"
                     ).pack(fill="x", padx=12, pady=(8, 2))
        if key == "image_plan":
            row = ctk.CTkFrame(block, fg_color="transparent")
            row.pack(fill="x", padx=6, pady=(0, 6))
            box = ctk.CTkTextbox(row, height=_rows_to_px(heights.get(key, 3)), fg_color=box_bg,
                                 border_width=1, border_color=box_border, corner_radius=6)
            box.pack(side="left", fill="x", expand=True)
            open_btn = ctk.CTkButton(row, text="打开", width=52, height=28,
                                     command=lambda b=box: self._open_image_plan(b))
            open_btn.pack(side="right", padx=(6, 0))
        else:
            h = 120 if prompt else _rows_to_px(heights.get(key, 3))
            box = ctk.CTkTextbox(block, height=h, fg_color=box_bg,
                                 border_width=1, border_color=box_border, corner_radius=6)
            box.pack(fill="x", padx=6, pady=(0, 6))
        box.bind("<KeyRelease>", self._mark_dirty)
        self._detail_boxes[key] = box
        return block

    def _build_add_extra_group(self) -> None:
        """②~⑦ 补充信息折叠组（新增态，2026-09-09 二次修订）。

        标题条与各字段卡片均为详情滚动区的兄弟控件：折叠时只创建不 pack（仅 1 行标题）；
        点"展开"用 pack(before=锚点) 把 ②~⑦ 卡片按顺序插回标题条与 ⑧⑨⑩ 之间，
        与页面其它内容一样正常参与滚动布局，避免"容器嵌套导致展开显示不全"。
        """
        bar = ctk.CTkFrame(self.detail_scroll, fg_color="#eef2f7",
                           corner_radius=10, border_width=1, border_color="#cfd9e5")
        bar.pack(fill="x", padx=10, pady=(8, 0))
        ctk.CTkLabel(bar, text="📋 ②~⑦ 补充信息",
                     font=("Microsoft YaHei", 12, "bold"),
                     text_color="#3b5a78", anchor="w").pack(side="left", padx=(12, 4), pady=5)
        ctk.CTkLabel(bar, text="（点“展开”填写）",
                     font=("Microsoft YaHei", 10), text_color="#9aa4b1"
                     ).pack(side="left", padx=(2, 6))
        self._add_group_toggle = ctk.CTkButton(bar, text="展开", width=56,
                                               height=24, **_ADD_BTN,
                                               command=self._toggle_add_group)
        self._add_group_toggle.pack(side="right", padx=(6, 8), pady=3)
        self._add_group_anchor = None  # 锚点：⑧ 中文版提示词字段块（随后创建时赋值）

        heights = {k: h for _l, k, h in _FIELDS}
        for label, key in (("② 介绍", "intro"), ("③ 溯源", "origin"),
                           ("④ 核心特征", "features"), ("⑤ 应用场景", "scenes"),
                           ("⑥ 代表作", "works"), ("⑦ 代表高清配图", "image_desc")):
            # 2026-09-07（第5条改进）：组内每字段同样用淡彩卡片块
            label_c, block_bg, box_bg, box_border = _field_style(key)
            blk = ctk.CTkFrame(self.detail_scroll, fg_color=block_bg,
                               corner_radius=10, border_width=1, border_color=box_border)
            ctk.CTkLabel(blk, text=label, text_color=label_c,
                         font=("Microsoft YaHei", 12, "bold"), anchor="w"
                         ).pack(fill="x", padx=12, pady=(8, 2))
            box = ctk.CTkTextbox(blk, height=_rows_to_px(heights.get(key, 3)),
                                 fg_color=box_bg, border_width=1,
                                 border_color=box_border, corner_radius=6)
            box.pack(fill="x", padx=6, pady=(0, 6))
            box.bind("<KeyRelease>", self._mark_dirty)
            self._detail_boxes[key] = box
            self._add_group_pairs.append((blk, box))

    def _toggle_add_group(self) -> None:
        """展开/收起 ②~⑦ 补充信息组（内容保留，仅切换整块显隐）"""
        anchor = getattr(self, "_add_group_anchor", None)
        if not self._add_group_open:
            if anchor is not None and anchor.winfo_exists():
                for blk, _box in self._add_group_pairs:
                    blk.pack(fill="x", pady=(4, 0), before=anchor)
            else:
                for blk, _box in self._add_group_pairs:
                    blk.pack(fill="x", pady=(4, 0))
            self._add_group_toggle.configure(text="收起")
            self._add_group_open = True
        else:
            for blk, _box in reversed(self._add_group_pairs):
                blk.pack_forget()
            self._add_group_toggle.configure(text="展开")
            self._add_group_open = False
        self._refresh_detail_header()

    def _reset_new_entry(self) -> None:
        """新增态"重置"：清空表单并回到折叠的默认形态"""
        if not self._adding_new:
            return
        self._name_entry.delete(0, "end")
        for key, box in self._detail_boxes.items():
            box.delete("1.0", "end")
        if self._add_group_open:
            self._toggle_add_group()
        self._detail_dirty = False
        self._name_entry.focus_set()

    def _save_new_entry(self, exit_mode: bool = False) -> bool:
        """保存新增条目；exit_mode=True 供"确认后切换"调用（保存后不连续新增）。

        默认（保存按钮）：保存后停留空白新增态并聚焦名称框，支持连续录入。
        名称缺失时给出提示并返回 False（阻止切换继续）。
        """
        if not self._adding_new:
            return False
        name = self._name_entry.get().strip()
        if not name:
            messagebox.showwarning("提示", "请填写风格名称后再保存新增", parent=self)
            self._name_entry.focus_set()
            return False
        e = Entry(category_id=self._add_target, name=name,
                  intro=self._box_text("intro"), origin=self._box_text("origin"),
                  features=self._box_text("features"), scenes=self._box_text("scenes"),
                  works=self._box_text("works"), image_desc=self._box_text("image_desc"),
                  prompt_cn=self._box_text("prompt_cn"),
                  prompt_en=self._box_text("prompt_en"),
                  image_plan=self._box_text("image_plan"))
        # 2026-09-07：新增前同内容一致性轻提示
        dup = self.db.find_content_duplicates(self.db.content_key(e))
        if dup:
            sample = "、".join(d["name"] for d in dup)
            if not messagebox.askyesno(
                    "内容重复提示",
                    f"检测到 {len(dup)} 个相同内容的条目（示例：{sample}）。\n\n仍要保存新增吗？",
                    parent=self):
                self._name_entry.focus_set()
                return False
        self.db.add_entry(e)
        self._detail_dirty = False
        self._adding_new = False
        self._add_target = None
        self.toast(f"✅ 已新增：{name}")
        self._restore_view()  # 刷新条目列表，使新条目立即出现
        if exit_mode:
            self._show_detail(None)  # 交还给切换流程：清空详情，等待显示目标
            return True
        self._start_new_entry()  # 连续录入：清空并重新进入空白新增态
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
        if self._detail_entry_id is None or self._browse_mode:  # 2026-09-09：浏览态禁止改动
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
        if self._detail_entry_id is None or self._browse_mode:  # 2026-09-09：浏览态禁止改动
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
        # 2026-09-07（阶段2）：条目"关联到/复制到/移动到"入口
        m.add_command(label="关联到…（多选）", state=lock_state,
                      command=lambda: self._link_entry(entry_id))
        m.add_command(label="复制到…（多选）", state=lock_state,
                      command=lambda: self._copy_entry_to_targets(entry_id))
        m.add_command(label="移动到…", state=lock_state,
                      command=lambda: self._move_entry(entry_id))
        ctx = self._current_cat_context()
        if ctx is not None and ctx in self.db.list_entry_locations(entry_id):
            m.add_command(label="解除在本分类的关联", state=lock_state,
                          command=lambda eid=entry_id, cid=ctx:
                          self._unlink_entry(eid, cid))
        m.add_separator()
        m.add_command(label="收藏 / 取消收藏", state=lock_state,
                      command=lambda: self._toggle_favorite(entry_id))
        m.add_command(label="复制提示词（全部）",
                      command=lambda: self._copy_entry(entry_id, _COPY_ALL))
        m.add_separator()
        m.add_command(label="删除", state=lock_state,
                      command=lambda: self._delete_entry(entry_id))
        m.tk_popup(event.x_root, event.y_root)

    # 2026-09-07 阶段2：条目 关联到 / 复制到 / 移动到 / 解除本分类关联
    def _current_cat_context(self):
        """当前条目列表所在的分类 id；未分类/搜索/常用等视图返回 None"""
        if self._view and self._view[0] == "cat":
            return self._cur_cat_id
        return None

    def _pick_entry_targets(self, mode: str, entry_id: int):
        """弹出目标分类选择器（link/copy 多选）；返回选中 id 列表，取消返回 None。

        已属该条目的位置自动带"（已在）"标记并在确定时剔除。
        """
        exclude = self.db.list_entry_locations(entry_id)
        dlg = MoveSelector(self, self.db, mode=mode, exclude=exclude)
        self.wait_window(dlg)
        if dlg.result != "ok":
            return None
        return dlg.selected_cat_ids

    def _link_entry(self, entry_id: int) -> None:
        if self._lock_on:
            return
        targets = self._pick_entry_targets("link", entry_id)
        if not targets:
            return
        before = set(self.db.list_entry_locations(entry_id))
        fresh = [c for c in targets if c not in before]
        if not fresh:
            self.toast("所选分类均为该条目已有位置，无需重复关联", color="#D9534F")
            return
        self.db.set_entry_locations(entry_id, add=fresh)
        self._restore_view()
        self.toast(f"✅ 已关联到 {len(fresh)} 个分类")

    def _copy_entry_to_targets(self, entry_id: int) -> None:
        if self._lock_on:
            return
        targets = self._pick_entry_targets("copy", entry_id)
        if not targets:
            return
        for cid in targets:
            self.db.copy_entry_to(entry_id, cid)
        self._restore_view()
        self.toast(f"✅ 已复制到 {len(targets)} 个分类（独立副本）")

    def _unlink_entry(self, entry_id: int, cat_id: int) -> None:
        if self._lock_on:
            return
        self.db.unlink_entry(entry_id, cat_id)
        self._restore_view()
        self.toast("✅ 已解除在本分类的关联")

    def _move_entry(self, entry_id: int) -> None:
        """移动到…：目标单选；可"移入未分类"。

        - 在有当前位置的分类列表里操作：默认"仅从当前位置移出并挂到目标，
          其它关联保留"；勾选"整体转移"则清空其它全部关联。
        - 在未分类/搜索/常用里操作（无当前位置）：按整体转移处理（旧语义）。
        """
        if self._lock_on:  # 2026-08-21（第005条）：锁定时禁止条目移动
            return
        ctx = self._current_cat_context()
        locs = self.db.list_entry_locations(entry_id)
        has_ctx = ctx is not None and ctx in locs
        dlg = MoveSelector(self, self.db, mode="move")
        self.wait_window(dlg)
        if dlg.result == "cancel":
            return
        target = dlg.selected_cat_ids[0] if (dlg.result == "ok"
                                             and dlg.selected_cat_ids) else None
        overall = bool(getattr(dlg, "overall", False))
        if has_ctx and not overall:
            remove = [ctx]               # 仅当前位置移出
            add = [] if target is None else [target]
        else:
            remove = list(locs)          # 整体转移（清空其它位置）
            add = [] if target is None else [target]
        if not remove and not add:
            return
        self.db.set_entry_locations(entry_id, remove=remove, add=add)
        self._restore_view()
        self.toast("✅ 已移动")

    def _delete_entry(self, entry_id: int) -> None:
        if self._lock_on:
            return
        e = self.db.get_entry(entry_id)
        if not e:
            return
        # 2026-09-07（阶段3）：多位置条目的删除会从全部位置消失，明确提示影响范围
        locs = self.db.list_entry_locations(entry_id)
        if len(locs) > 1:
            names = "、".join(self._cat_label(c) for c in locs[:6])
            more = "" if len(locs) <= 6 else f" 等 {len(locs)} 处"
            scope = (f"\n\n该条目当前同时存在于 {len(locs)} 个位置（{names}{more}），"
                     "删除后将在全部位置消失。")
        else:
            scope = ""
        # 2026-09-07（第2条改进）：删除＝移入回收站，可随时从"删除历史/回收站"恢复
        if not messagebox.askyesno("删除确认",
                                   f"⚠️ 确定要删除【{e['name']}】吗？{scope}"):
            return
        if not messagebox.askyesno("再次确认",
                                   "确定移入回收站？\n（可从详情区下方“♻ 删除历史/回收站”恢复）"):
            return
        self.db.trash_entry(entry_id, reason="手动删除")
        self._restore_view()
        if self._detail_entry_id == entry_id:
            self._show_detail(None)
        self.toast("已删除（可到回收站恢复）")

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
        self._apply_lock_state(rebuild_nav=True)  # 2026-09-07：仅锁定切换时重建导航列
        self.toast("已开启全局锁定，删除功能已禁用" if self._lock_on else "已解除锁定")

    def _set_child_states(self, widget, state: str) -> None:
        """递归设置某容器下所有控件的 state（锁定状态切换用，2026-08-21 第005条新增）"""
        for child in widget.winfo_children():
            try:
                child.configure(state=state)
            except Exception:
                pass
            self._set_child_states(child, state)

    def _apply_lock_state(self, rebuild_nav: bool = False) -> None:
        """锁定：只能查询和复制，禁止移动/删除/新增/重命名/导入/编辑保存等（2026-08-21 第005条扩展）。

        rebuild_nav（2026-09-07 第2条改进）：仅在锁定开关真正切换时才重建 4 个导航列；
        平时展示/切换条目也会调用本方法刷新详情控件状态，若每次都整列重建，
        会造成"悬浮逐级选择反应迟钝"与导航滚动位置被顶回顶部。
        """
        locked = self._lock_on
        state = "disabled" if locked else "normal"
        for attr in ("del_btn", "fav_btn", "move_btn", "link_btn", "copyto_btn",
                     "save_btn", "reset_btn",
                     "import_btn", "quick_add_btn"):
            w = getattr(self, attr, None)
            if w is not None and w.winfo_exists():
                w.configure(state=state)
        # 详情名称输入框 + 详情滚动区编辑控件（复制按钮不受影响）
        # 名称输入框已随表单放入详情区，lock 状态由 detail_scroll 子控件统一处理
        # （此处仅防御性兜底，widget 可能因重建已销毁）
        ne = getattr(self, "_name_entry", None)
        if ne is not None:
            try:
                if ne.winfo_exists():
                    ne.configure(state=state)
            except Exception:
                pass
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
        # 2026-09-07：锁定开关真正切换时才重建 4 个导航列（避免每次显示条目的重建开销）
        if rebuild_nav:
            self._refresh_projects()
            self._refresh_l0()
            self._refresh_l1()
            self._refresh_l2()
        # 2026-09-06：同步条目区"新增条目"按钮（随锁定/视图启用置灰）
        if self._add_entry_btn is not None and self._add_entry_btn.winfo_exists():
            self._add_entry_btn.configure(
                state="normal" if self._add_available() else "disabled")
        # 2026-09-06：新增条目态下 收藏/删除/移动/关联/复制 保持禁用（解锁后不误恢复）
        if self._adding_new and not locked:
            for w in (self.del_btn, self.fav_btn, self.move_btn, self.link_btn,
                      self.copyto_btn, self.copy_all_btn,
                      self.copy_cn_btn, self.copy_en_btn):
                try:
                    w.configure(state="disabled")
                except Exception:
                    pass
        # 2026-09-07（阶段2/第3条）：无当前条目时 删除/关联到/复制到/移动到 保持禁用
        # （删除按钮在详情区底部常驻栏，无条目时应为置灰而非"点了没反应"）
        # 2026-09-09（P2-9）：无条目时 收藏/复制全部/中文/英文/保存/重置 一并置灰，
        # 避免"看似可用实则空操作"。
        if not locked and not self._adding_new and self._detail_entry_id is None:
            for w in (self.del_btn, self.move_btn, self.link_btn, self.copyto_btn,
                      self.fav_btn, self.copy_all_btn, self.copy_cn_btn,
                      self.copy_en_btn, self.save_btn, self.reset_btn):
                try:
                    w.configure(state="disabled")
                except Exception:
                    pass
        # 2026-09-09（修正）：复制全部/中文/英文——只要有当前条目（编辑、浏览、锁定态都允许
        # 复制）就保持可用；此前只有"禁用侧"逻辑没有恢复侧，走过新增/无条目流程后按钮会一直
        # 灰显。这里每次状态刷新都统一按"有当前条目且非新增"来设置。
        copy_ok = self._detail_entry_id is not None and not self._adding_new
        for w in (self.copy_all_btn, self.copy_cn_btn, self.copy_en_btn):
            try:
                w.configure(state="normal" if copy_ok else "disabled")
            except Exception:
                pass
        # 2026-09-09：锁定/无条目状态处理完后再应用"浏览只读"，避免互相覆盖
        self._apply_browse()

    # ------------------------------------------------------------------ #
    # 浏览 / 编辑 切换（2026-09-09：避免浏览与编辑混用时的误操作）
    # ------------------------------------------------------------------ #
    def _on_edit_mode_change(self, value: str) -> None:
        """编辑/浏览分段开关回调：浏览=文本只读（可选中复制）+ 禁用编辑类按钮。"""
        self._browse_mode = (value == "👁 浏览")
        if self._browse_mode:
            self._apply_browse()
            self.toast("已切换为浏览模式（详情内容只读，可选中复制）", color="#25639c")
        else:
            self._apply_lock_state()  # 恢复编辑可用态（按钮/文本框状态由锁定等逻辑统一管理）
            self._apply_browse()
            self.toast("已切换为编辑模式")

    def _apply_browse(self) -> None:
        """按当前"浏览/编辑"模式刷新详情编辑控件状态（可重复调用，幂等）。

        - 浏览模式（已选条目且非新增）：名称与全部文本框只读但可选中复制
          （_set_boxes_readonly 只做键盘屏蔽，不影响鼠标选区与 Ctrl+C）；
          保存/重置/删除/移动/关联到/复制到 按钮禁用，收藏与复制提示词不受影响。
        - 编辑/锁定/无条目：只读标志复位；按钮启用状态交由 _apply_lock_state 管理。
        """
        browsing = (self._browse_mode and not self._adding_new
                    and self._detail_entry_id is not None)
        ds = getattr(self, "detail_scroll", None)
        if ds is not None and ds.winfo_exists():
            _set_boxes_readonly(ds, browsing)
        if browsing:
            for w in (self.save_btn, self.reset_btn, self.del_btn,
                      self.move_btn, self.link_btn, self.copyto_btn):
                try:
                    w.configure(state="disabled")
                except Exception:
                    pass

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
        """分类删除保护（2026-09-07 阶段3 接入 UI）：
        - 有子分类：不允许直接删除，可进入"级联删除"（需输入确认短语）；
        - 无子分类：安全删除——仅删除该分类，其直挂条目解除本位置
          （无其它位置的条目转「未分类」，条目数据保留）。
        """
        if self._lock_on:
            return
        cat = self.db.get_category(cat_id)
        if not cat:
            return
        if self.db.category_has_children(cat_id):
            stat = self.db.count_descendants(cat_id)
            if not messagebox.askyesno(
                    "删除分类",
                    f"【{cat['name']}】下仍有 {stat['categories']} 个子分类，不能直接删除。\n\n"
                    "请先处理下级分类；若需连同其全部下级与相关条目一并删除，"
                    "请点【是】进入级联删除（需输入确认短语）。"):
                return
            self._cascade_delete_category(cat_id, cat)
            return
        direct = len(self.db.list_entries(cat_id))
        if not messagebox.askyesno(
                "删除确认",
                f"确定删除分类【{cat['name']}】？\n"
                f"其 {direct} 条本级条目仅解除在本分类的关联"
                "（无其它位置的条目将转「未分类」，条目数据保留）。"):
            return
        self.db.delete_category_safe(cat_id)
        self.toast("✅ 分类已删除（直挂条目已解除/转未分类）")
        self._after_delete_category(cat)

    def _cascade_delete_category(self, cat_id: int, cat: dict) -> None:
        """级联删除整棵：分类结构永久删除；其中直挂条目先移入回收站（可恢复），
        输入确认短语 → 再次确认 → 执行。"""
        stat = self.db.count_descendants(cat_id)
        phrase = self.db._CASCADE_PHRASE
        got = simpledialog.askstring(
            "级联删除确认",
            f"将删除：\n【{cat['name']}】及其全部下级分类（共 {stat['categories'] + 1} 个分类）。\n"
            f"其中直挂条目 {stat['entries']} 条将移入回收站（可在“删除历史/回收站”中恢复，"
            "图片文件保留至回收站彻底清除）。\n\n"
            f"请输入确认短语「{phrase}」以继续：",
            parent=self)
        if not got:
            return
        if got.strip() != phrase:
            messagebox.showwarning("已取消", "确认短语不正确，删除已取消。")
            return
        if not messagebox.askyesno(
                "最后确认",
                "🚨 再次确认：分类及其全部下级分类结构将【永久删除】；\n"
                "相关条目移入回收站（彻底清除前可恢复）。确定删除？"):
            return
        self.db.delete_category_cascade(cat_id, phrase)
        self.toast("✅ 已级联删除（条目已入回收站）")
        self._after_delete_category(cat, reset_view=True)

    def _after_delete_category(self, cat: dict, reset_view: bool = False) -> None:
        """删除分类后刷新导航；若正在浏览被删分类（或级联删除了其子树）则回到根目录视图"""
        if cat["parent_id"] is None:
            self._refresh_l1()
        else:
            self._refresh_l2()
        if reset_view or self._cur_cat_id == cat["id"]:
            self._cur_cat_id = None
            if self._cur_domain_id:
                self._view = ("domain", self._cur_domain_id)
                self._clear_frame(self.l2_frame)
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
        # 2026-09-08（V1.7.0）：含删除清单的"变更包"一律走导入向导，防止盲目导入删除
        try:
            summary = json_io.read_pack_summary(path)
        except Exception as exc:
            messagebox.showerror("导入失败", f"文件无法读取：{exc}", parent=self)
            return
        if summary.get("del_total") or summary.get("type") == "change":
            self._run_change_import(path, summary)
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
        rec = result.get("recovered", 0)
        if result.get("mode") == "reverse":
            msg = (f"✅ 逆向恢复完成：恢复已删除 {rec} 条"
                   f"（另新增/修改 {result.get('entries', 0)} 条）")
            if result.get("skipped"):
                msg += f"，跳过重复 {result['skipped']} 条"
        elif rec:
            msg += f"，其中逆向恢复 {rec} 条"
        d = result.get("deleted")
        if d and (d.get("entries") or d.get("categories") or d.get("domains")):
            msg += (f"，同步删除 条目{d.get('entries', 0)}/分类{d.get('categories', 0)}/"
                    f"根目录{d.get('domains', 0)}（条目已移入回收站可恢复）")
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
    # 变更包：导入向导 / 导出浏览（2026-08-29 M4；2026-09-08 V1.7.0 更名+安全导入）
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

    def _import_change_pack(self) -> None:
        """导入变更包（2026-09-08 V1.7.0）：先预览摘要与执行范围，再执行。"""
        if self._lock_on:
            return
        path = filedialog.askopenfilename(title="选择变更包 JSON 文件", parent=self,
                                          filetypes=[("变更包/JSON", "*.json")])
        if not path:
            return
        if not self._confirm_unsaved():
            return
        try:
            summary = json_io.read_pack_summary(path)
        except Exception as exc:
            messagebox.showerror("导入失败", f"文件无法读取：{exc}", parent=self)
            return
        self._run_change_import(path, summary)

    def _run_change_import(self, path: str, summary: dict) -> None:
        """变更包导入向导主流程：预览→选择范围→确认→自动快照→导入。"""
        last_sync = self.db.get_meta(config.META_INCR_LAST_SYNC) or ""
        day = summary.get("day") or ""
        behind = bool(last_sync and day and last_sync[:10] < day)
        dlg = ChangeImportDialog(self, summary, behind)
        self.wait_window(dlg)
        if dlg.result is None:
            return  # 用户取消
        apply_additions, del_mode = dlg.result

        # 2026-09-09（审核 P1-5）：正常应用删除且含分类删除时，先披露目标端子树实际影响
        if del_mode == "apply" and summary.get("del_categories"):
            if not self._disclose_sync_delete_impact(path):
                self.toast("已取消导入", color="#D9534F")
                return

        # 导入前自动快照（可回滚；独立前缀 prompts_preimport_*，不参与自动清理）
        snap = backup_mod.preimport_snapshot(self.db.db_path)
        if not snap.get("ok"):
            messagebox.showwarning(
                "导入中止", f"无法生成导入前快照，已取消本次导入：{snap.get('error')}",
                parent=self)
            return

        del_units = summary.get("del_total", 0) if del_mode == "apply" else 0
        rec_units = summary.get("deleted_snapshots", 0) if del_mode == "reverse" else 0
        total = max(int(summary.get("add_entries", 0))
                    + max(int(del_units), int(rec_units)), 1)
        tip = ("（删除将先移入回收站）" if del_mode == "apply"
               else "（删除数据将反向重新导入）" if del_mode == "reverse" else "")
        pd = ProgressDialog(self, total=total,
                            message="正在导入变更包…" + tip)
        try:
            result = json_io.import_json(
                self.db, path,
                progress_cb=pd.update_progress,
                deletion_mode=del_mode,
                apply_additions=apply_additions)
            self.refresh_domains()
            self.toast(self._import_done_msg(result))
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc), parent=self)
        finally:
            pd.finish()

    def _disclose_sync_delete_impact(self, path: str) -> bool:
        """同步删除分类前，披露目标端子树实际影响（2026-09-09 审核 P1-5）。

        对变更包 deleted_categories 中“本库确实存在”的分类，统计现存子树规模
        （子分类/相关条目），向用户披露（可能含目标机独有内容）后再决定是否继续。
        无法解析文件或不存在目标分类时直接放行。
        """
        try:
            import json as _json
            data = _json.load(open(path, encoding="utf-8"))
        except Exception:
            return True
        seen = set()
        sub_cats = 0
        sub_entries = 0
        for dc in data.get("deleted_categories", []):
            chain = dc.get("chain") or []
            if not chain:
                continue
            cid = self.db.find_category_by_chain(chain)
            if cid is None or cid in seen:
                continue
            st = self.db.count_descendants(cid)
            # count_descendants：categories=子分类数，entries=子树内直挂条目数；
            # 父子链可能重叠统计，故文案注明“最大影响面（约）”
            sub_cats += st.get("categories", 0)
            sub_entries += st.get("entries", 0)
            seen.add(cid)
        if not (sub_cats or sub_entries):
            return True
        msg = ("同步删除分类将同时影响本库现存数据（最大影响面约）：\n"
               f"· 现存子分类 {sub_cats} 个\n"
               f"· 现存相关条目 {sub_entries} 条（将转入“未分类”保留）\n\n"
               "其中可能包含目标机独有、源机没有的子分类/内容。仍继续删除？")
        return messagebox.askyesno("删除范围披露", msg, parent=self)

    def _export_incremental_browse(self, fmt: str) -> None:
        """把选中的变更包 JSON 导出为 Excel/HTML 浏览文件（导入临时库后导出，不改主库）"""
        if self._lock_on:
            return
        src = filedialog.askopenfilename(
            title="选择变更包 JSON 文件", parent=self,
            filetypes=[("变更包/JSON", "*.json"), ("JSON", "*.json")])
        if not src:
            return
        if fmt == "excel":
            out = filedialog.asksaveasfilename(
                title="另存为 Excel", parent=self, defaultextension=".xlsx",
                initialfile="change_pack_export.xlsx",
                filetypes=[("Excel 工作簿", "*.xlsx")])
        else:
            out = filedialog.asksaveasfilename(
                title="另存为 HTML", parent=self, defaultextension=".html",
                initialfile="change_pack_export.html",
                filetypes=[("HTML 页面", "*.html")])
        if not out:
            return
        try:
            n = export_incremental_to(self.db, src, out, fmt)
        except Exception as exc:
            messagebox.showerror("导出失败", f"无法导出：{exc}", parent=self)
            return
        self.toast(f"✅ 已导出 {n} 条供浏览")

    def _find_today_change_pack(self) -> Optional[str]:
        """定位当日变更包文件（新前缀优先，兼容旧"增量"前缀遗留）。无则返回 None"""
        code = get_computer_code(self.db)
        day = datetime.now().strftime("%Y-%m-%d")
        d = incr_dir(self.db)
        if not os.path.isdir(d):
            return None
        for prefix in (config.INCR_FILE_PREFIX, config.INCR_LEGACY_PREFIX):
            hits = [os.path.join(d, f) for f in os.listdir(d)
                    if f.startswith(f"{prefix}_{code}_{day}_") and f.endswith(".json")]
            if hits:
                return max(hits, key=os.path.getmtime)
        return None

    def _export_incremental_file_to(self) -> None:
        """定位/复制当日变更包文件到指定位置（导出前披露增删计数）。"""
        src = self._find_today_change_pack()
        if not src:
            messagebox.showinfo("提示", "今日暂无变更包文件"
                                       "（无当日变更数据时不会生成）。", parent=self)
            return
        # 导出前披露：从文件名计数 + 包内 summary 双向校验
        try:
            summary = json_io.read_pack_summary(src)
        except Exception:
            summary = {}
        add_n = summary.get("add_entries", 0)
        del_n = summary.get("del_entries", 0)
        desc = f"文件：{os.path.basename(src)}\n新增/修改条目：{add_n}  删除条目：{del_n}"
        if summary.get("del_total"):
            desc += (f"\n（另含删除 分类 {summary.get('del_categories', 0)} / "
                     f"根目录 {summary.get('del_domains', 0)}）")
        if add_n == 0 and del_n > 0:
            messagebox.showwarning("⚠ 负增量（纯删除包）",
                                   desc + "\n\n该变更包为当日仅删除数据的“负增量”包，"
                                          "请谨慎分发/导入！", parent=self)
        else:
            messagebox.showinfo("导出变更包", desc + "\n\n确定导出该文件吗？")
        out = filedialog.asksaveasfilename(
            title="导出当日变更包文件到", parent=self,
            initialfile=os.path.basename(src), defaultextension=".json",
            filetypes=[("JSON", "*.json")])
        if not out:
            return
        try:
            shutil.copy2(src, out)
            self.toast("✅ 已导出当日变更包文件")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)

    def _compare_with_backup(self) -> None:
        """数据比对（只读诊断，V1.7.0）：与备份 *.db 对比当前库差异。"""
        if self._lock_on:
            return
        from .compare_dialog import CompareDialog
        CompareDialog(self, self.db)

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
        """销毁前确认未保存修改 + 保存设置 + 生成当日变更包（失败不阻塞退出）。

        2026-09-09（审核 P1-4 修复）：标题栏 X / 托盘"退出"（均最终走到 destroy）
        也会先弹出与切换时一致的"未保存修改"确认，避免静默丢弃正在编辑的内容。
        """
        if not getattr(self, "_closing_ok", False):
            try:
                if not self._confirm_unsaved():
                    return  # 用户取消关闭
            except Exception:
                pass  # 确认过程异常时不阻断关闭（避免退不出去）
            self._closing_ok = True
        # 2026-09-10（用户要求）：退出前取消未到点的搜索防抖定时器，避免回调打到已销毁窗口
        self._cancel_search_timer()
        try:
            self._save_settings()
        except Exception:
            pass
        try:  # 2026-08-29（M4）：每次关闭软件时执行每日变更包备份
            r = write_incremental(self.db)
            if not r["ok"] and r.get("error"):
                print(f"[变更包] 失败（不阻塞退出）：{r['error']}")
        except Exception as exc:
            print(f"[变更包] 失败（不阻塞退出）：{exc}")
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

    # ------------------------------------------------------------------ #
    # 删除历史/回收站 与 新增历史（2026-09-07 第2/3条改进）
    # ------------------------------------------------------------------ #
    def _open_recycle(self) -> None:
        """打开"删除历史 / 回收站"：可恢复被删条目，或彻底删除/清空"""
        from .history_dialog import RecycleBinDialog
        dlg = RecycleBinDialog(self, self.db, locked=self._lock_on)
        self.wait_window(dlg)

    def _open_recent_adds(self) -> None:
        """打开"新增历史"：查看最近新增的条目"""
        from .history_dialog import RecentAdditionsDialog
        RecentAdditionsDialog(self, self.db)

    def show_and_focus_search(self) -> None:
        """全局热键/托盘回调：恢复窗口并聚焦搜索框"""
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.attributes("-topmost", False)
        self.after(60, self.search_entry.focus_set)
