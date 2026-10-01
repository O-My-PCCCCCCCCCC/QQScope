# -*- coding: utf-8 -*-
'''QQScope · 头像 / 个人资料 / 联系人资料卡（SPEC 8.3）

由 server/app.py 的 _mount_optional_routers() 自动挂载：

    app.include_router(routes_profile.router)

约定
----
* 路径写相对路径（/api/...），挂载交给 Lead / app.py。
* 头像一律服务端抓取并缓存到 data/avatars/，命中缓存直接读盘；
  前端只用 /api/avatar?qq=... 作为图片地址，不直连腾讯 CDN。
* httpx 一律 trust_env=False：本机 Windows 系统代理会劫持外部请求。
* 抓取超时 8 秒、全局并发 8、同一目标加锁（避免缓存击穿 + 拖死接口）。
* 头像失败返回 404 JSON（前端首字兜底）；资料类接口永不 500，失败优雅降级。
* 绝不把 cookie / token / 密钥 / 解密密钥写进日志。
'''
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from core import paths, store
from core.sources import names as name_source

router = APIRouter()

# ── 常量 ────────────────────────────────────────────────────────────────────
AVATAR_DIR = paths.DATA / "avatars"
AVATAR_DIR.mkdir(parents=True, exist_ok=True)

USER_AVATAR_URL = "https://q1.qlogo.cn/g?b=qq&nk={qq}&s=640"
GROUP_AVATAR_URL = "https://p.qlogo.cn/gh/{gid}/{gid}/640"

AVATAR_TIMEOUT = 8.0            # 单次抓取上限（秒），满足超时不超过 10s
AVATAR_CONNECT_TIMEOUT = 4.0
AVATAR_MIN_BYTES = 1024         # 小于 1KB 视为 CDN 错误页/破图
AVATAR_MAX_BYTES = 8388608      # 8MB
AVATAR_MAX_CONCURRENCY = 8
AVATAR_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) QQScope/2.0"

_AVATAR_SEM = threading.BoundedSemaphore(AVATAR_MAX_CONCURRENCY)
_AVATAR_LOCKS: dict[str, threading.Lock] = {}
_AVATAR_LOCKS_GUARD = threading.Lock()

_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _to_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _clean(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (bytes, bytearray, memoryview)):
        try:
            v = bytes(v).decode("utf-8")
        except UnicodeDecodeError:
            v = bytes(v).decode("utf-8", "replace")
    return str(v).strip()


def _norm_int_keys(d) -> dict:
    out: dict = {}
    for k, v in (d or {}).items():
        try:
            out[int(k)] = v
        except (TypeError, ValueError):
            continue
    return out


def _norm_str_keys(d) -> dict:
    return {str(k): v for k, v in (d or {}).items()}

# ── 头像抓取 / 缓存 ─────────────────────────────────────────────────────────
def _media_type(data: bytes) -> str | None:
    '''按魔数判断图片类型；不是已知图片返回 None。'''
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _read_cached(path: Path) -> bytes | None:
    try:
        if not path.exists():
            return None
        size = path.stat().st_size
        if size < AVATAR_MIN_BYTES or size > AVATAR_MAX_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    return data if _media_type(data) else None


def _write_cached(path: Path, data: bytes) -> None:
    '''先写临时文件再原子替换，避免半截文件被读到。'''
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _download(url: str) -> bytes | None:
    '''抓取图片。失败 / 超时 / 非图片一律返回 None（绝不抛异常）。'''
    try:
        with _AVATAR_SEM:
            with httpx.Client(
                    trust_env=False,
                    timeout=httpx.Timeout(AVATAR_TIMEOUT,
                                          connect=AVATAR_CONNECT_TIMEOUT),
                    follow_redirects=True,
                    headers={"User-Agent": AVATAR_UA}) as client:
                resp = client.get(url)
    except Exception:  # noqa: BLE001 网络/代理/证书问题都快速失败
        return None
    if resp.status_code != 200:
        return None
    data = resp.content or b""
    if not (AVATAR_MIN_BYTES <= len(data) <= AVATAR_MAX_BYTES):
        return None
    return data if _media_type(data) else None


def _lock_for(key: str) -> threading.Lock:
    with _AVATAR_LOCKS_GUARD:
        lk = _AVATAR_LOCKS.get(key)
        if lk is None:
            lk = threading.Lock()
            _AVATAR_LOCKS[key] = lk
        return lk


