"""QQScope · QQ 动态（QZone）路由。

挂载：server/app.py 的 _mount_optional_routers() 会自动 import 本模块并
app.include_router(router)，所以这里只写相对路径，不加前缀。

关于 SPEC 8.5 的 POST /api/sources/qzone/sync：
app.py 里已存在通用路由 POST /api/sources/{sid}/sync，且注册顺序早于本模块，
会优先匹配 /api/sources/qzone/sync。为不修改 app.py，本模块在导入时把
core.sources.qzone 注册进 app.py 的 SOURCE_MODULES，让通用路由分发到 qzone。
这样 SPEC 路径无需改 app.py 即可生效（本文件也显式声明了一份同路径路由作为兜底）。
"""
from __future__ import annotations

import sys

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


def _register_source() -> None:
    """把 qzone 注册进运行中 app 的数据源表（幂等，绝不改 app.py 文件）。"""
    try:
        for name in ("__main__", "server.app", "app"):
            mod = sys.modules.get(name)
            table = getattr(mod, "SOURCE_MODULES", None)
            if isinstance(table, dict):
                table.setdefault("qzone", "core.sources.qzone")
                return
    except Exception:
        pass


_register_source()


def _qzone():
    try:
        from core.sources import qzone
        return qzone, ""
    except Exception as exc:  # noqa: BLE001
        return None, f"QZone 模块未就绪：{exc.__class__.__name__}: {exc}"


def _err(msg: str, code: int = 400, **extra) -> JSONResponse:
    body = {"error": msg}
    body.update(extra)
    return JSONResponse(body, status_code=code)


def _to_api(row: dict) -> dict:
    """feeds 表行 -> 接口字段（含最近 3 条评论）。

    注：QZone 的列表/详情 cgi 都不返回「点赞数」，所以 praise 恒为 0，
    并显式带 praise_available=false，前端不要把它当成真实点赞数。
    """
    comments = row.get("comments") or []
    if not isinstance(comments, list):
        comments = []
    try:
        recent = sorted(comments, key=lambda c: int((c or {}).get("ts") or 0))[-3:]
    except Exception:
        recent = comments[-3:]
    return {
        "id": row.get("id"),
        "ts": row.get("ts"),
        "author_qq": row.get("author_qq"),
        "author_name": row.get("author_name"),
        "content": row.get("content"),
        "images": row.get("images") or [],
        "praise": row.get("praise") or 0,
        "praise_available": False,
        "comment_count": row.get("comment_count",
                                row.get("comments") if isinstance(row.get("comments"), int) else 0) or 0,
        "forward_count": row.get("forward_count", row.get("forwards") or 0) or 0,
        "comments": recent,
        "comments_total": len(comments),
    }


@router.get("/api/feeds")
def list_feeds(account: int | None = None, limit: int = 20, pos: int = 0,
               scope: str = "all"):
    """读取已落库的 QQ 动态。

    空态明确区分：need_login（没登录态）/ empty（有登录态但没同步到动态）/ error（异常），
    并始终带 reason 字段说明原因，方便前端做空态引导。
    """
    if not account:
        return _err("缺少 account（多账号隔离：动态必须按账号隔离，禁止回退全局 qzone uin）",
                    400, status="error", reason="account_required")
    account = int(account)
    qzone, why = _qzone()
    if qzone is None:
        return _err(why, 503, status="error", reason="module")
    limit = max(1, min(int(limit or 20), 200))
    pos = max(0, int(pos or 0))
    scope = str(scope or "all").lower()
    if scope not in ("mine", "friends", "all"):
        scope = "all"
    try:
        rows = qzone.list_feeds(account, limit=limit, offset=pos, scope=scope)
        total = qzone.count_feeds(account, scope=scope)
    except Exception as exc:  # noqa: BLE001
        return _err(f"读取动态失败：{exc}", 500, status="error", reason="db")
    feeds = [_to_api(r) for r in rows]
    next_pos = pos + len(rows)
    payload = {
        "feeds": feeds,
        "total": total,
        "scope": scope,
        "next_pos": next_pos if (len(feeds) >= limit and next_pos < total) else None,
        "status": "ok",
        "reason": "",
    }
    if not feeds:
        try:
            cs = qzone.credential_status()
        except Exception as exc:  # noqa: BLE001
            cs = {"status": "error", "reason": "credential", "message": str(exc)}
        if cs.get("status") == "need_login":
            payload.update({"status": "need_login", "reason": "no_credential",
                            "message": cs.get("message"), "need_login": True})
        elif cs.get("status") == "error":
            payload.update({"status": "error", "reason": cs.get("reason") or "error",
                            "message": cs.get("message")})
        else:
            payload.update({"status": "empty", "reason": "no_feeds_synced",
                            "message": "已获取 QZone 登录态，但本地还没有动态；请先执行同步"})
    return payload

async def _body(request: Request) -> dict:
    try:
        b = await request.json()
    except Exception:
        b = {}
    return b if isinstance(b, dict) else {}


def _do_sync(opts: dict):
    qzone, why = _qzone()
    if qzone is None:
        return _err(why, 503, status="error", reason="module")
    try:
        return qzone.sync(opts or {})
    except Exception as exc:  # noqa: BLE001
        reason = getattr(exc, "reason", "error")
        need = bool(getattr(exc, "need_login", False)) or reason == "need_login"
        return _err(str(exc) or exc.__class__.__name__, 502,
                    status=reason, reason=reason, need_login=need)


@router.post("/api/qzone/sync")
async def qzone_sync(request: Request):
    """不冲突的同步入口（app.py 通用路由不会抢）。"""
    return _do_sync(await _body(request))


@router.post("/api/sources/qzone/sync")
async def qzone_sync_spec(request: Request):
    """SPEC 8.5 路径。若被 app.py 通用路由优先匹配，则由 SOURCE_MODULES 分发到 qzone。"""
    return _do_sync(await _body(request))


@router.get("/api/sources/qzone/status")
def qzone_status():
    qzone, why = _qzone()
    if qzone is None:
        return _err(why, 503)
    return qzone.status()
