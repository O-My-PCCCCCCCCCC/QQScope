# -*- coding: utf-8 -*-
'''QQScope · 缺失媒体补下载（NapCat / OneBot 扩展接口）

背景
----
QQ 会定期清理本地缓存，pack 数据里大量 media.file 为空（图片命中率仅 12.5%）。
本模块用 NapCat 的 OneBot 接口，把缺失媒体从腾讯服务器拉回来并回填 media.file。

实测结论（2026-10-01，NapCat 4.18.28 / QQ 1605289411）
------------------------------------------------------
* get_image / get_record / get_file 只接受两种参数：
    1) msgId+elementId 的编码 token（需要 QQ 内部 ID，pack 数据里没有）；
    2) NapCat 本地文件缓存里的文件名（但文件正是缺失的那个，必然 not found）。
  实测 get_image(file=文件名) 与 get_image(file=file_uuid) 都返回 file not found，
  所以「只靠 get_image」拉不回已被清理的历史媒体（这点与最初设想不同，已如实上报）。
* 真正可用、且不依赖历史翻页的路径是：
    a. nc_get_rkey 拿腾讯多媒体下载 rkey（ttl 约 57 分钟；type 10=私聊 / 20=群）；
    b. pack 的 messages.content 里本来就存了 fileid（缺失图片 97.7% 都有）；
    c. 拼 https://multimedia.nt.qq.com.cn + cdn_url + rkey 直接下载。
  get_image 仍保留为兜底：直链失败时用文件名问一次 NapCat 本地缓存，成功才记为
  method=get_image，绝不把失败算成功。

安全 / 礼貌
-----------
* rkey、token 都是凭据：只用于拼 URL，绝不写日志、绝不进 API 响应；
  报告里最多保留 appid 与 fileid 前 10 位。
* 限速：串行下载（并发 1），每条之间 sleep（默认 0.6s），可用 stop_event 中断。
* 上限：单次条数、总字节数（默认 2GB）都有硬上限。
* 只写 data/media_cache/，绝不改动 core/store.py / core/media.py 的代码。
'''
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import httpx

from core import paths, store

MEDIA_CACHE_DIR = paths.DATA / "media_cache"
FRAMEWORK_CONFIG = paths.DATA / "framework" / "onebot.json"

DEFAULT_BASE = "http://127.0.0.1:3000"
CDN_HOST = "https://multimedia.nt.qq.com.cn"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) QQScope/2.0"

DEFAULT_LIMIT = 200          # 单次任务默认条数
MAX_LIMIT = 500              # 单次任务硬上限
DEFAULT_MAX_BYTES = 2 * 1024 ** 3   # 2GB
RATE_SLEEP = 0.6             # 每条之间的间隔（秒）
HTTP_TIMEOUT = 30.0
KINDS = ("image", "sticker", "voice", "file", "video")

_APPID_RKEY_TYPE = {"1406": 10, "1407": 20}   # 1406=私聊 1407=群


class MediaFetchError(Exception):
    '''补下载过程中的可读错误（不含任何密钥）。'''


