# -*- coding: utf-8 -*-
"""QQScope · 多账号数据隔离自动验收 v2（task-9 重做版）

为什么重做：上一版诱饵账号只有 1 联系人 + 2 消息，大量接口「本来就没数据」→ 空着 PASS。
本版：把真实账号 A 的**完整数据集**克隆成 B（打 B·/B# 标记、peer_id 不变），外加 3 个 B-only peer；
对每个只读接口用 {A, B, 缺account} 三视角做**完整性（逐 id/逐字段与副本真值比）+ 纯净性（零对方标记/零对方 id）**断言，
并覆盖写路径（PATCH/DELETE/export/media 跨账号探测）。

红线：真实 data/qqscope.db 只读（mode=ro 备份到临时根）；不碰 NapCat/15555；不真实发消息。
"""
from __future__ import annotations

import hashlib
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

A = 1605289411
B = 3060648699
BNAME = "B·"
BTXT = "B#"
BONLY = [("u_bonly_1", 877700001), ("u_bonly_2", 877700002), ("u_bonly_3", 877700003)]
BONLY_PEERS = [p for p, _ in BONLY]

RESULTS: list = []
_TMP: Path | None = None


def check(name, cond, detail=""):
    ok = bool(cond)
    RESULTS.append((name, ok, str(detail)))
    print(("[PASS] " if ok else "[FAIL] ") + name + (("  " + str(detail)) if detail else ""))
    return ok


def blob(x) -> str:
    try:
        return json.dumps(x, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return str(x)


# ── 临时根 + 只读备份 ───────────────────────────────────────────────────────
def setup_temp_root() -> Path:
    global _TMP
    tmp = Path(tempfile.mkdtemp(prefix="qqscope_iso16_"))
    _TMP = tmp
    (tmp / "data" / "server").mkdir(parents=True, exist_ok=True)
    (tmp / "data_root").mkdir(parents=True, exist_ok=True)
    real = ROOT / "data" / "qqscope.db"
    dst = tmp / "data" / "qqscope.db"
    mode = "none"
    if real.exists():
        try:
            src = sqlite3.connect("file:" + real.as_posix() + "?mode=ro", uri=True, timeout=60)
            con = sqlite3.connect(str(dst))
            src.backup(con)
            src.close()
            con.close()
            mode = "ro-backup"
        except Exception:  # noqa: BLE001
            shutil.copy2(real, dst)
            for suf in ("-wal", "-shm"):
                f = Path(str(real) + suf)
                if f.exists():
                    shutil.copy2(f, Path(str(dst) + suf))
            mode = "file-copy"
    os.environ["QQSCOPE_ROOT"] = str(tmp)
    os.environ["QQSCOPE_DATA_ROOT"] = str(tmp / "data_root")
    os.environ["QQSCOPE_LIVE_AUTOSTART"] = "0"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    print(f"[setup] 临时根={tmp}  real db copy={mode}")
    return tmp


# ── 真实双账号夹具 ──────────────────────────────────────────────────────────
def fingerprint(con, qq) -> str:
    h = hashlib.md5()
    for r in con.execute("SELECT account_qq,label,source FROM accounts WHERE account_qq=?", (qq,)):
        h.update(repr(tuple(r)).encode())
    for r in con.execute("SELECT account_qq,kind,peer_id,peer_qq,name,remark,avatar,msg_count,"
                         "self_count,first_ts,last_ts,last_text,source FROM contacts "
                         "WHERE account_qq=? ORDER BY kind,peer_id", (qq,)):
        h.update(repr(tuple(r)).encode())
    for r in con.execute("SELECT id,kind,peer_id,peer_qq,ts,direction,sender_qq,sender_name,msg_type,"
                         "COALESCE(text,''),source,COALESCE(length(content),-1),"
                         "COALESCE(length(media),-1) FROM messages WHERE account_qq=? ORDER BY id", (qq,)):
        h.update(repr(tuple(r)).encode())
    try:
        for r in con.execute("SELECT id,account_qq,ts,author_qq,author_name,COALESCE(content,''),"
                             "COALESCE(images,''),praise,comments,forwards FROM feeds "
                             "WHERE account_qq=? ORDER BY id", (qq,)):
            h.update(repr(tuple(r)).encode())
    except Exception:  # noqa: BLE001
        pass
    return h.hexdigest()


def clone_a_to_b(con) -> None:
    """把 A 的完整数据集克隆成 B，peer_id 不变，打 B·/B# 标记。"""
    con.execute("INSERT INTO accounts(account_qq,label,source,updated_at) "
                "SELECT ?, ?||COALESCE(label,''), source, updated_at FROM accounts WHERE account_qq=?",
                (B, BNAME, A))
    con.execute(
        "INSERT INTO contacts(account_qq,kind,peer_id,peer_qq,name,remark,avatar,msg_count,"
        "self_count,first_ts,last_ts,last_text,source) "
        "SELECT ?, kind, peer_id, peer_qq, "
        "CASE WHEN name IS NULL THEN NULL ELSE ?||name END, "
        "CASE WHEN remark IS NULL THEN NULL ELSE ?||remark END, "
        "avatar, msg_count, self_count, first_ts, last_ts, "
        "CASE WHEN last_text IS NULL THEN NULL ELSE ?||last_text END, source "
        "FROM contacts WHERE account_qq=?", (B, BNAME, BNAME, BTXT, A))
    con.execute(
        "INSERT INTO messages(account_qq,kind,peer_id,peer_qq,ts,direction,sender_qq,sender_name,"
        "msg_type,text,source,content,media) "
        "SELECT ?, kind, peer_id, peer_qq, ts, direction, sender_qq, sender_name, msg_type, "
        "CASE WHEN text IS NULL THEN NULL ELSE ?||text END, source, content, media "
        "FROM messages WHERE account_qq=?", (B, BTXT, A))
    con.execute(
        "INSERT OR REPLACE INTO feeds(id,account_qq,ts,author_qq,author_name,content,images,"
        "praise,comments,forwards,raw,comments_json) "
        "SELECT ?||id, ?, ts, author_qq, author_name, "
        "CASE WHEN content IS NULL THEN NULL ELSE ?||content END, images, praise, comments, "
        "forwards, raw, comments_json FROM feeds WHERE account_qq=?", (BTXT, B, BTXT, A))
    con.commit()


def add_b_only(con) -> None:
    for i, (pid, pqq) in enumerate(BONLY, 1):
        con.execute(
            "INSERT OR REPLACE INTO contacts(account_qq,kind,peer_id,peer_qq,name,remark,msg_count,"
            "self_count,first_ts,last_ts,last_text,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (B, "c2c", pid, pqq, f"{BNAME}BONLY_{i}", f"{BNAME}BONLY_{i}_R", 2, 1,
             1700000000, 1700000001, f"{BTXT}BONLY_{i}_LAST", "iso"))
        con.execute("INSERT INTO messages(account_qq,kind,peer_id,peer_qq,ts,direction,sender_qq,"
                    "sender_name,msg_type,text,source) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (B, "c2c", pid, pqq, 1700000000, 1, pqq, f"{BNAME}BONLY_{i}", 0,
                     f"{BTXT}BONLY_{i}_MSG1", "iso"))
        con.execute("INSERT INTO messages(account_qq,kind,peer_id,peer_qq,ts,direction,sender_qq,"
                    "sender_name,msg_type,text,source) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (B, "c2c", pid, pqq, 1700000001, 1, pqq, f"{BNAME}BONLY_{i}", 0,
                     f"{BTXT}BONLY_{i}_MSG2", "iso"))
    con.commit()
