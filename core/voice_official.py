# -*- coding: utf-8 -*-
'''QQScope · 官方语音转文字回填（NapCat fetch_ptt_text）

背景
----
本地 faster-whisper 能转，但准确率一般、费 CPU。QQ 官方自带 PTT 转文字
（NapCat 的 fetch_ptt_text），又准又快（实测单条约 0.05s）。本模块把它做成
正式回填能力：扫描近期会话历史里的 [CQ:record] 消息 -> 调官方接口 -> 按
(会话, 时间) 回填到 store 的 voice 行。

与本地 whisper 的关系
----------------------
* 官方结果写入 media.voice_text / voice_lang='zh' / voice_engine='qq-official'，
  并置 voice_status='ok'。
* 覆盖前把本地 whisper 原文快照到 voice_text_local（连同 voice_lang_local /
  voice_engine_local / voice_status_local），便于逐条对比。
* 已经 voice_engine='qq-official' 的行直接跳过（幂等，不重复请求官方接口）。

礼貌 / 安全
-----------
* 每条 fetch_ptt_text 之间 sleep 1.0s，串行（并发 1），可被 stop_event 中断。
* 单条超时 70s（NapCat 侧已调 60s）。
* token 只从 data/framework/onebot.json 读，绝不外泄、绝不进日志。
* 失败如实分类 expired / not_ready / no_match / network，绝不把失败算成功。
'''
from __future__ import annotations

import json
import time
from pathlib import Path

from core import media_fetch as mf
from core import store

RATE_SLEEP = 1.0            # 官方接口限速：每条之间 1 秒
CALL_TIMEOUT = 70.0         # 单条超时
NOT_READY_SECONDS = 90      # 消息比这还新 -> 可能还没索引
PEER_LIMIT = 40
PER_PEER = 100


# ── 会话候选 ────────────────────────────────────────────────────────────────
def _store_voice_peers(account_qq: int, limit: int) -> list[dict]:
    '''本库里出现过语音的会话，按最近语音时间倒序（优先扫这些，命中率最高）。'''
    con = store.connect(account_qq)
    try:
        rows = con.execute(
            "SELECT kind, peer_id, peer_qq, MAX(ts) AS mt FROM messages "
            "WHERE account_qq=? AND json_extract(media,'$.kind')='voice' "
            "GROUP BY kind, peer_id ORDER BY mt DESC LIMIT ?",
            (int(account_qq), int(limit))).fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        kind = str(r["kind"])
        if kind == "group":
            key = media_fetch_int(r["peer_id"])
            if key > 0:
                out.append({"kind": "group", "key": key, "peer_id": str(r["peer_id"]),
                            "peer_qq": key, "src": "store"})
        else:
            qq = media_fetch_int(r["peer_qq"]) or media_fetch_int(r["peer_id"])
            if qq > 0:
                out.append({"kind": "c2c", "key": qq, "peer_id": str(r["peer_id"]),
                            "peer_qq": qq, "src": "store"})
    return out


def media_fetch_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _onebot_peers(base, token, limit: int) -> list[dict]:
    '''从 OneBot 的好友/群列表补一些会话（覆盖刚收到、本库还没有的语音）。'''
    out: list[dict] = []
    try:
        groups = mf.onebot_call("get_group_list", {}, base, token) or []
    except mf.MediaFetchError:
        groups = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        gid = media_fetch_int(g.get("group_id"))
        if gid > 0:
            out.append({"kind": "group", "key": gid, "peer_id": str(gid),
                        "peer_qq": gid, "src": "onebot"})
    try:
        friends = mf.onebot_call("get_friend_list", {}, base, token) or []
    except mf.MediaFetchError:
        friends = []
    for f in friends:
        if not isinstance(f, dict):
            continue
        qq = media_fetch_int(f.get("user_id"))
        if qq > 0:
            out.append({"kind": "c2c", "key": qq, "peer_id": str(qq),
                        "peer_qq": qq, "src": "onebot"})
    return out[:max(0, int(limit))]


def _peer_candidates(account_qq, peer_limit, base, token) -> list[dict]:
    seen: set = set()
    out: list[dict] = []
    for p in _store_voice_peers(account_qq, peer_limit):
        k = (p["kind"], p["key"])
        if k not in seen:
            seen.add(k)
            out.append(p)
    for p in _onebot_peers(base, token, peer_limit * 3):
        if len(out) >= peer_limit:
            break
        k = (p["kind"], p["key"])
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out[:max(1, int(peer_limit))]


