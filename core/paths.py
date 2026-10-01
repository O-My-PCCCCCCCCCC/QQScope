"""QQScope · 统一路径（便携版：一切从包根推导，可用环境变量覆盖）

task-11：后端数据物理分账号。每个账号一个目录：

    data/accounts/<qq>/
        qqscope.db     该账号自己的库（schema 不变，保留 account_qq 列）
        media_cache/ avatars/ export/ voice/ pack/ decrypt/

全局文件：data/accounts.db（账号注册表）、data/server/、data/framework/。
迁移前旧单库仍在 data/qqscope.db；迁移后改名 data/qqscope.db.migrated（绝不删除）。

覆盖项（都可选）：
    QQSCOPE_ROOT       包根目录（默认 = 本文件的上上级）
    QQSCOPE_DATA_ROOT  QQ 数据根目录（默认 = 当前用户 Documents\\Tencent Files）
    QQSCOPE_LEGACY_KEY 历史密钥文件（默认 = data/keys/legacy.key）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _env_path(name: str) -> Path | None:
    v = os.environ.get(name)
    return Path(v).expanduser().resolve() if v else None


# ── 包根 ────────────────────────────────────────────────────────────────────
ROOT = _env_path("QQSCOPE_ROOT") or Path(__file__).resolve().parent.parent

DATA        = ROOT / "data"
ACCOUNTS_DIR = DATA / "accounts"          # 每账号一个子目录
MASTER_DB   = DATA / "accounts.db"        # 账号注册表（只有 accounts 表）
LEGACY_DB   = DATA / "qqscope.db"         # 迁移前的单库
BACKUP_DIR  = DATA / "backup"

# 迁移前的全局目录（保留兼容；新代码应优先用 account_* 派生）
PACK_DIR    = DATA / "pack"
KEYS_DIR    = DATA / "keys"
DECRYPT_DIR = DATA / "decrypt"
EXPORT_DIR  = DATA / "export"
STORE_DB    = MASTER_DB                   # 兼容别名（新代码请用 MASTER_DB / account_db）

WEB_DIR     = ROOT / "web"
DIST_DIR    = ROOT / "app" / "dist"
TOOLS       = ROOT / "tools"
NT_UTIL     = TOOLS / "nt_msg_db_util"
NAPCAT_DIR  = TOOLS / "napcat"
NAPCAT_LEGACY_DIR = TOOLS / "napcat-win"


# ── 每账号目录 ──────────────────────────────────────────────────────────────
_PER_ACCOUNT_SUBDIRS = ("media_cache", "avatars", "export", "voice", "pack", "decrypt")


def account_dir(account_qq, create: bool = True) -> Path:
    """返回 data/accounts/<qq>/（默认创建）。"""
    d = ACCOUNTS_DIR / str(int(account_qq))
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def account_db(account_qq) -> Path:
    """返回该账号的库文件路径 data/accounts/<qq>/qqscope.db（不自动建库）。"""
    return ACCOUNTS_DIR / str(int(account_qq)) / "qqscope.db"


def account_subdir(account_qq, name: str, create: bool = True) -> Path:
    if name not in _PER_ACCOUNT_SUBDIRS:
        raise ValueError("未知的账号子目录：" + str(name))
    d = account_dir(account_qq, create=create) / name
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def account_media_cache(account_qq, create: bool = True) -> Path:
    return account_subdir(account_qq, "media_cache", create)


def account_avatars(account_qq, create: bool = True) -> Path:
    return account_subdir(account_qq, "avatars", create)


def account_export(account_qq, create: bool = True) -> Path:
    return account_subdir(account_qq, "export", create)


def account_voice(account_qq, create: bool = True) -> Path:
    return account_subdir(account_qq, "voice", create)


def account_pack(account_qq, create: bool = True) -> Path:
    return account_subdir(account_qq, "pack", create)


def account_decrypt(account_qq, create: bool = True) -> Path:
    return account_subdir(account_qq, "decrypt", create)


def accounts_split_done() -> bool:
    """是否已经拆过（data/accounts/ 下至少有一个账号目录）。"""
    try:
        return ACCOUNTS_DIR.exists() and any(ACCOUNTS_DIR.iterdir())
    except OSError:
        return False


def legacy_present() -> bool:
    return LEGACY_DB.exists()


# ── 便携 Python ─────────────────────────────────────────────────────────────
PORTABLE_PYTHON = ROOT / "python" / "python.exe"
NT_VENV_PYTHON  = NT_UTIL / ".venv" / "Scripts" / "python.exe"


def python_executable() -> Path:
    for p in (PORTABLE_PYTHON, NT_VENV_PYTHON):
        try:
            if p.exists():
                return p
        except OSError:
            pass
    return Path(sys.executable)


# ── 可选的外部路径 ─────────────────────────────────────────────────────────
LEGACY_KEY_FILE = _env_path("QQSCOPE_LEGACY_KEY") or (KEYS_DIR / "legacy.key")

DEFAULT_DATA_ROOT = _env_path("QQSCOPE_DATA_ROOT") or (
    Path.home() / "Documents" / "Tencent Files")


for _d in (DATA, ACCOUNTS_DIR, BACKUP_DIR):
    try:
        _d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass