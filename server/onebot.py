"""QQScope · OneBot 数据源（NapCat 框架对接）
在 Linux 框架（NapCat）里跑：扫码登录任意 QQ（动态）→ OneBot HTTP 接口
→ 本模块拉取好友/群列表 + 历史消息 → 转统一消息格式 → 生成数据包 → 仪表盘展示。
PC 与 Termux 通用。
"""
import json
import re
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PACK_DIR = ROOT / "data" / "pack"


def _api(base: str, action: str, params: dict, token: str = "") -> dict:
    url = base.rstrip("/") + "/" + action
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = httpx.post(url, json=params, headers=headers, timeout=30)
    r.raise_for_status()
    j = r.json()
    if j.get("status") != "ok":
        raise RuntimeError(f"OneBot 返回异常: {j.get('retcode')} {j.get('msg') or j.get('wording')}")
    return j.get("data") or {}


def cq_to_text(raw: str) -> str:
    """去掉 CQ 码（图片/表情等），保留纯文本"""
    if not raw:
        return ""
    return re.sub(r"\[CQ:[^\]]*\]", "", raw).strip()


def ob_msg_to_row(m: dict, self_uin: int) -> dict | None:
    text = cq_to_text(m.get("raw_message") or "")
    if not text:
        return None
    mt = m.get("message_type", "private")
    sender_uin = (m.get("sender") or {}).get("user_id") or m.get("user_id", 0)
    if mt == "group":
        peer = m.get("group_id", 0)
        kind = "group"
    else:
        peer = m.get("user_id", 0)
        kind = "c2c"
    return {
        "t": int(m.get("time") or 0),
        "d": 1 if sender_uin == self_uin else 0,
        "p": peer,
        "k": kind,
        "x": text,
    }


def sync(base: str, token: str = "", top_friends: int = 20, top_groups: int = 20,
         per_peer: int = 80) -> dict:
    """全量同步：登录信息 → 好友/群列表 → 历史消息 → 数据包"""
    login = _api(base, "get_login_info", {}, token)
    uin = int(login.get("user_id", 0))
    nick = login.get("nickname") or f"账号 {uin}"
    if not uin:
        raise RuntimeError("get_login_info 未返回账号")

    friends = _api(base, "get_friend_list", {}, token) or []
    groups = _api(base, "get_group_list", {}, token) or []

    rows: list[dict] = []
    errors: list[str] = []

    # 好友历史
    for f in friends[:top_friends]:
        try:
            msgs = _api(base, "get_friend_msg_history",
                        {"user_id": f["user_id"], "count": per_peer, "message_seq": 0}, token)
            for m in msgs or []:
                row = ob_msg_to_row(m, uin)
                if row and row["t"]:
                    rows.append(row)
        except Exception as e:
            errors.append(f"好友 {f.get('user_id')}: {e}")
    # 群历史
    for g in groups[:top_groups]:
        try:
            msgs = _api(base, "get_group_msg_history",
                        {"group_id": g["group_id"], "count": per_peer, "message_seq": 0}, token)
            for m in msgs or []:
                row = ob_msg_to_row(m, uin)
                if row and row["t"]:
                    rows.append(row)
        except Exception as e:
            errors.append(f"群 {g.get('group_id')}: {e}")

    rows.sort(key=lambda r: r["t"])
    total = len(rows)
    self_n = sum(1 for r in rows if r["d"] == 1)
    c2c_n = sum(1 for r in rows if r["k"] == "c2c")
    grp_n = sum(1 for r in rows if r["k"] == "group")

    meta = {
        "qq": uin,
        "label": nick,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_messages": total,
        "self_messages": self_n,
        "c2c_total": c2c_n,
        "c2c_self": sum(1 for r in rows if r["k"] == "c2c" and r["d"] == 1),
        "group_total": grp_n,
        "group_self": sum(1 for r in rows if r["k"] == "group" and r["d"] == 1),
        "time_start": rows[0]["t"] if rows else 0,
        "time_end": rows[-1]["t"] if rows else 0,
        "friend_count": len(friends),
        "group_count": len(groups),
        "friends": [{"qq": f["user_id"], "name": f.get("nickname", "")} for f in friends[:50]],
        "groups": [{"qq": g["group_id"], "name": g.get("group_name", "")} for g in groups[:50]],
        "source": "onebot",
    }

    out_dir = PACK_DIR / str(uin)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "messages.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
    with open(out_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    return {"ok": True, "uin": uin, "nickname": nick, "messages": total, "self": self_n,
            "friends": len(friends), "groups": len(groups), "errors": errors[:5]}
