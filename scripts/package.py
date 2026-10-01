# -*- coding: utf-8 -*-
"""QQScope 便携包构建器（task-35）

用法：
    python scripts/package.py                 # 组装 + 打 zip
    python scripts/package.py --no-zip        # 只组装目录
    python scripts/package.py --name XXX      # 自定义包名

产出：
    dist-package/<name>/            便携目录（可直接拷到别的电脑）
    dist-package/<name>.zip         压缩包

便携运行时方案（选 B 的变体）：
    复制「基础 Python 安装」（python.exe + DLLs + 标准库 Lib，**不含**庞大的全局 site-packages）
    + 把 tools/nt_msg_db_util/.venv 里那份已验证过的 site-packages（fastapi/uvicorn/httpx/
      sqlcipher3/protobuf/pydantic…，共 ~26MB）整体搬过去。
    理由：venv 本身不可重定位（pyvenv.cfg 写死 home），但「基础安装 + 现成依赖目录」是
    可重定位的；且完全不需要联网 pip，版本与开发机实测一致。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_ROOT = ROOT / "dist-package"
NT_VENV = ROOT / "tools" / "nt_msg_db_util" / ".venv"


def _venv_base_python() -> Path:
    """从 nt venv 的 pyvenv.cfg 推导基础解释器目录（不写死机器路径）。"""
    cfg = NT_VENV / "pyvenv.cfg"
    try:
        for line in cfg.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().lower().startswith("home"):
                return Path(line.split("=", 1)[1].strip())
    except OSError:
        pass
    raise SystemExit("[错误] 读不到 tools/nt_msg_db_util/.venv/pyvenv.cfg 的 home")
NT_SITE = ROOT / "tools" / "nt_msg_db_util" / ".venv" / "Lib" / "site-packages"

SKIP_DIRS = {"__pycache__", ".git", ".github", ".vscode", ".idea", ".pytest_cache"}
SKIP_SUFFIX = {".pyc", ".pyo", ".pdb"}
SKIP_ROOT_NAMES = {
    ".git", ".dsh", ".gitignore", "android", "android-native", "dist", "dist-package",
    "data", "research", "crypto.dll", "ssl.dll", "NOTICE.md", "README.md",
    "启动QQScope.bat", "扫码登录.png", "网页截图-会话页.png", "网页截图-修复后.png",
    "网页截图-动态页.png",
}


def log(msg: str) -> None:
    print(msg, flush=True)


def copy_tree(src: Path, dst: Path, skip: set[str] | None = None,
              skip_dirs: set[str] | None = None) -> int:
    """把 src 递归复制到 dst，跳过 skip 名字与 skip_dirs 目录。返回文件数。"""
    skip = skip or set()
    skip_dirs = skip_dirs or set()
    n = 0
    for base, dirs, files in os.walk(src):
        rel = Path(base).relative_to(src)
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and d not in skip_dirs]
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for f in files:
            if f in skip or Path(f).suffix.lower() in SKIP_SUFFIX or f in SKIP_DIRS:
                continue
            shutil.copy2(Path(base) / f, dst / rel / f)
            n += 1
    return n


def build_portable_python(dst: Path) -> None:
    """基础 Python（去掉 site-packages）+ nt venv 的依赖目录。"""
    py_dst = dst / "python"
    if py_dst.exists():
        shutil.rmtree(py_dst, ignore_errors=True)
    py_dst.mkdir(parents=True)
    base = _venv_base_python()
    if not (base / "python.exe").exists():
        raise SystemExit(f"[错误] 找不到 venv 对应的基础解释器：{base}")

    # 1) 解释器与标准库（含 python3xx.dll，动态匹配）
    for name in ("python.exe", "pythonw.exe", "vcruntime140.dll", "vcruntime140_1.dll"):
        p = base / name
        if p.exists():
            shutil.copy2(p, py_dst / name)
    for p in list(base.glob("python3*.dll")) + list(base.glob("python3.dll")):
        shutil.copy2(p, py_dst / p.name)
    for d in ("DLLs", "Lib", "include", "libs"):
        src = base / d
        if not src.exists():
            continue
        if d == "Lib":
            # 标准库：跳过 site-packages / test / tkinter（用不到）
            copy_tree(src, py_dst / "Lib",
                      skip_dirs={"site-packages", "test", "tkinter", "idlelib",
                                 "ensurepip", "distutils"})
        else:
            copy_tree(src, py_dst / d)
    # 标准库也要保留 .pyc 以外的必需文件；copy_tree 已跳过 pyc

    # 2) 依赖：直接搬 nt_msg_db_util venv 里那份（已实测的版本）
    site_dst = py_dst / "Lib" / "site-packages"
    site_dst.mkdir(parents=True, exist_ok=True)
    if not NT_SITE.exists():
        raise SystemExit(f"[错误] 找不到依赖目录：{NT_SITE}")
    for item in NT_SITE.iterdir():
        if item.name in SKIP_DIRS or item.name.endswith(".pyc"):
            continue
        if item.is_dir():
            shutil.copytree(item, site_dst / item.name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(item, site_dst / item.name)
    _mb = sum(f.stat().st_size for f in py_dst.rglob("*") if f.is_file()) / 1048576
    log(f"  便携 Python: {_mb:.0f} MB")


def portable_ize(dst: Path) -> None:
    """包里把 UI 占位符里的开发机绝对路径换成中性文案（仓库文件不动）。"""
    reps = [
        (r"C:\Users\Administrator\Documents\Tencent Files", ""),
        (r"E:\01-项目\QQScope", "<包目录>"),
    ]
    targets = [dst / "scripts" / "build_web.py",
               dst / "web" / "index.html",
               dst / "app" / "dist" / "QQScope.html"]
    for p in targets:
        if not p.exists():
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        o = t
        for a, b in reps:
            t = t.replace(a, b)
        if t != o:
            p.write_text(t, encoding="utf-8")
            log(f"  便携化重写: {p.relative_to(dst)}")


GBK = "gbk"


def write_gbk(path: Path, text: str) -> None:
    path.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode(GBK, "replace"))


def write_launchers(dst: Path, token: str) -> None:
    bat1 = f'''@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title QQScope 便携版

set "PY=%~dp0python\\python.exe"
if not exist "%PY%" (
    echo [错误] 缺少便携 Python：%PY%
    echo        请确认压缩包解压完整（python 目录必须一起解压）。
    pause
    exit /b 1
)

echo ============================================
echo   QQScope 便携版
echo ============================================
echo.

rem ---- 首次运行：建目录 + 写入机器人 token（取自随包 NapCat 配置）----
if not exist "data\\keys" mkdir "data\\keys" >nul 2>&1
if not exist "data\\server" mkdir "data\\server" >nul 2>&1
if not exist "data\\framework" mkdir "data\\framework" >nul 2>&1
if not exist "data\\framework\\onebot.json" (
    echo {{"base":"http://127.0.0.1:3000","token":"{token}"}}> "data\\framework\\onebot.json"
)

rem ---- 端口占用提示 ----
netstat -ano | findstr /r /c:":15555 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [提示] 15555 已在监听：可能已经有一个 QQScope 在跑，直接打开浏览器即可。
    start "" http://127.0.0.1:15555
    pause
    exit /b 0
)

echo [环境] Python : %PY%
echo [环境] 数据目录: %~dp0data
echo.

rem ---- 前端产物：缺了才构建 ----
if not exist "app\\dist\\QQScope.html" (
    echo [1/2] 首次运行，构建前端...
    chcp 65001 >nul
    set PYTHONIOENCODING=utf-8
    "%PY%" scripts\\build_web.py
    if errorlevel 1 (
        echo [警告] 前端构建失败，仍尝试启动后端（页面可能打不开）
    )
    chcp 936 >nul
)

echo [2/2] 启动后端：http://127.0.0.1:15555
echo        关闭本窗口即停止 QQScope。
echo.
start "" http://127.0.0.1:15555
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
"%PY%" server\\app.py
chcp 936 >nul
echo.
echo [退出] QQScope 后端已停止。
pause
'''

    bat2 = '''@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title QQScope 机器人框架

if not exist "tools\\napcat\\启动框架.bat" (
    echo [错误] 找不到机器人框架：tools\\napcat\\启动框架.bat
    echo        如果只需要离线聊天记录，可以不启动它。
    pause
    exit /b 1
)

echo [提示] 首次启动会打印二维码，请用手机 QQ 扫码登录（本人操作）。
echo        登录后 OneBot HTTP 监听 http://127.0.0.1:3000
echo.
call "tools\\napcat\\启动框架.bat"
'''

    write_gbk(dst / "①启动QQScope.bat", bat1)
    write_gbk(dst / "②启动机器人框架.bat", bat2)

    readme = f'''QQScope 便携版 · 使用说明
========================================

【三步走】
1. 双击「①启动QQScope.bat」：会自动打开浏览器 http://127.0.0.1:15555
   （首次启动可能要几秒；窗口不要关，关了就是退出程序）
2. 想用「机器人框架」拉在线消息：双击「②启动机器人框架.bat」，
   用手机 QQ 扫码登录（必须本人扫码），登录后回到网页点「数据源 → 机器人框架 → 拉取」
3. 只想看本机已有的聊天记录：在网页「数据源 → 数据包读取」里点扫描/导入即可（全离线）

【数据在哪 / 怎么迁移 / 怎么备份】
- 所有数据都在本目录的 data\\ 里：data\\qqscope.db（聊天记录主库）、
  data\\avatars\\（头像缓存）、data\\media_cache\\（媒体缓存）、data\\keys\\（解密密钥）
- 换电脑迁移：把旧机器 data\\ 整个目录拷到新机器同一个位置即可
- 备份：直接复制 data\\qqscope.db（建议先关掉程序）
- 注意：本便携包默认【不含】任何聊天数据，data\\ 是空的，避免隐私泄露

【机器人框架 token 在哪】
- 随包 NapCat 配置：tools\\napcat\\napcat\\config\\onebot11.json
- 后端读取的地址：data\\framework\\onebot.json（首次启动本脚本已自动生成）
- 如果两处 token 不一致，先在网页「设置」里改成同一个

【常见问题】
- 端口被占用：提示 15555 已在监听时，说明已经开着一个 QQScope，直接开浏览器即可；
  要换端口就改 data\\server\\settings.json 里的 port（默认 15555）
- 二维码过期：二维码约 2 分钟过期，重新双击「②启动机器人框架.bat」刷新
- 密钥失效：「数据包读取」读的是本机 QQ 数据库，QQ 大版本升级后密钥会变；
  把新密钥（16 字符）存成 data\\keys\\<QQ号>.key 再导入
- 提示缺少 python：压缩包没解压完整，重新完整解压（不要只拖一部分文件出来）
- 权限问题：解压到桌面/文档等可写目录，别放在 C:\\Program Files 下
- 杀毒软件拦截：NapCat 与 sqlcipher 属于本地注入/加密库，可能被误报，需要加白名单

【风险提示】
- 使用机器人框架（NapCat）属于非官方客户端，登录存在被风控/封号的概率，建议小号试
- 所有数据只存在本机，本程序不会上传聊天记录；AI 解读需要联网调用 DeepSeek（可在设置里关）

【目录说明】
- python\\        便携 Python 运行时（不用另装 Python）
- core\\ server\\  后端与数据层
- web\\ app\\dist\\ 前端页面
- tools\\napcat\\  机器人框架（约 325MB，可选）
- tools\\nt_msg_db_util\\  本地 QQ 数据库解密/解析工具链
- data\\          运行数据（首次启动自动创建，不进压缩包）
- web\\assets\\vendor\\  3D 环绕背景素材（three.js + kei.vrm，约 44MB）；
                    删掉也能用，界面会自动降级成静态光晕，其它功能不受影响
'''
    write_gbk(dst / "使用说明.txt", readme)


def build(name: str, do_zip: bool) -> Path:
    t0 = time.monotonic()
    dst = OUT_ROOT / name
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True)
    log(f"[1/5] 组装 {dst}")

    # 代码
    copy_tree(ROOT / "core", dst / "core")
    copy_tree(ROOT / "server", dst / "server", skip={"reader.py", "server.py"})
    copy_tree(ROOT / "scripts", dst / "scripts", skip={"inspect_plain.py", "package.py"})
    copy_tree(ROOT / "app", dst / "app")
    copy_tree(ROOT / "web", dst / "web", skip_dirs={"selftest"})
    copy_tree(ROOT / "tools" / "nt_msg_db_util", dst / "tools" / "nt_msg_db_util",
              skip_dirs={".venv"})

    # 机器人框架（完整，不动源目录）
    log("[2/5] 复制机器人框架 tools/napcat（约 325MB）...")
    napcat_src = ROOT / "tools" / "napcat"
    if napcat_src.exists():
        copy_tree(napcat_src, dst / "tools" / "napcat",
                  skip_dirs={"logs", "cache"})
    else:
        log("  [警告] tools/napcat 不存在，便携包将只有离线模式")

    # 便携 Python
    log("[3/5] 组装便携 Python 运行时 ...")
    build_portable_python(dst)

    # 空 data
    for sub in ("keys", "decrypt", "export", "pack", "backup", "server", "framework"):
        (dst / "data" / sub).mkdir(parents=True, exist_ok=True)

    # token（取自随包 NapCat 配置）
    token = ""
    cfg = dst / "tools" / "napcat" / "napcat" / "config" / "onebot11.json"
    if cfg.exists():
        try:
            d = json.loads(cfg.read_text(encoding="utf-8"))
            token = ((d.get("network") or {}).get("httpServers") or [{}])[0].get("token", "") or ""
        except Exception:  # noqa: BLE001
            token = ""

    log("[4/5] 写启动脚本与说明（GBK + CRLF）...")
    write_launchers(dst, token)
    portable_ize(dst)

    zip_path = None
    if do_zip:
        log("[5/5] 打包 zip ...")
        zip_path = OUT_ROOT / f"{name}.zip"
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for base, dirs, files in os.walk(dst):
                for f in files:
                    fp = Path(base) / f
                    z.write(fp, Path(name) / fp.relative_to(dst))
            # 空目录（data 下各子目录）显式写入，解压后才存在
            for sub in ("keys", "decrypt", "export", "pack", "backup", "server", "framework"):
                z.writestr(f"{name}/data/{sub}/", "")
        log(f"  zip: {zip_path}  {zip_path.stat().st_size/1048576:.0f} MB")
    log(f"完成，用时 {time.monotonic()-t0:.0f}s")
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="QQScope-Portable-v2")
    ap.add_argument("--no-zip", action="store_true")
    args = ap.parse_args()
    build(args.name, not args.no_zip)
    return 0


if __name__ == "__main__":
    sys.exit(main())