# ── 历史里的语音消息 ────────────────────────────────────────────────────────
def _history_records(peer: dict, per_peer: int, base, token) -> list[dict]:
    if peer["kind"] == "group":
        data = mf.onebot_call("get_group_msg_history",
                              {"group_id": peer["key"], "count": per_peer,
                               "message_seq": 0}, base, token)
    else:
        data = mf.onebot_call("get_friend_msg_history",
                              {"user_id": peer["key"], "count": per_peer,
                               "message_seq": 0}, base, token)
    msgs = (data or {}).get("messages") or []
    out = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        for seg in (m.get("message") or []):
            if isinstance(seg, dict) and seg.get("type") == "record":
                mid = m.get("message_id")
                ts = media_fetch_int(m.get("time"))
                if mid is not None and ts > 0:
                    out.append({"peer": peer, "message_id": mid, "ts": ts,
                                "name": (seg.get("data") or {}).get("file")})
                break
    return out

# ── 与 store 行匹配 / 回填 ──────────────────────────────────────────────────
def _loads(raw) -> dict:
    try:
        m = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        m = {}
    return m if isinstance(m, dict) else {}


def _find_store_row(account_qq, peer: dict, ts: int, tol: int = 4) -> dict | None:
    '''按 (会话, 时间) 找库里的 voice 行；找不到返回 None。'''
    con = store.connect(account_qq)
    try:
        if peer["kind"] == "group":
            rows = con.execute(
                "SELECT id, ts, media FROM messages WHERE account_qq=? AND kind='group' "
                "AND peer_id=? AND json_extract(media,'$.kind')='voice' "
                "AND ts BETWEEN ? AND ?",
                (int(account_qq), str(peer["key"]), int(ts) - tol, int(ts) + tol)).fetchall()
        else:
            rows = con.execute(
                "SELECT id, ts, media FROM messages WHERE account_qq=? AND kind='c2c' "
                "AND (peer_qq=? OR peer_id=?) AND json_extract(media,'$.kind')='voice' "
                "AND ts BETWEEN ? AND ?",
                (int(account_qq), peer["key"], str(peer["key"]),
                 int(ts) - tol, int(ts) + tol)).fetchall()
    finally:
        con.close()
    if not rows:
        return None

    def _score(r):
        m = _loads(r["media"])
        has_local = 1 if (m.get("voice_engine") or m.get("voice_text")) else 0
        return (abs(int(r["ts"]) - int(ts)), -has_local)

    best = min(rows, key=_score)
    return {"id": int(best["id"]), "ts": int(best["ts"]), "media": _loads(best["media"])}


def _apply_official(account_qq, msg_id: int, media: dict, text: str, elapsed: float = 0.0) -> bool:
    '''官方结果优先；覆盖前把 whisper 原文快照进 voice_text_local。'''
    m = dict(media or {})
    if str(m.get("voice_engine") or "") == "qq-official":
        return False
    if m.get("voice_text") and not m.get("voice_text_local"):
        m["voice_text_local"] = m.get("voice_text")
        m["voice_lang_local"] = m.get("voice_lang")
        m["voice_engine_local"] = m.get("voice_engine")
        m["voice_status_local"] = m.get("voice_status")
    m["voice_text"] = str(text).strip()
    m["voice_lang"] = "zh"
    m["voice_engine"] = "qq-official"
    m["voice_status"] = "ok"
    m["voice_error"] = None
    m["voice_official"] = True
    m["voice_official_at"] = int(time.time())
    m["voice_official_elapsed"] = round(float(elapsed or 0.0), 2)
    con = store.connect(account_qq)
    try:
        cur = con.execute("UPDATE messages SET media=? WHERE id=?",
                          (json.dumps(m, ensure_ascii=False), int(msg_id)))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def _classify_fail(message, ts: int, now: int) -> tuple[str, str]:
    '''官方接口失败 -> (reason, detail)，reason 只取四类之一。'''
    msg = str(message or "")
    low = msg.lower()
    if "消息不存在" in msg or "not found" in low:
        return "no_match", "message_gone"
    if ("超时" in msg or "timeout" in low or "连接" in msg
            or "拒绝" in msg or "connect" in low):
        return "network", "onebot_unreachable"
    age = max(0, int(now) - int(ts)) if ts else 0
    if "获取语音转文字结果失败" in msg or "失败" in msg:
        if age <= NOT_READY_SECONDS:
            return "not_ready", "too_new"
        return "expired", "no_official_result"
    return "network", "unknown"