# ── OneBot 配置 / 调用 ──────────────────────────────────────────────────────
def framework_config() -> dict:
    '''读 data/framework/onebot.json 里的 base/token；读不到就用默认值。'''
    out = {"base": DEFAULT_BASE, "token": ""}
    try:
        if FRAMEWORK_CONFIG.is_file():
            raw = json.loads(FRAMEWORK_CONFIG.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                if raw.get("base"):
                    out["base"] = str(raw["base"]).rstrip("/")
                if raw.get("token"):
                    out["token"] = str(raw["token"])
    except Exception:  # noqa: BLE001
        pass
    return out


def _resolve_ctx(base: str | None, token: str | None):
    cfg = framework_config()
    return (base or cfg["base"]).rstrip("/"), (token if token is not None else cfg["token"])


def _mask(s, keep: int = 10) -> str:
    s = str(s or "")
    return s[:keep] + "..." if len(s) > keep else s


def onebot_call(action: str, params: dict | None = None, base: str | None = None,
                token: str | None = None, timeout: float = HTTP_TIMEOUT) -> dict:
    '''调用 OneBot action，status=ok 返回 data，否则抛 MediaFetchError（不含 token）。'''
    b, t = _resolve_ctx(base, token)
    headers = {"Authorization": "Bearer " + t} if t else {}
    try:
        with httpx.Client(trust_env=False, timeout=timeout, headers=headers) as c:
            r = c.post(b + "/" + action, json=params or {})
    except httpx.TimeoutException as exc:
        raise MediaFetchError(f"连接 {b} 超时") from exc
    except httpx.HTTPError as exc:
        raise MediaFetchError(f"连接 {b} 失败：{type(exc).__name__}") from exc
    if r.status_code in (401, 403):
        raise MediaFetchError("OneBot 拒绝了请求（token 错误或未授权）")
    if r.status_code >= 400:
        raise MediaFetchError(f"OneBot HTTP {r.status_code}")
    try:
        j = r.json()
    except ValueError as exc:
        raise MediaFetchError("OneBot 返回不是 JSON") from exc
    if str(j.get("status")) != "ok":
        msg = str(j.get("message") or j.get("wording") or j.get("retcode") or "")
        raise MediaFetchError(f"OneBot {action} 失败：{msg[:120]}")
    return j.get("data") or {}


def get_rkeys(base: str | None = None, token: str | None = None) -> dict:
    '''取多媒体下载 rkey，返回 {type: "&rkey=..."}。失败返回 {}。'''
    try:
        data = onebot_call("nc_get_rkey", {}, base, token)
    except MediaFetchError:
        return {}
    out: dict[int, str] = {}
    seq = data if isinstance(data, list) else (data.get("rkeys") if isinstance(data, dict) else None)
    for ent in (seq or []):
        if not isinstance(ent, dict):
            continue
        rk = str(ent.get("rkey") or "")
        if not rk:
            continue
        if not rk.startswith("&rkey="):
            rk = "&rkey=" + rk.lstrip("&").split("rkey=")[-1]
        try:
            out[int(ent.get("type"))] = rk
        except (TypeError, ValueError):
            continue
    return out

# ── content 解析 / URL 拼装 ─────────────────────────────────────────────────
def _extract_source(content) -> dict:
    '''从 messages.content 里挖出可下载的 CDN 路径（图片/表情用）。

    返回 {"path","appid","expire_ts","fileid"}；挖不到 path 为 None。
    '''
    out = {"path": None, "appid": None, "expire_ts": 0, "fileid": None}
    try:
        c = json.loads(content) if isinstance(content, str) else (content or {})
    except (TypeError, ValueError):
        return out
    if not isinstance(c, dict):
        return out
    segs = c.get("segments") if isinstance(c.get("segments"), list) else [c]
    for s in segs:
        if not isinstance(s, dict):
            continue
        for key in ("cdn_url_1", "cdn_url", "cdn_url_2", "cdn_url_3"):
            u = s.get(key)
            if not isinstance(u, str) or not u:
                continue
            if "fileid=" not in u and "/download?" not in u:
                continue
            out["path"] = u
            m = re.search(r"appid=(\d+)", u)
            out["appid"] = m.group(1) if m else None
            m2 = re.search(r"fileid=([^&]+)", u)
            out["fileid"] = m2.group(1) if m2 else None
            try:
                out["expire_ts"] = int(s.get("expire_ts") or 0)
            except (TypeError, ValueError):
                out["expire_ts"] = 0
            return out
    return out


def _full_url(path: str, rkey: str) -> str:
    p = str(path)
    if p.startswith("http://") or p.startswith("https://"):
        url = p
    else:
        url = CDN_HOST + (p if p.startswith("/") else "/" + p)
    if rkey and "rkey=" not in url:
        url += rkey
    return url


def _rkey_order(appid, rkeys: dict) -> list[str]:
    '''按 appid 猜 rkey 类型（1406→10 / 1407→20），另一个作为重试。'''
    pref = _APPID_RKEY_TYPE.get(str(appid))
    order: list[str] = []
    for t in ([pref] if pref else []) + [10, 20]:
        rk = rkeys.get(t)
        if rk and rk not in order:
            order.append(rk)
    if not order:
        order.append("")
    return order


# ── 下载 / 校验 / 落盘 / 回填 ───────────────────────────────────────────────
_IMG_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"BM")
_IMG_KINDS = ("image", "sticker")
_VOICE_MAGIC = (b"#!AMR", b"#!SILK", b"\x02#!SILK")


