"""QQScope · 窗口B 数据源：机器人框架读取（在线）

通过 OneBot v11 HTTP 接口（NapCat / Lagrange / go-cqhttp 等）读取：
    get_login_info           账号身份
    get_friend_list          好友列表
    get_group_list           群列表
    get_friend_msg_history   好友历史消息
    get_group_msg_history    群历史消息
→ 转成统一消息格式 → 写入 core/store.py（统一主库）。

设计要点：
- 复用原 server/onebot.py 的 _api / cq_to_text / ob_msg_to_row / sync 思路，
  改造为直接写统一主库（不再生成 data/pack/*.json）。
- 单请求超时 ≤ 15s；probe 只做连通性 + 列表，绝不拉历史消息，保证远快于 sync。
- 所有失败都转成「人话中文」，不把异常栈丢给用户。
- 消息去重交给 store 的 ux_msg 唯一索引，重复 sync 不会重复入库。

对外契约（见 docs/SPEC-重构接口.md 第 3 节）：
    status() / probe(opts) / sync(opts, progress) / conversations(opts)
"""
from __future__ import annotations

import html
import json
import re
import socket
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

from core import paths, store
from core.qq_face import face_to_emoji

try:
    from core import media as media_mod
except Exception:  # pragma: no cover
    media_mod = None

try:  # httpx 只在真正发起 HTTP 请求时才需要，允许无 httpx 环境下 import 本模块
    import httpx
except Exception:  # pragma: no cover
    httpx = None


SOURCE_ID = "bot"
SOURCE_NAME = "机器人框架读取"
SOURCE_KIND = "bot"

DEFAULT_BASE = "http://127.0.0.1:3000"
# status() 里探测的常见 OneBot HTTP 端口（NapCat / Lagrange / go-cqhttp）
CANDIDATE_BASES = [
    "http://127.0.0.1:3000",   # NapCat OneBot HTTP 默认
    "http://127.0.0.1:6099",   # NapCat WebUI / 部分配置
    "http://127.0.0.1:5700",   # go-cqhttp / Lagrange 默认
]

NAPCAT_LAUNCHER = paths.NAPCAT_DIR / "启动框架.bat"
NAPCAT_LAUNCHER_LEGACY = paths.NAPCAT_LEGACY_DIR / "napcat" / "launcher.bat"


def _launcher() -> Path:
    """优先便携版启动器，找不到再回退旧目录。"""
    if NAPCAT_LAUNCHER.exists():
        return NAPCAT_LAUNCHER
    if NAPCAT_LAUNCHER_LEGACY.exists():
        return NAPCAT_LAUNCHER_LEGACY
    return NAPCAT_LAUNCHER

PROBE_TCP_TIMEOUT = 1.0       # probe 里的 TCP 预检，快速区分「拒绝」与「超时」
STATUS_TCP_TIMEOUT = 0.3      # status() 里逐端口探测，尽量别让页面卡住
CONNECT_TIMEOUT_PROBE = 2.0
CONNECT_TIMEOUT_SYNC = 5.0
READ_TIMEOUT_SYNC = 15.0      # 硬要求：单请求 ≤ 15s


class BotError(Exception):
    """带「人话中文」说明的 OneBot 访问错误。"""

    def __init__(self, message: str, unsupported: bool = False, unlogged: bool = False):
        super().__init__(message)
        self.unsupported = unsupported   # 框架不支持该 action / 参数
        self.unlogged = unlogged         # 未登录


# ── 小工具 ──────────────────────────────────────────────────────────────────
def _to_int(v, default: int = 0) -> int:
    try:
        if v is None or v == "":
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _norm_base(base: str | None) -> str:
    b = (base or DEFAULT_BASE).strip()
    if not b:
        b = DEFAULT_BASE
    if "://" not in b:
        b = "http://" + b
    return b.rstrip("/")


def _parse_host_port(base: str) -> tuple[str, int]:
    u = urlsplit(_norm_base(base))
    host = u.hostname or "127.0.0.1"
    port = u.port or (443 if u.scheme == "https" else 80)
    return host, port


def _tcp_check(base: str, timeout: float = 1.0) -> tuple[bool, str]:
    """快速 TCP 预检。返回 (是否可达, 失败原因: refused/timeout/dns/unreachable:*)。"""
    host, port = _parse_host_port(base)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, ""
    except (TimeoutError, socket.timeout):
        return False, "timeout"
    except ConnectionRefusedError:
        return False, "refused"
    except socket.gaierror:
        return False, "dns"
    except OSError as e:
        code = getattr(e, "winerror", None) or getattr(e, "errno", None)
        if code in (10061, 61):
            return False, "refused"
        if code in (11001, -2, -3, 8):      # WSAHOST_NOT_FOUND / EAI_NONAME
            return False, "dns"
        return False, f"unreachable:{type(e).__name__}"


def _tcp_error_msg(base: str, reason: str) -> str:
    if reason == "refused":
        return (f"连不上 {base}：目标计算机拒绝连接，请确认 NapCat 已启动"
                f"（启动器：{_launcher()}）")
    if reason == "timeout":
        return (f"连接 {base} 超时：目标无响应，请确认地址/端口正确、NapCat 已启动，"
                f"并检查防火墙是否放行")
    if reason == "dns":
        return f"连不上 {base}：域名解析失败，请检查地址拼写（示例：http://127.0.0.1:3000）"
    return f"连不上 {base}：网络不可达，请检查地址与网络连接（{reason}）"


