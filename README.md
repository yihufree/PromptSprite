# 🧩 PromptSprite（提示精灵）

> AI 提示词管理工具 —— 把杂乱无章的 Prompt 文档，变成 **五级分类书架 + 极速检索 + 一键复制 + 每日增量备份** 的本地神器。

![Platform](https://img.shields.io/badge/Platform-Windows_10%2F11-blue)
![Version](https://img.shields.io/badge/Version-1.4.0-blueviolet)
![Python](https://img.shields.io/badge/Python-3.10%2B-green)
![UI](https://img.shields.io/badge/UI-CustomTkinter-2E8B57)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Data](https://img.shields.io/badge/Data-3934_prompts-orange)

双击 `PromptSprite.exe` 即可使用。数据全部保存在本机，**无任何联网行为**。

---

## ✨ 功能特性

| 特性 | 说明 |
| ---- | ---- |
| ① 五级分类书架 | **项目类别 → 根目录 → 一级 → 二级 → 条目**；项目类别预置 5 项（日常学习记录/网上资源收集/个人梳理资源/本人创作作品/个人经验总结），根目录跨项目移动/复制 |
| ② 老数据迁移 | 旧版（根目录-一级-二级）数据升级：迁移向导人工核对映射、未命中弹窗选择、未回答兜底"未明确分类"（自动弹出/菜单重开） |
| ③ 9 字段详情 | ①名称…⑩图像方案；按根目录动态显隐；⑩图像方案可"打开"链接；编辑/悬停浮窗/图片预览 |
| ④ 一键复制 | 复制全部 / 复制中文 / 复制英文 |
| ⑤ 快捷悬停添加 | 悬停滑过分类即锁定，光标自动跳入输入框，连续录入不打断 |
| ⑥ 悬停选中与高亮 | 主界面"鼠标悬浮 0.3 秒即选中"，选中链路高亮；状态栏按悬停层级动态统计 |
| ⑦ 安全回收 | 全局锁定（🔒）+ 删除双重确认 + 删除分类条目转"未分类" + 删除根目录仅解除关联 |
| ⑧ 收藏与搜索 | ⭐常用入口；全局搜索实时过滤全部文本字段（LIKE 通配符转义） |
| ⑨ 导入导出 | JSON（v2/v3 兼容，v3 含项目归属）/ Excel / HTML / MD（进度条）；导入按内容自动去重；批量事务导入 |
| ⑩ 自动全量备份 | 每次启动自动备份，仅保留最近 5 份，失败状态栏警告 |
| ⑪ 每日增量备份 | 每次关闭自动生成"当日变更"JSON v3（`增量_{电脑代号}_{日期}.json`，幂等重生成、保留 30 天可设）；**覆盖 增/删/改/空分类 全同步**；可换机导入合并、可导出 Excel/HTML 浏览 |
| ⑫ 全局热键/托盘 | `Ctrl+Shift+P` 唤出聚焦搜索；`ESC` 隐藏；托盘恢复/退出 |
| ⑬ 内置数据 | 首次启动自动导入内置完整库（14 根目录 / 3934 条）；升级"非破坏性合并"不丢用户数据 |
| ⑭ 用户设置 | 记住窗口大小、默认视图、详情字段策略、电脑代号、增量保留天数；"ℹ 关于"显示版本信息 |
| ⑮ 分类复制/移动 | 根目录/一级/二级可"复制到…/移动到…"：下移加前缀（A→A.C）、上移去前缀、重名自动加序号、共享分类自动复制、移动不丢条目 |
| ⑯ 界面优化 | 导航列窄宽 + **长名称悬停提示** + **🗂 项目列一键折叠/展开** |
| ⑰ 一键打包 | `python build.py` 自动生成单文件 EXE |

## 📊 内置数据（开箱即用，14 根目录 / 3934 条）

| 项目类别 | 根目录 | 条目数 |
| ---- | ---- | ---- |
| 日常学习记录 | 视频 / 图像 / 音频 / 文学 / 学术 / 专业报告 | 1160 |
| 网上资源收集 | 海外AI绘画案例库 / AI绘画精选案例 / GPT Image 提示词库 / AI生图提示词大全 | 2555 |
| 个人梳理资源 | 视觉风格分类（内置手册） | 90 |
| 个人梳理资源 | 动画电影视觉风格全息图谱 | 93 |
| 本人创作作品 | yifree学习与作品 | 12 |
| 个人经验总结 | 计算机编程 | 23 |
| 未分类 | — | 1 |
| **合计** | **14** | **3934** |

数据来源：moosl/awesome-gpt-image-2-prompts、davidwuw0811-boop、ZeroLu/awesome-gpt-image、whataicc/aiprompt、CSDN 提示词拆解案例、preangelleo/illustration-style-samples、roblaughter/style-reference、andygock/stable-diffusion-style-cheatsheet、some9000/StylePile、vitthalsawant/Sketch-AI 等（均已标注 origin 溯源字段）。

## 🖥️ 界面速览

```
┌────────────────────────────────────────────────────────────────────┐
│ 🧩 PromptSprite │ 🔒锁定 │ 📂未分类 │ ⭐常用 │ [搜索] │ ⇩导入 ⇧导出 ✚快速新建 ⚙设置 │
├────────┬────────┬────────┬────────┬───────────────────────────────┤
│ 项目类别│ 根目录 │ 一级   │ 二级   │ 条目(卡片/列表) ｜ 详情(可编辑)  │
│ 个人梳理│ 视觉风格│ 按媒介 │ 写实   │ 35mm电影胶片风 ｜ 名称/收藏/删除  │
│ 网上资源│ 图像   │ 按地域 │ 国风   │ 赛博朋克人像    │ 复制/中/英 保存 │
│ …      │ …     │ …     │ …     │ …              │ …              │
└────────┴────────┴────────┴────────┴───────────────────────────────┘
│ 底部状态栏：总提示词数 3934｜项目类别 5｜根目录 14｜一级 120｜二级 229   │
└────────────────────────────────────────────────────────────────────┘
```

## 🚀 快速开始

### 方式一：直接使用（普通用户）
1. 从 [Releases](../../releases) 下载 `PromptSprite.exe`；
2. 双击运行（首次自动建库并导入内置提示词）；
3. 详细图文说明见 [《软件安装使用说明书 V6》](docs/20260829_软件安装使用说明书_v6.md)。

### 方式二：源码运行（开发者）
```powershell
# 1. 克隆仓库
git clone https://github.com/<你的用户名>/PromptSprite.git
cd PromptSprite

# 2. 创建虚拟环境并安装依赖
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 3. 启动（以下三种方式等价）
python app/main.py        # 直接运行主文件（推荐）
python run.py             # 根目录启动器
python -m app.main --smoke    # 冒烟测试：5 秒后自动退出
```

## 📦 打包发布
```powershell
python build.py           # 一键打包（自动装依赖/生成图标/PyInstaller 单 EXE）
```
产物：`dist\PromptSprite.exe`（单文件、无控制台窗口）。详见 [《打包发布指南 V7》](docs/20260829_打包发布指南_v7.md)。

## 🧱 项目结构（各文件功能）
```
PromptSprite/
├── app/
│   ├── main.py               # 程序入口：启动编排（备份→建库→迁移向导弹出→热键/托盘）
│   ├── config.py             # 全局常量：版本、目录、项目类别与映射、增量备份、meta 键
│   ├── database.py           # SQLite 数据层 v3：projects/domains/categories/entries/meta/
│   │                         #   deletion_log；迁移(v1→v2→v3)、复制/移动、归属分配、删除日志
│   ├── models.py             # 数据模型：Entry（9 字段 + 图片/收藏/时间戳）
│   ├── backup.py             # 启动静默全量备份（保留最近 5 份，快照不误删）
│   ├── incremental_backup.py # 每日增量备份：按日收集 条目/分类(含空分类)/删除清单，
│   │                         #   生成 JSON v3、换机合并、导出 Excel/HTML、日志清理
│   ├── hotkey.py             # 全局热键 Ctrl+Shift+P + 系统托盘（键盘钩子/托盘图标）
│   ├── self_test.py          # 统一自测入口（database/backup/incremental/md_parser + 冒烟）
│   ├── parser/
│   │   ├── md_parser.py      # Markdown 内置手册解析与"非破坏性合并"导入
│   │   ├── json_io.py        # JSON v2/v3 导入导出（v3 含项目归属 + 删除清单）
│   │   ├── excel_io.py       # Excel 导入导出（read_only 流式 + 判重；新建根目录归未明确分类）
│   │   └── html_export.py    # HTML 分享页导出（转义 + base64 图片）
│   ├── ui/
│   │   ├── main_window.py    # 主窗口：四列导航(项目/根目录/一级/二级)、详情、状态栏、锁定、
│   │   │                     #   导入导出/增量入口、项目列折叠、长名称悬停提示
│   │   ├── quick_add.py      # 快捷悬停添加窗口（四列导航：项目→根目录→一级→二级，悬停锁定、连续录入、长名称提示）
│   │   ├── copy_move_dialog.py # "复制到/移动到"目标树（项目→根目录→分类）与预览
│   │   ├── move_selector.py  # 条目"移动到分类"树形选择器
│   │   ├── migrate_dialog.py # 老版本数据迁移向导（映射核对→执行→报告）
│   │   ├── project_chooser.py # 共用项目类别选择弹窗（主窗口/快速新建复用）
│   │   ├── progress_dialog.py # 导入进度条模态窗口
│   │   └── settings_dialog.py # 设置（窗口/视图/详情/电脑代号/保留天数）+ 关于弹窗
│   └── resources/
│       ├── builtin_manual.md      # 内置手册（90 条）
│       └── builtin_prompts.db     # 内置完整数据库（打包时由 build.py 生成）
├── docs/                     # 需求规格V7 / 审核报告V6 / 说明书V6 / 打包指南V7 / 设计·施工方案 / 开发记录
├── Icons/PSicon.png          # 应用图标源
├── build.py                  # 一键打包（装依赖→生成图标→内嵌最新库→PyInstaller→校验）
├── run.py                    # 根目录启动器（等效 python -m app.main）
├── requirements.txt          # 运行依赖（customtkinter/Pillow/pyperclip/openpyxl/keyboard/pystray）
├── LICENSE / .gitignore / README.md
└── *.json / *.xlsx           # 数据资产文件（可直接导入）
```

## 🗄️ 数据模型（v3）
- 分类为**全局共享树**（`categories`），通过 `domain_category` 实现多领域共享一级分类；
- 新增 **projects** 表（项目类别，最高层级），`domains.project_id` 归属项目类别；
- 删除根目录仅解除关联；删除分类级联子分类、条目转入"未分类"；
- 旧库启动自动结构迁移（v1 → v2 → v3），根目录归属由"迁移向导"分配（幂等、单事务、可回退）。

## 📚 文档索引
| 文档 | 说明 |
| ---- | ---- |
| [项目需求规格和开发计划书（V7）](docs/20260829_项目需求规格和开发计划书_v7.md) | 当前版需求规格与规划 |
| [项目审核和修改建议报告（V6）](docs/20260829_项目审核和修改建议报告_v6.md) | V1.4.0 六维审核结论与 P2/P3 建议 |
| [项目复审报告（V7）](docs/20260829_项目审核和修改建议报告_v7.md) | 增量备份增强后复审：功能回归/冲突/依赖审计 |
| [软件安装使用说明书（V6）](docs/20260829_软件安装使用说明书_v6.md) | 面向小白的完整使用指南 |
| [打包发布指南（V7）](docs/20260829_打包发布指南_v7.md) | 打包与 GitHub Releases 发布全流程 |
| [四级分类与增量备份改造设计方案（V1）](docs/20260829_四级分类与增量备份改造设计方案_v1.md) | 改造设计与格式支持 |
| [四级分类与增量备份改造施工方案（V1）](docs/20260829_四级分类与增量备份改造施工方案_v1.md) | 施工阶段与门禁 |
| [开发工作记录 06](20260829_PromptSprite_06_开发工作记录.md) | 2026-08-29 起记录卷（第 009 条起） |

## 🤝 参与贡献
1. Fork 本仓库并创建特性分支；
2. 修改代码时遵循项目约定：修改处添加 `修改日期+内容` 注释；
3. 每次问答/开发内容按编号追加到开发工作记录（禁止覆盖/删除已有内容）；
4. 提交遵循 [Conventional Commits](https://www.conventionalcommits.org/) 规范；
5. 发起 Pull Request。


---

## 爱心打赏

☕ 如果你开心，欢迎送爱心请作者喝杯咖啡，让我更有动力去创造！
<p align="center">
  <img src="https://github.com/yihufree/XueYuTTS/blob/main/images/wechatpay_203903.png" alt="爱心 微信赞赏码" width="240">
</p>

---


## 📄 许可证
本项目采用 **MIT License**（见 [LICENSE](LICENSE)）。引用本项目或代码请保留版权声明。