def _serve_avatar(cache_path: Path, url: str) -> Response:
    data = _read_cached(cache_path)
    origin = "cache"
    if data is None:
        with _lock_for(str(cache_path)):
            data = _read_cached(cache_path)      # 双检：别人可能刚好下完
            if data is None:
                fetched = _download(url)
                if fetched is not None:
                    _write_cached(cache_path, fetched)
                    data = fetched
                    origin = "fetched"
    if data is None:
        return JSONResponse(
            status_code=404,
            content={"ok": False,
                     "detail": "头像获取失败（网络不可达 / CDN 无此头像），请使用首字兜底头像"})
    return Response(
        content=data,
        media_type=_media_type(data) or "image/png",
        headers={"Cache-Control": "public, max-age=86400",
                 "X-Avatar-Source": origin})


@router.get("/api/avatar")
def avatar(qq: int = 0):
    '''QQ 头像图片流。缓存 data/accounts/<owner>/avatars/<qq>.png（无法判定归属则全局 data/avatars）。'''
    if qq <= 0:
        return JSONResponse(status_code=404,
                            content={"ok": False, "detail": "缺少合法的 qq"})
    try:
        from core import store as _store
        owner = _store.account_for_uin(qq)
    except Exception:  # noqa: BLE001
        owner = None
    d = paths.account_avatars(owner) if owner else AVATAR_DIR
    return _serve_avatar(d / f"{qq}.png", USER_AVATAR_URL.format(qq=qq))


@router.get("/api/avatar/group")
def avatar_group(group: int = 0):
    '''群头像图片流。缓存 data/accounts/<owner>/avatars/group_<gid>.png（无法判定则全局）。'''
    if group <= 0:
        return JSONResponse(status_code=404,
                            content={"ok": False, "detail": "缺少合法的 group"})
    try:
        from core import store as _store
        owner = _store.account_for_uin(group)
    except Exception:  # noqa: BLE001
        owner = None
    d = paths.account_avatars(owner) if owner else AVATAR_DIR
    return _serve_avatar(d / f"group_{group}.png", GROUP_AVATAR_URL.format(gid=group))

# ── 昵称 / 资料包缓存（解密较贵，进程内缓存 5 分钟） ────────────────────────
_BUNDLE_TTL = 300.0
_BUNDLE_CACHE: dict[int, tuple[float, dict]] = {}
_BUNDLE_CACHE_LOCK = threading.Lock()
_BUNDLE_LOCKS: dict[int, threading.Lock] = {}


def _bundle(account_qq: int) -> dict:
    '''取昵称库 + 本人资料整包；同一账号串行解密，失败也返回可用结构。'''
    now = time.monotonic()
    with _BUNDLE_CACHE_LOCK:
        hit = _BUNDLE_CACHE.get(account_qq)
        if hit and now - hit[0] < _BUNDLE_TTL:
            return hit[1]
        lk = _BUNDLE_LOCKS.get(account_qq)
        if lk is None:
            lk = threading.Lock()
            _BUNDLE_LOCKS[account_qq] = lk
    with lk:
        now = time.monotonic()
        with _BUNDLE_CACHE_LOCK:
            hit = _BUNDLE_CACHE.get(account_qq)
            if hit and now - hit[0] < _BUNDLE_TTL:
                return hit[1]
        try:
            b = name_source.fetch_profile_bundle(account_qq)
        except Exception as exc:  # noqa: BLE001
            b = {"ok": False, "message": f"昵称/资料读取异常：{exc}",
                 "self": None, "friends": {}, "groups": {}, "people": {},
                 "friend_count": 0, "group_count": 0, "stats": {}}
        with _BUNDLE_CACHE_LOCK:
            _BUNDLE_CACHE[account_qq] = (now, b)
        return b


# ── 会话统计 ────────────────────────────────────────────────────────────────
def _session_stats(account: int, kind: str, peer_id: str) -> dict:
    '''直接按 messages 重算 msg_count / self_count / first_ts / last_ts。'''
    con = None
    try:
        con = store.connect(account)
        row = con.execute(
            "SELECT COUNT(*) n, "
            "COALESCE(SUM(CASE WHEN direction=1 THEN 1 ELSE 0 END),0) s, "
            "MIN(ts) f, MAX(ts) l FROM messages "
            "WHERE account_qq=? AND kind=? AND peer_id=?",
            (int(account), kind, str(peer_id))).fetchone()
    except Exception:  # noqa: BLE001
        return {"msg_count": None, "self_count": None,
                "first_ts": None, "last_ts": None}
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass
    if row is None:
        return {"msg_count": 0, "self_count": 0, "first_ts": None, "last_ts": None}
    return {"msg_count": _to_int(row["n"]), "self_count": _to_int(row["s"]),
            "first_ts": row["f"], "last_ts": row["l"]}


