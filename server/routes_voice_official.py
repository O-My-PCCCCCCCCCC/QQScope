# -*- coding: utf-8 -*-
'''QQScope · 官方语音转文字回填接口（task-24）

由 server/app.py 的 _mount_optional_routers() 自动挂载（模块不存在时自动跳过）：
    app.include_router(routes_voice_official.router)

接口
----
* POST /api/voice/official/sync   body {account_qq, peer_limit?, per_peer?, limit?}
       -> 后台线程跑，返回 {ok, job}；运行中重复启动返回 409
* GET  /api/voice/official/status?job=   -> 进度 / 最终报告
* POST /api/voice/official/stop?job=     -> 请求中断（当前这条做完即停）
* GET  /api/voice/official/stats?account= -> official / local / both 计数

说明
----
* 官方结果优先，但覆盖前把本地 whisper 原文快照到 media.voice_text_local，
  逐条对比不丢数据。
* 限速 1s/条、串行、可中断由 core.voice_official 负责。
* token 只从 data/framework/onebot.json 读，绝不进日志 / 响应。
'''
from __future__ import annotations

import threading
import time
import uuid

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

router = APIRouter()

_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()
_ACTIVE_JOB: str | None = None
_MAX_JOBS = 20


def _clamp(v, lo, hi, default):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _snapshot(job: dict) -> dict:
    return {
        "ok": True,
        "job": job["id"],
        "status": job["status"],
        "account_qq": job["account_qq"],
        "peer_limit": job["peer_limit"],
        "per_peer": job["per_peer"],
        "limit": job["limit"],
        "scanned": job["scanned"],
        "voice_found": job["voice_found"],
        "done": job["done"],
        "total": job["total"],
        "ok_count": job["ok_count"],
        "failed_count": job["failed_count"],
        "filled": job["filled"],
        "current": job.get("current"),
        "elapsed": round(time.time() - job["created"], 2),
        "error": job.get("error"),
        "result": job.get("result"),
    }


def _run_job(job: dict) -> None:
    global _ACTIVE_JOB
    try:
        from core import voice_official
    except Exception as exc:  # noqa: BLE001
        with _JOBS_LOCK:
            job.update(status="failed", error=f"voice_official 不可用：{exc}")
        _ACTIVE_JOB = None
        return

    def _progress(p: dict) -> None:
        with _JOBS_LOCK:
            if p.get("stage") == "scan":
                job["scanned"] = p.get("scanned", job["scanned"])
                job["voice_found"] = p.get("voice_found", job["voice_found"])
                job["total"] = p.get("voice_found", job["total"]) or job["total"]
            else:
                job["done"] = p.get("done", job["done"])
                job["total"] = p.get("total", job["total"])
                job["ok_count"] = p.get("ok_count", job["ok_count"])
                job["failed_count"] = p.get("failed_count", job["failed_count"])
                job["filled"] = p.get("filled", job["filled"])
                job["current"] = p.get("current")
            job["updated"] = time.time()

    try:
        rep = voice_official.transcribe_recent(
            job["account_qq"], peer_limit=job["peer_limit"],
            per_peer=job["per_peer"], limit=job["limit"],
            progress=_progress, stop_event=job["stop"])
        with _JOBS_LOCK:
            job["result"] = rep
            job["scanned"] = rep.get("scanned", job["scanned"])
            job["voice_found"] = rep.get("voice_found", job["voice_found"])
            job["total"] = rep.get("voice_found", job["total"])
            job["done"] = len(rep.get("results") or [])
            job["ok_count"] = rep.get("ok_count", job["ok_count"])
            job["failed_count"] = rep.get("failed_count", job["failed_count"])
            job["filled"] = rep.get("filled", job["filled"])
            job["current"] = None
            job["status"] = "stopped" if (rep.get("stopped") or job["stop"].is_set()) else (
                "done" if rep.get("ok") else "failed")
            job["error"] = None if rep.get("ok") else "; ".join(rep.get("errors") or [])[:300]
            job["updated"] = time.time()
    except Exception as exc:  # noqa: BLE001
        with _JOBS_LOCK:
            job.update(status="failed", error=f"{type(exc).__name__}: {exc}",
                       updated=time.time())
    finally:
        _ACTIVE_JOB = None

@router.post("/api/voice/official/sync")
async def voice_official_sync(body: dict | None = None):
    '''启动官方语音转文字回填后台任务（1s/条限速，可中断）。'''
    global _ACTIVE_JOB
    b = dict(body or {})
    account = _clamp(b.get("account_qq") or b.get("account"), 1, 10 ** 12, 0)
    if not account:
        return JSONResponse({"error": "缺少 account_qq"}, status_code=400)
    peer_limit = _clamp(b.get("peer_limit"), 1, 200, 40)
    per_peer = _clamp(b.get("per_peer"), 1, 500, 100)
    limit = _clamp(b.get("limit"), 1, 5000, 0) or None

    with _JOBS_LOCK:
        if _ACTIVE_JOB:
            return JSONResponse({"error": "已有官方转写任务在运行，请先等待或停止",
                                 "job": _ACTIVE_JOB}, status_code=409)
        jid = uuid.uuid4().hex[:12]
        job = {"id": jid, "account_qq": account, "peer_limit": peer_limit,
               "per_peer": per_peer, "limit": limit, "status": "running",
               "created": time.time(), "updated": time.time(),
               "scanned": 0, "voice_found": 0, "done": 0, "total": 0,
               "ok_count": 0, "failed_count": 0, "filled": 0,
               "current": None, "result": None, "error": None,
               "stop": threading.Event()}
        _JOBS[jid] = job
        while len(_JOBS) > _MAX_JOBS:
            oldest = min(_JOBS, key=lambda k: _JOBS[k]["created"])
            if oldest == jid:
                break
            _JOBS.pop(oldest, None)
        _ACTIVE_JOB = jid
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return {"ok": True, "job": jid, "status": "running", "account_qq": account,
            "peer_limit": peer_limit, "per_peer": per_peer, "limit": limit}


@router.get("/api/voice/official/status")
def voice_official_status(job: str | None = None):
    '''查官方转写任务进度 / 最终报告；不传 job 返回当前或最近一个。'''
    with _JOBS_LOCK:
        if job:
            j = _JOBS.get(job)
        else:
            j = _JOBS.get(_ACTIVE_JOB) if _ACTIVE_JOB else None
            if j is None and _JOBS:
                j = max(_JOBS.values(), key=lambda x: x["created"])
        if j is None:
            return {"ok": False, "error": "没有官方转写任务"}
        return _snapshot(j)


@router.post("/api/voice/official/stop")
def voice_official_stop(job: str | None = None):
    '''请求中断官方转写任务（当前这条做完即停）。'''
    with _JOBS_LOCK:
        jid = job or _ACTIVE_JOB
        j = _JOBS.get(jid) if jid else None
        if j is None:
            return JSONResponse({"error": "没有可停止的任务"}, status_code=404)
        j["stop"].set()
        j["updated"] = time.time()
        return {"ok": True, "job": j["id"], "status": "stopping"}


@router.get("/api/voice/official/stats")
def voice_official_stats(account: int = Query(..., description="账号 QQ")):
    '''official / local / both 计数（本地 whisper 原文快照仍在 voice_text_local）。'''
    from core import voice_official
    try:
        return voice_official.stats(account)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"官方语音统计失败：{exc}"}, status_code=500)