# 🧩 PromptSprite（提示精灵）

> AI 提示词管理工具 —— 把杂乱无章的 Prompt 文档，变成 **四级分类书架 + 极速检索 + 一键复制** 的本地神器。

![Platform](https://img.shields.io/badge/Platform-Windows_10%2F11-blue)
![Python](https://img.shields.io/badge/Python-3.10%2B-green)
![UI](https://img.shields.io/badge/UI-CustomTkinter-2E8B57)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Data](https://img.shields.io/badge/Data-3816_prompts-orange)

双击 `PromptSprite.exe` 即可使用。数据全部保存在本机，**无任何联网行为**。

---

## ✨ 功能特性

| 特性 | 说明 |
| ---- | ---- |
| ① 四级导航树 | 根目录(领域) → 一级(维度) → 二级(大类) → 条目；**多领域共享一级分类**（多对一） |
| ② 9 字段详情 | ①名称…⑩图像方案；按根目录动态显隐；⑩图像方案可"打开"链接；编辑/悬停浮窗/图片预览 |
| ③ 一键复制 | 复制全部 / 复制中文 / 复制英文 三种方式 |
| ④ 快捷悬停添加 | 鼠标悬停滑过分类即锁定，光标自动跳入输入框快速新建，连续录入不打断 |
| ⑤ 悬停选中与高亮 | 主界面"鼠标悬浮 0.3 秒即选中"，选中链路高亮；底部状态栏按悬停层级动态统计 |
| ⑥ 安全回收 | 全局锁定 + 删除双重确认 + 删除分类条目转"未分类" + 删除根目录仅解除关联 |
| ⑦ 收藏与搜索 | ⭐常用入口；全局搜索实时过滤全部文本字段 |
| ⑧ 导入导出 | JSON/Excel/HTML/MD（进度条）；**导入按内容自动去重**；批量事务导入 |
| ⑨ 自动备份 | 每次启动自动备份，仅保留最近 5 份，失败状态栏警告 |
| ⑩ 全局热键/托盘 | `Ctrl+Shift+P` 唤出聚焦搜索；`ESC` 隐藏；托盘恢复/退出 |
| ⑪ 内置数据 | 首次启动自动导入 90 条视觉风格提示词；升级"非破坏性合并"不丢用户数据 |
| ⑫ 用户设置 | 记住窗口大小、默认视图（卡片/列表）、详情字段策略（自动/全部/精简） |
| ⑬ 一键打包 | `python build.py` 自动生成单文件 EXE |

## 📊 内置数据（开箱即用）

| 根目录 | 条目数 |
| ---- | ---- |
| 海外AI绘画案例库 | 1791 |
| 图像（提示词拆解案例/手绘插画/SDXL摄影风格/时尚搭配速查表） | 1151 |
| AI绘画精选案例 | 494 |
| AI生图提示词大全 | 217 |
| 视觉风格分类（内置手册） | 90 |
| GPT Image 提示词库 | 53 |
| 计算机编程 | 20 |
| **合计** | **3816** |

数据来源：moosl/awesome-gpt-image-2-prompts、davidwuw0811-boop、ZeroLu/awesome-gpt-image、whataicc/aiprompt、CSDN 提示词拆解案例、preangelleo/illustration-style-samples、roblaughter/style-reference、andygock/stable-diffusion-style-cheatsheet、some9000/StylePile、vitthalsawant/Sketch-AI 等（均已标注 origin 溯源字段）。

## 🖥️ 界面速览

```
┌────────────────────────────────────────────────────────────────────┐
│ 🧩 PromptSprite │ 🔒锁定 │ 📂未分类 │ ⭐常用 │ [搜索] │ ⇩导入 ⇧导出 ✚快速新建 ⚙设置 │
├────────┬────────┬────────┬──────────────────┬───────────────────────┤
│ 根目录  │ 一级   │ 二级   │ 条目(卡片/列表)  │ 详情(9字段可编辑)      │
│ 视觉风格│ 按媒介 │ 写实   │ 35mm电影胶片风   │ 名称/收藏/删除         │
│ 图像   │ 按地域 │ 国风   │ 赛博朋克人像     │ 复制全部/中文/英文 保存 │
│ …      │ …     │ …     │ …                │ …                     │
└────────┴────────┴────────┴──────────────────┴───────────────────────┘
│ 底部状态栏：总提示词数 3816｜一级目录 …（悬停动态变化）                 │
└────────────────────────────────────────────────────────────────────┘
```

## 🚀 快速开始

### 方式一：直接使用（普通用户）

1. 从 [Releases](../../releases) 下载 `PromptSprite.exe`（约 21MB）；
2. 双击运行（首次运行自动建库并导入内置提示词）；
3. 详细图文说明见 [《软件安装使用说明书 V3》](docs/20260818_PromptSprite_软件安装使用说明书_v3.md)。

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
python app/main.py --smoke    # 冒烟测试：5 秒后自动退出
```

> 说明：入口会自动把项目根目录加入 `sys.path` 并采用绝对导入；缺少第三方库时启动自动安装依赖。

## 📦 打包发布

```powershell
python build.py           # 一键打包（自动装依赖/生成图标/PyInstaller 单 EXE）
```

产物：`dist\PromptSprite.exe`（单文件、无控制台窗口）。详见 [《打包发布指南 V3》](docs/20260818_PromptSprite_打包发布指南_v3.md)。

## 🧱 项目结构

```
PromptSprite/
├── app/
│   ├── main.py               # 程序入口（启动流程编排；可直接运行）
│   ├── config.py             # 全局配置常量
│   ├── database.py           # SQLite 数据层（v2：全局分类 + 领域多对一关联）
│   ├── models.py             # 数据模型（Entry）
│   ├── backup.py             # 启动静默备份（保留最近 5 份）
│   ├── hotkey.py             # 全局热键 + 系统托盘
│   ├── self_test.py          # 统一自测入口
│   ├── parser/               # md_parser / json_io / excel_io / html_export
│   ├── ui/                   # main_window / quick_add / move_selector / progress_dialog / settings_dialog
│   └── resources/builtin_manual.md   # 内置手册（90 条）
├── docs/                     # 需求规格V4 / 审核报告V3 / 说明书V3 / 打包指南V3 / 施工方案 / 开发记录
├── Icons/                    # 应用图标源（PSicon.png）
├── *.json / *.xlsx           # 数据资产文件（可直接导入）
├── build.py / run.py / requirements.txt / LICENSE / .gitignore
└── README.md
```

## 🗄️ 数据模型（v2）

- 分类为**全局共享树**（`categories` 无领域归属列），通过 `domain_category` 关联表实现"多个领域共享同一级分类"；
- 删除根目录**仅解除关联**（分类与条目保留）；删除分类级联子分类、其下条目自动转入"未分类"；
- 旧版数据库启动时自动迁移（v1 → v2），无需手工处理。

## 📚 文档索引

| 文档 | 说明 |
| ---- | ---- |
| [项目需求规格和开发规划设计方案（V4.0）](docs/20260818_PromptSprite项目需求规格和开发规划设计方案_v4.md) | 当前版需求规格与规划 |
| [软件安装使用说明书（V3.0）](docs/20260818_PromptSprite_软件安装使用说明书_v3.md) | 面向小白的完整使用指南 |
| [打包发布指南（V3.0）](docs/20260818_PromptSprite_打包发布指南_v3.md) | 打包与 GitHub Releases 发布全流程 |


## 🤝 参与贡献

1. Fork 本仓库并创建特性分支；
2. 修改代码时遵循项目约定：修改处添加 `修改日期+时间+内容` 注释；
3. 每次问答/开发内容按编号追加到开发工作记录（禁止覆盖/删除已有内容）；
4. 提交遵循 [Conventional Commits](https://www.conventionalcommits.org/) 规范；
5. 发起 Pull Request。

## 📄 许可证

本项目采用 **MIT License**（见 [LICENSE](LICENSE)）。引用本项目或代码请保留版权声明。
