# -*- coding: utf-8 -*-
"""QQScope · task-11 Phase 2：把旧全局目录里的文件按账号归位到 data/accounts/<qq>/。

可判定归属的才移动：
  export/<job>  -> 读 export/_meta/<job>.json 的 account_qq
  media_cache/  -> 用 messages.media 里的 md5 反查账号（唯一命中才移动）
  avatars/      -> uin 经 store.account_for_uin 映射（账号自身或唯一联系人）
  decrypt/<qq>/ -> 目录名是账号 QQ
  pack/<qq>/    -> 目录名是账号 QQ
无法判定的文件**不删也不移**，留在原处并列清单。

用法：
    python scripts/migrate_files_split.py --selftest   # 合成小夹具自测（默认安全）
    python scripts/migrate_files_split.py --yes        # 对真实 data/ 执行（先整体备份）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _backup(dirs, backup_root: Path) -> list:
    moved = []
    for d in dirs:
        if d.exists():
            dst = backup_root / d.name
            try:
                shutil.copytree(d, dst, dirs_exist_ok=True)
                moved.append(str(dst))
            except Exception:  # noqa: BLE001
                pass
    return moved


def _media_md5_map(paths, store) -> dict:
    """md5 -> {account_qq,...}（扫描各账号库 messages.media）。"""
    out: dict = {}
    try:
        dbs = sorted(p for p in paths.ACCOUNTS_DIR.glob("*/qqscope.db") if p.parent.name.isdigit())
    except OSError:
        dbs = []
    for dbp in dbs:
        qq = int(dbp.parent.name)
        try:
            con = sqlite3.connect(str(dbp))
            for (raw,) in con.execute("SELECT media FROM messages WHERE media IS NOT NULL"):
                try:
                    m = json.loads(raw or "{}")
                except Exception:  # noqa: BLE001
                    continue
                md5 = str((m or {}).get("md5") or "").strip().lower()
                if md5:
                    out.setdefault(md5, set()).add(qq)
            con.close()
        except Exception:  # noqa: BLE001
            pass
    return out


def run(root: Path, dry: bool = False) -> dict:
    os.environ["QQSCOPE_ROOT"] = str(root)
    sys.path.insert(0, str(ROOT))
    from core import paths, store  # noqa: E402

    data = paths.DATA
    report = {"root": str(root), "moved": {}, "left": {}, "backup": None, "dry_run": dry}
    dirs = [data / n for n in ("export", "media_cache", "avatars", "decrypt", "pack", "voice")]
    ts = time.strftime("%Y%m%d_%H%M%S")
    backup_root = data / "backup" / f"files_split_{ts}"
    if not dry:
        backup_root.mkdir(parents=True, exist_ok=True)
        report["backup"] = _backup(dirs, backup_root)

    moved: dict = {k: [] for k in ("export", "media_cache", "avatars", "decrypt", "pack", "voice")}
    left: dict = {k: [] for k in moved}

    def _move(src: Path, dst: Path, bucket: str):
        if dry:
            moved[bucket].append(f"{src} -> {dst}")
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            left[bucket].append(f"目标已存在，保留原文件: {src}")
            return
        shutil.move(str(src), str(dst))
        moved[bucket].append(str(dst))

    # export：按 _meta/<job>.json 归属
    ex = data / "export"
    meta = ex / "_meta"
    if ex.is_dir():
        for d in sorted(ex.iterdir()):
            if not d.is_dir() or d.name.startswith("_"):
                continue
            owner = None
            mf = meta / f"{d.name}.json"
            try:
                j = json.loads(mf.read_text(encoding="utf-8")) if mf.is_file() else {}
                owner = int(j.get("account_qq") or 0) or None
            except Exception:  # noqa: BLE001
                owner = None
            if owner:
                _move(d, paths.account_export(owner) / d.name, "export")
                z = ex / f"{d.name}.zip"
                if z.is_file():
                    _move(z, paths.account_export(owner) / z.name, "export")
            else:
                left["export"].append(str(d))

    # media_cache：md5 反查唯一账号
    mc = data / "media_cache"
    if mc.is_dir():
        md5map = _media_md5_map(paths, store)
        for f in sorted(mc.iterdir()):
            if not f.is_file():
                continue
            key = f.stem.lower()
            owners = md5map.get(key) or set()
            if len(owners) == 1:
                qq = next(iter(owners))
                _move(f, paths.account_media_cache(qq) / f.name, "media_cache")
            else:
                left["media_cache"].append(f.name)

    # avatars：uin -> 唯一账号
    av = data / "avatars"
    if av.is_dir():
        for f in sorted(av.iterdir()):
            if not f.is_file():
                continue
            stem = f.stem
            uin = None
            if stem.startswith("group_"):
                uin = stem[6:]
            elif stem.isdigit():
                uin = stem
            owner = None
            if uin and str(uin).isdigit():
                try:
                    owner = store.account_for_uin(int(uin))
                except Exception:  # noqa: BLE001
                    owner = None
            if owner:
                _move(f, paths.account_avatars(owner) / f.name, "avatars")
            else:
                left["avatars"].append(f.name)

    # decrypt / pack：目录名即账号 QQ
    for name, helper in (("decrypt", paths.account_decrypt), ("pack", paths.account_pack)):
        base = data / name
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if d.is_dir() and d.name.isdigit():
                _move(d, helper(int(d.name)) / d.name, name)
            else:
                left[name].append(str(d))

    report["moved"] = {k: v for k, v in moved.items() if v}
    report["left"] = {k: v for k, v in left.items() if v}
    report["moved_counts"] = {k: len(v) for k, v in moved.items()}
    report["left_counts"] = {k: len(v) for k, v in left.items()}
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="对真实 data/ 执行（先备份）")
    ap.add_argument("--selftest", action="store_true", help="合成小夹具自测")
    ap.add_argument("--dry", action="store_true", help="只列出将移动什么，不落盘")
    args = ap.parse_args()

    if args.selftest:
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="qqscope_files_"))
        (tmp / "data" / "accounts" / "111").mkdir(parents=True)
        (tmp / "data" / "accounts" / "222").mkdir(parents=True)
        for qq, md5 in ((111, "a" * 32), (222, "b" * 32)):
            con = sqlite3.connect(str(tmp / "data" / "accounts" / str(qq) / "qqscope.db"))
            con.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, media TEXT)")
            con.execute("INSERT INTO messages(media) VALUES(?)", (json.dumps({"md5": md5}),))
            con.commit(); con.close()
        (tmp / "data" / "media_cache").mkdir(parents=True)
        (tmp / "data" / "media_cache" / ("a" * 32 + ".bin")).write_bytes(b"x")
        (tmp / "data" / "media_cache" / "unknown.bin").write_bytes(b"x")
        (tmp / "data" / "avatars").mkdir(parents=True)
        (tmp / "data" / "avatars" / "111.png").write_bytes(b"x")
        (tmp / "data" / "avatars" / "999999.png").write_bytes(b"x")
        (tmp / "data" / "export" / "j1").mkdir(parents=True)
        (tmp / "data" / "export" / "j1" / "a.txt").write_text("x", encoding="utf-8")
        (tmp / "data" / "export" / "_meta").mkdir(parents=True)
        (tmp / "data" / "export" / "_meta" / "j1.json").write_text(
            json.dumps({"job": "j1", "account_qq": 111}), encoding="utf-8")
        (tmp / "data" / "decrypt" / "111").mkdir(parents=True)
        (tmp / "data" / "decrypt" / "111" / "k").write_bytes(b"x")
        rep = run(tmp, dry=False)
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        # 断言
        okall = True
        def A(cond, msg):
            nonlocal okall
            print(("  PASS " if cond else "  FAIL ") + msg)
            if not cond: okall = False
        A((tmp / "data" / "accounts" / "111" / "media_cache" / ("a" * 32 + ".bin")).is_file(), "media_cache 唯一 md5 -> 111")
        A((tmp / "data" / "media_cache" / "unknown.bin").is_file(), "无法判定 media 留在原处")
        A((tmp / "data" / "accounts" / "111" / "avatars" / "111.png").is_file(), "avatar 111 -> 111")
        A((tmp / "data" / "avatars" / "999999.png").is_file(), "无主 avatar 留在原处")
        A((tmp / "data" / "accounts" / "111" / "export" / "j1" / "a.txt").is_file(), "export j1 -> 111")
        A((tmp / "data" / "accounts" / "111" / "decrypt" / "111" / "k").is_file(), "decrypt/111 -> 111")
        A(bool(rep.get("backup")), "迁移前已备份")
        shutil.rmtree(tmp, ignore_errors=True)
        print("SELFTEST", "PASS" if okall else "FAIL")
        return 0 if okall else 1

    if not args.yes and not args.dry:
        print("默认不碰真实库；用 --selftest 验证，或显式 --yes 执行。")
        return 0
    rep = run(ROOT, dry=args.dry)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())