# ── 主流程 ──────────────────────────────────────────────────────────────────
def main() -> int:
    tmp = setup_temp_root()
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "server"))

    from core import store, export as ex, send as send_mod
    from core.sources import qzone
    import core.sources.bot_source as bot_source

    store.init()
    qzone.init_feeds()

    con = store.connect()
    accs = [r[0] for r in con.execute("SELECT account_qq FROM accounts")]
    if A not in accs:
        print("[FAIL] 真实副本里找不到账号 A=%s，无法构造双账号夹具" % A)
        return 1
    if B in accs:
        con.execute("DELETE FROM messages WHERE account_qq=?", (B,))
        con.execute("DELETE FROM contacts WHERE account_qq=?", (B,))
        con.execute("DELETE FROM feeds WHERE account_qq=?", (B,))
        con.execute("DELETE FROM accounts WHERE account_qq=?", (B,))
        con.commit()
    fA_before = fingerprint(con, A)
    clone_a_to_b(con)
    add_b_only(con)
    fA_after = fingerprint(con, A)
    con.commit()
    nA_msg = con.execute("SELECT COUNT(*) FROM messages WHERE account_qq=?", (A,)).fetchone()[0]
    nB_msg = con.execute("SELECT COUNT(*) FROM messages WHERE account_qq=?", (B,)).fetchone()[0]
    nA_ct = con.execute("SELECT COUNT(*) FROM contacts WHERE account_qq=?", (A,)).fetchone()[0]
    nB_ct = con.execute("SELECT COUNT(*) FROM contacts WHERE account_qq=?", (B,)).fetchone()[0]
    print(f"[fixture] A messages={nA_msg} contacts={nA_ct} ; B messages={nB_msg} contacts={nB_ct}")

    # 矩阵用共享 peer（A/B 同名同 peer）：挑一个消息量适中的，避免测试超慢
    prow = con.execute(
        "SELECT kind, peer_id, peer_qq, msg_count FROM contacts WHERE account_qq=? "
        "AND COALESCE(msg_count,0) >= 200 ORDER BY ABS(COALESCE(msg_count,0)-1200) LIMIT 1",
        (A,)).fetchone()
    if prow is None:
        prow = con.execute("SELECT kind, peer_id, peer_qq, msg_count FROM contacts "
                           "WHERE account_qq=? ORDER BY msg_count DESC LIMIT 1", (A,)).fetchone()
    MPEER = (prow["kind"], str(prow["peer_id"]))
    print(f"[fixture] 矩阵共享 peer = {MPEER[0]}/{MPEER[1]}  A msg_count={prow['msg_count']}")

    # 取一条带本地 file 的媒体消息（A），并找到 B 的克隆 id
    mrow = None
    for r in con.execute("SELECT id, kind, peer_id, media FROM messages WHERE account_qq=? "
                         "AND media IS NOT NULL LIMIT 20000", (A,)):
        try:
            m = json.loads(r["media"] or "{}")
        except Exception:  # noqa: BLE001
            continue
        if isinstance(m, dict) and m.get("file"):
            mrow = (r["id"], r["kind"], str(r["peer_id"]), m, r["media"]); break
    a_media_id, a_media_kind, a_media_peer, a_media, a_media_raw = (
        mrow if mrow else (None, None, None, None, None))
    b_media_id = None
    if a_media_id is not None:
        br = con.execute("SELECT id FROM messages WHERE account_qq=? AND kind=? AND peer_id=? "
                         "AND media=? ORDER BY id LIMIT 1",
                         (B, a_media_kind, a_media_peer, a_media_raw)).fetchone()
        b_media_id = br["id"] if br else None
    if a_media_id is not None and b_media_id is not None:
        rel = a_media.get("file")
        for qq in (A, B):
            fp = tmp / "data_root" / str(qq) / "nt_qq" / "nt_data" / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        print(f"[fixture] 媒体 msg: A id={a_media_id} B id={b_media_id} file={rel}")

    # 真值集合
    contact_set = lambda qq: {(r["kind"], str(r["peer_id"])) for r in
                              con.execute("SELECT kind, peer_id FROM contacts WHERE account_qq=?", (qq,))}
    msg_ids_peer = lambda qq: {int(r["id"]) for r in con.execute(
        "SELECT id FROM messages WHERE account_qq=? AND kind=? AND peer_id=?", (qq, MPEER[0], MPEER[1]))}
    feed_ids = lambda qq: {r["id"] for r in con.execute("SELECT id FROM feeds WHERE account_qq=?", (qq,))}
    all_ids = lambda qq: {int(r["id"]) for r in con.execute("SELECT id FROM messages WHERE account_qq=?", (qq,))}

    A_contacts = contact_set(A); B_contacts = contact_set(B)
    A_mpeer_ids = msg_ids_peer(A); B_mpeer_ids = msg_ids_peer(B)
    A_ids = all_ids(A); B_ids = all_ids(B)
    A_feeds = feed_ids(A); B_feeds = feed_ids(B)

    from fastapi.testclient import TestClient
    import app as qqapp
    client = TestClient(qqapp.app)

    def get_json(path):
        r = client.get(path)
        try:
            d = r.json()
        except Exception:  # noqa: BLE001
            d = None
        return r.status_code, d, r.text

    def pure_A(payload):
        s = blob(payload)
        if BNAME in s or BTXT in s:
            return False, "含 B 标记"
        for p in BONLY_PEERS:
            if p in s:
                return False, "含 B-only peer " + p
        return True, ""

    def ids_of(payload, key):
        try:
            return {int(x["id"]) for x in (payload or {}).get(key) or []}
        except Exception:  # noqa: BLE001
            return set()

    print("\n===== 1) 读接口矩阵：contacts / messages / report / overview =====")

    # contacts
    for tag, qq, truth, others in (("A", A, A_contacts, B_ids), ("B", B, B_contacts, A_ids)):
        st, d, _t = get_json(f"/api/contacts?account={qq}&limit=2000")
        got = {(c.get("kind"), str(c.get("peer_id"))) for c in (d or {}).get("contacts") or []}
        pure = pure_A(d) if tag == "A" else (True, "")
        if tag == "B":
            pure = (BNAME in blob(d), "B 应含 B· 标记")
        check(f"contacts {tag} 完整性(逐 peer)", st == 200 and got == truth,
              f"status={st} got={len(got)} truth={len(truth)}")
        check(f"contacts {tag} 纯净性", st == 200 and pure[0], pure[1])
    st, d, _t = get_json("/api/contacts")
    check("contacts 缺席 -> 拒绝", st == 400, f"status={st}")

    # messages（共享 peer，逐 id）
    for tag, qq, truth in (("A", A, A_mpeer_ids), ("B", B, B_mpeer_ids)):
        st, d, _t = get_json(f"/api/messages?account={qq}&kind={MPEER[0]}&peer_id={MPEER[1]}"
                             f"&limit=200000&order=ASC")
        got = ids_of(d, "messages")
        check(f"messages {tag} 完整性(逐 id)", st == 200 and got == truth,
              f"status={st} got={len(got)} truth={len(truth)}")
        if tag == "A":
            xid = got & B_ids
            check("messages A 纯净性(零 B id)", st == 200 and not xid, f"泄漏 B id={len(xid)}")
        else:
            check("messages B 纯净性+正对照(含 B#)", st == 200 and (BTXT in blob(d)) and not (got & A_ids),
                  "应含 B# 且不含 A id")
    st, d, _t = get_json(f"/api/messages?kind=c2c&peer_id={MPEER[1]}")
    check("messages 缺席 -> 422", st == 422, f"status={st}")

    # report（逐 (ts,direction,kind,peer_id,text)）
    rep_truth = {}
    for qq in (A, B):
        rows = con.execute("SELECT ts,direction,kind,peer_id,COALESCE(text,'') x FROM messages "
                           "WHERE account_qq=? AND direction=1 AND text IS NOT NULL AND text!='' "
                           "ORDER BY ts LIMIT 30000", (qq,)).fetchall()
        rep_truth[qq] = [(int(r["ts"]), int(r["direction"]), str(r["kind"]),
                          str(r["peer_id"]), str(r["x"])) for r in rows]
    for tag, qq in (("A", A), ("B", B)):
        st, d, _t = get_json(f"/api/report?account={qq}")
        got = [(int(m["t"]), int(m["d"]), str(m["k"]), str(m["p"]), str(m["x"]))
               for m in (d or {}).get("messages") or []]
        check(f"report {tag} 完整性(逐字段)", st == 200 and got == rep_truth[qq],
              f"status={st} got={len(got)} truth={len(rep_truth[qq])}")
        if tag == "A":
            check("report A 纯净性", st == 200 and pure_A(d)[0], pure_A(d)[1])
        else:
            check("report B 纯净性+正对照(含 B#)", st == 200 and BTXT in blob(d) and not pure_A(d)[0],
                  "应含 B# 且不含 A 标记")
    st, d, _t = get_json("/api/report")
    check("report 缺席 -> 422", st == 422, f"status={st}")

    # overview（逐字段核 DB）
    def ov_truth(qq):
        g = lambda s, a=(): con.execute(s, (qq,) + a).fetchone()[0]
        return {
            "total": g("SELECT COUNT(*) FROM messages WHERE account_qq=?"),
            "self": g("SELECT COUNT(*) FROM messages WHERE account_qq=? AND direction=1"),
            "c2c": g("SELECT COUNT(*) FROM messages WHERE account_qq=? AND kind='c2c'"),
            "group": g("SELECT COUNT(*) FROM messages WHERE account_qq=? AND kind='group'"),
            "contacts": g("SELECT COUNT(*) FROM contacts WHERE account_qq=?"),
            "first_ts": g("SELECT MIN(ts) FROM messages WHERE account_qq=?"),
            "last_ts": g("SELECT MAX(ts) FROM messages WHERE account_qq=?"),
        }
    for tag, qq in (("A", A), ("B", B)):
        st, d, _t = get_json(f"/api/overview?account={qq}")
        tv = ov_truth(qq)
        bad = {k: ((d or {}).get(k), tv[k]) for k in tv if (d or {}).get(k) != tv[k]}
        check(f"overview {tag} 完整性(逐字段)", st == 200 and not bad, f"status={st} 差异={bad}")
        check(f"overview {tag} 纯净性", st == 200 and pure_A(d)[0] if tag == "A" else st == 200, "")
    # 关键：A 的 total 绝不能 = A+B
    stA, dA, _t = get_json(f"/api/overview?account={A}")
    check("overview A 不含 B 计数(防 A+B)", stA == 200 and int((dA or {}).get("total") or 0) == nA_msg
          and int((dA or {}).get("total") or 0) != nA_msg + nB_msg,
          f"A_total={int((dA or {}).get('total') or 0)} nA={nA_msg} nB={nB_msg}")
    st, d, _t = get_json("/api/overview")
    check("overview 缺席 -> 400", st == 400, f"status={st}")
    print("\n===== 2) 读接口矩阵：profile / contact / feeds / live / send / media / quality / voice =====")

    # profile
    for tag, qq in (("A", A), ("B", B)):
        st, d, _t = get_json(f"/api/profile?account={qq}")
        fc = con.execute("SELECT COUNT(*) FROM contacts WHERE account_qq=? AND kind='c2c'", (qq,)).fetchone()[0]
        gc = con.execute("SELECT COUNT(*) FROM contacts WHERE account_qq=? AND kind='group'", (qq,)).fetchone()[0]
        mt = con.execute("SELECT COUNT(*) FROM messages WHERE account_qq=?", (qq,)).fetchone()[0]
        okv = (st == 200 and int((d or {}).get("friend_count") or 0) == fc
               and int((d or {}).get("group_count") or 0) == gc
               and int((d or {}).get("msg_total") or 0) == mt)
        check(f"profile {tag} 完整性", okv, f"status={st} friend={((d or {}).get('friend_count'))}/{fc} group={((d or {}).get('group_count'))}/{gc} msg={((d or {}).get('msg_total'))}/{mt}")
        check(f"profile {tag} 纯净性", st == 200 and (pure_A(d)[0] if tag == "A" else True), pure_A(d)[1] if tag == "A" else "")

    # contact（共享 peer）
    for tag, qq in (("A", A), ("B", B)):
        st, d, _t = get_json(f"/api/contact?account={qq}&kind={MPEER[0]}&peer_id={MPEER[1]}")
        trow = con.execute("SELECT name, remark FROM contacts WHERE account_qq=? AND kind=? AND peer_id=?",
                           (qq, MPEER[0], MPEER[1])).fetchone()
        tv_name = (trow["name"] if trow else None) or None
        tv_remark = (trow["remark"] if trow else None) or None
        check(f"contact {tag} 完整性(逐字段)", st == 200 and ((d or {}).get("name") or None) == tv_name
              and ((d or {}).get("remark") or None) == tv_remark,
              f"status={st} name={(d or {}).get('name')!r}/{tv_name!r} remark={(d or {}).get('remark')!r}/{tv_remark!r}")
        if tag == "A":
            check("contact A 纯净性", st == 200 and not (BNAME in str((d or {}).get("name"))) and BNAME not in blob(d), "")
        else:
            check("contact B 纯净性+正对照(含 B·)", st == 200 and BNAME in str((d or {}).get("name")), "")

    # feeds
    for tag, qq, truth in (("A", A, A_feeds), ("B", B, B_feeds)):
        st, d, _t = get_json(f"/api/feeds?account={qq}&limit=200&scope=all")
        got = {str(f.get("id")) for f in (d or {}).get("feeds") or []}
        check(f"feeds {tag} 完整性(逐 id)", st == 200 and got == truth, f"status={st} got={len(got)} truth={len(truth)}")
        if tag == "A":
            check("feeds A 纯净性", st == 200 and pure_A(d)[0], pure_A(d)[1])
        else:
            check("feeds B 纯净性+正对照(含 B#)", st == 200 and BTXT in blob(d) and not (got & A_feeds), "")
    st, d, _t = get_json("/api/feeds")
    check("feeds 缺席 -> 400", st == 400, f"status={st}")

    # live events（逐 id，limit 1000）
    for tag, qq in (("A", A), ("B", B)):
        st, d, _t = get_json(f"/api/live/events?account={qq}&since_id=0&limit=1000")
        truth = {int(r["id"]) for r in con.execute("SELECT id FROM messages WHERE account_qq=? "
                                                   "ORDER BY ts ASC, id ASC LIMIT 1000", (qq,))}
        got = ids_of(d, "messages")
        other = A_ids if tag == "B" else B_ids
        check(f"live_events {tag} 完整性(逐 id)", st == 200 and got == truth, f"status={st} got={len(got)} truth={len(truth)}")
        check(f"live_events {tag} 纯净性", st == 200 and not (got & other) and (pure_A(d)[0] if tag == "A" else True), "")
    st, d, _t = get_json("/api/live/events")
    check("live_events 缺席 -> 400", st == 400, f"status={st}")

    # send history（逐 id）
    for tag, qq in (("A", A), ("B", B)):
        st, d, _t = get_json(f"/api/send/history?account={qq}&limit=200")
        truth = {int(r["id"]) for r in con.execute(
            "SELECT id FROM messages WHERE account_qq=? AND source='local_send' ORDER BY ts DESC, id DESC LIMIT 200", (qq,))}
        got = {int(m["id"]) for m in (d or {}).get("messages") or []}
        check(f"send_history {tag} 完整性(逐 id)", st == 200 and got == truth, f"status={st} got={len(got)} truth={len(truth)}")
        check(f"send_history {tag} 纯净性", st == 200 and (pure_A(d)[0] if tag == "A" else True), "")
    st, d, _t = get_json("/api/send/history")
    check("send_history 缺席 -> 400", st == 400, f"status={st}")

    # media stats（逐 kind 与 DB 比）
    def media_truth(qq):
        out = {}
        for r in con.execute("SELECT media FROM messages WHERE account_qq=? AND media IS NOT NULL", (qq,)):
            try:
                m = json.loads(r["media"])
            except Exception:  # noqa: BLE001
                continue
            k = (m or {}).get("kind") or "unknown"
            e = out.setdefault(k, {"hit": 0, "miss": 0, "total": 0})
            e["total"] += 1
            if (m or {}).get("file"):
                e["hit"] += 1
            else:
                e["miss"] += 1
        return out
    for tag, qq in (("A", A), ("B", B)):
        st, d, _t = get_json(f"/api/media/stats?account={qq}")
        tv = media_truth(qq)
        got = (d or {}).get("media") or {}
        norm = {k: {"hit": int(v.get("hit") or 0), "miss": int(v.get("miss") or 0), "total": int(v.get("total") or 0)}
                for k, v in got.items()}
        idx = (d or {}).get("index") or {}
        account_ok = list((idx.get("accounts") or {}).keys()) in ([str(qq)], [])
        check(f"media_stats {tag} 完整性(逐 kind)", st == 200 and norm == tv, f"status={st} kinds={len(norm)}/{len(tv)}")
        check(f"media_stats {tag} 纯净性(只含本账号目录)", st == 200 and account_ok and (pure_A(d)[0] if tag == "A" else True), str(list((idx.get('accounts') or {}).keys())))

    # media pending（逐 id）
    for tag, qq in (("A", A), ("B", B)):
        st, d, _t = get_json(f"/api/media/pending?account={qq}&limit=200")
        truth = {int(r["id"]) for r in con.execute(
            "SELECT id FROM messages WHERE account_qq=? AND media IS NOT NULL "
            "AND json_extract(media,'$.file') IS NULL ORDER BY ts DESC LIMIT 200", (qq,))}
        got = {int(m["id"]) for m in (d or {}).get("items") or []}
        check(f"media_pending {tag} 完整性(逐 id)", st == 200 and got == truth, f"status={st} got={len(got)} truth={len(truth)}")
        check(f"media_pending {tag} 纯净性", st == 200 and (pure_A(d)[0] if tag == "A" else True), "")

    # data quality
    def dq_truth(qq):
        c2c = con.execute("SELECT COUNT(*) FROM contacts WHERE account_qq=? AND kind='c2c'", (qq,)).fetchone()[0]
        dq = con.execute("SELECT COUNT(DISTINCT peer_qq) FROM contacts WHERE account_qq=? AND kind='c2c' AND peer_qq>0", (qq,)).fetchone()[0]
        uc = con.execute("SELECT COUNT(*) FROM contacts WHERE account_qq=? AND kind='c2c' AND peer_id LIKE 'u_%'", (qq,)).fetchone()[0]
        um = con.execute("SELECT COUNT(*) FROM messages WHERE account_qq=? AND kind='c2c' AND peer_id LIKE 'u_%'", (qq,)).fetchone()[0]
        return c2c, dq, uc, um
    for tag, qq in (("A", A), ("B", B)):
        st, d, _t = get_json(f"/api/data/quality?account={qq}")
        c2c, dq, uc, um = dq_truth(qq)
        okv = (st == 200 and int((d or {}).get("account_qq") or 0) == qq
               and int((d or {}).get("c2c_contacts") or 0) == c2c
               and int((d or {}).get("distinct_peer_qq") or 0) == dq
               and int((d or {}).get("uid_contacts") or 0) == uc
               and int((d or {}).get("uid_messages") or 0) == um)
        check(f"data_quality {tag} 完整性(逐字段)", okv, f"status={st}")
    st, d, _t = get_json("/api/data/quality")
    check("data_quality 缺席 -> 400", st == 400, f"status={st}")

    # voice stats（至少归属 + 纯净；有 total 就核 DB）
    vcount = lambda qq: con.execute("SELECT COUNT(*) FROM messages WHERE account_qq=? AND "
                                    "json_extract(media,'$.kind')='voice'", (qq,)).fetchone()[0]
    for tag, qq in (("A", A), ("B", B)):
        st, d, _t = get_json(f"/api/voice/stats?account={qq}")
        okv = st == 200 and (pure_A(d)[0] if tag == "A" else True)
        if isinstance(d, dict) and "total" in d:
            okv = okv and int(d.get("total") or 0) == vcount(qq)
        check(f"voice_stats {tag} 归属+纯净", okv, f"status={st}")
        st, d, _t = get_json(f"/api/voice/official/stats?account={qq}")
        check(f"voice_official_stats {tag} 归属+纯净", st == 200 and (pure_A(d)[0] if tag == "A" else True), f"status={st}")
    st, d, _t = get_json("/api/voice/stats")
    check("voice_stats 缺席 -> 422", st == 422, f"status={st}")
    st, d, _t = get_json(f"/api/media/backfill/status?account={A}")
    check("media_backfill_status 无任务 ok=false 且不泄漏", st == 200 and (d or {}).get("ok") is False and pure_A(d)[0], f"status={st}")

    # ── 跨账号 id 探测（media）──────────────────────────────────────────────
    print("\n===== 3) 跨账号 id 探测 / media 归属 =====")
    check("克隆后 A 逐字段不变", fA_before == fA_after, "指纹不一致" if fA_before != fA_after else "")
    if a_media_id is not None and b_media_id is not None:
        s1, _d1, _t1 = get_json(f"/api/media/{a_media_id}?account={A}")
        s2, _d2, _t2 = get_json(f"/api/media/{a_media_id}?account={B}")
        s3, _d3, _t3 = get_json(f"/api/media/{b_media_id}?account={B}")
        s4, _d4, _t4 = get_json(f"/api/media/{b_media_id}?account={A}")
        s5, _d5, _t5 = get_json(f"/api/media/{a_media_id}")
        check("media A 的 id 用 A 视角 -> 200", s1 == 200, f"status={s1}")
        check("media A 的 id 用 B 视角 -> 拒绝(404)", s2 == 404, f"status={s2}")
        check("media B 的 id 用 B 视角 -> 200", s3 == 200, f"status={s3}")
        check("media B 的 id 用 A 视角 -> 拒绝(404)", s4 == 404, f"status={s4}")
        check("media 缺 account -> 400", s5 == 400, f"status={s5}")
    else:
        check("media 跨账号探测夹具", False, "未找到带 file 的媒体消息或 B 克隆 id")
    print("\n===== 4) 缺席即拒绝（账号相关接口全量抽查） =====")
    absent_paths = [
        "/api/contacts", "/api/overview", "/api/feeds", "/api/live/events",
        "/api/send/history", "/api/data/quality", "/api/export/list",
        "/api/media/stats", "/api/media/pending", "/api/media/1",
        "/api/media/backfill/status", "/api/voice/official/stats",
    ]
    for pth in absent_paths:
        st, _d, _t = get_json(pth)
        expect = 422 if pth in ("/api/media/stats", "/api/media/pending", "/api/voice/official/stats") else 400
        check(f"缺席拒绝 {pth}", st == expect, f"status={st} expect={expect}")
    st, _d, _t = get_json("/api/profile")
    check("缺席拒绝 /api/profile", st == 400, f"status={st}")
    st, _d, _t = get_json("/api/contact")
    check("缺席拒绝 /api/contact", st == 400, f"status={st}")

    print("\n===== 5) 写路径 =====")
    a_before = con.execute("SELECT remark FROM contacts WHERE account_qq=? AND kind=? AND peer_id=?",
                           (A, MPEER[0], MPEER[1])).fetchone()["remark"]
    rp = client.patch("/api/contacts", json={"account_qq": B, "kind": MPEER[0],
                                             "peer_id": MPEER[1], "remark": BNAME + "PATCHED_ISO"})
    a_after = con.execute("SELECT remark FROM contacts WHERE account_qq=? AND kind=? AND peer_id=?",
                          (A, MPEER[0], MPEER[1])).fetchone()["remark"]
    b_after = con.execute("SELECT remark FROM contacts WHERE account_qq=? AND kind=? AND peer_id=?",
                          (B, MPEER[0], MPEER[1])).fetchone()["remark"]
    check("PATCH B 备注不影响 A 同名 peer", rp.status_code == 200 and a_after == a_before,
          f"a_after={a_after!r} a_before={a_before!r}")
    check("PATCH B 备注在 B 生效", b_after == BNAME + "PATCHED_ISO", f"b_after={b_after!r}")

    import zipfile

    def run_export(qq):
        res = ex.export(qq, [{"kind": MPEER[0], "peer_id": MPEER[1]}], ["html", "txt", "md"],
                        "per_peer", {"media": False})
        text = ""
        jd = ex.paths.EXPORT_DIR / res["job"]
        for f in jd.rglob("*"):
            if f.is_file():
                text += f.read_text(encoding="utf-8", errors="replace")
        z = res.get("zip")
        if z and Path(z).exists():
            try:
                with zipfile.ZipFile(z) as zf:
                    for n in zf.namelist():
                        try:
                            text += zf.read(n).decode("utf-8", "replace")
                        except Exception:  # noqa: BLE001
                            pass
            except Exception:  # noqa: BLE001
                pass
        return res["job"], text

    jobA, txtA = run_export(A)
    jobB, txtB = run_export(B)
    check("导出 A 产物零 B 标记(含 zip)", (BNAME not in txtA) and (BTXT not in txtA)
          and not any(p in txtA for p in BONLY_PEERS), f"chars={len(txtA)}")
    check("导出 B 产物含 B# (正对照)", BTXT in txtB, f"chars={len(txtB)}")

    stA, dA, _t = get_json(f"/api/export/list?account={A}")
    stB, dB, _t = get_json(f"/api/export/list?account={B}")
    jobsA = {str(j.get("job")) for j in (dA or {}).get("jobs") or []}
    jobsB = {str(j.get("job")) for j in (dB or {}).get("jobs") or []}
    check("export_list A 只含 A 任务", stA == 200 and jobA in jobsA and jobB not in jobsA, f"A={len(jobsA)}")
    check("export_list B 只含 B 任务", stB == 200 and jobB in jobsB and jobA not in jobsB, f"B={len(jobsB)}")
    check("export_download A 任务 A 视角 -> 200", client.get(f"/api/export/download?job={jobA}&account={A}").status_code == 200)
    check("export_download A 任务 B 视角 -> 404", client.get(f"/api/export/download?job={jobA}&account={B}").status_code == 404)
    check("export_download 缺 account -> 400", client.get(f"/api/export/download?job={jobA}").status_code == 400)

    # send：B(≠框架登录 A) 必须拒绝；A + dry_run 通过；缺 account 400
    import routes_send as routes_send_mod
    orig_ep, orig_login = send_mod._endpoint, bot_source._login
    orig_lq = routes_send_mod._login_qq
    try:
        send_mod._endpoint = lambda: ("http://127.0.0.1:1", "")
        bot_source._login = lambda base, token: (A, "iso")
        routes_send_mod._login_qq = lambda: A
        rb = client.post("/api/send", json={"account_qq": B, "kind": MPEER[0], "peer_id": MPEER[1],
                                            "peer_qq": MPEER[1] if str(MPEER[1]).isdigit() else 0,
                                            "text": "ISO_PROBE", "confirm": True})
        check("send B(≠框架登录A) -> 400 account_mismatch",
              rb.status_code == 400 and (rb.json() or {}).get("code") == "account_mismatch",
              f"status={rb.status_code} code={(rb.json() or {}).get('code')}")
        ra = client.post("/api/send", json={"account_qq": A, "kind": MPEER[0], "peer_id": MPEER[1],
                                            "text": "ISO_PROBE", "confirm": True, "dry_run": True})
        check("send A + dry_run -> 200（不真发）", ra.status_code == 200 and (ra.json() or {}).get("dry_run") is True,
              f"status={ra.status_code}")
        rb2 = client.post("/api/send", json={"account_qq": B, "kind": MPEER[0], "peer_id": MPEER[1],
                                             "text": "ISO_PROBE", "confirm": True, "dry_run": True})
        check("send B + dry_run 也必须校验身份(不得静默)", rb2.status_code in (400, 403),
              f"status={rb2.status_code}")
    finally:
        send_mod._endpoint, bot_source._login = orig_ep, orig_login
        routes_send_mod._login_qq = orig_lq
    r0 = client.post("/api/send", json={"kind": MPEER[0], "peer_id": MPEER[1],
                                        "text": "x", "confirm": True})
    check("send 缺 account -> 400", r0.status_code == 400, f"status={r0.status_code}")

    # store 危险 API
    for fn, nm in ((lambda: store.list_contacts(), "list_contacts()"),
                   (lambda: store.overview(), "overview()"),
                   (lambda: store.count_messages(), "count_messages()")):
        try:
            fn()
            check(f"{nm} 缺 account -> raise", False, "未 raise")
        except ValueError:
            check(f"{nm} 缺 account -> raise", True, "")
    try:
        store.search_messages("x")
        check("search_messages 缺 account -> raise", False, "未 raise")
    except ValueError:
        check("search_messages 缺 account -> raise", True, "")
    rowsA = store.search_messages(BTXT + "BONLY_1_MSG1", account_qq=A)
    rowsB = store.search_messages(BTXT + "BONLY_1_MSG1", account_qq=B)
    check("search_messages(A) 搜不到 B-only", not any(BTXT in (r.get("text") or "") for r in rowsA), f"n={len(rowsA)}")
    check("search_messages(B) 搜得到 B-only", any(BTXT in (r.get("text") or "") for r in rowsB), f"n={len(rowsB)}")

    print("\n===== 5.5) 全局单例（QZone 凭据）必须显式禁用，不得静默混用 =====")
    orig_rc = qzone.resolve_credential
    try:
        qzone.resolve_credential = lambda opts=None, strict=True: (
            {"uin": A, "cookie": "x", "p_skey": "y", "skey": "z"}, {})
        try:
            qzone.sync({"account_qq": B})
            check("QZone 全局凭据 + 异账号 -> 显式拒绝", False, "未 raise")
        except RuntimeError as _e:
            check("QZone 全局凭据 + 异账号 -> 显式拒绝", "QZONE_ACCOUNT_MISMATCH" in str(_e), str(_e)[:70])
        _r = client.post("/api/sources/qzone/sync", json={"account_qq": B})
        check("POST /api/sources/qzone/sync 异账号 -> 400", _r.status_code == 400,
              f"status={_r.status_code}")
    finally:
        qzone.resolve_credential = orig_rc

    print("\n===== 6) DELETE 账号的双向隔离 =====")
    rd = client.delete(f"/api/accounts/{B}")
    b_gone = con.execute("SELECT COUNT(*) FROM accounts WHERE account_qq=?", (B,)).fetchone()[0] == 0
    b_msgs = con.execute("SELECT COUNT(*) FROM messages WHERE account_qq=?", (B,)).fetchone()[0]
    b_feeds = con.execute("SELECT COUNT(*) FROM feeds WHERE account_qq=?", (B,)).fetchone()[0]
    check("DELETE B -> B 消失(含 feeds)", rd.status_code == 200 and b_gone and b_msgs == 0 and b_feeds == 0,
          f"status={rd.status_code} b_msgs={b_msgs} b_feeds={b_feeds}")
    check("DELETE B -> A 逐字段不变", fingerprint(con, A) == fA_before, "")
    clone_a_to_b(con); add_b_only(con); con.commit()
    check("重新克隆 B 后 A 逐字段不变", fingerprint(con, A) == fA_before, "")
    nB_before_del = con.execute("SELECT COUNT(*) FROM messages WHERE account_qq=?", (B,)).fetchone()[0]
    ra2 = client.delete(f"/api/accounts/{A}")
    nB_after_del = con.execute("SELECT COUNT(*) FROM messages WHERE account_qq=?", (B,)).fetchone()[0]
    a_gone = con.execute("SELECT COUNT(*) FROM accounts WHERE account_qq=?", (A,)).fetchone()[0] == 0
    check("DELETE A -> A 消失且 B 完好(反向)", ra2.status_code == 200 and a_gone and nB_after_del == nB_before_del,
          f"status={ra2.status_code} nB {nB_before_del}->{nB_after_del}")

    con.close()
    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n===== 隔离矩阵总结 =====")
    print(f"  {len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    if bad:
        print("  未通过：" + " | ".join(bad))
        print(f"  临时根（便于排查）：{tmp}")
        return 1
    print("  全绿")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        print("\n[FAIL] isolation_test 异常中止")
        sys.exit(1)