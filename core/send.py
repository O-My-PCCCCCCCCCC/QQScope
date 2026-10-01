# -*- coding: utf-8 -*-
"""QQScope · 网页端发送文本消息（写操作）

⚠️ 本模块会把消息真的发给 QQ 联系人。安全约定：
* 只做「用户手动点一下发一条」——绝不群发 / 批量 / 定时 / 自动重试。
* 真正的二次确认在 HTTP 层（server/routes_send.py）用 confirm:true 强制，
  本模块自身只负责发送一次 + 写库。
* 调用方必须是用户主动触发的接口；任何脚本/自动化不得直接调用。

对外：
    send_text(account_qq, kind, peer_id, peer_qq, text) -> dict
    send_history(account_qq=None, limit=50) -> list[dict]
"""
from __future__ import annotations

import json
import threading
import time

from core import paths, store

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None

from core.sources import bot_source

SOURCE = "local_send"
MAX_LEN = 2000
PEER_MIN_INTERVAL = 3.0     # 同一会话 3 秒最多 1 条
GLOBAL_WINDOW = 30.0        # 全局 30 秒窗口
GLOBAL_MAX = 10             # 窗口内最多 10 条

_ENDPOINT_FILE = paths.ROOT / "data" / "framework" / "onebot.json"
_SETTINGS_FILE = paths.ROOT / "data" / "server" / "settings.json"

_lock = threading.Lock()
_last_by_peer: dict[str, float] = {}
_global_times: list[float] = []


class SendError(Exception):
    """发送失败（带 code 供路由映射 HTTP 状态码）。"""

    def __init__(self, message: str, code: str = "send_failed"):
        super().__init__(message)
        self.code = code


def _to_int(v, default: int = 0) -> int:
    try:
        if v is None or v == "":
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _endpoint() -> tuple[str, str]:
    """OneBot base/token：优先 data/framework/onebot.json，其次设置里的 onebot。"""
    for p, wrapped in ((_ENDPOINT_FILE, False), (_SETTINGS_FILE, True)):
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        ob = j.get("onebot") if wrapped else j
        if not isinstance(ob, dict):
            continue
        base = str(ob.get("base") or "").strip()
        if base:
            return base, str(ob.get("token") or "").strip()
    return "", ""


def _reserve(account_qq: int, kind: str, peer_id) -> tuple[bool, float, str]:
    """限速：同一会话 3s/1；全局 30s/10。返回 (ok, retry_after, reason)。"""
    key = f"{int(account_qq or 0)}:{kind}:{peer_id}"
    now = time.monotonic()
    with _lock:
        cutoff = now - GLOBAL_WINDOW
        while _global_times and _global_times[0] < cutoff:
            _global_times.pop(0)
        last = _last_by_peer.get(key)
        if last is not None and now - last < PEER_MIN_INTERVAL:
            wait = round(PEER_MIN_INTERVAL - (now - last), 1)
            return False, wait, f"同一会话 3 秒内只能发 1 条，请 {wait} 秒后再试"
        if len(_global_times) >= GLOBAL_MAX:
            wait = round(GLOBAL_WINDOW - (now - _global_times[0]), 1)
            return False, wait, f"发送过于频繁（30 秒最多 {GLOBAL_MAX} 条），请 {wait} 秒后再试"
        _last_by_peer[key] = now
        _global_times.append(now)
        return True, 0.0, ""


def _human(retcode, wording: str) -> str:
    w = str(wording or "")
    low = w.lower()
    if any(k in w for k in ("未登录", "请登录", "登录失效", "未登陆")) or "not logged" in low or "login" in low:
        return f"账号未登录：{w or '请先在 NapCat 扫码登录'}"
    if any(k in w for k in ("风控", "频率", "频繁", "太快", "限制")) or "risk" in low \
            or "limit" in low or "too fast" in low:
        return f"发送被腾讯风控/频率限制拦截：{w}"
    if any(k in w for k in ("不存在", "找不到", "无效")) or "not found" in low or "invalid" in low:
        return f"目标不存在或不可用：{w}"
    return f"发送失败（retcode={retcode}）：{w or '框架返回未知错误'}"


def _code_for(wording: str) -> str:
    w = str(wording or "")
    low = w.lower()
    if any(k in w for k in ("未登录", "请登录", "登录失效", "未登陆")) or "not logged" in low or "login" in low:
        return "not_logged"
    if any(k in w for k in ("不存在", "找不到", "无效")) or "not found" in low or "invalid" in low:
        return "bad_target"
    return "send_failed"


def _call(base: str, action: str, params: dict, token: str, timeout: float = 15.0) -> dict:
    """调 OneBot 一次，返回完整 JSON 响应（含 status/retcode/data）。"""
    if httpx is None:
        raise SendError("缺少 httpx 依赖，无法发送", "framework")
    url = bot_source._norm_base(base) + "/" + action
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = httpx.post(url, json=dict(params or {}), headers=headers,
                       timeout=httpx.Timeout(connect=5.0, read=timeout, write=timeout, pool=timeout),
                       trust_env=False)
    except httpx.TimeoutException:
        raise SendError("发送超时：请确认 NapCat 在运行、网络正常", "framework")
    except httpx.ConnectError as e:
        low = str(e).lower()
        if "refused" in low or "10061" in low or "拒绝" in str(e):
            raise SendError("连不上 NapCat：目标计算机拒绝连接，请确认框架已启动", "framework")
        raise SendError("连不上 NapCat：网络不可达，请检查框架地址", "framework")
    except httpx.InvalidURL:
        raise SendError("OneBot 地址不合法，请检查 data/framework/onebot.json", "framework")
    except httpx.HTTPError:
        raise SendError("发送请求失败：网络异常，请稍后重试", "framework")
    if r.status_code in (401, 403):
        raise SendError("NapCat 拒绝请求：token 错误或未授权", "not_logged")
    if r.status_code >= 400:
        raise SendError(f"NapCat 返回 HTTP {r.status_code}", "send_failed")
    try:
        j = r.json()
    except Exception:
        raise SendError("NapCat 返回的不是合法 JSON", "send_failed")
    if not isinstance(j, dict):
        raise SendError("NapCat 返回格式异常", "send_failed")
    retcode = j.get("retcode")
    if j.get("status") != "ok" or retcode not in (0, "0", None):
        w = str(j.get("wording") or j.get("msg") or j.get("message") or "")
        raise SendError(_human(retcode, w), _code_for(w))
    return j


