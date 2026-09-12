# -*- coding: utf-8 -*-
"""
settings_dialog.py - 用户设置对话框
创建日期：2026-08-18（"设置"入口新增）

设置项（持久化到数据库 meta 表）：
  1. 记住窗口大小（开关）：关闭/退出时保存窗口大小，下次启动恢复
  2. 默认视图模式：卡片 / 列表
  3. 详情字段显示策略：自动（按根目录）/ 全部显示 / 精简（隐藏 ③-⑦）

用法：SettingsDialog(master, db) —— 确定后写回 meta 并调用 master.apply_settings()。
"""
import os
import sys

import customtkinter as ctk

from .. import config


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, master, db):
        super().__init__(master)
        self.db = db
        self.master = master

        self.title("⚙ 设置")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        # 读取当前设置
        self._remember = db.get_meta(config.META_REMEMBER_SIZE) != "0"
        self._view_mode = db.get_meta(config.META_VIEW_MODE) or "card"
        self._detail_mode = db.get_meta(config.META_DETAIL_MODE) or config.DETAIL_MODE_AUTO
        self._computer_code = db.get_meta(config.META_COMPUTER_CODE) or ""
        try:
            self._incr_keep_days = int(db.get_meta(config.META_INCR_KEEP_DAYS)
                                       or config.INCR_KEEP_DAYS)
        except (TypeError, ValueError):
            self._incr_keep_days = config.INCR_KEEP_DAYS
        try:
            self._backup_keep = int(db.get_meta(config.META_BACKUP_KEEP)
                                    or config.BACKUP_KEEP_COUNT)
        except (TypeError, ValueError):
            self._backup_keep = config.BACKUP_KEEP_COUNT
        self._snapshot = db.get_meta(config.META_CHANGE_PACK_SNAPSHOT) == "1"

        self._build()
        self._center()

    def _build(self) -> None:
        pad = 20
        ctk.CTkLabel(self, text="用户设置",
                     font=("Microsoft YaHei", 15, "bold")).grid(
            row=0, column=0, columnspan=2, padx=pad, pady=(16, 4), sticky="w")

        # 1. 记住窗口大小
        ctk.CTkLabel(self, text="记住窗口大小",
                     font=("Microsoft YaHei", 13)).grid(row=1, column=0, padx=pad, pady=8, sticky="w")
        self.sw_size = ctk.CTkSwitch(self, text="关闭时保存，下次启动恢复",
                                     command=self._on_remember_toggle)
        self.sw_size.select() if self._remember else self.sw_size.deselect()
        self.sw_size.grid(row=1, column=1, padx=pad, pady=8, sticky="w")

        # 2. 默认视图模式
        ctk.CTkLabel(self, text="默认视图模式",
                     font=("Microsoft YaHei", 13)).grid(row=2, column=0, padx=pad, pady=8, sticky="w")
        self.seg_view = ctk.CTkSegmentedButton(self, values=["卡片", "列表"], width=180)
        self.seg_view.set("卡片" if self._view_mode == "card" else "列表")
        self.seg_view.grid(row=2, column=1, padx=pad, pady=8, sticky="w")

        # 3. 详情字段显示策略
        ctk.CTkLabel(self, text="详情字段显示",
                     font=("Microsoft YaHei", 13)).grid(row=3, column=0, padx=pad, pady=8, sticky="w")
        self.seg_detail = ctk.CTkSegmentedButton(
            self, values=["自动", "全部显示", "精简"], width=260)
        _map = {config.DETAIL_MODE_AUTO: "自动", config.DETAIL_MODE_FULL: "全部显示",
                config.DETAIL_MODE_COMPACT: "精简"}
        self.seg_detail.set(_map.get(self._detail_mode, "自动"))
        self.seg_detail.grid(row=3, column=1, padx=pad, pady=8, sticky="w")
        ctk.CTkLabel(self, text="自动：视觉风格分类/视频/图像全显示，其余隐藏③-⑦；\n"
                              "全部显示：9 字段始终显示；精简：始终隐藏③-⑦",
                     text_color="gray", justify="left", font=("Microsoft YaHei", 11)
                     ).grid(row=4, column=0, columnspan=2, padx=pad, pady=(0, 4), sticky="w")

        # 4. 电脑代号（增量备份文件名区分多机，2026-08-29 M4 新增）
        ctk.CTkLabel(self, text="电脑代号",
                     font=("Microsoft YaHei", 13)).grid(row=5, column=0, padx=pad, pady=8, sticky="w")
        self.entry_code = ctk.CTkEntry(self, placeholder_text="默认取主机名")
        self.entry_code.grid(row=5, column=1, padx=pad, pady=8, sticky="ew")
        if self._computer_code:
            self.entry_code.insert(0, self._computer_code)
        ctk.CTkLabel(self, text="用于变更包文件名区分不同电脑，可随时修改",
                     text_color="gray", font=("Microsoft YaHei", 11)
                     ).grid(row=6, column=0, columnspan=2, padx=pad, pady=(0, 4), sticky="w")

        # 5. 变更包保留天数（2026-08-29 M4 新增；2026-09-08 V1.7.0 更名）
        ctk.CTkLabel(self, text="变更包保留天数",
                     font=("Microsoft YaHei", 13)).grid(row=7, column=0, padx=pad, pady=8, sticky="w")
        self.entry_keep = ctk.CTkEntry(self, width=120)
        self.entry_keep.grid(row=7, column=1, padx=pad, pady=8, sticky="w")
        self.entry_keep.insert(0, str(self._incr_keep_days))

        # 6. 自动全量备份保留份数（2026-09-08 V1.7.0 备份策略：按天去重 + 份数上限）
        ctk.CTkLabel(self, text="自动备份保留份数",
                     font=("Microsoft YaHei", 13)).grid(row=8, column=0, padx=pad, pady=8, sticky="w")
        self.entry_bkeep = ctk.CTkEntry(self, width=120)
        self.entry_bkeep.grid(row=8, column=1, padx=pad, pady=8, sticky="w")
        self.entry_bkeep.insert(0, str(self._backup_keep))
        ctk.CTkLabel(self, text="同日只保留最后一份；手动快照（snapshot_* 等）永不自动清理",
                     text_color="gray", font=("Microsoft YaHei", 11)
                     ).grid(row=9, column=0, columnspan=2, padx=pad, pady=(0, 4), sticky="w")

        # 7. 变更包是否携带被删快照（2026-09-08 V1.7.0：⑤逆向恢复的前提；默认关）
        ctk.CTkLabel(self, text="变更包携带被删快照",
                     font=("Microsoft YaHei", 13)).grid(row=10, column=0, padx=pad, pady=8, sticky="w")
        self.sw_snapshot = ctk.CTkSwitch(self, text="导出时包含被删条目完整内容（默认关）")
        self.sw_snapshot.select() if self._snapshot else self.sw_snapshot.deselect()
        self.sw_snapshot.grid(row=10, column=1, padx=pad, pady=8, sticky="w")
        ctk.CTkLabel(self, text="⚠ 开启后变更包内会包含“已删除”的内容（体积增大、含敏感数据），\n"
                                "  但可在导入时使用“⑤ 逆向恢复”找回被删数据。",
                     text_color="#D9534F", justify="left", font=("Microsoft YaHei", 11)
                     ).grid(row=11, column=0, columnspan=2, padx=pad, pady=(0, 4), sticky="w")

        # 按钮
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=12, column=0, columnspan=2, sticky="e", padx=pad, pady=(8, 16))
        ctk.CTkButton(btn_row, text="ℹ 关于", width=96, fg_color="#8a94a6",  # 2026-08-21（第006条）：关于入口
                      command=self._show_about).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="确定", width=96, fg_color="#2E8B57",
                      command=self._apply).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="取消", width=96,
                      command=self.destroy).pack(side="left", padx=4)

    def _show_about(self) -> None:
        """关于窗口：软件名、版本、本机运行信息、功能亮点与各版本修订（2026-09-12 完善至 V1.9.0）。

        2026-09-12：修订说明随版本累积变长，内容区改为"可滚动 + 高度不超过屏幕 68%"，
        避免在 1366×768 等小屏上"关闭"按钮被挤出屏幕。
        """
        win = ctk.CTkToplevel(self)
        win.title("关于 PromptSprite")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()
        # 本机运行信息（2026-09-07 新增：运行文件 / 程序路径 / 提示词长度）
        if config.is_frozen():
            exe_name = os.path.basename(sys.executable)      # 打包态：EXE 全名
            exe_dir = os.path.dirname(sys.executable)        # 打包态：EXE 所在目录
            run_desc = f"运行文件：{exe_name}"
            path_desc = f"程序路径：{exe_dir}"
        else:
            script = os.path.basename(sys.argv[0]) if sys.argv else ""
            run_desc = f"运行文件：开发模式（{script}），未打包 EXE"
            path_desc = f"程序路径：{config.PROJECT_ROOT}"
        # P2-10：内置/当前数据动态取数（不硬编码，避免与真实库脱节）
        _st = {}
        try:
            _st = self.db.stats()
        except Exception:
            _st = {}
        data_line = (f"· 当前库数据：{_st.get('projects', 0)} 项目类别 · "
                     f"{_st.get('domains', 0)} 根目录 · "
                     f"{_st.get('entries', 0)} 条提示词（动态统计）\n")
        info = (
            f"{config.APP_NAME} · 提示精灵\n"
            f"版本 V{config.APP_VERSION}（2026-09-12 界面修订版）\n"
            "──────────────────────────\n"
            "本地优先的 AI 提示词管理工具：数据 100% 存在本机，无任何联网行为。\n"
            "· 五级分类书架（项目类别 / 根目录 / 一级分类 / 二级分类 / 条目）\n"
            f"{data_line}"
            "· 条目多位置：关联到（一处编辑各处同步）/ 复制到（独立副本）/ 移动到（可整体转移）\n"
            "· 条目就地新增醒目入口；任一分级分类可持有并显示本级条目\n"
            "· 详情区多位置提示；删除分类三级保护（级联删除需输入确认短语）\n"
            "· 每日变更包（按电脑代号区分，可换机合并导入）；纯删除包有醒目标识、可逆向恢复\n"
            "· 导入向导：新增 / 删除分两维选择（忽略删除 / 应用删除 / 逆向恢复），导入前自动快照\n"
            "· 一键复制 / 新建 / 全局热键 Ctrl+Shift+P 唤起；自动全量备份（按天去重、默认保留 30 份）\n"
            "· JSON / Excel / Markdown / HTML 多格式导入导出；可与备份库数据比对\n"
            "· 老版本数据自动 / 引导迁移（未明确分类兜底）\n"
            "──────────────────────────\n"
            "V1.9.0 界面修订（2026-09-12）：\n"
            "   - 详情区字段浮动提示窗口重构：修复“光标与提示窗重叠时闪烁”；提示窗固定在\n"
            "     详情区左侧、不遮盖正文；隐藏四级目录后自动压窄\n"
            "   - 提示窗增强：内容可滚动（滚动条 / 滚轮）、随文本框同步滚动、光标所在行加深、\n"
            "     字号 14pt、高度最少 18 行 / 最多 30 行\n"
            "   - 新增“💬 打开/关闭浮动提示窗口”总开关：一键关闭“条目名称一览”与\n"
            "     “详情区字段提示”两类浮动提示（按钮 / 导航长名称提示不受影响）\n"
            "   - “🗂”按钮新增浮动提示“打开/关闭四级目录”，并支持两态配色：\n"
            "     目录打开＝绿色、目录关闭＝黄色\n"
            "   - “条目名称一览”浮层文字字号与条目名称一致，最小高度 25 行\n"
            "V1.8.0 界面修订（2026-09-10）：\n"
            "   - 工具栏重排：锁定 · 目录隐藏/显示 · 无类条目 · 常用 · 新建 · 导入 · 导出 · 设置 · 搜索(🔍)\n"
            "   - 🗂 目录隐藏/目录显示：可任选隐藏各分类列（含“全部隐藏/全部显示”），\n"
            "     底部小图标 🗂 一键全隐/全显；窗口最小宽度随之动态调整\n"
            "   - ✏️ 编辑 / 👁 浏览 切换：浏览态详情只读、可选中复制，防误改\n"
            "   - 搜索防抖：输入停顿约 1.5 秒后才查询（点 🔍 或回车可立即搜索）\n"
            "   - 条目列悬停“条目名称一览”浮层：可移入浮层滚动，滚轮与条目区同步\n"
            "   - 详情区 ②~⑦ 整组默认折叠为 1 行；命令条与底部栏按钮重排、宽度贴合文字\n"
            "· 打包版内置精简模板（示例提示词，以实际打包库为准）\n"
            "──────────────────────────\n"
            f"{run_desc}\n"
            f"{path_desc}\n"
            "提示词长度：中文提示词不设固定字符上限——数据库为 SQLite TEXT 类型，\n"
            "             单字段可存上亿字符，超出文本框高度时自动展开/滚动查看\n"
            "──────────────────────────\n"
            "技术栈：Python 3.12 · CustomTkinter · SQLite\n"
            "数据文件：data/prompts.db（随软件目录整体迁移即可换机使用）\n"
            "开源仓库：https://github.com/yihufree/PromptSprite\n"
            "（MIT License，欢迎 Star / Issue）\n"
            "© 2026 PromptSprite 开发组 · 仅供学习与个人使用"
        )
        # 2026-09-12（V1.9.0）：内容区改为"只读文本框 + 原生滚动"，高度取"内容所需"与
        # "屏幕 68%"的较小值，确保小屏（如 1366×768）上"关闭"按钮也不会被挤出屏幕。
        try:
            import tkinter.font as tkfont
            _f = tkfont.Font(root=win, font=("Microsoft YaHei", 13))
            _line_h = _f.metrics("linespace") or 22
            _need_h = (info.count("\n") + 1) * _line_h + 20
            _max_w = max((_f.measure(_ln) for _ln in info.split("\n")), default=420)
        except Exception:
            _need_h, _max_w = 560, 620
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        box = ctk.CTkTextbox(win, width=200, height=200, wrap="word",
                             fg_color="transparent", border_width=0,
                             font=("Microsoft YaHei", 13))
        # CTk 会按系统 DPI 缩放控件尺寸，故按控件实际缩放把"像素目标"换算成控件单位，
        # 否则高 DPI 下高度会被放大、仍可能超出屏幕。
        try:
            scale = float(box._get_widget_scaling()) or 1.0
        except Exception:
            scale = 1.0
        want_w = min(_max_w + 70, int(sw * 0.92), 780)
        want_h = max(min(_need_h, int(sh * 0.68)), 200)
        box.configure(width=want_w / scale, height=want_h / scale)
        box.pack(padx=10, pady=(12, 2))
        box.insert("1.0", info)
        box.configure(state="disabled")
        ctk.CTkButton(win, text="关闭", width=88,
                      command=win.destroy).pack(pady=(6, 14))
        win.update_idletasks()
        ww, wh = win.winfo_reqwidth(), win.winfo_reqheight()
        # 2026-09-12（用户要求）：初始位置改为"窗口上边距屏幕顶端 100 像素"（原为按父窗口垂直居中）；
        # 横向仍在父窗口（设置窗口）内居中；极端小屏时兜底不越出屏幕下边。
        x = self.winfo_x() + (self.winfo_width() - ww) // 2
        y = 100
        if y + wh > sh - 8:
            y = max(sh - wh - 8, 0)
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        win.lift()

    def _on_remember_toggle(self) -> None:
        # 打开"记住窗口大小"时，立即记录当前窗口大小
        if self.sw_size.get() == 1:
            self.db.set_meta(config.META_WINDOW_SIZE, self.master._current_size())

    def _apply(self) -> None:
        self.db.set_meta(config.META_REMEMBER_SIZE, "1" if self.sw_size.get() == 1 else "0")
        view = "card" if self.seg_view.get() == "卡片" else "list"
        self.db.set_meta(config.META_VIEW_MODE, view)
        _rmap = {"自动": config.DETAIL_MODE_AUTO, "全部显示": config.DETAIL_MODE_FULL,
                 "精简": config.DETAIL_MODE_COMPACT}
        self.db.set_meta(config.META_DETAIL_MODE, _rmap.get(self.seg_detail.get(),
                                                           config.DETAIL_MODE_AUTO))
        # 电脑代号（增量备份区分多机，2026-08-29 M4）
        code = self.entry_code.get().strip()
        if code:
            self.db.set_meta(config.META_COMPUTER_CODE, code)
        # 变更包保留天数（2026-08-29 M4；2026-09-08 V1.7.0 更名）
        keep = self.entry_keep.get().strip()
        if keep.isdigit() and int(keep) >= 1:
            self.db.set_meta(config.META_INCR_KEEP_DAYS, str(int(keep)))
        # 自动全量备份保留份数（2026-09-08 V1.7.0）
        bkeep = self.entry_bkeep.get().strip()
        if bkeep.isdigit() and int(bkeep) >= 1:
            self.db.set_meta(config.META_BACKUP_KEEP, str(int(bkeep)))
        # 变更包携带被删快照（2026-09-08 V1.7.0；默认关）
        self.db.set_meta(config.META_CHANGE_PACK_SNAPSHOT,
                         "1" if self.sw_snapshot.get() else "0")
        if hasattr(self.master, "apply_settings"):
            self.master.apply_settings()
        self.destroy()

    def _center(self) -> None:
        """设置窗口初始位置（2026-09-12 用户要求）。

        横向仍在父窗口（主窗口）内居中；**窗口上边固定距屏幕顶端 100 像素**（原为按父窗口
        垂直居中）；并兜底不越出屏幕下边。
        """
        self.update_idletasks()
        x = self.master.winfo_x() + (self.master.winfo_width() - self.winfo_width()) // 2
        y = 100
        sh = self.winfo_screenheight()
        if y + self.winfo_height() > sh - 8:
            y = max(sh - self.winfo_height() - 8, 0)
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.lift()