def _require_httpx() -> None:
    if httpx is None:
        raise BotError(
            "缺少 httpx 依赖：请用随包 python\\python.exe 运行后端；"
            "开发机上也可用 tools/nt_msg_db_util/.venv 的解释器。")


def _human_retcode(action: str, retcode, wording: str) -> BotError:
    wording = wording or ""
    low = wording.lower()
    if retcode in (102, 103, 104, 1403, 1404) or "不支持" in wording or \
            "unsupported" in low or "unknown action" in low:
        return BotError(
            f"框架不支持动作 {action}（retcode={retcode} {wording or '未实现'}）："
            f"不同框架（NapCat / Lagrange / go-cqhttp）动作名可能不同，请升级框架或改用 NapCat",
            unsupported=True)
    if "登录" in wording or "login" in low or "not logged" in low:
        return BotError(f"{action} 调用失败：账号未登录，请先在 NapCat 扫码登录后再试",
                        unlogged=True)
    return BotError(f"{action} 调用失败（retcode={retcode}）：{wording or '框架返回未知错误'}")


# ── OneBot HTTP 调用 ────────────────────────────────────────────────────────
def _api(base: str, action: str, params: dict | None = None, token: str = "",
         probe: bool = False) -> object:
    """调用 OneBot HTTP action，返回 data 字段。

    失败一律抛 BotError（中文人话），绝不泄漏异常栈。
    """
    _require_httpx()
    base = _norm_base(base)
    url = f"{base}/{action}"
    payload = dict(params or {})
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    timeout = httpx.Timeout(
        connect=CONNECT_TIMEOUT_PROBE if probe else CONNECT_TIMEOUT_SYNC,
        read=6.0 if probe else READ_TIMEOUT_SYNC,
        write=6.0 if probe else READ_TIMEOUT_SYNC,
        pool=6.0 if probe else READ_TIMEOUT_SYNC,
    )

    def _post(use_query_token: bool):
        u = f"{url}?access_token={quote(token)}" if (use_query_token and token) else url
        # trust_env=False：显式忽略系统/环境代理。Windows 系统代理会把 127.0.0.1
        # 也劫持走，导致莫名 HTTP 502；同时避免 token 被代理看到。
        return httpx.post(u, json=payload, headers=headers, timeout=timeout,
                          trust_env=False)

    try:
        r = _post(False)
        # 部分框架只认 query 参数形式的 access_token，401 时自动再试一次
        if r.status_code in (401, 403) and token:
            r = _post(True)
    except httpx.TimeoutException:
        raise BotError(f"连接 {base} 超时：请确认 NapCat 已启动、端口正确，或检查防火墙是否放行")
    except httpx.ConnectError as e:
        low = str(e).lower()
        if "refused" in low or "10061" in low or "拒绝" in str(e):
            raise BotError(f"连不上 {base}：目标计算机拒绝连接，请确认 NapCat 已启动"
                           f"（启动器：{_launcher()}）")
        raise BotError(f"连不上 {base}：网络不可达，请检查地址/端口是否正确")
    except httpx.InvalidURL:
        raise BotError(f"地址不合法：{base}（示例：http://127.0.0.1:3000）")
    except httpx.HTTPError:
        raise BotError(f"请求 {base} 失败：网络异常，请稍后重试")

    if r.status_code in (401, 403):
        raise BotError(f"{base} 拒绝了请求（HTTP {r.status_code}）：token 错误或未授权，"
                       f"请检查 token 配置是否与 OneBot 一致")
    if r.status_code >= 400:
        raise BotError(f"{base} 返回 HTTP {r.status_code}：该地址可能不是 OneBot HTTP 服务")

    try:
        j = r.json()
    except Exception:
        raise BotError(f"{base} 返回的不是合法 JSON：请确认这是 OneBot HTTP 服务"
                       f"（例如 http://127.0.0.1:3000）")
    if not isinstance(j, dict):
        raise BotError(f"{base} 返回格式异常：OneBot 响应应为 JSON 对象")

    retcode = j.get("retcode")
    status = j.get("status")
    wording = str(j.get("wording") or j.get("msg") or j.get("message") or "")
    ok = (status in ("ok", "async")) or (retcode in (0, "0", None) and "data" in j)
    if not ok:
        raise _human_retcode(action, retcode, wording)

    data = j.get("data")
    return data if data is not None else {}


