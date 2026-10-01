# -*- coding: utf-8 -*-
"""QQScope · 语音转文字接口（task-14）

由 server/app.py 的 _mount_optional_routers() 自动挂载：

    app.include_router(routes_voice.router)

约定
----
* POST /api/voice/transcribe 只在后台线程启动 voice.run()，立即返回 job；
  重复调用幂等（已有任务在跑就返回同一个 job）。
* GET /api/voice/progress 返回 阶段 / 已完成 / 总数 / 当前文件。
* GET /api/voice/stats 返回真实统计（成功 / 失败 / 语言跑偏分开计）。
* 失败一律如实反馈，不把失败算成功。
"""
from __future__ import annotations

import threading
import time

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from core import voice

router = APIRouter()

_lock = threading.Lock()
_job: dict = {"job": None, "state": {
    "stage": "idle", "running": False, "done": 0, "total": 0,
    "current": "", "started_at": None, "finished_at": None,
    "result": None, "error": None,
}}


def _state(**kw) -> dict:
    with _lock:
        _job["state"].update(kw)
        out = dict(_job["state"])
        out["job"] = _job["job"]
        return out


def _run_job(opts: dict) -> None:
    def prog(stage: str, done: int, total: int, current: str) -> None:
        _state(stage=stage, done=int(done), total=int(total), current=str(current))

    try:
        res = voice.run(
            opts["account"],
            confirm=True,
            limit=opts.get("limit") or opts.get("recent"),
            newest=bool(opts.get("recent")),
            workers=int(opts.get("workers") or voice.DEFAULT_WORKERS),
            model_size=opts.get("model_size") or voice.DEFAULT_MODEL,
            threads=int(opts.get("threads") or voice.DEFAULT_THREADS),
            progress=prog,
        )
        _state(stage="完成" if res.get("ok") else "失败", result=res,
               running=False, finished_at=int(time.time()),
               error=None if res.get("ok") else res.get("message"))
    except Exception as exc:  # noqa: BLE001
        _state(stage="失败", error=f"{exc.__class__.__name__}: {exc}",
               running=False, finished_at=int(time.time()))


@router.post("/api/voice/transcribe")
async def voice_transcribe(body: dict | None = None):
    """后台启动语音转写；已有任务在跑时返回同一个 job（幂等）。"""
    b = dict(body or {})
    if not b.get("confirm"):
        return JSONResponse(
            {"error": "本地 ASR 已暂停（用户反馈机器卡）。确认要继续请传 {\"confirm\": true}；"
                      "同一时间只允许一个任务，且不要与 CLI 同时跑。"},
            status_code=409)
    account = b.get("account_qq") or b.get("account")
    if not account:
        return JSONResponse({"error": "缺少 account_qq"}, status_code=400)
    try:
        account = int(account)
    except (TypeError, ValueError):
        return JSONResponse({"error": f"account_qq 不是数字：{account}"}, status_code=400)

    with _lock:
        if _job["state"].get("running"):
            cur = dict(_job["state"])
            cur["job"] = _job["job"]
            if account and int(cur.get("account") or 0) not in (0, account):
                return JSONResponse({"error": "已有其它账号的转写任务在运行，请先等待完成"},
                                    status_code=409)
            return {"ok": True, "job": _job["job"], "state": cur,
                    "message": "已有转写任务在运行，返回同一个 job"}
        job = f"voice-{int(time.time())}"
        _job["job"] = job
        _job["state"] = {
            "stage": "启动", "running": True, "done": 0, "total": 0,
            "current": "", "started_at": int(time.time()), "finished_at": None,
            "result": None, "error": None, "account": account,
        }
    opts = {
        "account": account,
        "limit": b.get("limit"),
        "recent": b.get("recent"),
        "workers": b.get("workers"),
        "model_size": b.get("model_size"),
        "threads": b.get("threads"),
    }
    threading.Thread(target=_run_job, args=(opts,), daemon=True).start()
    return {"ok": True, "job": job}


@router.get("/api/voice/progress")
def voice_progress(account: int | None = None):
    with _lock:
        st = dict(_job["state"])
        st["job"] = _job["job"]
    if account and int(st.get("account") or 0) not in (0, int(account)):
        return {"job": st.get("job"), "stage": "idle", "running": False, "done": 0,
                "total": 0, "current": "", "started_at": None, "finished_at": None,
                "result": None, "error": None, "account": int(account)}
    return st


@router.get("/api/voice/stats")
def voice_stats(account: int = Query(..., description="账号 QQ")):
    try:
        return voice.stats(account)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"语音统计失败：{exc}"}, status_code=500)