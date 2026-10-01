# -*- coding: utf-8 -*-
"""为前端 DOM 隔离测试起一个「真实双账号副本」后端（默认 :15557）。

- 真实 data/qqscope.db 只读 mode=ro 备份到临时根；所有写都在副本；
- 先克隆 A->B，再分别给 A 打 A·/A#、给 B 打 B·/B#（这样 B 里不会含 A·）；
- monkeypatch 掉 framework/bot/pack 的探测，绝不碰 NapCat、不启动实时采集；
- 只监听 127.0.0.1；进程由父级（check_v16_dom.mjs）kill，只停自己起的这个 PID。

用法：python scripts/isolation_serve.py --port 15557
就绪时 stdout 打印：ISO_READY <临时根>
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
A = 1605289411
B = 3060648699
BONLY = [("u_bonly_1", 877700001), ("u_bonly_2", 877700002), ("u_bonly_3", 877700003)]


def _copy_db(tmp: Path) -> sqlite3.Connection:
    real = ROOT / "data" / "qqscope.db"
    dst = tmp / "data" / "qqscope.db"
    src = sqlite3.connect("file:" + real.as_posix() + "?mode=ro", uri=True, timeout=60)
    con = sqlite3.connect(str(dst))
    src.backup(con)
    src.close()
    return con


def _clone_a_to_b(con: sqlite3.Connection) -> None:
    con.execute("INSERT INTO accounts(account_qq,label,source,updated_at) "
                "SELECT ?, label, source, updated_at FROM accounts WHERE account_qq=?", (B, A))
    con.execute("INSERT INTO contacts(account_qq,kind,peer_id,peer_qq,name,remark,avatar,msg_count,"
                "self_count,first_ts,last_ts,last_text,source) "
                "SELECT ?, kind, peer_id, peer_qq, name, remark, avatar, msg_count, self_count, "
                "first_ts, last_ts, last_text, source FROM contacts WHERE account_qq=?", (B, A))
    con.execute("INSERT INTO messages(account_qq,kind,peer_id,peer_qq,ts,direction,sender_qq,"
                "sender_name,msg_type,text,source,content,media) "
                "SELECT ?, kind, peer_id, peer_qq, ts, direction, sender_qq, sender_name, msg_type, "
                "text, source, content, media FROM messages WHERE account_qq=?", (B, A))
    con.execute("INSERT OR REPLACE INTO feeds(id,account_qq,ts,author_qq,author_name,content,images,"
                "praise,comments,forwards,raw,comments_json) "
                "SELECT 'B#'||id, ?, ts, author_qq, author_name, content, images, praise, comments, "
                "forwards, raw, comments_json FROM feeds WHERE account_qq=?", (B, A))
    for i, (pid, pqq) in enumerate(BONLY, 1):
        con.execute("INSERT OR REPLACE INTO contacts(account_qq,kind,peer_id,peer_qq,name,remark,"
                    "msg_count,self_count,first_ts,last_ts,last_text,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (B, "c2c", pid, pqq, f"B·BONLY_{i}", f"B·BONLY_{i}_R", 2, 1, 1700000000,
                     1700000001, f"B#BONLY_{i}_LAST", "iso"))
    con.commit()


def _mark(con: sqlite3.Connection, qq: int, prefix: str, tprefix: str) -> None:
    con.execute("UPDATE accounts SET label=?||COALESCE(label,'') WHERE account_qq=?", (prefix, qq))
    con.execute("UPDATE contacts SET "
                "name=CASE WHEN name IS NULL THEN NULL ELSE ?||name END, "
                "remark=CASE WHEN remark IS NULL THEN NULL ELSE ?||remark END, "
                "last_text=CASE WHEN last_text IS NULL THEN NULL ELSE ?||last_text END "
                "WHERE account_qq=?", (prefix, prefix, tprefix, qq))
    con.execute("UPDATE messages SET text=CASE WHEN text IS NULL THEN NULL ELSE ?||text END "
                "WHERE account_qq=?", (tprefix, qq))
    con.execute("UPDATE feeds SET "
                "content=CASE WHEN content IS NULL THEN NULL ELSE ?||content END, "
                "author_name=CASE WHEN author_name IS NULL THEN NULL ELSE ?||author_name END "
                "WHERE account_qq=?", (tprefix, prefix, qq))
    con.commit()


def build(tmp: Path) -> None:
    con = _copy_db(tmp)
    try:
        _clone_a_to_b(con)
        _mark(con, A, "A·", "A#")
        _mark(con, B, "B·", "B#")
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=15557)
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="qqscope_dom_"))
    (tmp / "data" / "server").mkdir(parents=True, exist_ok=True)
    (tmp / "data_root").mkdir(parents=True, exist_ok=True)

    os.environ["QQSCOPE_ROOT"] = str(tmp)
    os.environ["QQSCOPE_DATA_ROOT"] = str(tmp / "data_root")
    os.environ["QQSCOPE_LIVE_AUTOSTART"] = "0"
    os.environ["PYTHONIOENCODING"] = "utf-8"

    build(tmp)

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "server"))

    # 不碰 NapCat / 不扫本机框架：把探测函数换成静态值
    from core import framework_log
    fake_gate = {"framework_running": True, "logged_in": True, "account": A,
                 "nickname": "DOMLOGIN", "sync_allowed": True, "can_stop": True,
                 "pid": 0, "qr": {}}
    framework_log.login_gate = lambda *a, **k: dict(fake_gate)
    framework_log.framework_status = lambda *a, **k: {"running": True, "pid": 0,
                                                      "logged_in": True, "account": A,
                                                      "detail": {"source": "iso"}}
    framework_log.qrcode_info = lambda *a, **k: {"available": False, "mtime": 0}
    try:
        from core.sources import bot_source, pack_source
        bot_source.status = lambda *a, **k: {"ready": True, "message": "iso-dom", "detail": {}}
        pack_source.status = lambda *a, **k: {"ready": True, "message": "iso-dom", "detail": {}}
    except Exception:  # noqa: BLE001
        pass

    import app as qqapp
    import uvicorn

    print("ISO_READY " + str(tmp), flush=True)
    try:
        uvicorn.run(qqapp.app, host="127.0.0.1", port=int(args.port), log_level="warning")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())