"""QQScope · 自动读取引擎
定位 QQ 本地数据 → 用已保存密钥自动解密 → 结构化导出 → 统一导入 → 数据包
密钥已存本地（data/keys/<qq>.key），此后无需再扫码登录。
"""
import json
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UV = Path(r"E:\06-开发环境\Environment\tools\uv\uv.exe")
TOOL = ROOT / "tools" / "nt_msg_db_util"
KEYS_DIR = ROOT / "data" / "keys"
DECRYPT_DIR = ROOT / "data" / "decrypt"
PACK_DIR = ROOT / "data" / "pack"

DEFAULT_DATA_ROOT = Path(r"C:\Users\Administrator\Documents\Tencent Files")


def run(cmd, cwd=None, env=None):
    e = dict(subprocess.os.environ)
    e["PYTHONIOENCODING"] = "utf-8"
    if env:
        e.update(env)
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=e,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def discover_accounts(data_root=None):
    """扫描 QQ 数据目录，找出含 nt_msg.db 的账号目录"""
    root = Path(data_root or DEFAULT_DATA_ROOT)
    found = []
    if not root.exists():
        return found
    for d in root.iterdir():
        if not d.is_dir() or not d.name.isdigit():
            continue
        db = d / "nt_qq" / "nt_db" / "nt_msg.db"
        if db.exists():
            found.append({"qq": int(d.name), "msg_db": str(db)})
    return sorted(found, key=lambda a: a["qq"])


def key_for(qq):
    """账号密钥：data/keys/<qq>.key；历史文件 nt_msg_main.key / nt_msg_xiaohao.key 兜底"""
    f = KEYS_DIR / f"{qq}.key"
    if f.exists():
        return f.read_text(encoding="ascii").strip()
    for alt in ("nt_msg_main.key", "nt_msg_xiaohao.key"):
        f2 = KEYS_DIR / alt
        if f2.exists():
            v = f2.read_text(encoding="ascii").strip()
            # 主号密钥文件对应 1605289411，小号对应 3101168700
            if (alt == "nt_msg_main.key" and qq == 1605289411) or (alt == "nt_msg_xiaohao.key" and qq == 3101168700):
                return v
    return None


def refresh(data_root=None, force=False):
    """自动刷新：解密 + 导入 + 打包。返回结果摘要。"""
    accounts = discover_accounts(data_root)
    result = {"ok": True, "accounts": [], "skipped": []}

    if not accounts:
        result["ok"] = False
        result["error"] = f"未在 {data_root or DEFAULT_DATA_ROOT} 发现 QQ 账号数据"
        return result

    prepared = []
    for acc in accounts:
        qq = acc["qq"]
        key = key_for(qq)
        if not key:
            result["skipped"].append({"qq": qq, "reason": "无密钥"})
            continue
        workdir = DECRYPT_DIR / str(qq)
        workdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(acc["msg_db"], workdir / "nt_msg.db")
        for suf in ("-wal", "-shm"):
            src = Path(acc["msg_db"] + suf)
            if src.exists():
                shutil.copy2(src, workdir / f"nt_msg.db{suf}")

        # 1) 剥头 + 解密
        r1 = run([str(UV), "run", "--project", str(TOOL), "python", str(TOOL / "1.decrypt.py")],
                 cwd=workdir, env={"NTQQ_DB_KEY": key})
        if r1.returncode != 0:
            result["skipped"].append({"qq": qq, "reason": f"解密失败: {r1.stdout[-200:]}{r1.stderr[-200:]}"})
            continue
        # 2) 结构化导出
        export_db = workdir / "nt_msg_export.db"
        r2 = run([str(UV), "run", "--project", str(TOOL), "python", str(TOOL / "3.export.py"),
                  "--src", str(workdir / "nt_msg_plain.db"), "--dst", str(export_db), "--batch", "5000"],
                 cwd=workdir)
        if r2.returncode != 0 or not export_db.exists():
            result["skipped"].append({"qq": qq, "reason": f"导出失败: {r2.stdout[-200:]}{r2.stderr[-200:]}"})
            continue
        prepared.append({"qq": qq, "export_db": str(export_db), "default_nick": f"账号 {qq}"})

    if not prepared:
        result["ok"] = False
        result["error"] = "没有任何账号能完成解密导入"
        return result

    # 3) 统一导入 + 数据包（动态账号）
    env = {"QQSCOPE_ACCOUNTS": json.dumps(prepared, ensure_ascii=False)}
    r3 = run([str(UV), "run", "--project", str(TOOL), "python", str(ROOT / "scripts" / "import_and_pack.py")],
             env=env)
    if r3.returncode != 0:
        result["ok"] = False
        result["error"] = f"导入失败: {r3.stdout[-300:]}{r3.stderr[-300:]}"
        return result

    result["accounts"] = prepared
    result["last_refresh"] = int(time.time())
    result["log"] = (r3.stdout + r3.stderr)[-500:]
    return result


def list_packs():
    """已生成的数据包账号列表（meta.json 摘要）"""
    out = []
    if not PACK_DIR.exists():
        return out
    for d in sorted(PACK_DIR.iterdir()):
        meta_f = d / "meta.json"
        if d.is_dir() and meta_f.exists():
            try:
                meta = json.loads(meta_f.read_text(encoding="utf-8"))
                meta["pack_dir"] = str(d)
                out.append(meta)
            except Exception:
                continue
    return out
