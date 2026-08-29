# -*- coding: utf-8 -*-
"""
config.py - PromptSprite 全局配置常量
创建日期：2026-08-12（阶段一：项目初始化与数据地基）
"""
import os
import sys

# ---------- 应用基础信息 ----------
APP_NAME = "PromptSprite"
APP_VERSION = "1.4.0"  # 2026-08-29（施工M6）：四级分类体系 + 老数据迁移 + 增量备份

# ---------- 数据库 / 目录 ----------
DB_FILE_NAME = "prompts.db"
BACKUP_DIR_NAME = "backup"
IMAGES_DIR_NAME = "images"

# 首次建库时预置的根目录（专业领域）
PRESET_DOMAINS = ["计算机编程", "视频", "图像", "音频", "文学", "学术", "专业报告", "视觉风格分类"]

# ---------- 详情字段显示模式（2026-08-18 新增）----------
# 查看以下根目录时，详情区 9 字段全部显示；
# 查看其余根目录（计算机编程/音频/文学/学术/专业报告）时隐藏 ③-⑦，突出提示词内容。
DETAIL_FULL_FIELDS_DOMAINS = ("视觉风格分类", "视频", "图像")
# 需要隐藏的字段键（③溯源 ④核心特征 ⑤应用场景 ⑥代表作 ⑦代表高清配图）
DETAIL_HIDDEN_KEYS = ("origin", "features", "scenes", "works", "image_desc")

# ---------- 内置手册（首次启动自动导入） ----------
# 版本号变更会触发启动时"非破坏性合并"导入：分类按名复用、条目按名 upsert，
# 不再清空任何现有数据（2026-08-18，P0-2 修复）
# 内置手册归入"视觉风格分类"根目录（视频/图像保留为空根目录）
BUILTIN_MANUAL_VERSION = "007-2026-08-18"
BUILTIN_MANUAL_RESOURCE = "resources/builtin_manual.md"
META_BUILTIN_IMPORTED = "builtin_manual_version"

# ---------- 内置完整数据库（打包携带最新数据，2026-08-18 第023条新增） ----------
# 打包时由开发态 data/prompts.db 生成 app/resources/builtin_prompts.db 内嵌进 EXE；
# 打包态首次运行若无 data/prompts.db，则从该资源复制完整最新库（含全部根目录/分类/条目）。
BUILTIN_DB_RESOURCE = "resources/builtin_prompts.db"

# ---------- 全局热键 ----------
GLOBAL_HOTKEY = "ctrl+shift+p"

# ---------- 用户设置（meta 键，2026-08-18 新增"设置"入口）----------
META_REMEMBER_SIZE = "settings_remember_size"   # "1"/"0"：是否记住窗口大小
META_WINDOW_SIZE = "settings_window_size"       # "WxH"：上次窗口大小
META_VIEW_MODE = "settings_view_mode"           # "card"/"list"：默认视图模式
META_DETAIL_MODE = "settings_detail_mode"       # "auto"/"full"/"compact"：详情字段显示策略
DETAIL_MODE_AUTO = "auto"       # 按根目录自动显隐（默认）
DETAIL_MODE_FULL = "full"       # 始终全部显示
DETAIL_MODE_COMPACT = "compact" # 始终精简（隐藏 ③-⑦）

# ---------- 自动备份 ----------
BACKUP_KEEP_COUNT = 5

# ---------- 项目类别（四级分类最高层级，2026-08-29 施工新增） ----------
# 预置项目类别（最高层级）
PROJECT_PRESETS = ["日常学习记录", "网上资源收集", "个人梳理资源", "本人创作作品", "个人经验总结"]
# 迁移时"未命中且用户未回答"的兜底项目类别（惰性创建）
PROJECT_FALLBACK = "未明确分类"
# 根目录名（库中实际名称）→ 项目类别名；迁移前弹出供人工核对（施工方案决策 2）
PROJECT_DOMAIN_MAPPING = {
    "日常学习记录": ["视频", "图像", "音频", "文学", "学术", "专业报告"],
    "网上资源收集": ["海外AI绘画案例库", "AI绘画精选案例", "GPT Image 提示词库", "AI生图提示词大全"],
    "个人梳理资源": ["视觉风格分类"],
    "本人创作作品": ["yifree学习与作品"],
    "个人经验总结": ["计算机编程"],
}

# ---------- 增量备份（2026-08-29 施工新增） ----------
INCR_DIR_NAME = "incremental"          # data/backup/incremental/
INCR_KEEP_DAYS = 30                    # 增量备份文件按日保留天数（用户确认）
INCR_FILE_PREFIX = "增量"
META_COMPUTER_CODE = "settings_computer_code"   # 电脑代号
META_INCR_LAST_SYNC = "incr_last_sync"          # 增量备份游标
META_INCR_KEEP_DAYS = "settings_incr_keep_days" # 增量备份保留天数（用户设置，默认 INCR_KEEP_DAYS）
META_MIGRATE_MAPPING_VER = "migrate_mapping_version"  # 迁移映射表版本
META_MIGRATE_WIZARD_DISMISSED = "migrate_wizard_dismissed"  # 迁移向导是否已取消过（避免每次启动打扰）

# 项目根目录：config.py 位于 app/ 下，取其上级
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_frozen() -> bool:
    """是否处于 PyInstaller 打包态"""
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> str:
    """只读资源基目录：打包态为 PyInstaller 解压目录 sys._MEIPASS"""
    if is_frozen():
        return sys._MEIPASS
    return PROJECT_ROOT


def data_dir() -> str:
    """可写数据目录：打包态为 exe 同目录下 data/，开发态为项目根下 data/"""
    if is_frozen():
        return os.path.join(os.path.dirname(sys.executable), "data")
    return os.path.join(PROJECT_ROOT, "data")


def builtin_manual_path() -> str:
    """内置手册路径：打包态在解压目录 resources/ 下，开发态在 app/resources/ 下。

    修复（2026-08-12）：打包态曾误拼为 resources/resources/… 导致 EXE 首次导入失败，
    现直接用 BUILTIN_MANUAL_RESOURCE（已含 resources/ 前缀）。
    """
    if is_frozen():
        return os.path.join(resource_dir(), BUILTIN_MANUAL_RESOURCE)
    return os.path.join(PROJECT_ROOT, "app", BUILTIN_MANUAL_RESOURCE)


def builtin_db_path() -> str:
    """内置完整数据库路径（2026-08-18 第023条新增）：
    打包态在解压目录 resources/ 下（EXE 内嵌的最新完整库）；
    开发态直接返回主库路径（无需额外资源，本方法仅打包态首次建库使用）。
    """
    if is_frozen():
        return os.path.join(resource_dir(), BUILTIN_DB_RESOURCE)
    return os.path.join(data_dir(), DB_FILE_NAME)
