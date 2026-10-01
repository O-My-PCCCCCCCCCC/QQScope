# -*- coding: utf-8 -*-
"""QQScope · task-11 迁移 CLI：把旧单库 data/qqscope.db 拆分成每账号独立库。

用法：
    python scripts/migrate_split.py --copy     # 在真实库的副本上验证（默认，安全）
    python scripts/migrate_split.py --yes      # 对真实 data/qqscope.db 执行（先备份、旧库改名保留）

迁移逻辑在 core.store.migrate_legacy_split()：
  1) 先备份 data/backup/legacy_split_<ts>.db（sqlite backup，保证一致）
  2) 按 account_qq 拆到 data/accounts/<qq>/qqscope.db
  3) 旧库改名 data/qqscope.db.migrated（连同 -wal/-shm），绝不删除
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _copy_legacy(src_root: Path, dst_root: Path) -> None:
    src = src_root / "data" / "qqscope.db"
    dst = dst_root / "data" / "qqscope.db"
    dst.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect("file:" + src.as_posix() + "?mode=ro", uri=True, timeout=60)
    out = sqlite3.connect(str(dst))
    con.backup(out)
    out.close()
    con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--copy", action="store_true", help="在真实库副本上验证（默认）")
    ap.add_argument("--yes", action="store_true", help="确认对真实 data/qqscope.db 执行")
    ap.add_argument("--force", action="store_true", help="即使 data/accounts/ 已存在也强制迁移")
    args = ap.parse_args()

    real_db = ROOT / "data" / "qqscope.db"
    if not real_db.exists():
        print(json.dumps({"ok": False, "reason": "no_legacy", "message": "没有旧单库，无需迁移"},
                         ensure_ascii=False))
        return 0

    if not args.yes:
        tmp = Path(tempfile.mkdtemp(prefix="qqscope_split_"))
        _copy_legacy(ROOT, tmp)
        os.environ["QQSCOPE_ROOT"] = str(tmp)
        target = tmp
        mode = "copy"
    else:
        target = ROOT
        mode = "real"
        os.environ.setdefault("QQSCOPE_AUTO_SPLIT", "0")

    sys.path.insert(0, str(ROOT))
    from core import paths, store  # noqa: E402

    store.init()
    res = store.migrate_legacy_split(force=args.force)
    out = {"mode": mode, "root": str(target)}
    out.update(res)
    # 每个账号目录的行数
    if res.get("ok"):
        rows = {}
        for qq in res.get("accounts") or []:
            con = store.connect(int(qq))
            try:
                rows[str(qq)] = {
                    "contacts": con.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],
                    "messages": con.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                    "feeds": con.execute("SELECT COUNT(*) FROM feeds").fetchone()[0]
                    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='feeds'").fetchone()
                    else 0,
                }
            finally:
                con.close()
        out["per_account_rows"] = rows
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if res.get("ok") else 0


if __name__ == "__main__":
    sys.exit(main())