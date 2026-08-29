# -*- coding: utf-8 -*-
"""
backup.py - 启动自动静默备份
创建日期：2026-08-12（阶段一：项目初始化与数据地基）

逻辑：
  1. 主库 prompts.db 存在 → 复制到 data/backup/prompts_时间戳.db
  2. 仅保留最近 BACKUP_KEEP_COUNT(5) 份，更早的自动删除
  3. 失败（磁盘满等）→ 返回失败信息，由调用方在状态栏显示黄点警告

自测：python -m app.backup
"""
import os
import re
import shutil
from datetime import datetime
from typing import Optional

from .config import data_dir, DB_FILE_NAME, BACKUP_DIR_NAME, BACKUP_KEEP_COUNT

# 2026-08-29（B4 修复）：仅匹配标准自动备份名（prompts_YYYY-MM-DD_HH-MM-SS_ffffff.db），
# 避免把 "prompts_施工前_*.db" 等快照/手动文件当作备份清理。
_BACKUP_FILE_RE = re.compile(r"^prompts_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_\d+\.db$")


def backup_db(db_path: Optional[str] = None, keep: int = BACKUP_KEEP_COUNT) -> dict:
    """执行一次启动备份。

    返回：{'ok': bool, 'path': Optional[str], 'error': Optional[str]}
    """
    result = {"ok": False, "path": None, "error": None}
    db_path = db_path or os.path.join(data_dir(), DB_FILE_NAME)

    if not os.path.isfile(db_path):
        result["error"] = "主数据库不存在，跳过备份"
        return result

    backup_dir = os.path.join(os.path.dirname(db_path), BACKUP_DIR_NAME)
    try:
        os.makedirs(backup_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
        dest = os.path.join(backup_dir, f"prompts_{ts}.db")
        shutil.copy2(db_path, dest)
        _cleanup_old_backups(backup_dir, keep)
        result["ok"] = True
        result["path"] = dest
    except Exception as exc:  # 磁盘满等异常 → 静默返回失败
        result["error"] = str(exc)
    return result


def _cleanup_old_backups(backup_dir: str, keep: int) -> None:
    """按文件名时间排序，只保留最近 keep 份（仅匹配标准备份名，快照等其他文件不受影响）"""
    files = sorted(
        f for f in os.listdir(backup_dir)
        if _BACKUP_FILE_RE.match(f)
    )
    for old in files[:-keep] if keep > 0 else files:
        try:
            os.remove(os.path.join(backup_dir, old))
        except OSError:
            pass


def _selftest() -> None:
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="promptsprite_backup_")
    try:
        db_path = os.path.join(tmp, "prompts.db")
        with open(db_path, "w", encoding="utf-8") as f:
            f.write("test-db-content")

        # 1. 连跑 7 次备份 → 只保留 5 份
        # 2026-08-29（B4 修复验证）：先放一个"快照"文件，验证清理不会误删非标准备份名文件
        snap = os.path.join(tmp, BACKUP_DIR_NAME, "prompts_施工前_20260829.db")
        os.makedirs(os.path.dirname(snap), exist_ok=True)
        with open(snap, "w", encoding="utf-8") as f:
            f.write("snapshot")
        for _ in range(7):
            r = backup_db(db_path)
            assert r["ok"], r
        files = [f for f in os.listdir(os.path.join(tmp, BACKUP_DIR_NAME)) if f.endswith(".db")]
        assert len(files) == 6, f"应保留5份备份+1份快照，实际 {len(files)}"  # 5 备份 + 快照
        assert os.path.isfile(snap), "快照不应被备份清理误删"
        print("[1] 备份 + 保留最近5份 + 快照不受影响 通过")

        # 2. 主库不存在 → 跳过（不视为错误）
        r = backup_db(os.path.join(tmp, "missing.db"))
        assert not r["ok"] and "不存在" in r["error"]
        print("[2] 主库不存在跳过 通过")
        print("=== 备份模块自测通过 ===")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    _selftest()