def send_text(account_qq, kind, peer_id, peer_qq, text) -> dict:
    """发送一条文本。返回 {ok, message_id, error, ...}。

    成功后会写 store（source='local_send', direction=1, ts=now）。
    """
    content = str(text or "").strip()
    if not content:
        return {"ok": False, "message_id": None, "error": "消息内容不能为空", "code": "empty"}
    if len(content) > MAX_LEN:
        return {"ok": False, "message_id": None,
                "error": f"单条消息不能超过 {MAX_LEN} 字", "code": "too_long"}
    kind = str(kind or "").lower()
    if kind not in ("c2c", "group"):
        return {"ok": False, "message_id": None, "error": "kind 只能是 c2c 或 group",
                "code": "bad_kind"}

    target = _to_int(peer_qq) or (_to_int(peer_id) if str(peer_id).isdigit() else 0)
    if target <= 0:
        msg = "缺少群号" if kind == "group" else "缺少对方 QQ 号（uid 会话必须带 peer_qq）"
        return {"ok": False, "message_id": None, "error": msg, "code": "bad_target"}

    base, token = _endpoint()
    if not base:
        return {"ok": False, "message_id": None,
                "error": "未配置 OneBot 地址（data/framework/onebot.json）", "code": "framework"}

    # 账号身份（同时验证框架在线 / 已登录）
    try:
        uin, nick = bot_source._login(base, token)
    except bot_source.BotError as e:
        code = "not_logged" if getattr(e, "unlogged", False) else "framework"
        return {"ok": False, "message_id": None, "error": str(e), "code": code}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message_id": None, "error": f"连接框架失败：{e}", "code": "framework"}
    if _to_int(account_qq) != uin:
        return {"ok": False, "message_id": None,
                "error": f"当前框架登录的是 {uin}，与请求账号 {account_qq or '(空)'} 不一致；"
                         f"多账号隔离下必须以当前登录账号发送",
                "code": "account_mismatch"}

    # 限速（放在真正发送之前）
    ok, wait, why = _reserve(uin, kind, peer_id)
    if not ok:
        return {"ok": False, "message_id": None, "error": why,
                "code": "rate_limited", "retry_after": wait}

    action = "send_private_msg" if kind == "c2c" else "send_group_msg"
    key = "user_id" if kind == "c2c" else "group_id"
    params = {key: int(target), "message": content}
    try:
        raw = _call(base, action, params, token)
    except SendError as e:
        return {"ok": False, "message_id": None, "error": str(e), "code": e.code}

    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    message_id = data.get("message_id") or data.get("messageId")

    ts = int(time.time())
    pid = str(peer_id if peer_id not in (None, "") else target)
    row = {
        "account_qq": int(uin), "kind": kind, "peer_id": pid, "peer_qq": int(target),
        "ts": ts, "direction": 1, "sender_qq": int(uin), "sender_name": nick,
        "msg_type": 0, "text": content, "source": SOURCE, "media": None,
        "content": json.dumps({"onebot_action": action, "onebot_params": params,
                               "onebot_response": raw}, ensure_ascii=False),
    }
    try:
        added = store.insert_messages([row])
    except Exception as e:  # noqa: BLE001
        return {"ok": True, "message_id": message_id, "account_qq": int(uin),
                "nickname": nick, "raw": raw, "stored": 0, "peer_id": pid, "text": content,
                "ts": ts, "error": f"消息已发出，但写入本地库失败：{e}"}

    if added:
        try:
            with store.tx() as con:
                con.execute(
                    "UPDATE contacts SET last_ts=?, last_text=?, msg_count=msg_count+1, "
                    "self_count=self_count+1 WHERE account_qq=? AND kind=? AND peer_id=?",
                    (ts, content, int(uin), kind, pid))
        except Exception:
            pass

    return {"ok": True, "message_id": message_id, "account_qq": int(uin),
            "nickname": nick, "raw": raw, "stored": added,
            "peer_id": pid, "text": content, "ts": ts}


def send_history(account_qq=None, limit: int = 50) -> list[dict]:
    """本机通过网页发过的消息（source='local_send'），按时间倒序。"""
    limit = max(1, min(_to_int(limit, 50) or 50, 200))
    con = store.connect()
    try:
        if account_qq:
            cur = con.execute(
                "SELECT * FROM messages WHERE source=? AND account_qq=? "
                "ORDER BY ts DESC, id DESC LIMIT ?", (SOURCE, _to_int(account_qq), limit))
        else:
            cur = con.execute(
                "SELECT * FROM messages WHERE source=? ORDER BY ts DESC, id DESC LIMIT ?",
                (SOURCE, limit))
        return [dict(r) for r in cur.fetchall()]
    finally:
        con.close()