def _bump(report: dict, reason: str, detail: str) -> None:
    report["by_reason"][reason] = report["by_reason"].get(reason, 0) + 1
    report["by_detail"][detail] = report["by_detail"].get(detail, 0) + 1

# ── 主流程 ──────────────────────────────────────────────────────────────────
def transcribe_recent(account_qq, peer_limit=PEER_LIMIT, per_peer=PER_PEER,
                      workers=1, progress=None, limit=None, base=None,
                      token=None, sleep=RATE_SLEEP, stop_event=None) -> dict:
    '''扫描近期会话 -> 官方 fetch_ptt_text -> 回填 store（官方优先，保留 whisper 原文）。

    workers 目前固定串行（并发 1）以保证 1s 限速；limit 为本次最多请求条数。
    '''
    qq = int(account_qq)
    t0 = time.monotonic()
    now = int(time.time())
    cfg = mf.framework_config()
    b = (base or cfg["base"]).rstrip("/")
    tk = token if token is not None else cfg["token"]

    report = {"ok": True, "account_qq": qq, "scanned": 0, "voice_found": 0,
              "ok_count": 0, "failed_count": 0, "filled": 0,
              "skipped_expired": 0, "skipped_official": 0, "no_match": 0,
              "by_reason": {}, "by_detail": {}, "errors": [],
              "stopped": False, "results": [], "call_seconds": []}

    try:
        peers = _peer_candidates(qq, peer_limit, b, tk)
    except Exception as exc:  # noqa: BLE001
        report["ok"] = False
        report["errors"].append(f"枚举会话失败：{exc}")
        report["elapsed"] = round(time.monotonic() - t0, 2)
        report.pop("call_seconds", None)
        return report

    records: list[dict] = []
    for p in peers:
        if stop_event is not None and stop_event.is_set():
            report["stopped"] = True
            break
        try:
            recs = _history_records(p, per_peer, b, tk)
        except mf.MediaFetchError as exc:
            report["errors"].append(f"{p['kind']}:{p['key']} 历史失败：{str(exc)[:80]}")
            continue
        records.extend(recs)
        report["scanned"] += 1
        if progress:
            try:
                progress({"stage": "scan", "scanned": report["scanned"],
                          "peers": len(peers), "voice_found": len(records)})
            except Exception:  # noqa: BLE001
                pass

    report["voice_found"] = len(records)
    # 轮转各会话，避免某个热闹群把 limit 名额吃光
    buckets: dict = {}
    for r in records:
        buckets.setdefault((r["peer"]["kind"], r["peer"]["key"]), []).append(r)
    for v in buckets.values():
        v.sort(key=lambda x: -int(x.get("ts") or 0))
    ordered: list = []
    while buckets:
        for k in list(buckets.keys()):
            v = buckets[k]
            if v:
                ordered.append(v.pop(0))
            if not v:
                buckets.pop(k, None)
    records = ordered
    if limit:
        try:
            records = records[:max(1, int(limit))]
        except (TypeError, ValueError):
            pass
    total = len(records)

    for idx, rec in enumerate(records, 1):
        if stop_event is not None and stop_event.is_set():
            report["stopped"] = True
            break
        peer = rec["peer"]
        peer_label = f"{peer['kind']}:{peer['key']}"
        row = _find_store_row(qq, peer, rec["ts"])
        cur_media = (row or {}).get("media") or {}

        if row is not None and str(cur_media.get("voice_engine") or "") == "qq-official":
            report["skipped_official"] += 1
            report["results"].append({"message_id": rec["message_id"], "ts": rec["ts"],
                                      "peer": peer_label, "status": "skipped_official",
                                      "text": cur_media.get("voice_text")})
            if progress:
                _emit(progress, idx, total, report, rec, "skipped_official")
            continue

        call_t0 = time.monotonic()
        err = None
        data = None
        try:
            data = mf.onebot_call("fetch_ptt_text",
                                  {"message_id": rec["message_id"]},
                                  b, tk, timeout=CALL_TIMEOUT)
        except mf.MediaFetchError as exc:
            err = str(exc)
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
        call_elapsed = round(time.monotonic() - call_t0, 3)
        report["call_seconds"].append(call_elapsed)

        if err is not None:
            reason, detail = _classify_fail(err, rec["ts"], now)
            report["failed_count"] += 1
            _bump(report, reason, detail)
            if reason == "expired":
                report["skipped_expired"] += 1
            report["results"].append({"message_id": rec["message_id"], "ts": rec["ts"],
                                      "peer": peer_label, "status": "failed",
                                      "reason": reason, "detail": detail,
                                      "error": err[:160], "call_seconds": call_elapsed})
            if progress:
                _emit(progress, idx, total, report, rec, "failed")
        else:
            text = str((data or {}).get("text") or "").strip()
            if not text:
                reason = ("not_ready" if (now - int(rec["ts"])) <= NOT_READY_SECONDS
                          else "expired")
                report["failed_count"] += 1
                _bump(report, reason, "empty_text")
                if reason == "expired":
                    report["skipped_expired"] += 1
                report["results"].append({"message_id": rec["message_id"], "ts": rec["ts"],
                                          "peer": peer_label, "status": "failed",
                                          "reason": reason, "detail": "empty_text",
                                          "call_seconds": call_elapsed})
                if progress:
                    _emit(progress, idx, total, report, rec, "failed")
            else:
                report["ok_count"] += 1
                if row is None:
                    report["no_match"] += 1
                    _bump(report, "no_match", "store_row_missing")
                    report["results"].append({"message_id": rec["message_id"], "ts": rec["ts"],
                                              "peer": peer_label, "status": "ok_no_store",
                                              "text": text, "call_seconds": call_elapsed})
                    if progress:
                        _emit(progress, idx, total, report, rec, "ok_no_store")
                else:
                    local_text = cur_media.get("voice_text")
                    engine_before = cur_media.get("voice_engine")
                    wrote = _apply_official(account_qq, row["id"], cur_media, text, call_elapsed)
                    if wrote:
                        report["filled"] += 1
                        status = "filled"
                    else:
                        report["skipped_official"] += 1
                        status = "skipped_official"
                    report["results"].append({
                        "message_id": rec["message_id"], "msg_row_id": row["id"],
                        "ts": rec["ts"], "peer": peer_label, "status": status,
                        "text": text, "local_text": local_text,
                        "engine_before": engine_before, "call_seconds": call_elapsed})
                    if progress:
                        _emit(progress, idx, total, report, rec, status)

        if idx < total and sleep:
            if stop_event is not None:
                if stop_event.wait(float(sleep)):
                    report["stopped"] = True
                    break
            else:
                time.sleep(float(sleep))

    cs = report.pop("call_seconds", []) or []
    report["calls"] = len(cs)
    report["avg_call_seconds"] = round(sum(cs) / len(cs), 3) if cs else None
    report["workers_effective"] = 1
    report["elapsed"] = round(time.monotonic() - t0, 2)
    return report


