# -*- coding: utf-8 -*-
"""QQScope · 实时采集路由（轮询版）

由 server/app.py 的 _mount_optional_routers() 自动发现并挂载（按 routes_*.py 文件名）。
后端启动时会自动 start()（可用环境变量 QQSCOPE_LIVE_AUTOSTART=0 或
data/server/settings.json 的 live.auto=false 关掉）。

接口：
    GET  /api/live/status
    POST /api/live/focus   {kind, peer_id, peer_qq?}
    POST /api/live/start
    POST /api/live/stop
    GET  /api/live/events?account=&since=&since_id=&limit=
所有动作写 /api/logs（source='live'）。
"""
from __future__ import annotations

import json
import os
import sys

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core import live_sync as live_mod
from core import paths

router = APIRouter()


def _log(level: str, msg: str) -> None:
    for name in ("__main__", "server.app", "app"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "log"):
            try:
                mod.log(level, "live", msg)
                return
            except Exception:  # noqa: BLE001
                pass
    print(f"[{level}] [live] {msg}", flush=True)


def _live() -> live_mod.LiveSync:
    return live_mod.get_live()


async def _body(request: Request) -> dict:
    try:
        b = await request.json()
    except Exception:  # noqa: BLE001
        b = {}
    return b if isinstance(b, dict) else {}


def _err(msg: str, status: int = 400, **extra) -> JSONResponse:
    body = {"error": msg}
    body.update(extra)
    return JSONResponse(body, status_code=status)


def _check_account(live, account):
    """多账号隔离：实时采集接口必须显式带 account，且与框架当前登录号一致。"""
    try:
        acc = int(account or 0)
    except (TypeError, ValueError):
        return 400, "account 不是数字"
    if acc <= 0:
        return 400, "缺少 account（多账号隔离：实时采集必须指定账号）"
    uin = int(getattr(live, "uin", 0) or 0)
    if uin and acc != uin:
        return 403, f"account（{acc}）与当前框架登录账号（{uin}）不一致"
    return None


@router.get("/api/live/status")
def api_live_status():
    return _live().status()


@router.post("/api/live/focus")
async def api_live_focus(request: Request):
    b = await _body(request)
    live = _live()
    bad = _check_account(live, b.get("account") or b.get("account_qq"))
    if bad:
        return _err(bad[1], bad[0])
    kind = b.get("kind")
    peer_id = b.get("peer_id")
    try:
        res = live.set_focus(kind, peer_id, b.get("peer_qq") or 0)
    except Exception as exc:  # noqa: BLE001
        _log("error", f"设置 focus 失败：{exc.__class__.__name__}: {exc}")
        return _err(f"设置 focus 失败：{exc}", 500)
    if res.get("focus"):
        _log("info", f"focus -> {res['focus']['kind']}:{res['focus']['peer_id']}")
    else:
        _log("info", "focus 已清除")
    return res


@router.post("/api/live/start")
async def api_live_start(request: Request):
    b = await _body(request)
    live = _live()
    bad = _check_account(live, b.get("account") or b.get("account_qq"))
    if bad:
        return _err(bad[1], bad[0])
    try:
        res = live.start()
    except Exception as exc:  # noqa: BLE001
        _log("error", f"启动实时采集失败：{exc.__class__.__name__}: {exc}")
        return _err(f"启动实时采集失败：{exc}", 500)
    _log("info", f"start -> running={res.get('running')}（{res.get('message')}）")
    res["status"] = live.status()
    return res


@router.post("/api/live/stop")
def api_live_stop():
    try:
        res = _live().stop()
    except Exception as exc:  # noqa: BLE001
        _log("error", f"停止实时采集失败：{exc.__class__.__name__}: {exc}")
        return _err(f"停止实时采集失败：{exc}", 500)
    _log("info", f"stop -> running={res.get('running')}（{res.get('message')}）")
    res["status"] = _live().status()
    return res


@router.get("/api/live/events")
def api_live_events(account: int | None = None, since: int = 0,
                    since_id: int = 0, limit: int = 300):
    if not account:
        return _err("缺少 account（多账号隔离：实时增量必须指定账号）", 400)
    try:
        return _live().events(since=since, account=int(account),
                              since_id=since_id, limit=limit)
    except Exception as exc:  # noqa: BLE001
        _log("error", f"读取增量失败：{exc.__class__.__name__}: {exc}")
        return _err(f"读取增量失败：{exc}", 500)


# ── 后端启动时自动开（可在设置里关）─────────────────────────────────────────
def _autostart_enabled() -> bool:
    if str(os.environ.get("QQSCOPE_LIVE_AUTOSTART", "1")).lower() in ("0", "false", "no", "off"):
        return False
    try:
        p = paths.ROOT / "data" / "server" / "settings.json"
        s = json.loads(p.read_text(encoding="utf-8"))
        live = s.get("live") or {}
        if live.get("auto") is False or live.get("enabled") is False:
            return False
    except Exception:  # noqa: BLE001
        pass
    return True


def _autostart() -> None:
    if not _autostart_enabled():
        _log("info", "实时采集自动启动已关闭（settings live.auto=false 或环境变量）")
        return
    try:
        res = _live().start()
        _log("info", f"随后端自动开启实时采集：{res.get('message')}")
    except Exception as exc:  # noqa: BLE001
        _log("error", f"自动开启实时采集失败：{exc.__class__.__name__}: {exc}")


_autostart()