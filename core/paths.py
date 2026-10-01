"""QQScope · 统一路径（便携版：一切从包根推导，可用环境变量覆盖）

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
PACK_DIR    = DATA / "pack"
KEYS_DIR    = DATA / "keys"
DECRYPT_DIR = DATA / "decrypt"
EXPORT_DIR  = DATA / "export"
WEB_DIR     = ROOT / "web"
DIST_DIR    = ROOT / "app" / "dist"
TOOLS       = ROOT / "tools"
NT_UTIL     = TOOLS / "nt_msg_db_util"      # 解密/解析工具链（第三方源码，勿改）
NAPCAT_DIR  = TOOLS / "napcat"              # 便携版机器人框架（新目录）
NAPCAT_LEGACY_DIR = TOOLS / "napcat-win"    # 旧框架目录（回退，可能不存在）

STORE_DB    = DATA / "qqscope.db"           # 统一主库

# ── 便携 Python ─────────────────────────────────────────────────────────────
PORTABLE_PYTHON = ROOT / "python" / "python.exe"                     # 随包运行时
NT_VENV_PYTHON  = NT_UTIL / ".venv" / "Scripts" / "python.exe"       # 开发机上的工具链 venv


def python_executable() -> Path:
    """解密/解析子进程用哪个解释器：随包 python/ → nt_msg_db_util venv → 当前解释器。"""
    for p in (PORTABLE_PYTHON, NT_VENV_PYTHON):
        try:
            if p.exists():
                return p
        except OSError:
            pass
    return Path(sys.executable)


# ── 可选的外部路径（默认落在包内，绝不写死绝对路径）────────────────────────
LEGACY_KEY_FILE = _env_path("QQSCOPE_LEGACY_KEY") or (KEYS_DIR / "legacy.key")

DEFAULT_DATA_ROOT = _env_path("QQSCOPE_DATA_ROOT") or (
    Path.home() / "Documents" / "Tencent Files")


for _d in (DATA, PACK_DIR, KEYS_DIR, DECRYPT_DIR, EXPORT_DIR):
    _d.mkdir(parents=True, exist_ok=True)