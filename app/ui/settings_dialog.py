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

        # 按钮
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=5, column=0, columnspan=2, sticky="e", padx=pad, pady=(8, 16))
        ctk.CTkButton(btn_row, text="ℹ 关于", width=96, fg_color="#8a94a6",  # 2026-08-21（第006条）：关于入口
                      command=self._show_about).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="确定", width=96, fg_color="#2E8B57",
                      command=self._apply).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="取消", width=96,
                      command=self.destroy).pack(side="left", padx=4)

    def _show_about(self) -> None:
        """关于窗口：软件名、版本、功能亮点与技术信息（2026-08-22 第008条修订）"""
        win = ctk.CTkToplevel(self)
        win.title("关于 PromptSprite")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()
        info = (
            f"{config.APP_NAME} · 提示精灵\n"
            f"版本 V{config.APP_VERSION}（2026-08-22 发布）\n"
            "──────────────────────────\n"
            "本地优先的 AI 提示词管理工具：\n"
            "· 四级分类书架（根目录/分类/子分类/条目），极速检索\n"
            "· 一键复制 / 快速新建 / 全局热键 Ctrl+Shift+P 唤起\n"
            "· JSON / Excel / Markdown / HTML 多格式导入导出\n"
            "· 自动备份（保留最近 5 份），数据 100% 本机存储，无任何联网行为\n"
            "──────────────────────────\n"
            "技术栈：Python 3.12 · CustomTkinter · SQLite\n"
            "数据文件：data/prompts.db（随软件目录整体迁移即可换机使用）\n"
            "© 2026 PromptSprite 开发组 · 仅供学习与个人使用"
        )
        ctk.CTkLabel(win, text=info, font=("Microsoft YaHei", 13),
                     justify="left").pack(padx=24, pady=(20, 8))
        ctk.CTkButton(win, text="关闭", width=88,
                      command=win.destroy).pack(pady=(4, 16))
        win.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - win.winfo_width()) // 2
        y = self.winfo_y() + (self.winfo_height() - win.winfo_height()) // 2
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
        if hasattr(self.master, "apply_settings"):
            self.master.apply_settings()
        self.destroy()

    def _center(self) -> None:
        self.update_idletasks()
        x = self.master.winfo_x() + (self.master.winfo_width() - self.winfo_width()) // 2
        y = self.master.winfo_y() + (self.master.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.lift()
