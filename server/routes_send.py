# -*- coding: utf-8 -*-
"""QQScope · 网页端发送文本消息路由（写操作，必须二次确认）

由 server/app.py 的 _mount_optional_routers() 自动 import 并
app.include_router(router)，所以这里写完整路径、不加前缀。

安全设计
--------
* POST /api/send 必须带 confirm:true，否则 428（防误触 / 防自动化误发）。
* 同一会话 3 秒最多 1 条；全局 30 秒最多 10 条（限速在 core.send 里做）。
* 单条 ≤ 2000 字符，空文本拒绝。
* 只支持「一次调用发一条」，没有群发/批量/定时入口。
* 发送内容会记进 /api/logs（source='send'），方便用户自查发了什么。
"""
from __future__ import annotations

import sys

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core import send as send_mod

router = APIRouter()

MAX_LEN = send_mod.MAX_LEN


def _log(level: str, msg: str) -> None:
    """写进运行中 app.py 的 LOG_BUFFER（/api/logs 能看到）。找不到就退化成 print。"""
    for name in ("__main__", "server.app", "app"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "log"):
            try:
                mod.log(level, "send", msg)
                return
            except Exception:  # noqa: BLE001
                pass
    print(f"[{level}] [send] {msg}", flush=True)


def _err(msg: str, status: int = 400, **extra) -> JSONResponse:
    body = {"error": msg}
    body.update(extra)
    return JSONResponse(body, status_code=status)


async def _body(request: Request) -> dict:
    try:
        b = await request.json()
    except Exception:  # noqa: BLE001
        b = {}
    return b if isinstance(b, dict) else {}


# ── 安全策略（2026-10-01 误发事故后新增）────────────────────────────────────
# 背景：自测时误把消息发给了真人。现在默认策略是「只许发给自己」，
# 要发给别人/群必须在设置里显式打开。
def _send_policy() -> dict:
    for name in ("__main__", "server.app", "app"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "load_settings"):
            try:
                return (mod.load_settings().get("send") or {})
            except Exception:  # noqa: BLE001
                pass
    return {}


def _login_qq() -> int:
    """取当前框架登录的账号（用于白名单比对）。失败返回 0。"""
    try:
        from core import send as _s
        base, token = _s._endpoint()
        import httpx
        r = httpx.post(f"{base}/get_login_info", json={},
                       headers=({"Authorization": f"Bearer {token}"} if token else {}),
                       timeout=8, trust_env=False)
        return int(((r.json() or {}).get("data") or {}).get("user_id") or 0)
    except Exception:  # noqa: BLE001
        return 0


@router.post("/api/send")
async def api_send(request: Request):
    """发一条文本消息。必须 confirm:true；目标必须是用户手动选择的会话。"""
    b = await _body(request)
    kind = str(b.get("kind") or "").strip().lower()
    peer_id = b.get("peer_id")
    peer_qq = b.get("peer_qq") or 0
    account_qq = b.get("account_qq") or 0
    text = b.get("text")
    who = f"{kind or '?'}/{(peer_id if peer_id not in (None, '') else peer_qq)}"

    # 0) 多账号隔离：必须显式带 account_qq（禁止静默按登录号发送）
    try:
        account_qq = int(account_qq)
    except (TypeError, ValueError):
        return _err("account_qq 不是数字", 400)
    if account_qq <= 0:
        return _err("缺少 account_qq（多账号隔离：发送必须显式指定当前登录账号）", 400)

    # 1) 二次确认（铁律：没有 confirm:true 绝不发送）
    if b.get("confirm") is not True:
        _log("warn", f"拒绝未确认的发送请求（{who}）：缺少 confirm:true")
        return _err("发送真实消息需要 confirm:true", 428)

    # 2) 入参校验
    if not isinstance(text, str) or not text.strip():
        _log("warn", f"拒绝空消息（{who}）")
        return _err("消息内容不能为空", 400)
    if len(text) > MAX_LEN:
        _log("warn", f"拒绝超长消息（{who}）：{len(text)} 字")
        return _err(f"单条消息不能超过 {MAX_LEN} 字", 400)
    if kind not in ("c2c", "group"):
        return _err("kind 只能是 c2c 或 group", 400)

    # 2.5) 多账号隔离：dry_run 也必须先校验身份，禁止静默跨账号
    login = _login_qq()
    if login and account_qq != login:
        _log("warn", f"拒绝跨账号发送（{who}）：请求 {account_qq}，当前登录 {login}")
        return _err(f"account_qq（{account_qq}）与当前框架登录账号（{login}）不一致，已拒绝发送",
                    400, code="account_mismatch")

    # 2.6) 安全策略：默认只许发给自己；群聊默认禁止
    policy = _send_policy()
    if bool(b.get("dry_run")) or bool(policy.get("dry_run")):
        _log("info", f"演练模式（{who}）：已校验但不真实发送：{text[:60]}")
        return {"ok": True, "dry_run": True, "message_id": None,
                "would_send": {"kind": kind, "peer_id": peer_id, "peer_qq": peer_qq, "text": text[:200]}}

    if kind == "group" and not policy.get("allow_groups"):
        _log("warn", f"策略拦截群发（{who}）")
        return _err("安全策略：默认禁止向群聊发送。确认要开启请在设置里打开「允许发送到群聊」",
                    403, code="policy_blocked")
    if kind == "c2c" and login and int(peer_qq or 0) != login and not policy.get("allow_others"):
        _log("warn", f"策略拦截发给他人（{who}），当前登录 {login}")
        return _err(f"安全策略：默认只允许发给自己（{login}）。要给他人发消息，请在设置里打开「允许发给他人」",
                    403, code="policy_blocked", login_qq=login)

    # 3) 真正发送（core.send 里还会做限速 / 账号校验 / 写库）
    try:
        res = send_mod.send_text(account_qq, kind, peer_id, peer_qq, text)
    except Exception as exc:  # noqa: BLE001
        _log("error", f"发送异常（{who}）：{exc.__class__.__name__}: {exc}")
        return _err(f"发送失败：{exc}", 500)

    if res.get("ok"):
        _log("info", f"已发送 {who} -> message_id={res.get('message_id')}：{text[:80]}")
        return {
            "ok": True,
            "message_id": res.get("message_id"),
            "account_qq": res.get("account_qq"),
            "nickname": res.get("nickname"),
            "kind": kind,
            "peer_id": res.get("peer_id"),
            "ts": res.get("ts"),
            "stored": res.get("stored"),
            "raw": res.get("raw"),
        }

    code = res.get("code") or "send_failed"
    err = res.get("error") or "发送失败"
    if code == "rate_limited":
        _log("warn", f"限速拦截（{who}）：{err}")
        return _err(err, 429, retry_after=res.get("retry_after"))
    _log("warn", f"发送失败（{who}）：{err}")
    http_code = 400 if code in ("empty", "too_long", "bad_kind", "bad_target",
                                "account_mismatch") else 502
    return _err(err, http_code, code=code)


@router.get("/api/send/history")
def api_send_history(account: int | None = None, limit: int = 50):
    """本机通过网页发过的消息（source='local_send'）。必须带 account，禁止跨账号。"""
    if not account:
        return _err("缺少 account（多账号隔离：发送历史必须指定账号）", 400)
    try:
        limit = max(1, min(int(limit or 50), 200))
    except (TypeError, ValueError):
        limit = 50
    try:
        rows = send_mod.send_history(int(account), limit)
    except Exception as exc:  # noqa: BLE001
        return _err(f"读取发送历史失败：{exc}", 500)
    return {"messages": rows, "total": len(rows), "source": send_mod.SOURCE}