def _valid_payload(kind: str, data: bytes) -> bool:
    '''按媒体类型做最简魔数校验，避免把错误页当成功。'''
    if not data:
        return False
    if kind in _IMG_KINDS:
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return True
        return any(data.startswith(m) for m in _IMG_MAGIC)
    if kind == "voice":
        return any(data.startswith(m) for m in _VOICE_MAGIC)
    return True


def _ext_for(kind: str, name, data: bytes) -> str:
    suf = Path(str(name or "")).suffix.lower()
    if suf and 1 < len(suf) <= 6 and re.match(r"^\.[a-z0-9]+$", suf):
        return suf
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if kind == "voice":
        return ".amr"
    if kind == "video":
        return ".mp4"
    return ".bin"


def save_cache(data: bytes, key: str, ext: str) -> Path:
    MEDIA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^0-9a-zA-Z_.-]", "_", str(key or "media"))[:80] or "media"
    p = MEDIA_CACHE_DIR / (safe + ext)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(p)
    return p


def backfill(msg_id: int, abs_path) -> bool:
    '''把本地缓存路径回填进 messages.media.file（绝对路径，media.resolve 支持）。'''
    con = store.connect()
    try:
        cur = con.execute(
            "UPDATE messages SET media=json_set(media,'$.file',?) WHERE id=?",
            (str(abs_path), int(msg_id)))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def _download(url: str):
    '''返回 (status, body, exception)。失败不抛。'''
    try:
        with httpx.Client(trust_env=False,
                          timeout=httpx.Timeout(HTTP_TIMEOUT, connect=10.0),
                          follow_redirects=True,
                          headers={"User-Agent": USER_AGENT}) as c:
            r = c.get(url)
        return r.status_code, (r.content or b""), None
    except Exception as exc:  # noqa: BLE001
        return 0, b"", exc


def classify_failure(status, body: bytes | None, exc: Exception | None = None) -> str:
    '''把失败归类为 expired / not_found / rate_limited / network。'''
    if exc is not None:
        return "network"
    low = (body or b"")[:500].lower()
    if status == 429 or b"frequen" in low or b"too many" in low or b"rate limit" in low:
        return "rate_limited"
    if status in (400, 401, 403, 410):
        return "expired"
    if status == 404:
        return "not_found"
    if b"expir" in low or b"rkey" in low:
        return "expired"
    if b"not found" in low or b"notfound" in low:
        return "not_found"
    if status == 200:
        return "bad_data"
    return "not_found"


def try_get_image(name: str, kind: str, base=None, token=None):
    '''兜底：用文件名问 NapCat 本地缓存（get_image）。成功返回 (bytes, method)。'''
    if kind not in _IMG_KINDS or not name:
        return None
    try:
        data = onebot_call("get_image", {"file": name}, base, token, timeout=20.0)
    except MediaFetchError:
        return None
    if not isinstance(data, dict):
        return None
    url = str(data.get("url") or "")
    local = str(data.get("file") or "")
    if url.startswith("http"):
        st, body, exc = _download(url)
        if exc is None and st == 200 and body:
            return body, "get_image"
    if local:
        p = Path(local)
        try:
            if p.is_file() and p.stat().st_size > 0:
                return p.read_bytes(), "get_image_local"
        except OSError:
            pass
    return None

# ── 待补清单 ────────────────────────────────────────────────────────────────
def _norm_kinds(kinds) -> list[str]:
    if not kinds:
        return ["image"]
    if isinstance(kinds, str):
        kinds = [kinds]
    out = [str(k).strip().lower() for k in kinds if str(k).strip()]
    out = [k for k in out if k in KINDS]
    return out or ["image"]


