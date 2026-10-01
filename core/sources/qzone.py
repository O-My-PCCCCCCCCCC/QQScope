"""QQScope · QQ 动态（QZone）数据源。

本机 nt_db 里没有任何动态 / QQ 空间数据，所以动态只能走网络。QZone H5 接口
（emotion_cgi_msglist_v6）需要登录态 cookie（至少 uin + p_skey / skey）。

cookie 来源优先级：
  1) opts["cookie"] / opts["p_skey"]                    （显式传入，如探测脚本 / 接口）
  2) data/server/settings.json 的 qzone 配置            （用户手工填写）
  3) OneBot 的 get_cookies / get_credentials            （需要 NapCat 已扫码登录）

拿不到登录态时，所有出口一律返回明确中文错误，绝不编造数据。

安全约定：cookie / p_skey / skey 永不写日志、永不进接口响应、永不进证据文件，
一律脱敏成 p_skey=***。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from core import paths, store

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None


SOURCE_ID = "qzone"
SOURCE_NAME = "QQ 动态（QZone）"
SOURCE_KIND = "qzone"

SETTINGS_FILE = paths.DATA / "server" / "settings.json"
PROBE_DIR = paths.DATA / "qzone_probe"

API_PATH = "/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
ENDPOINTS = [
    ("user.qzone.qq.com", "https://user.qzone.qq.com" + API_PATH),
    ("h5.qzone.qq.com", "https://h5.qzone.qq.com" + API_PATH),
]
DEFAULT_NUM = 20
MAX_NUM = 50
TIMEOUT = 15.0          # 硬要求：单请求 <= 15s
PREVIEW_MAX = 2048      # 证据里保留的响应前 2KB
MIN_INTERVAL = 1.0      # 礼貌限速：相邻 QZone 请求至少间隔 1s

_last_request_ts = 0.0


def _throttle() -> None:
    """相邻 QZone 请求至少间隔 MIN_INTERVAL 秒（限速，礼貌抓取）。"""
    global _last_request_ts
    now = time.time()
    wait = MIN_INTERVAL - (now - _last_request_ts)
    if wait > 0:
        time.sleep(wait)
    _last_request_ts = time.time()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

NEED_LOGIN_MSG = ("需要先连接机器人框架获取登录态：请先启动 NapCat/OneBot 并扫码登录，"
                  "或在 data/server/settings.json 的 qzone.cookie 里手工填写 QZone Cookie")


class QzoneError(Exception):
    """带人话中文说明的 QZone 访问错误。"""

    def __init__(self, message: str, need_login: bool = False, transport: bool = False,
                 reason: str = "error"):
        super().__init__(message)
        self.need_login = need_login
        self.transport = transport
        self.reason = reason

# ── 配置 / 登录态 ───────────────────────────────────────────────────────────
def load_settings() -> dict:
    try:
        if SETTINGS_FILE.exists():
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def get_gtk(p_skey: str) -> int:
    """QZone g_tk：由 p_skey 计算。"""
    h = 5381
    for ch in str(p_skey or ""):
        h += (h << 5) + ord(ch)
    return h & 0x7FFFFFFF


def _pairs_from(obj) -> list[tuple[str, str]]:
    """把各种 cookie 形态统一成 (name, value) 列表。"""
    out: list[tuple[str, str]] = []
    if obj is None:
        return out
    if isinstance(obj, str):
        for part in obj.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            k, v = part.split("=", 1)
            out.append((k.strip(), v.strip()))
        return out
    if isinstance(obj, dict):
        for kk in ("cookies", "data", "cookie"):
            if kk in obj:
                got = _pairs_from(obj[kk])
                if got:
                    return got
        for k, v in obj.items():
            if isinstance(v, (str, int)):
                out.append((str(k), str(v)))
        return out
    if isinstance(obj, (list, tuple)):
        for it in obj:
            out.extend(_pairs_from(it))
    return out


def parse_cookie(raw) -> dict:
    """解析 cookie -> {cookie, uin, p_skey, skey, names}。不做任何落盘。"""
    jar: dict[str, str] = {}
    for k, v in _pairs_from(raw):
        if k and k not in jar:
            jar[k] = v

    def pick(*names: str) -> str:
        for n in names:
            for k, v in jar.items():
                if k.lower() == n:
                    return v
        return ""

    uin_raw = (pick("uin", "p_uin", "ptui_loginuin") or "").strip()
    uin_digits = re.sub(r"^o", "", uin_raw)
    p_skey = pick("p_skey", "p_skey_t") or pick("skey")
    skey = pick("skey")
    cookie = "; ".join(f"{k}={v}" for k, v in jar.items())
    return {
        "cookie": cookie,
        "uin": int(uin_digits) if uin_digits.isdigit() else 0,
        "p_skey": p_skey,
        "skey": skey,
        "names": sorted(jar.keys()),
    }


def cookie_secrets(cred: dict | None) -> list[str]:
    """取出 cookie 里所有值，供脱敏用（不返回名字，避免误伤）。"""
    cred = cred or {}
    vals = [cred.get("p_skey"), cred.get("skey")]
    for part in str(cred.get("cookie") or "").split(";"):
        if "=" in part:
            vals.append(part.split("=", 1)[1].strip())
    return [v for v in vals if v and len(v) >= 4]


_RE_KEYVAL = re.compile(r"(?i)(p_skey|skey|p_uin|ptui_loginuin)\s*=\s*[^;&\s]+")
_RE_GTK = re.compile(r"(?i)(g_tk|gtk)=[^&\s]+")
_RE_COOKIE_HDR = re.compile(r"(?i)(cookie\s*[:=]\s*)[^\r\n]+")


def redact(text, secrets=None) -> str:
    s = "" if text is None else str(text)
    for sec in secrets or []:
        if sec:
            s = s.replace(str(sec), "***")
    s = _RE_KEYVAL.sub(lambda m: m.group(1) + "=***", s)
    s = _RE_GTK.sub(lambda m: m.group(1) + "=***", s)
    s = _RE_COOKIE_HDR.sub(lambda m: m.group(1) + "***", s)
    return s


def redact_url(url) -> str:
    return _RE_GTK.sub(lambda m: m.group(1) + "=***", str(url))

def _cookie_from_parts(uin: int, p_skey: str, skey: str = "") -> str:
    parts = [f"uin=o{int(uin or 0)}"]
    if p_skey:
        parts.append(f"p_skey={p_skey}")
    if skey:
        parts.append(f"skey={skey}")
    return "; ".join(parts)


def _usable(cred: dict | None) -> bool:
    return bool(cred and (cred.get("p_skey") or cred.get("skey")))


def _default_uin() -> int:
    """没有显式 uin 时，退回本地主库里最大的那个账号（事实数据）。"""
    try:
        accs = store.list_accounts()
        if accs:
            return int(accs[0].get("account_qq") or 0)
    except Exception:
        pass
    return 0


def _napcat_tokens() -> list[str]:
    """只读发现 NapCat 的 OneBot HTTP token（onebot11*.json）；绝不外泄 / 不落盘。"""
    out: list[str] = []
    for root in (paths.NAPCAT_DIR, paths.ROOT / "tools" / "napcat"):
        try:
            base = Path(root)
            if not base.is_dir():
                continue
            for cfg in base.rglob("onebot11*.json"):
                if "node_modules" in cfg.parts:
                    continue
                try:
                    j = json.loads(cfg.read_text(encoding="utf-8"))
                except Exception:
                    continue
                for srv in (((j.get("network") or {}).get("httpServers")) or []):
                    tok = str(srv.get("token") or "").strip()
                    if tok:
                        out.append(tok)
                ht = str((j.get("http") or {}).get("token") or "").strip()
                if ht:
                    out.append(ht)
        except Exception:
            continue
    seen: set[str] = set()
    res: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            res.append(t)
    return res


def _onebot_credential(opts: dict, settings: dict) -> tuple[dict | None, dict]:
    """尝试通过 OneBot get_cookies / get_credentials 取登录态。

    返回 (凭证 | None, 尝试信息)。尝试信息里绝不含任何 token / cookie 值。
    token 来源：opts / settings.onebot.token；都没有时只读发现 NapCat 配置。
    """
    ob = dict(settings.get("onebot") or {})
    base = str(opts.get("base") or ob.get("base") or "http://127.0.0.1:3000")
    token = str(opts.get("token") or ob.get("token") or "").strip()
    token_source = "opts/settings" if token else ""
    if not token:
        for cand in _napcat_tokens():
            token, token_source = cand, "napcat-config"
            break
    info = {"tried": True, "ok": False, "base": base,
            "token_source": token_source or "none", "message": ""}
    try:
        from core.sources import bot_source
    except Exception as exc:  # noqa: BLE001
        info["message"] = f"OneBot 模块不可用：{exc.__class__.__name__}"
        return None, info
    if getattr(bot_source, "httpx", None) is None:
        info["message"] = "缺少 httpx 依赖，无法调用 OneBot"
        return None, info
    errors: list[str] = []
    for action in ("get_cookies", "get_credentials"):
        try:
            data = bot_source._api(base, action, {"domain": "qzone.qq.com"}, token, probe=True)
            parsed = parse_cookie(data)
            if parsed.get("p_skey") or parsed.get("skey") or parsed.get("uin"):
                parsed["source"] = f"onebot:{action}"
                info.update({"ok": True, "action": action,
                             "message": f"已通过 OneBot {action} 获取登录态"})
                return parsed, info
            errors.append(f"{action} 返回里没有可用 cookie")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{action}: {exc}")
    info["message"] = "；".join(errors[:2]) or "OneBot 未返回登录态"
    return None, info


def resolve_credential(opts: dict | None = None, strict: bool = True) -> tuple[dict | None, dict]:
    """按优先级解析登录态，返回 (凭证 | None, 元信息)。

    strict=True 且拿不到可用登录态时抛 QzoneError(need_login=True)。
    元信息永远不含 cookie 值，可安全写日志 / 返回前端。
    """
    opts = dict(opts or {})
    settings = load_settings()
    qz = dict(settings.get("qzone") or {})
    cred: dict | None = None
    source = ""

    # 1) 显式传入
    if opts.get("cookie"):
        cred = parse_cookie(opts["cookie"])
        source = "opts.cookie"
    elif opts.get("p_skey"):
        uin = int(opts.get("uin") or qz.get("uin") or _default_uin() or 0)
        cred = parse_cookie(_cookie_from_parts(uin, opts.get("p_skey"), opts.get("skey")))
        source = "opts.p_skey"

    # 2) 后端设置里手工填写
    if not _usable(cred):
        raw = qz.get("cookie") or qz.get("cookies")
        if raw:
            c2 = parse_cookie(raw)
            if _usable(c2) or c2.get("uin"):
                cred, source = c2, "settings.qzone.cookie"
    if not _usable(cred) and qz.get("p_skey"):
        uin = int(qz.get("uin") or opts.get("uin") or _default_uin() or 0)
        c3 = parse_cookie(_cookie_from_parts(uin, qz.get("p_skey"), qz.get("skey")))
        if _usable(c3):
            cred, source = c3, "settings.qzone.p_skey"

    # 3) OneBot get_cookies / get_credentials
    onebot_info = {"tried": False, "ok": False, "message": ""}
    if not _usable(cred):
        parsed, onebot_info = _onebot_credential(opts, settings)
        if parsed:
            cred, source = parsed, parsed.get("source") or "onebot"

    uin = int((cred or {}).get("uin") or opts.get("uin") or qz.get("uin")
              or _default_uin() or 0)
    if cred is not None and not cred.get("uin"):
        cred["uin"] = uin
    meta = {
        "source": source or "none",
        "uin": uin,
        "has_p_skey": _usable(cred),
        "onebot": onebot_info,
    }
    if strict and not _usable(cred):
        raise QzoneError(NEED_LOGIN_MSG, need_login=True, reason="need_login")
    return cred, meta

# ── HTTP ────────────────────────────────────────────────────────────────────
def build_url(uin: int, pos: int = 0, num: int = DEFAULT_NUM, g_tk: int = 0,
              endpoint: str | None = None) -> str:
    ep = endpoint or ENDPOINTS[0][1]
    from urllib.parse import urlencode
    qs = urlencode({
        "uin": str(int(uin or 0)), "ftype": 0, "sort": 0, "pos": int(pos),
        "num": int(num), "replynum": 100, "g_tk": int(g_tk),
        "callback": "_Callback", "code_version": 1, "format": "json",
        "need_private_comment": 1,
    })
    return f"{ep}?{qs}"


def _clamp_num(v) -> int:
    try:
        n = int(v)
    except Exception:
        n = DEFAULT_NUM
    return max(1, min(n, MAX_NUM))


def _strip_jsonp(text: str):
    s = (text or "").strip()
    m = re.match(r"^[A-Za-z_$][\w$.]*\((.*)\);?\s*$", s, re.S)
    if m:
        s = m.group(1)
    try:
        return json.loads(s)
    except Exception:
        return None


def _http_get(url: str, cookie: str = "", uin: int = 0, timeout: float = TIMEOUT):
    if httpx is None:
        raise QzoneError("缺少 httpx 依赖：请用项目自带 .venv Python 运行后端",
                         transport=True, reason="network")
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": f"https://user.qzone.qq.com/{int(uin or 0)}",
    }
    if cookie:
        headers["Cookie"] = cookie
    _throttle()
    try:
        return httpx.get(url, headers=headers, timeout=timeout, trust_env=False,
                         follow_redirects=False)
    except httpx.TimeoutException:
        raise QzoneError(f"请求 QZone 超时（>{int(timeout)}s）：网络不通或被拦截",
                         transport=True, reason="network")
    except httpx.HTTPError as exc:
        raise QzoneError(f"请求 QZone 失败：{exc.__class__.__name__}",
                         transport=True, reason="network")


def _first_int(it: dict, keys, default: int = 0) -> int:
    for k in keys:
        v = it.get(k)
        if v not in (None, ""):
            try:
                return int(v)
            except Exception:
                pass
    return default


def _collect_images(it: dict) -> list[str]:
    urls: list[str] = []

    def add(v) -> None:
        if isinstance(v, str):
            if v.startswith("http"):
                urls.append(v)
        elif isinstance(v, dict):
            for kk in ("url1", "url2", "url3", "origin_url", "hd_url", "url"):
                if isinstance(v.get(kk), str) and v[kk].startswith("http"):
                    urls.append(v[kk])
                    break
        elif isinstance(v, (list, tuple)):
            for x in v:
                add(x)

    for key in ("pic", "pics", "photolist", "photo", "rt_pic"):
        add(it.get(key))
    out: list[str] = []
    for u in urls:
        if u not in out:
            out.append(u)
    return out


def feed_from_item(it, account_qq: int, default_author: int = 0) -> dict | None:
    """把 QZone msglist 单条消息转成 feeds 表行；无 id 则丢弃。

    account_qq = 采集账号（我）；author_qq = 实际发布者（看好友空间时是好友）。
    """
    if not isinstance(it, dict):
        return None
    fid = it.get("tid") or it.get("id") or it.get("cellid") or it.get("fid")
    if fid in (None, ""):
        return None
    content = it.get("content") or it.get("rt_con") or it.get("summary") or ""
    if isinstance(content, (dict, list)):
        content = json.dumps(content, ensure_ascii=False)
    author = _first_int(it, ("uin", "author_uin", "qq"), 0) or int(default_author or 0)
    return {
        "id": str(fid),
        "account_qq": int(account_qq or 0),
        "ts": _first_int(it, ("created_time", "abstime", "time", "create_time")),
        "author_qq": author,
        "author_name": str(it.get("name") or it.get("nickname") or ""),
        "content": str(content),
        "images": json.dumps(_collect_images(it), ensure_ascii=False),
        # 注：QZone 列表/详情接口都不返回「点赞数」，只能给 0；
        # comment(cmtnum) / forward(fwdnum) 是接口真实值。
        "praise": _first_int(it, ("likecnt", "praise", "praise_cnt", "like_count")),
        "comments": _first_int(it, ("cmtnum", "comment_count", "cmt_cnt")),
        "forwards": _first_int(it, ("fwdnum", "forward_count", "fwd_cnt")),
        "raw": json.dumps(it, ensure_ascii=False)[:20000],
    }


def fetch_feeds(cred: dict, pos: int = 0, num: int = DEFAULT_NUM,
                endpoint: str | None = None,
                owner: int | None = None) -> tuple[list[dict], int | None]:
    """拉一页动态。owner 指定看谁的空间（默认自己）；失败抛 QzoneError。"""
    me = int(cred.get("uin") or 0)
    if not me:
        raise QzoneError("登录态缺少 uin，无法请求 QZone", need_login=True, reason="need_login")
    owner_uin = int(owner or me)
    url = build_url(owner_uin, pos=pos, num=num,
                    g_tk=get_gtk(cred.get("p_skey") or cred.get("skey")), endpoint=endpoint)
    r = _http_get(url, cred.get("cookie") or "", owner_uin)
    text = r.text or ""
    j = _strip_jsonp(text)
    if j is None:
        if "登录" in text or "login" in text.lower():
            raise QzoneError(NEED_LOGIN_MSG, need_login=True, reason="expired")
        if r.status_code in (401, 403):
            raise QzoneError(f"QZone 拒绝访问（HTTP {r.status_code}），可能被风控或需重新登录",
                             reason="blocked")
        raise QzoneError(f"QZone 返回非 JSON（HTTP {r.status_code}），接口可能改版", reason="error")
    code = j.get("code")
    msg = str(j.get("message") or j.get("msg") or "")
    if code == -3000 or "登录" in msg:
        raise QzoneError(f"QZone 登录态已失效：{msg or '请先登录空间'}",
                         need_login=True, reason="expired")
    if code != 0:
        raise QzoneError(f"QZone 接口返回异常：code={code} {msg}", reason="error")
    items = j.get("msglist") or []
    rows = [row for row in (feed_from_item(it, me, default_author=owner_uin) for it in items) if row]
    total = _first_int(j, ("total",), 0)
    next_pos: int | None = None
    if items:
        nxt = int(pos) + len(items)
        if (total and nxt < total) or (not total and len(items) >= num):
            next_pos = nxt
    return rows, next_pos


def detail_url(owner_uin: int, tid: str, g_tk: int, endpoint: str | None = None) -> str:
    from urllib.parse import urlencode
    ep = (endpoint or ENDPOINTS[0][1]).replace("emotion_cgi_msglist_v6",
                                               "emotion_cgi_msgdetail_v6")
    return ep + "?" + urlencode({
        "uin": int(owner_uin or 0), "tid": str(tid), "format": "json",
        "g_tk": int(g_tk), "code_version": 1,
        "need_private_comment": 1, "replynum": 100,
    })


def parse_comments(j: dict) -> list[dict]:
    """从详情响应里取 commentlist -> [{nickname, uin, content, ts}]。"""
    cl = j.get("commentlist") or j.get("comments") or []
    out: list[dict] = []
    if isinstance(cl, list):
        for c in cl:
            if not isinstance(c, dict):
                continue
            out.append({
                "nickname": str(c.get("name") or c.get("nickname") or ""),
                "uin": _first_int(c, ("uin",), 0),
                "content": str(c.get("content") or ""),
                "ts": _first_int(c, ("create_time", "created_time", "time"), 0),
            })
    return out


def fetch_detail(cred: dict, owner_uin: int, tid: str) -> dict:
    """拉单条动态详情（含评论列表）。失败抛 QzoneError（人话中文）。"""
    me = int(cred.get("uin") or 0)
    if not me or not tid:
        raise QzoneError("详情参数不足", reason="error")
    url = detail_url(int(owner_uin or me), tid,
                     get_gtk(cred.get("p_skey") or cred.get("skey")))
    r = _http_get(url, cred.get("cookie") or "", int(owner_uin or me))
    j = _strip_jsonp(r.text or "")
    if not isinstance(j, dict):
        if r.status_code in (401, 403):
            raise QzoneError(f"详情被拒绝（HTTP {r.status_code}）", reason="blocked")
        raise QzoneError("详情返回非 JSON", reason="error")
    code = j.get("code")
    if code == -3000 or "登录" in str(j.get("message") or ""):
        raise QzoneError("详情需要登录态", need_login=True, reason="expired")
    if code != 0:
        raise QzoneError(f"详情接口返回异常：code={code}", reason="error")
    return {
        "tid": str(tid),
        "comment_count": _first_int(j, ("cmtnum", "msgTotal"), 0),
        "forward_count": _first_int(j, ("fwdnum", "rt_fwdnum"), 0),
        "comments": parse_comments(j),
        "raw": json.dumps(j, ensure_ascii=False)[:20000],
    }


# ── 落库（表建在统一主库里，但 schema 完全由本模块持有，core/store.py 不动）───
FEEDS_SCHEMA = """
CREATE TABLE IF NOT EXISTS feeds(
  id          TEXT PRIMARY KEY,
  account_qq  INTEGER,
  ts          INTEGER,
  author_qq   INTEGER,
  author_name TEXT,
  content     TEXT,
  images      TEXT,
  praise      INTEGER,
  comments    INTEGER,
  forwards    INTEGER,
  raw         TEXT,
  comments_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_feeds_acc_ts ON feeds(account_qq, ts DESC);
CREATE INDEX IF NOT EXISTS ix_feeds_author ON feeds(account_qq, author_qq, ts DESC);
"""

FEED_COLS = ("id", "account_qq", "ts", "author_qq", "author_name", "content",
             "images", "praise", "comments", "forwards", "raw")


def _connect(account_qq=None):
    if not account_qq:
        raise ValueError("qzone._connect 必须指定 account_qq（task-11 每账号独立库）")
    con = store.connect(account_qq)
    con.executescript(FEEDS_SCHEMA)
    try:  # 老库补齐 comments_json 列（幂等）
        cols = [r[1] for r in con.execute("PRAGMA table_info(feeds)")]
        if "comments_json" not in cols:
            con.execute("ALTER TABLE feeds ADD COLUMN comments_json TEXT")
        con.commit()
    except Exception:
        pass
    return con


def init_feeds(account_qq=None) -> None:
    if account_qq:
        con = _connect(account_qq)
        con.close()
        return
    try:
        for a in store.list_accounts():
            con = _connect(int(a["account_qq"]))
            con.close()
    except Exception:  # noqa: BLE001
        pass


def save_feeds(rows) -> int:
    """按 id 去重写入/更新（不覆盖已存的 comments_json），返回新增条数。"""
    rows = [r for r in (rows or []) if r and r.get("id")]
    if not rows:
        return 0
    groups: dict = {}
    for r in rows:
        try:
            groups.setdefault(int(r.get("account_qq") or 0), []).append(r)
        except (TypeError, ValueError):
            continue
    n = 0
    sets = ",".join(f"{c}=excluded.{c}" for c in FEED_COLS if c != "id")
    for qq, rs in groups.items():
        if not qq:
            continue
        con = _connect(qq)
        try:
            before = con.execute("SELECT COUNT(*) FROM feeds").fetchone()[0]
            con.executemany(
                f"INSERT INTO feeds({','.join(FEED_COLS)}) "
                f"VALUES({','.join('?' * len(FEED_COLS))}) "
                f"ON CONFLICT(id) DO UPDATE SET {sets}",
                [tuple(r.get(c) for c in FEED_COLS) for r in rs])
            con.commit()
            after = con.execute("SELECT COUNT(*) FROM feeds").fetchone()[0]
            n += int(after - before)
        finally:
            con.close()
    return n


def save_comments(account_qq, feed_id: str, comments: list) -> int:
    """只更新评论列表，不动其它字段。"""
    if not feed_id:
        return 0
    con = _connect(account_qq)
    try:
        cur = con.execute("UPDATE feeds SET comments_json=? WHERE id=?",
                          (json.dumps(comments or [], ensure_ascii=False), str(feed_id)))
        con.commit()
        return int(cur.rowcount or 0)
    finally:
        con.close()


def list_feeds(account_qq: int | None = None, limit: int = 20,
               offset: int = 0, scope: str = "all") -> list[dict]:
    con = _connect(account_qq)
    try:
        sql = "SELECT * FROM feeds WHERE 1=1"
        args: list = []
        if account_qq:
            sql += " AND account_qq=?"
            args.append(int(account_qq))
        if scope == "mine" and account_qq:
            sql += " AND author_qq=?"
            args.append(int(account_qq))
        elif scope == "friends" and account_qq:
            sql += " AND author_qq<>?"
            args.append(int(account_qq))
        sql += " ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?"
        args += [int(limit), int(offset)]
        out = []
        for r in con.execute(sql, args).fetchall():
            d = dict(r)
            d["comment_count"] = int(d.get("comments") or 0)
            d["forward_count"] = int(d.get("forwards") or 0)
            try:
                d["images"] = json.loads(d.get("images") or "[]")
            except Exception:
                d["images"] = []
            if not isinstance(d["images"], list):
                d["images"] = []
            try:
                d["comments"] = json.loads(d.get("comments_json") or "[]")
            except Exception:
                d["comments"] = []
            if not isinstance(d["comments"], list):
                d["comments"] = []
            d.pop("comments_json", None)
            d.pop("raw", None)
            out.append(d)
        return out
    finally:
        con.close()


def count_feeds(account_qq: int | None = None, scope: str = "all") -> int:
    con = _connect(account_qq)
    try:
        sql = "SELECT COUNT(*) FROM feeds WHERE 1=1"
        args: list = []
        if account_qq:
            sql += " AND account_qq=?"
            args.append(int(account_qq))
        if scope == "mine" and account_qq:
            sql += " AND author_qq=?"
            args.append(int(account_qq))
        elif scope == "friends" and account_qq:
            sql += " AND author_qq<>?"
            args.append(int(account_qq))
        return int(con.execute(sql, args).fetchone()[0])
    finally:
        con.close()


def _default_friends(account_qq: int, limit: int = 10) -> list[int]:
    """从本地联系人里挑最活跃的好友 QQ（按消息数），用于按好友空间拉动态。"""
    try:
        cs = store.list_contacts(account_qq, kind="c2c", order="msg_count DESC",
                                 limit=max(1, int(limit)))
        out = []
        for c in cs:
            qq = int(c.get("peer_qq") or 0)
            if qq and qq != int(account_qq) and qq not in out:
                out.append(qq)
        return out
    except Exception:
        return []


# ── 可行性探测 ──────────────────────────────────────────────────────────────
def _http_probe(host: str, endpoint: str, cred: dict | None, num: int = DEFAULT_NUM,
                pos: int = 0) -> dict:
    """对单个入口发一次真实请求，返回可安全落盘 / 上报的探测记录。"""
    cred = cred or {}
    uin = int(cred.get("uin") or _default_uin() or 0)
    p_skey = cred.get("p_skey") or cred.get("skey") or ""
    url = build_url(uin, pos=pos, num=num, g_tk=get_gtk(p_skey), endpoint=endpoint)
    rec = {
        "host": host,
        "endpoint": endpoint,
        "url_redacted": redact_url(url),
        "uin": uin,
        "http_status": None,
        "content_type": "",
        "elapsed": 0.0,
        "ok": False,
        "verdict": "",
        "code": None,
        "message": "",
        "body_preview": "",
    }
    t0 = time.time()
    try:
        r = _http_get(url, cred.get("cookie") or "", uin)
    except QzoneError as exc:
        rec["elapsed"] = round(time.time() - t0, 3)
        rec["verdict"] = "unreachable"
        rec["message"] = str(exc)
        return rec

    rec["elapsed"] = round(time.time() - t0, 3)
    rec["http_status"] = r.status_code
    rec["content_type"] = r.headers.get("content-type", "")
    text = r.text or ""
    rec["body_preview"] = redact(text[:PREVIEW_MAX], cookie_secrets(cred))
    j = _strip_jsonp(text)
    if j is None:
        low = text.lower()
        if "登录" in text or "login" in low or r.status_code in (301, 302, 401):
            rec.update({"verdict": "need_login",
                        "message": f"响应要求登录（HTTP {r.status_code}）"})
        elif r.status_code == 403:
            rec.update({"verdict": "blocked",
                        "message": "HTTP 403 被拦截/风控：该入口可能要求特定 Referer/Cookie"})
        else:
            rec.update({"verdict": "not_json",
                        "message": f"非 JSON 响应（HTTP {r.status_code}），接口可能改版"})
        return rec

    code = j.get("code")
    msg = str(j.get("message") or j.get("msg") or "")
    rec["code"] = code
    rec["message"] = msg
    if code == 0 and isinstance(j.get("msglist"), list):
        rec.update({"ok": True, "verdict": "ok",
                    "message": f"成功，返回 {len(j['msglist'])} 条动态"})
    elif code == -3000 or "登录" in msg:
        rec.update({"verdict": "need_login",
                    "message": f"需要登录态（code={code} {msg}）"})
    elif r.status_code in (401, 403):
        rec.update({"verdict": "blocked", "message": f"HTTP {r.status_code} 被拦截/风控"})
    else:
        rec.update({"verdict": "error", "message": f"接口返回 code={code} {msg}"})
    return rec


def probe(opts: dict | None = None) -> dict:
    """真实请求探测 QZone 接口，返回结论 + 脱敏证据（不抛异常）。"""
    opts = dict(opts or {})
    cred, meta = resolve_credential(opts, strict=False)
    num = _clamp_num(opts.get("num"))
    recs = [_http_probe(host, ep, cred, num=num) for host, ep in ENDPOINTS]
    ok = any(r.get("ok") for r in recs)
    verdicts = [r.get("verdict") for r in recs]
    if ok:
        verdict, msg = "ok", "QZone 接口可用，登录态有效。"
    elif "need_login" in verdicts:
        verdict, msg = "need_login", NEED_LOGIN_MSG
    elif "blocked" in verdicts:
        verdict, msg = "blocked", "QZone 被拦截/风控（HTTP 403），请稍后再试或更换网络"
    elif verdicts and all(v == "unreachable" for v in verdicts):
        verdict, msg = "network", "网络不可达：无法连接 QZone"
    else:
        verdict = "error"
        msg = "QZone 接口异常：" + "；".join(
            f"{r['host']}={r.get('verdict') or 'unknown'}" for r in recs)
    return {
        "ok": ok,
        "verdict": verdict,
        "reason": verdict,
        "need_login": verdict == "need_login",
        "message": msg,
        "credential": meta,
        "endpoints": recs,
        "endpoints_tested": [u for _, u in ENDPOINTS],
    }

# ── 契约：status / probe / sync / conversations ────────────────────────────
def sync(opts: dict | None = None, progress=None) -> dict:
    """拉取 QZone 动态并落库（按 feeds.id 增量去重）。

    opts["scope"]       : mine（默认，自己）/ friends（好友空间）/ all
    opts["friends"]     : 好友 QQ 列表；缺省按本地联系人消息数取 top N
    opts["friend_limit"]: 好友空间上限（默认 10）
    opts["detail_max"]  : 最多补多少条详情（默认 40，用于拿评论列表）
    """
    opts = dict(opts or {})
    cred, meta = resolve_credential(opts, strict=True)
    me = int(cred.get("uin") or 0)
    account_qq = int(opts.get("account_qq") or me)
    # 多账号隔离：QZone 凭据（cookie/uin）是全局单例，绝不能用它把别人的动态写进 account_qq。
    if opts.get("account_qq") and me and int(opts["account_qq"]) != me:
        raise RuntimeError(
            "QZONE_ACCOUNT_MISMATCH：QZone 登录态是全局单例（当前 uin=%s），不能用它同步账号 %s 的动态；"
            "请先在该账号下登录 QZone，或显式关闭动态同步。" % (me, int(opts["account_qq"])))
    scope = str(opts.get("scope") or "mine").lower()
    if scope not in ("mine", "friends", "all"):
        scope = "mine"
    num = _clamp_num(opts.get("num"))
    pages = max(1, min(int(opts.get("pages") or 3), 20))
    detail_max = max(0, min(int(opts.get("detail_max") or 40), 200))
    friend_limit = max(1, min(int(opts.get("friend_limit") or 10), 50))

    owners: list[int] = []
    if scope in ("mine", "all"):
        owners.append(me)
    if scope in ("friends", "all"):
        friends = opts.get("friends")
        if not friends:
            friends = _default_friends(account_qq, friend_limit)
        for f in (friends or [])[:friend_limit]:
            try:
                fu = int(f)
            except Exception:
                continue
            if fu and fu != me and fu not in owners:
                owners.append(fu)
    if not owners:
        owners = [me]

    rows: list[dict] = []
    seen: set[str] = set()
    fetched = 0
    used_pages = 0
    errors: list[str] = []
    for oi, owner in enumerate(owners):
        pos = 0
        for i in range(pages):
            if progress:
                pct = int((oi * pages + i) * 80 / max(1, len(owners) * pages))
                progress("拉取动态", pct, f"空间 {owner} 第 {i + 1}/{pages} 页")
            try:
                page_rows, next_pos = fetch_feeds(cred, pos=pos, num=num, owner=owner)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{owner}: {exc}")
                break
            used_pages += 1
            fetched += len(page_rows)
            for r in page_rows:
                if r["id"] not in seen:
                    seen.add(r["id"])
                    rows.append(r)
            if not next_pos or not page_rows:
                break
            pos = next_pos

    detail_done = 0
    detail_failed = 0
    for r in rows:
        if detail_done >= detail_max:
            break
        if int(r.get("comments") or 0) <= 0:
            continue
        if progress:
            progress("补评论", 85, f"详情 {detail_done + 1}/{detail_max}")
        try:
            d = fetch_detail(cred, r.get("author_qq") or r.get("account_qq"), r["id"])
            r["_comments"] = d.get("comments") or []
            if d.get("comment_count") is not None:
                r["comments"] = int(d["comment_count"])
            if d.get("forward_count") is not None:
                r["forwards"] = int(d["forward_count"])
            detail_done += 1
        except Exception:  # noqa: BLE001
            detail_failed += 1

    if progress:
        progress("落库", 92, "写入 feeds 表")
    added = save_feeds(rows)
    saved_comments = 0
    for r in rows:
        if "_comments" in r:
            save_comments(account_qq, r["id"], r["_comments"])
            saved_comments += 1
    total = count_feeds(account_qq, scope=scope)
    msg = (f"新增 {added} 条动态（解析 {fetched} 条，补评论 {saved_comments} 条，"
           f"库中 {scope} 共 {total} 条）")
    if errors:
        msg += f"；{len(errors)} 个空间失败"
    return {
        "ok": True,
        "message": msg,
        "imported": added,
        "fetched": fetched,
        "comments_saved": saved_comments,
        "detail_failed": detail_failed,
        "total": total,
        "account_qq": account_qq,
        "scope": scope,
        "owners": owners,
        "pages": used_pages,
        "errors": errors[:5],
        "source": meta.get("source"),
    }


_CRED_CACHE: dict = {"at": 0.0, "data": None}


def credential_status(force: bool = False, ttl: float = 30.0) -> dict:
    """轻量判断当前能否拿到 QZone 登录态（不请求 QZone，结果缓存 30s）。

    返回 {status: ok|need_login|error, reason, message, source, uin, has_p_skey}。
    永不包含 cookie / token 值。
    """
    now = time.time()
    if not force and _CRED_CACHE["data"] and (now - _CRED_CACHE["at"]) < ttl:
        return dict(_CRED_CACHE["data"])
    if httpx is None:
        data = {"status": "error", "reason": "no_httpx",
                "message": "缺少 httpx 依赖：请用项目自带 .venv Python 运行后端"}
    else:
        try:
            cred, meta = resolve_credential({}, strict=False)
        except Exception as exc:  # noqa: BLE001
            cred, meta = None, {"onebot": {"message": str(exc)}}
        if _usable(cred):
            data = {"status": "ok", "reason": "credential_ok",
                    "message": "已获取 QZone 登录态",
                    "source": meta.get("source"), "uin": meta.get("uin"),
                    "has_p_skey": True}
        else:
            ob = (meta or {}).get("onebot") or {}
            detail = str(ob.get("message") or "")
            msg = NEED_LOGIN_MSG + (f"（OneBot：{detail}）" if detail else "")
            data = {"status": "need_login", "reason": "no_credential",
                    "message": msg, "source": meta.get("source"),
                    "uin": meta.get("uin"), "has_p_skey": False}
    _CRED_CACHE["at"] = now
    _CRED_CACHE["data"] = data
    return dict(data)


def status() -> dict:
    cs = credential_status()
    detail = {
        "httpx": bool(httpx),
        "endpoints": [u for _, u in ENDPOINTS],
        "settings_file": str(SETTINGS_FILE),
        "probe_dir": str(PROBE_DIR),
        "cookie_sources": ["opts", "settings.qzone",
                           "onebot:get_cookies (domain=qzone.qq.com)",
                           "onebot:get_credentials", "napcat-config token"],
        "credential": {k: cs.get(k) for k in ("status", "reason", "source", "uin")},
        "rate_limit": f"{MIN_INTERVAL}s",
    }
    ready = cs.get("status") == "ok"
    return {"id": SOURCE_ID, "name": SOURCE_NAME, "kind": SOURCE_KIND,
            "ready": ready, "message": cs.get("message") or NEED_LOGIN_MSG, "detail": detail}


def conversations(opts: dict | None = None) -> list:
    """动态没有会话概念，占位以满足数据源契约。"""
    return []
