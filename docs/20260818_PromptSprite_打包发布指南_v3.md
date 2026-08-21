# PromptSprite（提示精灵）打包发布指南（V3.0 修订版）

> 版本：V3.0（2026-08-18）
> 前版：V2.0（2026-08-18《20260818_PromptSprite_打包发布指南_v2.md》）
> 修订：新增数据文件（json/xlsx）随仓库发布的说明、GitHub 仓库发布文档要求（README/LICENSE）、发布检查清单更新（3816 条数据规模）、V1.2 版本说明模板。

---

## 一、前置要求

| 项目 | 要求 |
| ---- | ---- |
| 操作系统 | Windows 10/11（必须用 Windows 打包 Windows EXE） |
| Python | 3.10+（项目在 3.12 验证） |
| 依赖 | `requirements.txt`（customtkinter/Pillow/pyperclip/openpyxl/keyboard/pystray） |
| 可选 | Git、GitHub 账号（发布用） |

---

## 二、一键打包（推荐）

在项目根目录执行：

```powershell
python build.py
```

自动 5 步：安装/校验 pyinstaller → 安装依赖 → 由 `Icons/PSicon.png` 生成 `build/app.ico` → PyInstaller（onefile + windowed）→ 校验 `dist\PromptSprite.exe` 并显示大小。

- 优先使用 `.venv` 的 Python 打包；无 `.venv` 用当前解释器；
- 备用入口：`build\build.bat`（纯 ASCII，双击或执行）。

---

## 三、PyInstaller 配置要点（build/PromptSprite.spec）

| 配置 | 值 | 说明 |
| ---- | ---- | ---- |
| 模式 | onefile | 单文件 EXE |
| console | False（windowed） | 无控制台窗口 |
| 图标 | build/app.ico | 由 Icons/PSicon.png 生成 |
| datas | `app/resources/builtin_manual.md`、`app/resources/builtin_prompts.db`、`Icons/PSicon.png` | 内嵌资源（打包态经 `sys._MEIPASS` 读取；builtin_prompts.db 为第 023 条新增的内置完整数据库） |

**路径规则（app/config.py）**：
- 打包态：资源读 `sys._MEIPASS`（含内置手册 + 内置完整数据库）；**数据写入 EXE 同目录 `data\`**；
- 开发态：资源读 `app/resources\`；数据写入项目根 `data\`。

> ⚠️ **V3 新增重点（第 023 条）**：**每次打包都会携带开发态最新完整数据库**——
> - `build.py` 打包前自动复制 `data/prompts.db` → `app/resources/builtin_prompts.db` 内嵌进 EXE；
> - EXE **首次运行**（exe 旁无 `data\prompts.db`，含拷贝到新位置）时自动从内嵌资源复制最新完整库（3816 条、12 根目录），开箱即用；
> - 打包后自动更新 `dist\data\prompts.db` 为最新数据（保留已有 images/backup）；
> - 已有 `data\prompts.db` 的用户库**不覆盖**（保护用户数据）；如需最新开发数据，可删除 data 目录后重启，或导入最新 JSON。

---

## 四、打包后验证清单（10 项，必测）

| # | 验证项 | 预期 |
| - | ---- | ---- |
| 1 | 首次运行 | 自动生成 EXE 旁 `data\`（prompts.db/backup/images） |
| 2 | 内置数据 | 自动导入 90 条，归入"视觉风格分类" |
| 3 | 导航 | 三列悬停选中正常、高亮正常 |
| 4 | 详情显隐 | 视觉风格分类/视频/图像 9 字段全显示；计算机编程等隐藏 ③-⑦ |
| 5 | 提示词高度 | 有内容默认 6 行可见；"展开"自适应高度；无内容 1 行 |
| 6 | 复制 | 复制全部/中文/英文 可粘贴 |
| 7 | 快速新建 | 悬停锁定 + 保存成功，不打断主窗口 |
| 8 | 导入导出 | JSON/Excel/HTML 各导出并重新导入（计数一致、自动去重） |
| 9 | 热键/托盘 | Ctrl+Shift+P 有效（或管理员）；ESC 隐藏、托盘恢复 |
| 10 | 备份/重启 | 重启后 `data\backup\` 有新备份，数据完整不重复导入 |

> 说明：打包态 `--smoke` 自动退出参数在 windowed 单文件 EXE 下不生效（第 010 条实测），自动化冒烟请使用开发态 `python app/main.py --smoke`；EXE 正常使用不受影响。

---

## 五、发布到 GitHub Releases

### 5.1 发布前检查清单

1. **版本号**：`app/config.py` 的 `APP_VERSION` / `BUILTIN_MANUAL_VERSION` 确认（当前 0.2.0 / 007-2026-08-18；2026-08-21 第006条：0.1.0 → 0.2.0）；
2. **文档同步**（本次 V1.2 发布应全部更新）：
   - README.md（功能/快速开始/数据规模/文档索引，索引指向 V4/V3 文档）；
   - 说明书 V3（docs/20260818_PromptSprite_软件安装使用说明书_v3.md）；
   - 需求规格 V4（docs/20260818_PromptSprite项目需求规格和开发规划设计方案_v4.md）；
   - 审核报告 V3（docs/20260818_PromptSprite_项目审核和修改建议报告_v3.md）；
   - 打包指南 V3（本文档）；
   - LICENSE（MIT，见 §5.5）。
3. **自测**：`python -m app.self_test` 全过；
4. **数据核对**：真实库 3816 条、未分类 0；数据文件（*.json/*.xlsx）已生成并与库一致；
5. **清理**：`data\`、`*.code-workspace`、`_selftest_out.txt` 等已被 `.gitignore` 排除（检查 `git status` 确认无敏感文件）。

### 5.2 提交与 Tag

```powershell
# 1. 检查
git status
git add -A
git diff --cached --stat