def list_missing(account_qq, kinds=None, limit=DEFAULT_LIMIT) -> list[dict]:
    '''列出 media.file 为空的媒体行（按时间倒序，优先补最新的）。'''
    qq = int(account_qq)
    ks = _norm_kinds(kinds)
    try:
        lim = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    except (TypeError, ValueError):
        lim = DEFAULT_LIMIT
    ph = ",".join("?" * len(ks))
    sql = ("SELECT id, kind, peer_id, peer_qq, ts, content, media FROM messages "
           "WHERE account_qq=? AND media IS NOT NULL "
           "AND json_extract(media,'$.file') IS NULL "
           "AND json_extract(media,'$.kind') IN (" + ph + ") "
           "ORDER BY ts DESC LIMIT ?")
    con = store.connect()
    try:
        rows = con.execute(sql, [qq, *ks, lim]).fetchall()
    finally:
        con.close()
    out: list[dict] = []
    for r in rows:
        try:
            m = json.loads(r["media"] or "{}")
        except (TypeError, ValueError):
            m = {}
        out.append({"id": r["id"], "kind": r["kind"], "peer_id": r["peer_id"],
                    "peer_qq": r["peer_qq"], "ts": r["ts"],
                    "content": r["content"], "media": m,
                    "media_kind": str(m.get("kind") or "unknown")})
    return out


# ── 单条补下载 ──────────────────────────────────────────────────────────────
def _fetch_one(it: dict, rkeys: dict, base=None, token=None) -> dict:
    m = it.get("media") or {}
    kind = str(it.get("media_kind") or m.get("kind") or it.get("kind") or "unknown")
    rec = {"id": it.get("id"), "kind": kind, "name": m.get("name"),
           "md5": m.get("md5"), "ts": it.get("ts"),
           "peer": str(it.get("peer_qq") or it.get("peer_id") or ""),
           "method": None, "http": None, "bytes": 0, "saved": None,
           "ok": False, "reason": None, "detail": None,
           "appid": None, "fileid_prefix": None}

    src = _extract_source(it.get("content"))
    rec["appid"] = src.get("appid")
    rec["fileid_prefix"] = _mask(src.get("fileid"), 10)

    if kind not in KINDS:
        rec["reason"] = "unsupported"
        rec["detail"] = "未知媒体类型"
        return rec
    if not src.get("path") and kind in ("voice", "file", "video"):
        rec["reason"] = "unsupported"
        rec["detail"] = "该类型 content 无 cdn_url，需 get_record/get_file（本期只验证图片）"
        return rec

    data = None
    method = None
    reason = None
    last_http = None
    if not src.get("path"):
        reason = "not_found"
    else:
        for rk in _rkey_order(src.get("appid"), rkeys):
            st, body, exc = _download(_full_url(src["path"], rk))
            last_http = st
            if exc is not None:
                reason = classify_failure(None, None, exc)
                continue
            if st == 200 and _valid_payload(kind, body):
                data, method, reason = body, "cdn", None
                break
            reason = classify_failure(st, body)
            if reason == "expired":
                continue          # 换另一种 rkey 再试一次
            break

    if data is None:              # 兜底：问一次 NapCat 本地缓存（get_image）
        got = try_get_image(rec["name"], kind, base, token)
        if got:
            data, method, reason = got[0], got[1], None

    rec["http"] = last_http
    if data is None:
        rec["reason"] = reason or "not_found"
        if not src.get("path") and not rec["detail"]:
            rec["detail"] = "content 里没有可用的 cdn_url"
        return rec

    ext = _ext_for(kind, rec["name"], data)
    try:
        p = save_cache(data, rec["md5"] or ("id_" + str(rec["id"])), ext)
        backfill(rec["id"], p)
    except Exception as exc:  # noqa: BLE001
        rec["reason"] = "network" if isinstance(exc, OSError) else "bad_data"
        rec["detail"] = type(exc).__name__
        return rec

    rec.update({"ok": True, "method": method, "bytes": len(data),
                "saved": str(p), "reason": None})
    return rec