def _hourly(account: int, kind: str, peer_id: str) -> list[int]:
    '''该会话 24 小时消息分布（本地时区，与 store.hour_hist 一致）。'''
    con = None
    try:
        con = store.connect(account)
        rows = con.execute(
            "SELECT CAST(strftime('%H',ts,'unixepoch','localtime') AS INT) h, "
            "COUNT(*) n FROM messages WHERE account_qq=? AND kind=? AND peer_id=? "
            "GROUP BY h", (int(account), kind, str(peer_id))).fetchall()
    except Exception:  # noqa: BLE001
        return [0] * 24
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass
    got: dict[int, int] = {}
    for r in rows:
        try:
            got[_to_int(r["h"])] = _to_int(r["n"])
        except Exception:  # noqa: BLE001
            continue
    return [got.get(h, 0) for h in range(24)]

_TOP_SCAN = 20000                       # 参与词频统计的最近消息条数上限
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{1,23}")
_CJK_RE = re.compile(r"[\u3400-\u9fff]+")
_NOISE_RE = re.compile(r"https?://\S+|www\.\S+|\[[^\]]{0,24}\]|@\S+")
_STOP = frozenset("""
我们 你们 他们 她们 它们 什么 怎么 这个 那个 哪个 不是 就是 没有 可以 现在 知道 觉得
真的 时候 因为 所以 但是 还是 已经 自己 如果 然后 这样 这里 那里 这么 那么 一下 一起
出来 起来 东西 事情 问题 今天 明天 昨天 感觉 应该 不要 不能 可能 有点 其实 突然 果然
最近 以后 之前 的话 一样 好像 而且 或者 只是 都是 也是 还有 不过 一定 肯定 估计 哈哈
呵呵 嘿嘿 嘻嘻 谢谢 抱歉 不好 好吧 好的 嗯嗯 啊啊 哦哦 由于 虽然 即使 无论 并且 以及
要是 假如 于是 接着 最后 开始 一直 总是 经常 有时 比较 非常 特别 十分 确实 因此 可是
然而 另外 此外 例如 比如 甚至 毕竟 反正 究竟 到底 怎么样 多多少少 一些 一个 一点 有点
有些 没事 什么 怎么 那么 多少 干嘛 各位 大家 感觉 之前 之后 现在 时候 一个 我们 你们
the and for you are not with this that have from your but they will can all any was were
""".split())
_STOP = frozenset(w for w in _STOP if w)


