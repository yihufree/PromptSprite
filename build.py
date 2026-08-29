# -*- coding: utf-8 -*-
"""
build.py - PromptSprite 一键打包脚本（单 EXE）
用法：python build.py

流程（自动完成，无需手工分步）：
  1. 确保 pyinstaller 已安装（缺失自动安装）
  2. 确保 requirements.txt 运行依赖已安装（缺失自动安装）
  3. 由 Icons/PSicon.png 生成 build/app.ico
  4. 准备内置完整数据库：复制 data/prompts.db → app/resources/builtin_prompts.db（内嵌进 EXE）
  5. 执行 PyInstaller（onefile + windowed），把所有必要依赖与最新数据打入单 EXE
  6. 更新打包数据文件夹：复制 data/prompts.db → dist/data/prompts.db
  7. 校验产物 dist/PromptSprite.exe 并显示大小

说明：
  - 优先使用 .venv 的 Python 打包（依赖已齐备）；无 .venv 时使用当前解释器。
  - 打包产物为单文件：dist/PromptSprite.exe（内置手册、图标、完整最新数据库一并内嵌）。
  - 每次打包均携带开发态最新数据（3934 条等），保证 EXE 开箱即用最新内容。
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
REQUIREMENTS = os.path.join(ROOT, "requirements.txt")
SPEC = os.path.join(ROOT, "build", "PromptSprite.spec")
ICON_GEN = os.path.join(ROOT, "build", "generate_icon.py")
EXE_OUT = os.path.join(ROOT, "dist", "PromptSprite.exe")
DEV_DB = os.path.join(ROOT, "data", "prompts.db")                  # 开发态主库（最新数据）
BUILTIN_DB = os.path.join(ROOT, "app", "resources", "builtin_prompts.db")  # 内嵌库（打包进 EXE）
DIST_DB = os.path.join(ROOT, "dist", "data", "prompts.db")         # EXE 旁数据文件夹

# 优先使用 .venv 的解释器，其次使用当前解释器
VENV_PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
PY = VENV_PY if os.path.isfile(VENV_PY) else sys.executable


def _run(args, cwd=None):
    """打印并执行命令"""
    print(">>", " ".join(args))
    subprocess.run(args, cwd=cwd, check=True)


def _prepare_builtin_db() -> None:
    """打包前：将开发态最新主库复制为内嵌库（build/PromptSprite.spec 的 datas 引用）。

    2026-08-18（第023条新增）：确保每次打包都携带开发中最后更新的数据。
    """
    if not os.path.isfile(DEV_DB):
        print(f"[数据] 未找到 {DEV_DB}，本次打包将不含完整数据库（仅内置手册 90 条）")
        return
    os.makedirs(os.path.join(ROOT, "app", "resources"), exist_ok=True)
    shutil.copy2(DEV_DB, BUILTIN_DB)
    print(f"[数据] 内置完整数据库已就绪（{os.path.getsize(BUILTIN_DB) / 1024 / 1024:.1f} MB，来自开发态最新库）")


def _update_dist_data() -> None:
    """打包后：将最新主库复制到 EXE 旁数据文件夹，保证 dist 开箱即用最新数据。

    2026-08-18（第023条新增）：只更新 prompts.db，保留已有 images/backup 等用户数据。
    """
    if not os.path.isfile(DEV_DB):
        print("[数据] 开发态主库不存在，跳过 dist/data 更新")
        return
    os.makedirs(os.path.join(ROOT, "dist", "data"), exist_ok=True)
    shutil.copy2(DEV_DB, DIST_DB)
    print(f"[数据] dist/data/prompts.db 已更新为最新数据（{os.path.getsize(DIST_DB) / 1024 / 1024:.1f} MB）")


def main() -> None:
    print(f"使用解释器：{PY}")

    # 1. 安装 pyinstaller（如缺失）
    try:
        import PyInstaller  # noqa: F401
        print("[1/7] pyinstaller 已安装")
    except ImportError:
        print("[1/7] 安装 pyinstaller ...")
        _run([PY, "-m", "pip", "install", "pyinstaller"])

    # 2. 安装/校验运行依赖
    print("[2/7] 安装/校验运行依赖（requirements.txt）...")
    _run([PY, "-m", "pip", "install", "-r", REQUIREMENTS])

    # 3. 生成应用图标
    print("[3/7] 生成应用图标 build/app.ico ...")
    _run([PY, ICON_GEN])

    # 4. 准备内置完整数据库（2026-08-18 第023条新增）
    print("[4/7] 准备内置完整数据库（打包进 EXE）...")
    _prepare_builtin_db()

    # 5. 执行 PyInstaller 打包（在项目根目录运行，产物落在 dist/）
    print("[5/7] 执行 PyInstaller 打包（onefile + windowed，含最新数据）...")
    _run([PY, "-m", "PyInstaller", "--noconfirm", "--clean", SPEC], cwd=ROOT)

    # 6. 更新 EXE 旁数据文件夹（2026-08-18 第023条新增）
    print("[6/7] 更新 EXE 旁数据文件夹 dist/data ...")
    _update_dist_data()

    # 7. 校验产物
    if os.path.isfile(EXE_OUT):
        size_mb = os.path.getsize(EXE_OUT) / 1024 / 1024
        print(f"=== 打包完成：{EXE_OUT}（{size_mb:.1f} MB）===")
        print("已内置最新完整数据库；首次运行自动在 exe 同目录生成/复用 data/。")
        print("全局热键需管理员权限或杀毒软件白名单（失败不影响其他功能）。")
    else:
        print("未找到产物，请查看上方错误信息。")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"打包失败（退出码 {exc.returncode}），请查看上方错误信息。")
        sys.exit(exc.returncode)