# ── 批量补下载（限速 / 可中断 / 带上限） ────────────────────────────────────
def fetch_missing(account_qq, kinds=None, limit=50, max_bytes=DEFAULT_MAX_BYTES,
                  progress=None, stop_event=None, base=None, token=None,
                  sleep=RATE_SLEEP) -> dict:
    '''补下载缺失媒体。

    kinds      : 默认 ['image']；'sticker' 同图片，'voice'/'file'/'video' 本期未实现
    limit      : 单次最多条数（硬上限 MAX_LIMIT=500）
    max_bytes  : 单次总字节上限（默认 2GB）
    progress   : 可选回调 progress(snapshot_dict)
    stop_event : threading.Event，可随时中断（当前条下载完即停）
    返回报告：逐条结果 + 成功率 + 失败原因分布；**不含任何 rkey/token**。
    '''
    qq = int(account_qq)
    ks = _norm_kinds(kinds)
    try:
        lim = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    except (TypeError, ValueError):
        lim = DEFAULT_LIMIT
    try:
        cap = int(max_bytes) if max_bytes else DEFAULT_MAX_BYTES
    except (TypeError, ValueError):
        cap = DEFAULT_MAX_BYTES

    t0 = time.monotonic()
    try:
        items = list_missing(qq, ks, lim)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "account_qq": qq, "kinds": ks, "requested": 0,
                "success": 0, "failed": 0, "bytes": 0, "elapsed": 0.0,
                "stopped": False, "by_reason": {}, "by_kind": {}, "items": [],
                "error": f"查询缺失媒体失败：{exc}"}

    rkeys = get_rkeys(base, token)
    report = {"ok": True, "account_qq": qq, "kinds": ks, "requested": len(items),
              "success": 0, "failed": 0, "bytes": 0, "elapsed": 0.0,
              "stopped": False, "reason_hint": None,
              "by_reason": {}, "by_kind": {}, "items": []}
    if not rkeys:
        report["reason_hint"] = "nc_get_rkey 为空：NapCat 未登录或 rkey 接口不可用"
    if not items:
        report["processed"] = 0
        report["success_rate"] = 0.0
        report["avg_bytes"] = 0
        report["elapsed"] = round(time.monotonic() - t0, 2)
        return report

    total = 0
    for idx, it in enumerate(items, 1):
        if stop_event is not None and stop_event.is_set():
            report["stopped"] = True
            break
        if total >= cap:
            report["stopped"] = True
            report["reason_hint"] = "达到总字节上限，已提前停止"
            break
        rec = _fetch_one(it, rkeys, base, token)
        report["items"].append(rec)
        if rec["ok"]:
            report["success"] += 1
            total += int(rec.get("bytes") or 0)
            report["bytes"] = total
        else:
            report["failed"] += 1
            r = rec.get("reason") or "not_found"
            report["by_reason"][r] = report["by_reason"].get(r, 0) + 1
        bk = report["by_kind"].setdefault(rec["kind"], {"success": 0, "failed": 0})
        bk["success" if rec["ok"] else "failed"] += 1
        if progress:
            try:
                progress({"done": idx, "total": len(items),
                          "success": report["success"], "failed": report["failed"],
                          "bytes": total,
                          "current": {"id": rec["id"], "kind": rec["kind"],
                                      "ok": rec["ok"], "reason": rec["reason"],
                                      "http": rec["http"], "bytes": rec["bytes"]}})
            except Exception:  # noqa: BLE001
                pass
        if idx < len(items) and sleep:
            if stop_event is not None:
                if stop_event.wait(float(sleep)):
                    report["stopped"] = True
                    break
            else:
                time.sleep(float(sleep))

    report["processed"] = len(report["items"])
    report["elapsed"] = round(time.monotonic() - t0, 2)
    report["success_rate"] = round(report["success"] / len(items), 4) if items else 0.0
    report["avg_bytes"] = int(report["bytes"] / report["success"]) if report["success"] else 0
    return report


def onebot_health(base=None, token=None) -> dict:
    '''探测 NapCat / OneBot 是否在线（报告里不含 token）。'''
    try:
        info = onebot_call("get_login_info", {}, base, token, timeout=10.0)
    except MediaFetchError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "user_id": info.get("user_id"), "nickname": info.get("nickname")}