def _top_words(account: int, kind: str, peer_id: str, limit: int = 12) -> list[dict]:
    '''该会话高频词：中文 2-gram + 英文/数字词，过滤常见虚词，取 Top N。'''
    con = None
    try:
        con = store.connect(account)
        rows = con.execute(
            "SELECT text FROM messages WHERE account_qq=? AND kind=? AND peer_id=? "
            "AND text IS NOT NULL AND text<>'' ORDER BY ts DESC LIMIT ?",
            (int(account), kind, str(peer_id), _TOP_SCAN)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass

    counter: dict[str, int] = {}
    for r in rows:
        text = _clean(r["text"])
        if not text:
            continue
        text = _NOISE_RE.sub(" ", text)
        for seg in _CJK_RE.findall(text):
            for i in range(len(seg) - 1):
                w = seg[i:i + 2]
                if w in _STOP:
                    continue
                counter[w] = counter.get(w, 0) + 1
        for w in _WORD_RE.findall(text.lower()):
            if w in _STOP or w.isdigit():
                continue
            counter[w] = counter.get(w, 0) + 1

    top = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return [{"word": w, "count": n} for w, n in top]

# ── 联系人信息解析 ──────────────────────────────────────────────────────────
def _peer_info(bundle: dict, kind: str, peer_id: str, peer_qq: int) -> dict:
    '''从昵称库里尽力反查对方的 uid / QQ / 昵称 / 备注，拿不到就留空。'''
    info = {"name": None, "remark": None, "uid": None, "qq": _to_int(peer_qq)}

    if kind == "group":
        gid = info["qq"]
        if gid <= 0 and str(peer_id).lstrip("-").isdigit():
            gid = _to_int(peer_id)
        info["qq"] = gid
        rec = (_norm_int_keys(bundle.get("groups")).get(gid) or {})
        info["name"] = _clean(rec.get("name")) or None
        return info

    pid = str(peer_id or "")
    people = _norm_str_keys(bundle.get("people"))
    friends = _norm_int_keys(bundle.get("friends"))
    rec = people.get(pid) or {}
    if not rec and info["qq"] > 0:
        rec = friends.get(info["qq"]) or {}
        if not rec:
            for r in people.values():
                if _to_int(r.get("qq")) == info["qq"]:
                    rec = r
                    break
    if not rec and pid.lstrip("-").isdigit():
        rec = friends.get(_to_int(pid)) or {}
    if rec:
        info["name"] = _clean(rec.get("name")) or info["name"]
        info["remark"] = _clean(rec.get("remark")) or None
        if not info["qq"]:
            info["qq"] = _to_int(rec.get("qq"))
        if rec.get("uid"):
            info["uid"] = str(rec["uid"])
    if not info["uid"] and pid.startswith("u_"):
        info["uid"] = pid
    return info

# ── 接口：自己的资料 ────────────────────────────────────────────────────────
@router.get("/api/profile")
def profile(account: int = 0):
    '''本账号资料。任何失败都优雅降级（昵称变为 账号 QQ），绝不 500。'''
    if account <= 0:
        return JSONResponse(status_code=400,
                            content={"ok": False, "detail": "缺少 account"})

    bundle = _bundle(account)
    selfp = bundle.get("self") or {}
    nickname = _clean(selfp.get("nickname")) or f"账号 {account}"
    signature = _clean(selfp.get("signature"))

    try:
        ov = store.overview(account) or {}
    except Exception:  # noqa: BLE001
        ov = {}
    try:
        active_days = len(store.daily_stats(account))
    except Exception:  # noqa: BLE001
        active_days = 0

    friend_count = _to_int(bundle.get("friend_count"))
    group_count = _to_int(bundle.get("group_count"))
    if not friend_count:
        friend_count = len(store.list_contacts(account, kind="c2c", limit=200000))
    if not group_count:
        group_count = len(store.list_contacts(account, kind="group", limit=200000))

    return {
        "account_qq": account,
        "nickname": nickname,
        "signature": signature,
        "avatar_url": USER_AVATAR_URL.format(qq=account),
        "qid": None,                       # profile_info.db 里没有 QID 列，如实置空
        "uid": _clean(selfp.get("uid")) or None,
        "friend_count": friend_count,
        "group_count": group_count,
        "msg_total": _to_int(ov.get("total")),
        "active_days": active_days,
        "first_ts": ov.get("first_ts"),
        "last_ts": ov.get("last_ts"),
        "profile_ok": bool(bundle.get("ok")),
    }

# ── 接口：联系人资料卡 ──────────────────────────────────────────────────────
@router.get("/api/contact")
def contact(account: int = 0, kind: str = "", peer_id: str = ""):
    '''联系人资料卡：主数据 store.get_contact + messages 统计。'''
    if account <= 0 or kind not in ("c2c", "group") or not str(peer_id).strip():
        return JSONResponse(
            status_code=400,
            content={"ok": False,
                     "detail": "缺少 account / kind / peer_id（kind 只能是 c2c 或 group）"})
    pid = str(peer_id).strip()

    try:
        c = store.get_contact(account, kind, pid)
    except Exception:  # noqa: BLE001
        c = None
    if not c:
        return JSONResponse(status_code=404,
                            content={"ok": False, "detail": "找不到该会话（account/kind/peer_id 不匹配）"})

    bundle = _bundle(account)
    peer = _peer_info(bundle, kind, pid, _to_int(c.get("peer_qq")))

    stats = _session_stats(account, kind, pid)
    if stats.get("msg_count"):
        msg_count = _to_int(stats["msg_count"])
        self_count = _to_int(stats["self_count"])
        first_ts = stats["first_ts"]
        last_ts = stats["last_ts"]
    else:                                   # messages 为空时退回 contacts 行内统计
        msg_count = _to_int(c.get("msg_count"))
        self_count = _to_int(c.get("self_count"))
        first_ts = c.get("first_ts")
        last_ts = c.get("last_ts")

    last_text = _clean(c.get("last_text")) or None
    if not last_text:
        try:
            msgs = store.list_messages(account, kind, pid, limit=1, order="DESC")
            if msgs:
                last_text = _clean(msgs[0].get("text")) or None
        except Exception:  # noqa: BLE001
            last_text = None

    name = _clean(c.get("name")) or peer.get("name") or ""
    qq = _to_int(c.get("peer_qq")) or _to_int(peer.get("qq"))
    if not name:
        if kind == "group":
            name = f"群 {qq or pid}"
        elif qq:
            name = f"QQ {qq}"
        else:
            name = pid
    remark = _clean(c.get("remark")) or peer.get("remark") or None
    uid = peer.get("uid") or (pid if pid.startswith("u_") else None)

    if kind == "group":
        gid = qq or (_to_int(pid) if pid.lstrip("-").isdigit() else 0)
        qq = gid
        avatar_url = GROUP_AVATAR_URL.format(gid=gid) if gid > 0 else None
    else:
        avatar_url = USER_AVATAR_URL.format(qq=qq) if qq > 0 else None

    return {
        "name": name,
        "remark": remark,
        "peer_qq": qq or None,
        "kind": kind,
        "uid": uid,
        "avatar_url": avatar_url,
        "msg_count": msg_count,
        "self_count": self_count,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "last_text": last_text,
        "hourly": _hourly(account, kind, pid),
        "top_words": _top_words(account, kind, pid),
    }