def _emit(progress, idx, total, report, rec, status) -> None:
    try:
        progress({"stage": "transcribe", "done": idx, "total": total,
                  "ok_count": report["ok_count"], "failed_count": report["failed_count"],
                  "filled": report["filled"],
                  "current": {"message_id": rec["message_id"], "ts": rec["ts"],
                              "peer": f"{rec['peer']['kind']}:{rec['peer']['key']}",
                              "status": status}})
    except Exception:  # noqa: BLE001
        pass


# ── 统计（official / local / both） ────────────────────────────────────────
def stats(account_qq) -> dict:
    qq = int(account_qq)
    con = store.connect(qq)
    try:
        row = con.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN json_extract(media,'$.voice_engine')='qq-official' "
            "         THEN 1 ELSE 0 END) AS official, "
            "SUM(CASE WHEN json_extract(media,'$.voice_engine') LIKE 'faster-whisper%' "
            "          OR json_extract(media,'$.voice_engine_local') LIKE 'faster-whisper%' "
            "         THEN 1 ELSE 0 END) AS local, "
            "SUM(CASE WHEN json_extract(media,'$.voice_engine')='qq-official' "
            "          AND json_extract(media,'$.voice_engine_local') LIKE 'faster-whisper%' "
            "         THEN 1 ELSE 0 END) AS both "
            "FROM messages WHERE account_qq=? AND json_extract(media,'$.kind')='voice'",
            (qq,)).fetchone()
    finally:
        con.close()
    official = int(row["official"] or 0)
    local = int(row["local"] or 0)
    both = int(row["both"] or 0)
    return {"account": qq, "voice_total": int(row["total"] or 0),
            "official": official, "local": local, "both": both,
            "official_only": official - both, "local_only": local - both}