def _unwrap_list(data) -> list:
    """兼容 data 直接是 list，或 {"messages":[...]} / {"list":[...]} 等包装。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("messages", "message", "list", "friends", "groups", "history", "data"):
            v = data.get(key)
            if isinstance(v, list):
                return v
    return []


# ── CQ 码解析（逐类真解析，绝不整段删）─────────────────────────────────────
# 注意：老版本是「CQ 码 → 中文占位符」（[图片]/[表情]/[@]），文本被剥掉了 emoji。
# 现在改成真解析：face → Unicode emoji、image/record/video/file → media JSON、
# at → @QQ、未知 CQ 原样保留。下面保留老逻辑 _legacy_raw_to_text()，
# 只为「老库就地升级」时能认出旧行，避免重复入库。

_CQ_CODE_RE = re.compile(r"\[CQ:([A-Za-z_0-9]+)((?:,[^\]]*)?)\]")
_LEGACY_CQ_RE = re.compile(r"\[CQ:([a-zA-Z_]+)[^\]]*\]")
_CQ_STRIP_RE = re.compile(r"\[CQ:[^\]]*\]")

# 老版本占位符表（仅用于 _legacy_raw_to_text，勿删）
_SEG_PLACEHOLDER = {
    "image": "[图片]", "face": "[表情]", "mface": "[表情]", "record": "[语音]",
    "video": "[视频]", "file": "[文件]", "at": "[@]", "reply": "",
    "json": "[卡片]", "xml": "[卡片]", "forward": "[转发]", "poke": "[戳一戳]",
    "rps": "", "dice": "", "shake": "", "markdown": "[卡片]",
}

# 卡片类 CQ（无文本，产出 kind=card 的 media）
_CARD_FALLBACK = {
    "json": "[卡片]", "ark": "[卡片]", "xml": "[卡片]", "markdown": "[卡片]",
    "forward": "[合并转发的聊天记录]", "flashtransfer": "[闪传文件]",
    "music": "[音乐]", "location": "[位置]", "contact": "[名片]",
    "poke": "[戳一戳]", "redbag": "[红包]", "gift": "[礼物]",
    "share": "[分享]", "card": "[卡片]", "ttt": "[卡片]",
}
_CARD_TYPES = set(_CARD_FALLBACK)

_SIZE_UNITS = (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024), ("B", 1))
_MD5_NAME_RE = re.compile(r"^([0-9a-fA-F]{32})(?:\.[A-Za-z0-9]+)?$")
_MD5_PREFIX_RE = re.compile(r"^([0-9a-fA-F]{32})_")


def cq_to_text(raw: str) -> str:
    """只保留纯文本、剥掉 CQ 码（保留兼容旧调用；新代码请用 parse_cq）。"""
    if not raw:
        return ""
    return _CQ_STRIP_RE.sub("", raw).strip()


def _legacy_raw_to_text(raw: str) -> str:
    """老版本文本产出（CQ → 占位符，未知类型 → [type]），仅用于老库升级匹配。"""
    if not raw:
        return ""
    return _LEGACY_CQ_RE.sub(
        lambda mm: _SEG_PLACEHOLDER.get(mm.group(1).lower(),
                                        f"[{mm.group(1).lower()}]"), raw).strip()


def _legacy_message_text(m: dict) -> str:
    """老版本文本产出（含 message 数组路径），仅用于老库升级匹配。"""
    raw = m.get("raw_message")
    if isinstance(raw, str) and raw.strip():
        return _legacy_raw_to_text(raw)
    msg = m.get("message")
    if isinstance(msg, str):
        return _legacy_raw_to_text(msg)
    if isinstance(msg, list):
        parts: list[str] = []
        for seg in msg:
            if not isinstance(seg, dict):
                continue
            typ = str(seg.get("type") or "").lower()
            data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
            if typ == "text":
                parts.append(str(data.get("text", "")))
            else:
                parts.append(_SEG_PLACEHOLDER.get(typ, f"[{typ}]"))
        return "".join(parts).strip()
    return ""


def _s(v) -> str:
    return "" if v is None else str(v)


def _int_or_none(v):
    try:
        if v is None or v == "":
            return None
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _size_text(n) -> str:
    n = _int_or_none(n) or 0
    for unit, div in _SIZE_UNITS:
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} B"


def _md5_from_name(name):
    """从 CQ 的 file / file_id 里猜 md5：<32hex>.ext 或 <32hex>_<uuid>。"""
    base = _s(name).replace("\\", "/").rsplit("/", 1)[-1]
    if not base:
        return None
    m = _MD5_NAME_RE.match(base)
    if m:
        return m.group(1).lower()
    m = _MD5_PREFIX_RE.match(base)
    return m.group(1).lower() if m else None


def _segments_from_raw(raw: str) -> list:
    """CQ 字符串 → [(type, data_dict, 原始CQ码)]；先按逗号切分再 html.unescape。"""
    segs: list = []
    pos = 0
    for m in _CQ_CODE_RE.finditer(raw):
        if m.start() > pos:
            segs.append(("text", {"text": raw[pos:m.start()]}, None))
        typ = m.group(1).lower()
        data: dict = {}
        for part in (m.group(2) or "").split(","):
            if not part:
                continue
            if "=" in part:
                k, v = part.split("=", 1)
                data[k.strip()] = html.unescape(v)
            else:
                data[part.strip()] = ""
        segs.append((typ, data, m.group(0)))
        pos = m.end()
    if pos < len(raw):
        segs.append(("text", {"text": raw[pos:]}, None))
    return segs


def _segments_from_message(msg: list) -> list:
    segs: list = []
    for seg in msg:
        if not isinstance(seg, dict):
            continue
        typ = str(seg.get("type") or "").lower()
        data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
        segs.append((typ, dict(data), None))
    return segs


def _build_media(typ: str, data: dict):
    """image / record / video / file → SPEC 10.3 的 media dict（file 稍后本地补齐）。"""
    if typ == "image":
        f = _s(data.get("file")); url = _s(data.get("url"))
        size = _int_or_none(data.get("file_size")) or 0
        m = {"kind": "image", "md5": _md5_from_name(f), "name": f, "size": size,
             "duration": None, "file": None, "fallback": "[图片]",
             "cq_file": f or None, "cq_url": url or None}
        sub = data.get("sub_type")
        if sub not in (None, ""):
            m["sub_type"] = _int_or_none(sub)
        summary = _s(data.get("summary"))
        if summary:
            m["summary"] = summary
        return m
    if typ == "record":
        f = _s(data.get("file")); url = _s(data.get("url")); p = _s(data.get("path"))
        size = _int_or_none(data.get("file_size")) or 0
        return {"kind": "voice", "md5": _md5_from_name(f), "name": f, "size": size,
                "duration": None, "file": None, "fallback": "[语音]",
                "cq_file": f or None, "cq_url": url or None, "cq_path": p or None}
    if typ == "video":
        f = _s(data.get("file")); url = _s(data.get("url")); p = _s(data.get("path"))
        size = _int_or_none(data.get("file_size")) or 0
        return {"kind": "video", "md5": _md5_from_name(f), "name": f, "size": size,
                "duration": None, "file": None, "fallback": "[视频]",
                "cq_file": f or None, "cq_url": url or None, "cq_path": p or None}
    if typ == "file":
        name = _s(data.get("file")) or _s(data.get("name"))
        file_id = _s(data.get("file_id"))
        size = _int_or_none(data.get("file_size")) or 0
        fb = f"[文件: {name} ({_size_text(size)})]" if name else "[文件]"
        return {"kind": "file", "md5": _md5_from_name(file_id) or _md5_from_name(name),
                "name": name, "size": size, "duration": None, "file": None,
                "fallback": fb, "cq_file": name or None, "cq_file_id": file_id or None}
    return None


def _build_card(typ: str, data: dict) -> dict:
    m = {"kind": "card", "md5": None, "name": "", "size": 0, "duration": None,
         "file": None, "fallback": _CARD_FALLBACK.get(typ, "[卡片]")}
    ident = _s(data.get("id")) or _s(data.get("fileSetId")) or _s(data.get("qq"))
    if ident:
        m["cq_id"] = ident
    return m


def _finalize_media(media_list: list):
    """多段媒体只留第一个（store.media 是单对象），其余在 fallback 标 +N。"""
    if not media_list:
        return None
    first = dict(media_list[0])
    extra = len(media_list) - 1
    if extra > 0:
        fb = str(first.get("fallback") or "[媒体]")
        first["fallback"] = (fb[:-1] + f"+{extra}]") if fb.endswith("]") else (fb + f"+{extra}")
        first["extra_media"] = extra
    return first


def _media_placeholder_text(media: dict) -> str:
    """纯媒体消息的 text：用 fallback + 短标识，避免空文本互相去重丢消息。"""
    base = str(media.get("fallback") or "[媒体]")
    ident = (media.get("md5") or media.get("cq_file") or media.get("cq_file_id")
             or media.get("cq_id") or media.get("cq_path") or "")
    ident = re.sub(r"[^0-9A-Za-z]", "", str(ident))[:8]
    if not ident:
        return base
    return (base[:-1] + f" {ident}]") if base.endswith("]") else (base + f" {ident}")


def _render_segments(segments: list, self_uin: int = 0):
    text_parts: list[str] = []
    media_list: list[dict] = []
    for typ, data, raw_code in segments:
        typ = (typ or "").lower()
        if typ == "text":
            t = data.get("text")
            if t is None:
                t = data.get("content")
            if t:
                text_parts.append(str(t))
        elif typ == "face":
            fid = data.get("id")
            emo = face_to_emoji(fid)
            if emo:
                text_parts.append(emo)
            else:
                fid_s = _s(fid).strip()
                text_parts.append(f"[表情{fid_s}]" if fid_s else "[表情]")
        elif typ == "at":
            qq = _s(data.get("qq")).strip()
            if qq.lower() == "all":
                text_parts.append("@全体成员")
            elif qq:
                text_parts.append("@" + qq)
            else:
                text_parts.append("[@]")
        elif typ in ("image", "record", "video", "file"):
            m = _build_media(typ, data)
            if m:
                media_list.append(m)
        elif typ == "reply":
            pass  # 引用不占正文（和 QQ 显示一致）
        elif typ in _CARD_TYPES:
            media_list.append(_build_card(typ, data))
        else:
            # 未知 CQ：原样保留，宁可露出原始码也不要凭空丢内容
            text_parts.append(raw_code if raw_code else f"[{typ}]")
    return "".join(text_parts).strip(), _finalize_media(media_list)


def parse_cq(raw: str, self_uin: int = 0):
    """OneBot CQ 字符串 → (text, media)。media 为 dict 或 None。"""
    if not raw:
        return "", None
    return _render_segments(_segments_from_raw(str(raw)), self_uin)


def parse_onebot_message(m: dict, self_uin: int = 0):
    """优先 raw_message，其次 message（数组/字符串）。→ (text, media)"""
    raw = m.get("raw_message")
    if isinstance(raw, str) and raw.strip():
        return parse_cq(raw, self_uin)
    msg = m.get("message")
    if isinstance(msg, str) and msg.strip():
        return parse_cq(msg, self_uin)
    if isinstance(msg, list):
        return _render_segments(_segments_from_message(msg), self_uin)
    return "", None


def _enrich_media(account_qq: int, m: dict) -> dict:
    """本地补齐 media.file（md5 索引 / CQ path），语音顺带算时长。"""
    if not m or media_mod is None:
        return m
    try:
        out = media_mod.fill_media(account_qq, m) or m
    except Exception:
        out = m
    if not out.get("file"):
        p = out.get("cq_path")
        if p:
            try:
                if Path(p).is_file():
                    rel = media_mod.rel_path(account_qq, p)
                    if rel:
                        out["file"] = rel
            except Exception:
                pass
    return out


def ob_msg_text(m: dict) -> str:
    """只取文本（保留兼容旧调用）。"""
    return parse_onebot_message(m)[0]


def ob_msg_to_row(m: dict, self_uin: int, kind: str | None = None,
                  peer_id: str | None = None, peer_qq: int | None = None,
                  account_qq: int | None = None) -> dict | None:
    """OneBot 消息 → 统一主库 messages 行（含 text / media / content）。

    direction: 自己发出=1，收到=0（群聊同样按 sender 判定）。
    peer_id  : 私聊=对方 uid（无 uid 时用 QQ 号字符串），群聊=群号字符串。
    纯媒体消息（图片/语音/视频/文件/卡片）text 为空也要入库。
    """
    text, media = parse_onebot_message(m, self_uin)
    if not text and not media:
        return None
    if not text and media:
        text = _media_placeholder_text(media)

    aqq = _to_int(account_qq or self_uin, 0)
    if media:
        media = _enrich_media(aqq, media)

    mt = m.get("message_type") or ("group" if kind == "group" else "private")
    if kind is None:
        kind = "group" if mt == "group" else "c2c"

    sender = m.get("sender") if isinstance(m.get("sender"), dict) else {}
    sender_uin = _to_int(sender.get("user_id") or m.get("user_id"), 0)

    if kind == "group":
        gid = _to_int(m.get("group_id"), 0)
        peer = peer_id if peer_id is not None else str(gid)
        pqq = peer_qq if peer_qq is not None else gid
    else:
        uid = _to_int(m.get("user_id"), 0)
        peer = peer_id if peer_id is not None else str(uid)
        pqq = peer_qq if peer_qq is not None else uid

    direction = 1 if (self_uin and sender_uin == self_uin) else 0
    if direction == 0 and m.get("post_type") == "message_sent":
        direction = 1  # 部分框架用 post_type 标记自己发出的消息

    try:
        media_json = json.dumps(media, ensure_ascii=False) if media else None
    except Exception:
        media_json = None
    try:
        content_json = json.dumps(m, ensure_ascii=False)
    except Exception:
        content_json = None

    row = {
        "account_qq": aqq,
        "kind": kind,
        "peer_id": str(peer),
        "peer_qq": pqq,
        "ts": _to_int(m.get("time"), 0),
        "direction": direction,
        "sender_qq": sender_uin,
        "sender_name": str(sender.get("card") or sender.get("nickname")
                           or sender.get("name") or ""),
        "msg_type": 0,
        "text": text,
        "source": SOURCE_ID,
        "media": media_json,
        "content": content_json,
    }
    row["_legacy_text"] = _legacy_message_text(m)
    return row

# ── 列表 / 身份 ─────────────────────────────────────────────────────────────
def _login(base: str, token: str, probe: bool = False) -> tuple[int, str]:
    data = _api(base, "get_login_info", {}, token, probe=probe)
    if not isinstance(data, dict):
        data = {}
    uin = _to_int(data.get("user_id") or data.get("uin"), 0)
    if not uin:
        raise BotError("OneBot 未返回账号信息：可能尚未登录，请先在 NapCat 扫码登录后再试",
                       unlogged=True)
    nick = str(data.get("nickname") or data.get("nick") or f"QQ {uin}")
    return uin, nick


def _fetch_list(base: str, token: str, action: str, probe: bool = False) -> list:
    return _unwrap_list(_api(base, action, {}, token, probe=probe))


def _to_conversations(account_qq: int, friends: list, groups: list) -> list[dict]:
    out: list[dict] = []
    for f in friends or []:
        if not isinstance(f, dict):
            continue
        qq = _to_int(f.get("user_id") or f.get("uin"), 0)
        uid = str(f.get("uid") or qq)          # 私聊优先 uid
        out.append({
            "account_qq": int(account_qq), "kind": "c2c", "peer_id": uid, "peer_qq": qq,
            "name": str(f.get("nickname") or f.get("nick") or f.get("name") or ""),
            "remark": str(f.get("remark") or f.get("note") or ""),
            "avatar": str(f.get("avatar") or f.get("avatar_url") or ""),
            "source": SOURCE_ID,
        })
    for g in groups or []:
        if not isinstance(g, dict):
            continue
        gid = _to_int(g.get("group_id") or g.get("group"), 0)
        out.append({
            "account_qq": int(account_qq), "kind": "group", "peer_id": str(gid),
            "peer_qq": gid,
            "name": str(g.get("group_name") or g.get("name") or ""),
            "remark": "", "avatar": str(g.get("avatar") or ""), "source": SOURCE_ID,
        })
    return out


# 不同框架的历史消息 action / 参数名兼容（按顺序尝试，命中即返回）
_PRIVATE_HISTORY = [
    ("get_friend_msg_history", lambda qq, n: {"user_id": qq, "count": n, "message_seq": 0}),
    ("get_friend_msg_history", lambda qq, n: {"user_id": qq, "count": n}),
    ("get_friend_msg_history", lambda qq, n: {"user_id": qq, "count": n, "message_id": 0}),
    ("get_friends_msg_history", lambda qq, n: {"user_id": qq, "count": n}),
    ("get_chat_history", lambda qq, n: {"user_id": qq, "count": n}),
]
_GROUP_HISTORY = [
    ("get_group_msg_history", lambda gid, n: {"group_id": gid, "count": n, "message_seq": 0}),
    ("get_group_msg_history", lambda gid, n: {"group_id": gid, "count": n}),
    ("get_group_msg_history", lambda gid, n: {"group_id": gid, "count": n, "message_id": 0}),
    ("get_group_chat_history", lambda gid, n: {"group_id": gid, "count": n}),
]


def _history(base: str, token: str, kind: str, qq: int, count: int) -> list:
    """拉某个会话的历史消息（做 action/参数兼容）。"""
    plan = _PRIVATE_HISTORY if kind == "c2c" else _GROUP_HISTORY
    last: BotError | None = None
    for action, build in plan:
        try:
            return _unwrap_list(_api(base, action, build(qq, int(count)), token))
        except BotError as e:
            last = e
            if e.unsupported:      # 动作/参数不被支持时才换下一个兼容写法
                continue
            raise
    raise last or BotError(f"无法拉取{'群' if kind == 'group' else '好友'} {qq} 的历史消息")


def _upgrade_legacy_rows(rows: list[dict]) -> tuple[list[dict], int, int]:
    """把老版本 bot 行就地升级成新解析结果，避免同一消息重复入库。

    老版本行特征：source='bot' AND media IS NULL AND content IS NULL。
    按 (账号, kind, peer_id, ts, direction, 旧文本) 精确匹配唯一一行后：
      * 若目标新文本已存在（pack/bot 已有同一 ux_msg 键）→ 老行是重复，删除；
      * 否则 UPDATE，把 text/media/content 一起换成新值；
      * UPDATE 撞唯一索引 → 说明已有同行，删除老行。
    返回 (待插入行, 升级行数, 去重删除行数)。
    """
    if not rows:
        return rows, 0, 0
    to_insert: list[dict] = []
    migrated = deduped = 0
    key_sql = ("account_qq=? AND kind=? AND peer_id=? AND ts=? AND direction=? "
               "AND COALESCE(text,'')=COALESCE(?,'')")
    cons: dict = {}

    def _con(qq):
        if qq not in cons:
            cons[qq] = store.connect(qq)
        return cons[qq]

    try:
        for r in rows:
            try:
                con = _con(int(r.get("account_qq") or 0))
            except Exception:
                to_insert.append(r)
                continue
            legacy = r.get("_legacy_text")
            has_media = bool(r.get("media"))
            if legacy is None:
                to_insert.append(r)
                continue
            if legacy == r.get("text") and not has_media:
                to_insert.append(r)      # 文本没变也没媒体，无需升级
                continue
            try:
                cand = con.execute(
                    "SELECT id FROM messages WHERE account_qq=? AND kind=? AND peer_id=? "
                    "AND ts=? AND direction=? AND source='bot' AND media IS NULL "
                    "AND content IS NULL AND COALESCE(text,'')=COALESCE(?,'') LIMIT 2",
                    (r["account_qq"], r["kind"], r["peer_id"], r["ts"],
                     r["direction"], legacy)).fetchall()
            except Exception:
                cand = []
            if len(cand) != 1:
                to_insert.append(r)
                continue
            cid = cand[0]["id"]
            try:
                exists = con.execute(
                    "SELECT 1 FROM messages WHERE " + key_sql + " AND id<>? LIMIT 1",
                    (r["account_qq"], r["kind"], r["peer_id"], r["ts"],
                     r["direction"], r.get("text"), cid)).fetchone()
            except Exception:
                exists = None
            if exists:
                try:
                    con.execute("DELETE FROM messages WHERE id=?", (cid,))
                    deduped += 1
                    continue
                except Exception:
                    to_insert.append(r)
                    continue
            try:
                con.execute("UPDATE messages SET text=?, media=?, content=? WHERE id=?",
                            (r.get("text"), r.get("media"), r.get("content"), cid))
                migrated += 1
                continue
            except Exception:
                try:
                    con.execute("DELETE FROM messages WHERE id=?", (cid,))
                    deduped += 1
                except Exception:
                    to_insert.append(r)
                continue
        for _c in cons.values():
            _c.commit()
    except Exception:
        for _c in cons.values():
            try:
                _c.rollback()
            except Exception:
                pass
        return rows, 0, 0
    finally:
        for _c in cons.values():
            try:
                _c.close()
            except Exception:
                pass
    return to_insert, migrated, deduped



# ── 契约：status / probe / sync / conversations ────────────────────────────
def status() -> dict:
    """探测本机是否有 OneBot HTTP 服务在跑；不自动启动 NapCat。"""
    launcher = _launcher()
    launcher_exists = launcher.exists()
    reachable: list[str] = []
    for b in CANDIDATE_BASES:
        ok, _ = _tcp_check(b, timeout=STATUS_TCP_TIMEOUT)
        if ok:
            reachable.append(b)

    detail = {
        "candidates": CANDIDATE_BASES,
        "reachable": reachable,
        "default_base": DEFAULT_BASE,
        "napcat_launcher": str(launcher),
        "napcat_launcher_exists": launcher_exists,
        "httpx": bool(httpx),
        "actions": ["get_login_info", "get_friend_list", "get_group_list",
                    "get_friend_msg_history", "get_group_msg_history"],
        "hint": "NapCat 需用户扫码登录；本程序不会自动启动框架，只做探测与提示。",
    }

    if httpx is None:
        return {"id": SOURCE_ID, "name": SOURCE_NAME, "kind": SOURCE_KIND, "ready": False,
                "message": "缺少 httpx 依赖，无法访问 OneBot。请用项目自带的 .venv Python 运行后端。",
                "detail": detail}

    if reachable:
        msg = (f"检测到 OneBot HTTP 端口开放：{'、'.join(reachable)}。"
               f"如尚未登录，请先在 NapCat 扫码；随后可执行 probe / sync。")
        ready = True
    else:
        hint = (f"可一键启动：{launcher}（需扫码登录）" if launcher_exists
                else "未找到本地 NapCat 启动器（tools/napcat/启动框架.bat）")
        msg = (f"未检测到 OneBot HTTP 服务（已探测 {'、'.join(CANDIDATE_BASES)} 均无监听）。"
               f"请先启动 NapCat 并扫码登录；{hint}")
        ready = False

    return {"id": SOURCE_ID, "name": SOURCE_NAME, "kind": SOURCE_KIND,
            "ready": ready, "message": msg, "detail": detail}


def probe(opts: dict | None = None) -> dict:
    """只探测不写入：连通性 + 登录身份 + 好友/群列表。"""
    opts = dict(opts or {})
    base = _norm_base(opts.get("base"))
    token = str(opts.get("token") or "").strip()
    result = {"ok": False, "message": "", "accounts": [], "conversations": []}

    # 1) TCP 预检：比 sync 快得多，且能给出更准确的「拒绝/超时」原因
    ok_tcp, reason = _tcp_check(base, timeout=PROBE_TCP_TIMEOUT)
    if not ok_tcp:
        result["message"] = _tcp_error_msg(base, reason)
        result["detail"] = {"base": base, "stage": "tcp"}
        return result

    # 2) 登录身份
    try:
        uin, nick = _login(base, token, probe=True)
    except BotError as e:
        result["message"] = str(e)
        result["detail"] = {"base": base, "stage": "get_login_info"}
        return result

    accounts = [{"account_qq": uin, "label": nick, "source": SOURCE_ID}]

    # 3) 列表（失败不算整体失败，只要登录成功就算连通）
    friends: list = []
    groups: list = []
    warnings: list[str] = []
    try:
        friends = _fetch_list(base, token, "get_friend_list", probe=True)
    except BotError as e:
        warnings.append(f"好友列表获取失败：{e}")
    try:
        groups = _fetch_list(base, token, "get_group_list", probe=True)
    except BotError as e:
        warnings.append(f"群列表获取失败：{e}")

    convs = _to_conversations(uin, friends, groups)
    msg = f"连接成功：{nick}（QQ {uin}），好友 {len(friends)} 个，群 {len(groups)} 个"
    if warnings:
        msg += "；" + "；".join(warnings)
    result.update({
        "ok": True, "message": msg, "accounts": accounts, "conversations": convs,
        "detail": {"base": base, "account_qq": uin, "nickname": nick,
                   "friends": len(friends), "groups": len(groups), "warnings": warnings},
    })
    return result


def conversations(opts: dict | None = None) -> list[dict]:
    """列出可采集的会话。失败时抛 BotError（由调用方转成人话错误）。"""
    opts = dict(opts or {})
    base = _norm_base(opts.get("base"))
    token = str(opts.get("token") or "").strip()
    uin, _ = _login(base, token, probe=True)
    friends = _fetch_list(base, token, "get_friend_list", probe=True)
    groups = _fetch_list(base, token, "get_group_list", probe=True)
    return _to_conversations(uin, friends, groups)


def sync(opts: dict | None = None, progress=None) -> dict:
    """执行采集并写入主库。

    返回 {ok, message, imported, accounts, elapsed, log, ...}
    progress(stage, pct, msg) 为可选回调。
    """
    t0 = time.time()
    opts = dict(opts or {})
    base = _norm_base(opts.get("base"))
    token = str(opts.get("token") or "").strip()
    top_friends = max(0, _to_int(opts.get("top_friends"), 30))
    top_groups = max(0, _to_int(opts.get("top_groups"), 30))
    per_peer = _to_int(opts.get("per_peer"), 200)
    if per_peer <= 0:
        per_peer = 200

    log_lines: list[str] = []

    def emit(stage: str, pct: int, msg: str) -> None:
        log_lines.append(f"[{stage}] {msg}")
        if progress:
            try:
                progress(stage, int(pct), msg)
            except Exception:
                pass  # 进度回调坏掉不能影响同步

    def fail(msg: str) -> dict:
        return {"ok": False, "message": msg, "imported": 0, "accounts": [],
                "elapsed": round(time.time() - t0, 2), "log": "\n".join(log_lines)}

    emit("准备", 1, f"目标 OneBot：{base}")

    # 先做 TCP 预检：比等 httpx 超时快得多，错误也更好懂
    ok_tcp, reason = _tcp_check(base, timeout=PROBE_TCP_TIMEOUT)
    if not ok_tcp:
        return fail(_tcp_error_msg(base, reason))

    try:
        store.init()
    except Exception as e:
        return fail(f"初始化主库失败：{e}")

    try:
        uin, nick = _login(base, token, probe=False)
    except BotError as e:
        return fail(str(e))
    emit("账号", 5, f"已登录：{nick}（QQ {uin}）")

    friends: list = []
    groups: list = []
    errors: list[str] = []
    try:
        friends = _fetch_list(base, token, "get_friend_list")
    except BotError as e:
        errors.append(f"好友列表：{e}")
    try:
        groups = _fetch_list(base, token, "get_group_list")
    except BotError as e:
        errors.append(f"群列表：{e}")
    emit("联系人", 8, f"好友 {len(friends)} 个，群 {len(groups)} 个")

    try:
        store.upsert_account(uin, label=nick, source=SOURCE_ID)
        store.upsert_contacts(_to_conversations(uin, friends, groups))
    except Exception as e:
        return fail(f"写入联系人失败：{e}")

    sel_friends = friends[:top_friends]
    sel_groups = groups[:top_groups]
    total_jobs = len(sel_friends) + len(sel_groups)
    rows: list[dict] = []
    done = 0

    def _pct() -> int:
        return 10 + int(85 * done / max(total_jobs, 1))

    for f in sel_friends:
        qq = _to_int(f.get("user_id"), 0) if isinstance(f, dict) else 0
        peer_id = str((f.get("uid") if isinstance(f, dict) else None) or qq)
        try:
            for m in _history(base, token, "c2c", qq, per_peer):
                if not isinstance(m, dict):
                    continue
                row = ob_msg_to_row(m, uin, kind="c2c", peer_id=peer_id,
                                    peer_qq=qq, account_qq=uin)
                if row and row["ts"]:
                    rows.append(row)
        except BotError as e:
            errors.append(f"好友 {qq}：{e}")
        done += 1
        emit("拉取", _pct(), f"好友 {qq}（{done}/{total_jobs}）")

    for g in sel_groups:
        gid = _to_int(g.get("group_id"), 0) if isinstance(g, dict) else 0
        try:
            for m in _history(base, token, "group", gid, per_peer):
                if not isinstance(m, dict):
                    continue
                row = ob_msg_to_row(m, uin, kind="group", peer_id=str(gid),
                                    peer_qq=gid, account_qq=uin)
                if row and row["ts"]:
                    rows.append(row)
        except BotError as e:
            errors.append(f"群 {gid}：{e}")
        done += 1
        emit("拉取", _pct(), f"群 {gid}（{done}/{total_jobs}）")

    scanned = len(rows)
    media_rows = sum(1 for r in rows if r.get("media"))
    emit("入库", 96, f"解析到 {scanned} 条消息（含媒体 {media_rows} 条），写入主库（自动去重）…")
    try:
        rows, migrated, deduped = _upgrade_legacy_rows(rows)
        imported = store.insert_messages(rows)
        store.refresh_contact_stats(uin)
    except Exception as e:
        return fail(f"写入主库失败：{e}")
    emit("完成", 100,
         f"本次新增 {imported} 条（升级 {migrated} 条，去重删除 {deduped} 条）")

    msg = (f"同步完成：{nick}（QQ {uin}），扫描 {scanned} 条，新增 {imported} 条，"
           f"其中带媒体 {media_rows} 条")
    if migrated or deduped:
        msg += f"；老版本就地升级 {migrated} 条、去重删除 {deduped} 条"
    if errors:
        msg += f"；{len(errors)} 个会话/列表失败"
    return {
        "ok": True, "message": msg, "imported": imported, "accounts": [uin],
        "elapsed": round(time.time() - t0, 2), "log": "\n".join(log_lines),
        "nickname": nick, "scanned": scanned, "media_rows": media_rows,
        "migrated": migrated, "deduped": deduped,
        "friends": len(sel_friends), "groups": len(sel_groups),
        "errors": errors[:10],
    }