# -*- coding: utf-8 -*-
"""QQScope · 多账号数据隔离自动验收（task-7 / v15）

红线（本脚本自证）：
  * 绝不改真实 data/qqscope.db —— 用 sqlite 只读备份到临时目录，所有写入都在副本；
  * 不碰 NapCat / 不重启 15555 —— 纯进程内 FastAPI TestClient，不监听任何端口；
  * 不真实发消息 —— /api/send 只测「拒绝」分支，正向用 dry_run。

用法：
    tools\\nt_msg_db_util\\.venv\\Scripts\\python.exe scripts\\isolation_test.py
退出码 0 = 全部 PASS。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

DECOY = 999999999
DSTR = "DECOY_ONLY_9"
DPEER = "u_decoy_9"

RESULTS: list = []
_TMP: Path | None = None


def check(name, cond, detail=""):
    ok = bool(cond)
    RESULTS.append((name, ok, str(detail)))
    print(("[PASS] " if ok else "[FAIL] ") + name + (("  " + str(detail)) if detail else ""))
    return ok


def blob(data) -> str:
    try:
        return json.dumps(data, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return str(data)


# ── 1) 临时根 + 只读备份真实库（绝不写真实库）──────────────────────────────
def setup_temp_root() -> Path:
    global _TMP
    tmp = Path(tempfile.mkdtemp(prefix="qqscope_iso_"))
    _TMP = tmp
    (tmp / "data" / "server").mkdir(parents=True, exist_ok=True)
    (tmp / "data_root").mkdir(parents=True, exist_ok=True)
    real = ROOT / "data" / "qqscope.db"
    dst = tmp / "data" / "qqscope.db"
    copied = "none"
    if real.exists():
        try:
            src = sqlite3.connect("file:" + real.as_posix() + "?mode=ro", uri=True, timeout=30)
            con = sqlite3.connect(str(dst))
            src.backup(con)
            src.close()
            con.close()
            copied = "ro-backup"
        except Exception:  # noqa: BLE001
            shutil.copy2(real, dst)
            for suf in ("-wal", "-shm"):
                f = Path(str(real) + suf)
                if f.exists():
                    shutil.copy2(f, Path(str(dst) + suf))
            copied = "file-copy"
    os.environ["QQSCOPE_ROOT"] = str(tmp)
    os.environ["QQSCOPE_DATA_ROOT"] = str(tmp / "data_root")
    os.environ["QQSCOPE_LIVE_AUTOSTART"] = "0"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    print(f"[setup] 临时根 = {tmp}  (real db copy: {copied})")
    return tmp


# ── 2) 在副本里造诱饵账号 999999999 ────────────────────────────────────────
def seed_decoy(tmp: Path) -> int:
    from core import store
    from core.sources import qzone
    store.init()
    qcon = qzone._connect()
    qcon.close()
    con = store.connect()
    try:
        con.execute("INSERT OR REPLACE INTO accounts(account_qq,label,source,updated_at) "
                    "VALUES(?,?,?,?)", (DECOY, DSTR + "_account", "iso", int(time.time())))
        con.execute(
            "INSERT OR REPLACE INTO contacts(account_qq,kind,peer_id,peer_qq,name,remark,avatar,"
            "msg_count,self_count,first_ts,last_ts,last_text,source) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (DECOY, "c2c", DPEER, DECOY, DSTR + "_NAME", DSTR + "_REMARK", None,
             3, 3, 1700000000, 1700000003, DSTR + "_LAST", "iso"))
        rows = [
            (DECOY, "c2c", DPEER, DECOY, 1700000000, 1, DECOY, DSTR + "_SENDER", 0,
             DSTR + "_MSG", "iso", None,
             json.dumps({"kind": "image", "file": "Pic/decoy/decoy.png",
                         "name": DSTR + "_MEDIA.png", "md5": "0" * 32}, ensure_ascii=False)),
            (DECOY, "c2c", DPEER, DECOY, 1700000001, 1, DECOY, DSTR + "_SENDER", 0,
             DSTR + "_MSG2", "iso", None,
             json.dumps({"kind": "image", "md5": "a" * 32}, ensure_ascii=False)),
            (DECOY, "c2c", DPEER, DECOY, 1700000002, 1, DECOY, DSTR + "_SENDER", 0,
             DSTR + "_SEND", "local_send", None, None),
        ]
        con.executemany(
            "INSERT INTO messages(account_qq,kind,peer_id,peer_qq,ts,direction,sender_qq,"
            "sender_name,msg_type,text,source,content,media) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        mid = con.execute("SELECT id FROM messages WHERE account_qq=? AND text=?",
                          (DECOY, DSTR + "_MSG")).fetchone()[0]
        con.execute(
            "INSERT OR REPLACE INTO feeds(id,account_qq,ts,author_qq,author_name,content,images,"
            "praise,comments,forwards,raw,comments_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (DSTR + "_FEEDID", DECOY, 1700000002, DECOY, DSTR + "_AUTHOR", DSTR + "_FEED",
             "[]", 0, 0, 0, None, "[]"))
        con.commit()
        return int(mid)
    finally:
        con.close()


def ensure_real_account() -> int:
    """挑一个真实账号 A；若库是空的（全新环境）就造一个合成 A。"""
    from core import store
    accs = [a for a in store.list_accounts() if int(a["account_qq"]) != DECOY]
    for a in accs:
        if a["contacts"] > 0 and a["messages"] > 0:
            return int(a["account_qq"])
    if accs:
        return int(accs[0]["account_qq"])
    A = 123456789
    con = store.connect()
    try:
        con.execute("INSERT OR REPLACE INTO accounts(account_qq,label,source,updated_at) "
                    "VALUES(?,?,?,?)", (A, "REAL_A_ONLY", "iso", int(time.time())))
        con.execute("INSERT OR REPLACE INTO contacts(account_qq,kind,peer_id,peer_qq,name,"
                    "msg_count,self_count,first_ts,last_ts,last_text,source) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (A, "c2c", "u_real_a", 222222222, "REAL_A_FRIEND",
                     1, 1, 1700000000, 1700000000, "REAL_A_TEXT", "iso"))
        con.execute("INSERT INTO messages(account_qq,kind,peer_id,peer_qq,ts,direction,sender_qq,"
                    "sender_name,msg_type,text,source) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (A, "c2c", "u_real_a", 222222222, 1700000000, 1, A, "REAL_A", 0,
                     "REAL_A_TEXT", "iso"))
        con.execute("INSERT OR REPLACE INTO feeds(id,account_qq,ts,author_qq,author_name,content,"
                    "images,comments_json) VALUES(?,?,?,?,?,?,?,?)",
                    ("REAL_A_FEED", A, 1700000000, A, "REAL_A", "REAL_A_FEED", "[]", "[]"))
        con.commit()
    finally:
        con.close()
    return A


def main() -> int:
    tmp = setup_temp_root()
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "server"))

    decoy_mid = seed_decoy(tmp)
    A = ensure_real_account()
    print(f"[setup] 真实账号 A = {A} ; 诱饵账号 = {DECOY} ; 诱饵 msg_id = {decoy_mid}")

    # 诱饵媒体真实文件（让「正确账号」能拿到 200）
    mf = tmp / "data_root" / str(DECOY) / "nt_qq" / "nt_data" / "Pic" / "decoy" / "decoy.png"
    mf.parent.mkdir(parents=True, exist_ok=True)
    mf.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)

    from fastapi.testclient import TestClient
    import app as qqapp                     # server/app.py（真实启动方式）
    from core import store, export as ex, send as send_mod
    import core.sources.bot_source as bot_source

    qqapp_client = TestClient(qqapp.app)

    def get(path):
        r = qqapp_client.get(path)
        try:
            data = r.json()
        except Exception:  # noqa: BLE001
            data = None
        return r.status_code, data

    def post(path, body):
        r = qqapp_client.post(path, json=body)
        try:
            data = r.json()
        except Exception:  # noqa: BLE001
            data = None
        return r.status_code, data

    print("\n===== A) 缺 account 一律拒绝（不得全局兜底）=====")
    for path in ("/api/contacts", "/api/overview", "/api/feeds", "/api/live/events",
                 "/api/send/history", "/api/data/quality", "/api/export/list",
                 "/api/media/1"):
        st, _d = get(path)
        check(f"缺 account -> {path} 拒绝", st in (400, 422), f"status={st}")
    st, _d = get(f"/api/media/{decoy_mid}")
    check(f"缺 account -> /api/media/{decoy_mid} 拒绝", st in (400, 422), f"status={st}")

    print("\n===== B) contacts 隔离 =====")
    st, d = get(f"/api/contacts?account={A}")
    check("A 的 contacts 不含诱饵", st == 200 and DSTR not in blob(d) and DPEER not in blob(d), f"status={st}")
    st2, d2 = get(f"/api/contacts?account={DECOY}")
    check("诱饵账号 contacts 能看到诱饵（正对照）", st2 == 200 and DSTR in blob(d2), f"status={st2}")

    print("\n===== C) messages / report / overview 隔离 =====")
    st, d = get(f"/api/messages?account={A}&kind=c2c&peer_id={DPEER}")
    check("A 按诱饵 peer 查消息 -> 空", st == 200 and not (d or {}).get("messages"), f"status={st} n={len((d or {}).get('messages') or [])}")
    st, d = get(f"/api/messages?account={DECOY}&kind=c2c&peer_id={DPEER}")
    check("诱饵账号按诱饵 peer 查消息 -> 能看到", st == 200 and DSTR in blob(d), f"status={st}")
    st, d = get(f"/api/report?account={A}")
    check("A 的 report 不含诱饵", st == 200 and DSTR not in blob(d), f"status={st}")
    st, d = get(f"/api/report?account={DECOY}")
    check("诱饵账号 report 能看到诱饵", st == 200 and DSTR in blob(d), f"status={st}")
    con = store.connect()
    try:
        a_total = con.execute("SELECT COUNT(*) FROM messages WHERE account_qq=?", (A,)).fetchone()[0]
        a_contacts = con.execute("SELECT COUNT(*) FROM contacts WHERE account_qq=?", (A,)).fetchone()[0]
    finally:
        con.close()
    st, d = get(f"/api/overview?account={A}")
    check("A 的 overview 只统计 A（总数/会话数对得上，不含诱饵）",
          st == 200 and int((d or {}).get("total") or 0) == a_total
          and int((d or {}).get("contacts") or 0) == a_contacts,
          f"A_total={a_total} got={((d or {}).get('total'))} contactsA={a_contacts} got={((d or {}).get('contacts'))}")
    st, d = get(f"/api/overview?account={DECOY}")
    check("诱饵账号 overview 只统计诱饵", st == 200 and int((d or {}).get("total") or 0) >= 3, f"status={st}")

    print("\n===== D) profile / contact 隔离 =====")
    st, d = get(f"/api/contact?account={A}&kind=c2c&peer_id={DPEER}")
    check("A 查诱饵资料 -> 404 找不到", st == 404 and DSTR not in blob(d), f"status={st}")
    st, d = get(f"/api/contact?account={DECOY}&kind=c2c&peer_id={DPEER}")
    check("诱饵账号查诱饵资料 -> 能看到", st == 200 and DSTR in blob(d) and DPEER in blob(d), f"status={st}")
    st, d = get(f"/api/profile?account={A}")
    check("A 的 profile 不含诱饵", st == 200 and int((d or {}).get("account_qq") or 0) == A and DSTR not in blob(d), f"status={st}")
    st, d = get(f"/api/profile?account={DECOY}")
    check("诱饵账号 profile 归属正确", st == 200 and int((d or {}).get("account_qq") or 0) == DECOY, f"status={st}")

    print("\n===== E) feeds（QZone 动态）隔离 =====")
    st, d = get(f"/api/feeds?account={A}&limit=50")
    check("A 的 feeds 不含诱饵", st == 200 and DSTR not in blob(d), f"status={st}")
    st, d = get(f"/api/feeds?account={DECOY}&limit=50&scope=all")
    check("诱饵账号 feeds 能看到诱饵", st == 200 and DSTR in blob(d), f"status={st}")
    st, d = get(f"/api/feeds?account={DECOY}&scope=mine")
    check("scope=mine 不再回退全局 uin（仍按 account 过滤）", st == 200 and DSTR in blob(d), f"status={st}")

    print("\n===== F) live events 隔离 =====")
    st, d = get(f"/api/live/events?account={A}&limit=50")
    check("A 的 live events 不含诱饵", st == 200 and DSTR not in blob(d) and DPEER not in blob(d), f"status={st}")
    st, d = get(f"/api/live/events?account={DECOY}&limit=50")
    check("诱饵账号 live events 能看到诱饵", st == 200 and DSTR in blob(d), f"status={st}")

    print("\n===== G) send history 隔离 =====")
    st, d = get(f"/api/send/history?account={A}")
    check("A 的 send history 不含诱饵", st == 200 and DSTR not in blob(d), f"status={st}")
    st, d = get(f"/api/send/history?account={DECOY}")
    check("诱饵账号 send history 能看到诱饵", st == 200 and DSTR in blob(d), f"status={st}")

    print("\n===== H) media 归属校验 =====")
    st, d = get(f"/api/media/{decoy_mid}?account={A}")
    check("拿 A 的 account 读诱饵媒体 -> 拒绝(404)", st == 404 and DSTR not in blob(d), f"status={st}")
    r = qqapp_client.get(f"/api/media/{decoy_mid}?account={DECOY}")
    check("正确账号读诱饵媒体 -> 200 文件流", r.status_code == 200 and r.headers.get("X-Media-Kind") == "image",
          f"status={r.status_code} kind={r.headers.get('X-Media-Kind')}")
    st, d = get(f"/api/media/stats?account={A}")
    check("A 的 media stats 不含诱饵", st == 200 and DSTR not in blob(d) and str(DECOY) not in blob(d), f"status={st}")
    st, d = get(f"/api/media/stats?account={DECOY}")
    check("诱饵账号 media stats 只含诱饵目录", st == 200 and str(DECOY) in blob(d), f"status={st}")
    st, d = get(f"/api/media/pending?account={A}")
    check("A 的 media pending 不含诱饵", st == 200 and DSTR not in blob(d), f"status={st}")
    st, d = get(f"/api/media/pending?account={DECOY}")
    check("诱饵账号 media pending 能看到诱饵", st == 200 and DSTR in blob(d), f"status={st}")

    print("\n===== I) data quality 隔离 =====")
    st, d = get(f"/api/data/quality?account={A}")
    check("A 的 data quality 归 A", st == 200 and int((d or {}).get("account_qq") or 0) == A, f"status={st}")
    st, d = get(f"/api/data/quality?account={DECOY}")
    check("诱饵账号 data quality 归诱饵", st == 200 and int((d or {}).get("account_qq") or 0) == DECOY
          and int((d or {}).get("c2c_contacts") or 0) >= 1, f"status={st}")

    print("\n===== J) 导出隔离（真实写盘只在临时目录）=====")
    con = store.connect()
    try:
        target = con.execute("SELECT kind, peer_id FROM contacts WHERE account_qq=? "
                             "AND COALESCE(msg_count,0) > 0 "
                             "ORDER BY msg_count ASC LIMIT 1", (A,)).fetchone()
    finally:
        con.close()
    targets = [{"kind": target["kind"], "peer_id": target["peer_id"]}] if target else []
    res = ex.export(A, targets, ["txt", "html"], "per_peer", {"media": False}) if targets else {}
    job = res.get("job") or ""
    text_all = ""
    if job:
        jd = ex.paths.EXPORT_DIR / job
        for f in jd.rglob("*"):
            if f.is_file():
                text_all += f.read_text(encoding="utf-8", errors="replace")
    check("导出 A 的会话不含诱饵文本", bool(job) and DSTR not in text_all, f"job={job} files_chars={len(text_all)}")
    meta = ex.job_meta(job) if job else None
    check("导出任务元数据归属 A", bool(meta) and int(meta.get("account_qq") or 0) == A, f"meta={meta}")
    st, d = get(f"/api/export/list?account={A}")
    check("A 的导出历史能看到自己的任务", st == 200 and job in blob(d), f"status={st}")
    st, d = get(f"/api/export/list?account={DECOY}")
    check("诱饵账号导出历史看不到 A 的任务", st == 200 and job not in blob(d), f"status={st}")
    if job:
        st, _d = get(f"/api/export/download?job={job}&account={A}")
        check("A 下载自己的导出 -> 200", st == 200, f"status={st}")
        st, _d = get(f"/api/export/download?job={job}&account={DECOY}")
        check("诱饵账号下载 A 的导出 -> 拒绝", st == 404, f"status={st}")
        st, _d = get(f"/api/export/download?job={job}")
        check("缺 account 下载导出 -> 拒绝", st == 400, f"status={st}")

    print("\n===== K) /api/send 账号一致性 =====")
    st, d = post("/api/send", {"kind": "c2c", "peer_id": str(A), "peer_qq": A,
                               "text": "ISO_PROBE", "confirm": True})
    check("send 缺 account_qq -> 400", st == 400 and "account_qq" in blob(d), f"status={st}")
    st, d = post("/api/send", {"account_qq": A, "kind": "c2c", "peer_id": str(A), "peer_qq": A,
                               "text": "ISO_PROBE", "confirm": True, "dry_run": True})
    check("send 账号一致 + dry_run -> 200（不真发）", st == 200 and (d or {}).get("dry_run") is True, f"status={st}")
    # 账号不一致：monkeypatch 本地登录号，确保未到达真正发送就拒绝
    orig_ep, orig_login = send_mod._endpoint, bot_source._login
    try:
        send_mod._endpoint = lambda: ("http://127.0.0.1:1", "")
        bot_source._login = lambda base, token: (int(A), "iso")
        st, d = post("/api/send", {"account_qq": DECOY, "kind": "c2c", "peer_id": str(A),
                                   "peer_qq": A, "text": "ISO_PROBE", "confirm": True})
        check("send account 与登录号不一致 -> 400 拒绝", st == 400 and (d or {}).get("code") == "account_mismatch",
              f"status={st} code={(d or {}).get('code')}")
    finally:
        send_mod._endpoint, bot_source._login = orig_ep, orig_login

    print("\n===== L) store 危险 API 强校验 =====")
    try:
        store.search_messages(DSTR + "_MSG")
        check("search_messages 缺 account -> raise", False, "没有 raise")
    except ValueError as e:
        check("search_messages 缺 account -> raise", True, str(e)[:40])
    rows = store.search_messages(DSTR + "_MSG", account_qq=A)
    check("search_messages(A) 搜不到诱饵", all(DSTR not in (r.get("text") or "") for r in rows), f"n={len(rows)}")
    rows = store.search_messages(DSTR + "_MSG", account_qq=DECOY)
    check("search_messages(诱饵账号) 搜得到", any(DSTR in (r.get("text") or "") for r in rows), f"n={len(rows)}")
    for fn, nm in ((lambda: store.list_contacts(), "list_contacts()"),
                   (lambda: store.overview(), "overview()"),
                   (lambda: store.count_messages(), "count_messages()")):
        try:
            fn()
            check(f"{nm} 缺 account -> raise", False, "没有 raise")
        except ValueError:
            check(f"{nm} 缺 account -> raise", True, "")

    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n===== 隔离测试总结 =====")
    print(f"  {len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    if bad:
        print("  未通过：" + ", ".join(bad))
        print(f"  临时根（便于排查）：{tmp}")
        return 1
    print("  全绿 ✅")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        print("\n[FAIL] isolation_test 异常中止")
        sys.exit(1)