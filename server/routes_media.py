# -*- coding: utf-8 -*-
"""QQScope · 媒体接口（SPEC 第 10.4 节）

由 server/app.py 的 _mount_optional_routers() 自动挂载：

    app.include_router(routes_media.router)

约定
----
* /api/media/{msg_id} 按 store 的 messages.id 取媒体，本地有就返回文件流，
  本地没有就 404 + JSON，绝不 500。
* 响应头固定带 X-Media-Kind 与 X-Media-Source: cache|missing。
* 媒体一律读本地 nt_data 缓存，不直连腾讯 CDN；token / cookie 不落日志。
* 静态路径（stats / pending）必须声明在 /api/media/{msg_id} 之前。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse

from core import media, store

router = APIRouter()


def _not_found(msg: str, kind: str | None = None) -> JSONResponse:
    headers = {"X-Media-Source": "missing"}
    if kind:
        headers["X-Media-Kind"] = str(kind)
    return JSONResponse({"error": msg, "source": "missing"}, status_code=404, headers=headers)


@router.get("/api/media/stats")
def media_stats(account: int = Query(..., description="账号 QQ")):
    """各类媒体的本地命中率 + 本地索引概况。"""
    try:
        stats = media.media_stats(account)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"媒体统计失败：{exc}"}, status_code=500)
    return {"account": int(account), "media": stats, "index": media.index_stats(account)}


@router.get("/api/media/pending")
def media_pending(account: int = Query(...), kind: str | None = None, limit: int = 200):
    """本地缺失的媒体清单（供以后补下载用）。"""
    try:
        items = media.pending(account, kind, limit)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"媒体清单查询失败：{exc}"}, status_code=500)
    return {"account": int(account), "kind": kind or "", "count": len(items), "items": items}


@router.get("/api/media/{msg_id}")
def media_file(msg_id: int,
               account: int | None = Query(None, description="账号 QQ（必填，多账号隔离）")):
    """按 messages.id + account 返回媒体文件流；必须校验归属，绝不跨账号。"""
    if not account:
        return JSONResponse({"error": "缺少 account（多账号隔离：媒体必须指定所属账号）",
                             "source": "missing"}, status_code=400)
    account = int(account)
    con = store.connect(account)
    try:
        row = con.execute(
            "SELECT id, account_qq, kind, peer_id, ts, text, media FROM messages WHERE id=?",
            (int(msg_id),)).fetchone()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"读取消息失败：{exc}"}, status_code=500)
    finally:
        con.close()

    if row is None or int(row["account_qq"] or 0) != account:
        return _not_found("消息不存在或不属于该账号")
    raw = row["media"]
    if not raw:
        return _not_found("这条消息没有媒体内容")
    try:
        m = json.loads(raw)
    except (TypeError, ValueError):
        return _not_found("媒体信息已损坏")
    if not isinstance(m, dict):
        return _not_found("媒体信息已损坏")

    kind = str(m.get("kind") or "unknown")
    try:
        path = media.resolve(row["account_qq"], m)
    except Exception:  # noqa: BLE001
        path = None
    if not path:
        fb = m.get("fallback") or kind
        return _not_found(f"本地没有缓存文件：{fb}", kind)

    ctype = media.guess_content_type(path)
    return FileResponse(path, media_type=ctype, headers={
        "X-Media-Kind": kind,
        "X-Media-Source": "cache",
        "Cache-Control": "private, max-age=3600",
    })


@router.get("/api/data/quality")
def data_quality(account: int | None = None):
    """数据健康度：c2c 会话数 vs 唯一 QQ 数（重复会话数应为 0）。"""
    from core import normalize

    if not account:
        return JSONResponse({"error": "缺少 account（多账号隔离：数据健康度必须指定账号）"},
                            status_code=400)
    try:
        return normalize.duplicate_stats(int(account))
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"数据健康度统计失败：{exc}"}, status_code=500)


@router.post("/api/sources/pack/rescan-media")
async def rescan_media(body: dict | None = None):
    """只重扫 content/media 列（复用已解密的缓存，不重新解密整库）。"""
    from core.sources import pack_source

    opts = dict(body or {})
    try:
        res = pack_source.rescan_media(opts)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"媒体重扫失败：{exc}"}, status_code=500)
    return res

# ── 缺失媒体补下载后台任务（task-20 末尾追加） ─────────────────────────────
# 本段只追加接口，不改动上面任何已有函数。
# 任务在后台线程跑；限速 / 可中断 / 总上限由 core.media_fetch 负责，
# 这里只做任务注册、进度快照与启停。进度与报告里绝不含 rkey/token。
import threading
import time
import uuid

_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()
_ACTIVE_JOB: str | None = None
_MAX_JOBS = 20


def _clamp_int(v, lo, hi, default):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _job_snapshot(job: dict) -> dict:
    return {
        "ok": True,
        "job": job["id"],
        "status": job["status"],
        "account": job["account"],
        "kinds": job["kinds"],
        "limit": job["limit"],
        "dry_run": job["dry_run"],
        "done": job["done"],
        "total": job["total"],
        "success": job["success"],
        "failed": job["failed"],
        "bytes": job["bytes"],
        "current": job.get("current"),
        "elapsed": round(time.time() - job["created"], 2),
        "error": job.get("error"),
        "result": job.get("result"),
    }


def _run_backfill_job(job: dict) -> None:
    global _ACTIVE_JOB
    try:
        from core import media_fetch
    except Exception as exc:  # noqa: BLE001
        with _JOBS_LOCK:
            job.update(status="failed", error=f"media_fetch 不可用：{exc}",
                       updated=time.time())
        _ACTIVE_JOB = None
        return

    def _progress(p: dict) -> None:
        with _JOBS_LOCK:
            job.update(done=p.get("done", 0), total=p.get("total", job["total"]),
                       success=p.get("success", 0), failed=p.get("failed", 0),
                       bytes=p.get("bytes", 0), current=p.get("current"),
                       updated=time.time())

    try:
        if job["dry_run"]:
            items = media_fetch.list_missing(job["account"], job["kinds"], job["limit"])
            previews = []
            for it in items:
                src = media_fetch._extract_source(it.get("content"))
                previews.append({
                    "id": it["id"], "kind": it.get("media_kind"),
                    "name": (it.get("media") or {}).get("name"),
                    "peer": str(it.get("peer_qq") or it.get("peer_id") or ""),
                    "has_cdn": bool(src.get("path")), "appid": src.get("appid")})
            rep = {"ok": True, "dry_run": True, "account_qq": job["account"],
                   "kinds": job["kinds"], "requested": len(items),
                   "with_cdn": sum(1 for p in previews if p["has_cdn"]),
                   "items": previews}
        else:
            rep = media_fetch.fetch_missing(
                job["account"], kinds=job["kinds"], limit=job["limit"],
                max_bytes=job["max_bytes"], progress=_progress,
                stop_event=job["stop"])
        with _JOBS_LOCK:
            job["result"] = rep
            job["success"] = rep.get("success", job["success"])
            job["failed"] = rep.get("failed", job["failed"])
            job["bytes"] = rep.get("bytes", job["bytes"])
            job["done"] = rep.get("processed", len(rep.get("items") or []))
            job["total"] = rep.get("requested", job["total"])
            job["status"] = "stopped" if (rep.get("stopped") or job["stop"].is_set()) else "done"
            job["current"] = None
            job["updated"] = time.time()
    except Exception as exc:  # noqa: BLE001
        with _JOBS_LOCK:
            job.update(status="failed", error=f"{type(exc).__name__}: {exc}",
                       updated=time.time())
    finally:
        _ACTIVE_JOB = None

@router.post("/api/media/backfill")
async def media_backfill_start(body: dict | None = None):
    '''启动缺失媒体补下载后台任务（默认上限 500 条 / 2GB，限速且可中断）。'''
    global _ACTIVE_JOB
    from core import media_fetch

    b = dict(body or {})
    account = _clamp_int(b.get("account") or b.get("account_qq"), 1, 10 ** 12, 0)
    if not account:
        return JSONResponse({"error": "缺少 account"}, status_code=400)
    kinds = b.get("kinds") or ["image"]
    if isinstance(kinds, str):
        kinds = [kinds]
    kinds = [str(k).strip().lower() for k in kinds if str(k).strip()]
    kinds = [k for k in kinds if k in media_fetch.KINDS] or ["image"]
    limit = _clamp_int(b.get("limit"), 1, media_fetch.MAX_LIMIT, media_fetch.DEFAULT_LIMIT)
    max_bytes = _clamp_int(b.get("max_bytes"), 1, 50 * 1024 ** 3,
                           media_fetch.DEFAULT_MAX_BYTES)
    dry_run = bool(b.get("dry_run"))

    with _JOBS_LOCK:
        if _ACTIVE_JOB:
            return JSONResponse({"error": "已有补下载任务在运行，请先等待或停止",
                                 "job": _ACTIVE_JOB}, status_code=409)
        jid = uuid.uuid4().hex[:12]
        job = {"id": jid, "account": account, "kinds": kinds, "limit": limit,
               "max_bytes": max_bytes, "dry_run": dry_run, "status": "running",
               "created": time.time(), "updated": time.time(), "done": 0,
               "total": limit, "success": 0, "failed": 0, "bytes": 0,
               "current": None, "result": None, "error": None,
               "stop": threading.Event()}
        _JOBS[jid] = job
        while len(_JOBS) > _MAX_JOBS:
            oldest = min(_JOBS, key=lambda k: _JOBS[k]["created"])
            if oldest == jid:
                break
            _JOBS.pop(oldest, None)
        _ACTIVE_JOB = jid
    threading.Thread(target=_run_backfill_job, args=(job,), daemon=True).start()
    return {"ok": True, "job": jid, "status": "running", "account": account,
            "kinds": kinds, "limit": limit, "max_bytes": max_bytes, "dry_run": dry_run}


@router.get("/api/media/backfill/status")
def media_backfill_status(job: str | None = None, account: int | None = None):
    '''查补下载任务进度 / 结果；不传 job 就返回当前或最近一个任务。'''
    if not account:
        return JSONResponse({"error": "缺少 account（多账号隔离：补下载状态必须指定账号）"},
                            status_code=400)
    with _JOBS_LOCK:
        if job:
            j = _JOBS.get(job)
        else:
            j = _JOBS.get(_ACTIVE_JOB) if _ACTIVE_JOB else None
            if j is None and _JOBS:
                j = max(_JOBS.values(), key=lambda x: x["created"])
        if j is None:
            return {"ok": False, "error": "没有补下载任务"}
        if account and int(j.get("account") or 0) != int(account):
            return {"ok": False, "error": "任务不属于该账号"}
        return _job_snapshot(j)


@router.post("/api/media/backfill/stop")
def media_backfill_stop(job: str | None = None, account: int | None = None):
    '''请求中断补下载任务（当前这一条下载完才停）。'''
    if not account:
        return JSONResponse({"error": "缺少 account（多账号隔离：补下载停止必须指定账号）"},
                            status_code=400)
    with _JOBS_LOCK:
        jid = job or _ACTIVE_JOB
        j = _JOBS.get(jid) if jid else None
        if j is None:
            return JSONResponse({"error": "没有可停止的任务"}, status_code=404)
        if account and int(j.get("account") or 0) != int(account):
            return JSONResponse({"error": "任务不属于该账号"}, status_code=404)
        j["stop"].set()
        j["updated"] = time.time()
        return {"ok": True, "job": j["id"], "status": "stopping"}