# 2. 提交（Conventional Commits）
git commit -m "feat: V1.2 发布（导入去重+设置入口+904条数据，12根目录/3816条）

- 导入按详情内容判重、批量事务
- 新增设置入口（窗口大小/视图模式/详情策略）
- 新增状态栏悬停动态统计
- 导入 CSDN/ZeroLu/whataicc/手绘插画等 904 条提示词
- 文档升级至 V4/V3"

# 3. 打标签并推送
git tag v1.2.0
git push origin main --tags
```

### 5.3 创建 Release

1. 仓库 → Releases → **Draft a new release**；
2. 选择标签（v1.2.0），填写版本说明（见 §5.4 模板）；
3. 上传附件：`dist\PromptSprite.exe`；
4. 发布。

### 5.4 Release 说明模板（V1.2）

```markdown
## PromptSprite v1.2.0（2026-08-18）

### 新增
- 内置 3800+ 条多来源 AI 绘画提示词（12 个根目录：海外案例库/精选案例/GPT Image 提示词库/AI 生图提示词大全/手绘插画/SDXL 摄影风格/时尚搭配速查表等）
- 设置入口：记住窗口大小、卡片/列表视图切换、详情字段策略（自动/全部/精简）
- 底部状态栏悬停动态统计（根目录/一级/二级/条目）

### 修复/改进
- Excel/JSON 导入按"详情内容"自动去重，重复导入不再产生重复条目
- 大文件导入改为批量事务，速度更快
- 图片路径读写越界校验（data/ 目录内）
- 删除分类其下条目自动转入"未分类"，删除根目录仅解除关联

### 使用
- 下载 PromptSprite.exe → 双击运行（首次自动建库导入内置数据）
- 数据保存在 EXE 同目录 data\ 下；迁移请用 JSON 导出/导入
- 详见软件安装使用说明书 V3
```

### 5.5 LICENSE（MIT）

项目根目录创建 `LICENSE` 文件（内容见 §六示例），并在 README 中更新 License 徽章与实际链接。

---

## 六、GitHub 仓库文档要求（本次 V1.2 发布同步）

| 文件 | 要求 | 状态 |
| ---- | ---- | ---- |
| README.md | 功能特性/界面速览/快速开始（EXE 与源码）/打包/项目结构/文档索引/贡献/许可证 | 需更新至 V1.2（含 3816 条数据、12 根目录、文档索引 V4/V3） |
| LICENSE | MIT 协议全文（Copyright © 2026 PromptSprite） | 需新建 |
| .gitignore | data/、dist/、build/ 中间产物、IDE 文件、测试残留（已覆盖） | 已具备 |
| docs/ | 说明书 V3、需求规格 V4、审核报告 V3、打包指南 V3、施工方案、开发工作记录 01/02 | 已更新 |
| 数据文件 | `*.json` / `*.xlsx`（约 30 个）作为**数据资产**提交，供用户直接导入 | 已生成（csdn_prompt_cases / zerolu_awesome_gpt_image / whataicc_aiprompt / illustration_style_samples / sdxl_style_reference / style_cheatsheet / stylepile_sketchai / awesome_gpt_image2_*） |

> 注意：**不要提交** `data\`（运行时数据库）、`dist\`（EXE 产物）、`build\` 中间产物、`.venv\`。

---

## 七、常见问题排查

| 现象 | 原因/解决 |
| ---- | ---- |
| EXE 启动后分类全空 | 内置手册未打进包（检查 spec datas）或首次未导入（看是否被旧 data 干扰） |
| EXE 与源码数据不一致 | 两者 `data\` 目录独立；用 JSON 导出/导入迁移（§三） |
| 缺少模块 | spec `hiddenimports` 补充（pystray/keyboard 等） |
| 热键无效 | 管理员/白名单 |
| 杀毒误报 | onefile 自解压特性；加白名单或改用 onedir |
| 打包体积 | 正常 20~25MB（当前 21.7MB）；UPX 可选 |
| GitHub 上传被拒（大文件） | EXE 走 Releases 附件，不要提交 git；单文件 <100MB 无碍 |

---

## 八、发布目录建议

```
PromptSprite/
├── app/                  # 源码
├── build/                # PyInstaller 配置
├── Icons/                # 应用图标
├── docs/                 # 需求规格V4/审核报告V3/说明书V3/打包指南V3/施工方案/开发记录
├── *.json / *.xlsx       # 数据资产（约 30 个文件）
├── build.py / run.py / requirements.txt / README.md / LICENSE / .gitignore
└── data/（不提交） dist/（不提交）
```

> `dist\`（EXE 产物）、`data\`（运行时数据）不提交 git；EXE 通过 GitHub Releases 发布。
