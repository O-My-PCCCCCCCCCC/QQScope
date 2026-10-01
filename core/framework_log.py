# -*- coding: utf-8 -*-
"""QQScope · 框架（NapCat）终端日志

启动脚本把框架的 stdout / stderr 重定向到：
  data/framework/run.log   正常输出（NapCat 的错误多数也走 stdout，带 [error] 标记）
  data/framework/run.err   stderr（实测 NapCat 基本不用，通常 0 字节）

对外：
  read_log(limit, since_offset=None, since_offset_err=None)  增量读，只读尾部
  tail(limit)                                                 只读文件尾部 N 行
  scrub(text)                                                 敏感信息脱敏
  framework_status()                                          进程/端口/登录态

设计要点：
  · 日志带 ANSI 颜色码（\\x1b[32minfo\\x1b[39m）和 CRLF，必须先清洗再解析。
  · 只读尾部（默认 256KB），绝不整读大文件；单次返回最多 2000 行。
  · offset 只在「整行」之后推进，写到一半的行留到下次。
  · 文件超过 20MB 只给提示，不做轮转。
"""
from __future__ import annotations

import collections
import json
import os
import re
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRAMEWORK_DIR = ROOT / "data" / "framework"
LOG_FILE = FRAMEWORK_DIR / "run.log"
ERR_FILE = FRAMEWORK_DIR / "run.err"
ONEBOT_JSON = FRAMEWORK_DIR / "onebot.json"

MAX_MEM_LINES = 2000            # 内存中最多保留的日志行数
TAIL_BYTES = 256 * 1024         # 初始加载只读文件尾部这么多字节
WARN_BYTES = 20 * 1024 * 1024   # 超过 20MB 给提示
SEARCH_BYTES = 4 * 1024 * 1024  # 带 grep 时最多回扫这么多字节（仍不整读大文件）
QRCODE_FILE = ROOT / "tools" / "napcat" / "napcat" / "cache" / "qrcode.png"
NAPCAT_DIR = ROOT / "tools" / "napcat"                 # node.exe / index.js 所在目录
SPAWNED_FILE = FRAMEWORK_DIR / "spawned.json"            # 记录「我们启动的」框架 PID
WAIT_LOGIN_INTERVAL = 10.0        # live_sync 未登录时的重查间隔（秒）
MAX_LINE_CHARS = 2000           # 单行最长字符数（截图/密文行可能很长）

# 内存里的滚动缓冲（满足「只保留最近 2000 行」）
BUFFER: "collections.deque[dict]" = collections.deque(maxlen=MAX_MEM_LINES)

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
LINE_RE = re.compile(
    r"^(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})\s*\[([A-Za-z]+)\]\s?(.*)$")
LEVEL_MAP = {"error": "error", "err": "error", "fatal": "error", "panic": "error",
             "warn": "warn", "warning": "warn"}

_TOKEN_KEYS = ("access_token", "webui_token", "refresh_token", "token")


def _to_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return int(default)


def _clean_raw(s: str) -> str:
    """去掉 ANSI 颜色码 / 回车 / 其它控制字符（保留 \\t）。"""
    s = ANSI_RE.sub("", s)
    s = s.replace("\r", "")
    return "".join(ch for ch in s if ch == "\t" or ord(ch) >= 32)


# ── 脱敏 ────────────────────────────────────────────────────────────────────
_SUBS = [
    # token=xxx / token: xxx / "webui_token": "xxx"
    (re.compile(r'(?i)((?:access_|webui_|refresh_)?token["\']?\s*[:=]\s*["\']?)([^\s"\',;&]+)'), r"\1***"),
    # p_skey / skey / pskey / s_key
    (re.compile(r'(?i)\b(p_skey|pskey|s_key|skey)\b\s*[:=]\s*["\']?([^\s"\',;&]+)'), r"\1=***"),
    # cookie / set-cookie / authorization
    (re.compile(r'(?i)((?:set-)?cookie|authorization)\s*[:=]\s*([^\r\n]+)'), r"\1: ***"),
    # qrcode=<value>
    (re.compile(r'(?i)\bqrcode\b\s*[:=]\s*["\']?([^\s"\',;&]+)'), "qrcode=***"),
    # 光秃秃的 qrcode 字样（按任务要求：出现即打码）
    (re.compile(r'(?i)qrcode'), "***"),
    # 二维码解码 URL 里的 k= 密钥
    (re.compile(r'(?i)(txz\.qq\.com/p\?k=)[^&\s]+'), r"\1***"),
    # 通用 URL 参数 ?key= / &token= / &k= / ?access_token=
    (re.compile(r'(?i)([?&](?:access_token|token|key|k|skey)=)[^&\s]+'), r"\1***"),
    # 形如 sk-xxxx / Bearer xxxx 的裸密钥
    (re.compile(r'(?i)\b(sk-[A-Za-z0-9_\-]{6,})'), "***"),
    (re.compile(r'(?i)(bearer\s+)[A-Za-z0-9_\-\.]{8,}'), r"\1***"),
]


def _known_secrets() -> list[str]:
    """从 onebot.json 读出真实 token，做字面量兜底替换（名字没出现也能打码）。"""
    out: list[str] = []
    try:
        cfg = json.loads(ONEBOT_JSON.read_text(encoding="utf-8"))
        for k in ("token", "webui_token"):
            v = str(cfg.get(k) or "").strip()
            if len(v) >= 8:
                out.append(v)
    except Exception:  # noqa: BLE001
        pass
    return out


_SECRETS_CACHE: list[str] | None = None


def scrub(text: str) -> str:
    """把 token / qrcode / p_skey / skey / cookie 等敏感信息打成 ***。"""
    global _SECRETS_CACHE
    if text is None:
        return ""
    s = str(text)
    for pat, rep in _SUBS:
        s = pat.sub(rep, s)
    if _SECRETS_CACHE is None:
        _SECRETS_CACHE = _known_secrets()
    for secret in _SECRETS_CACHE:
        s = s.replace(secret, "***")
    return s


# ── 解析 ────────────────────────────────────────────────────────────────────
def _to_epoch(mm: int, dd: int, hh: int, mi: int, ss: int) -> int | None:
    """把 MM-DD HH:MM:SS 变成 Unix 秒（年份取当前年，跨年自动回退一年）。"""
    lt = time.localtime()
    for year in (lt.tm_year, lt.tm_year - 1):
        try:
            t = time.mktime((year, mm, dd, hh, mi, ss, 0, 0, -1))
        except (OverflowError, ValueError):
            return None
        if t - time.time() > 86400:      # 明显在未来 -> 是去年
            continue
        return int(t)
    return None


def _parse_chunk(raw: bytes, fname: str, drop_first: bool = False,
                default_ts: int | None = None) -> list[dict]:
    """把一段字节切成行记录。不完整行由调用方按 offset 处理。

    在「单个文件内部」补 ts / level：续行借相邻行；孤儿行（例如 run.err 里
    一行没有任何时间戳的报错）用该文件 mtime 兜底 —— 这样合并排序时
    stderr 不会因为 ts=None 被顶到最前面。
    """
    text = raw.decode("utf-8", "replace")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if drop_first and lines:
        lines.pop(0)                      # 从文件中间读起，第一行可能是半行
    out: list[dict] = []
    for ln in lines:
        s = _clean_raw(ln)
        if not s.strip():
            continue
        m = LINE_RE.match(s)
        if m:
            mm, dd, hh, mi, ss, lvl, msg = m.groups()
            ts = _to_epoch(int(mm), int(dd), int(hh), int(mi), int(ss))
            level = LEVEL_MAP.get(lvl.lower(), "info")
        else:
            ts, level = None, None
            msg = s
        out.append({"ts": ts, "level": level, "text": scrub(msg)[:MAX_LINE_CHARS],
                    "file": fname})
    return _fill_ts(out, default_ts=default_ts)


def _fill_ts(recs: list[dict], default_ts: int | None = None) -> list[dict]:
    """续行（二维码 / 堆栈 / 无时间戳的 stderr）补齐 ts 与 level。

    必须在 run.log / run.err 合并之后调用：单文件只有一个孤儿行时借不到邻居，
    这时用 default_ts（文件 mtime / now）兜底，避免前端拿到 ts=None。
    """
    last_ts = None
    for r in recs:
        if r["ts"] is not None:
            last_ts = r["ts"]
        elif last_ts is not None:
            r["ts"] = last_ts
    nxt_ts = None
    for r in reversed(recs):
        if r["ts"] is not None:
            nxt_ts = r["ts"]
        elif nxt_ts is not None:
            r["ts"] = nxt_ts
    if default_ts is not None:
        for r in recs:
            if r["ts"] is None:
                r["ts"] = int(default_ts)
    last_lvl = "info"
    for r in recs:
        if r["level"] is None:
            r["level"] = last_lvl
        else:
            last_lvl = r["level"]
    return recs


def _merge(a: list[dict], b: list[dict]) -> list[dict]:
    """run.log / run.err 合并；按时间戳稳定排序（同一秒保持各自出现顺序）。"""
    if not a:
        return b
    if not b:
        return a
    out = a + b
    out.sort(key=lambda r: (r["ts"] if r["ts"] is not None else 0))
    return out


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _exists(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _read_tail_bytes(path: Path, nbytes: int) -> tuple[bytes, bool]:
    """只读文件尾部 nbytes；返回 (bytes, 是否从中间截断)。"""
    size = _size(path)
    if size <= 0:
        return b"", False
    start = max(0, size - int(nbytes))
    with open(path, "rb") as f:
        f.seek(start)
        return f.read(size - start), start > 0


def tail(limit: int = 300, grep: str | None = None) -> list[dict]:
    """只读文件尾部，返回最近 limit 行（按时间顺序）。

    grep 不为空时回扫最多 SEARCH_BYTES（默认 4MB）并只保留命中行，
    用于在滚滚消息里捞框架事件（如 `[NapCat]`、`登录`、`error`）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 300
    limit = max(1, min(limit, MAX_MEM_LINES))
    window = SEARCH_BYTES if grep else TAIL_BYTES
    recs: list[dict] = []
    for path in (LOG_FILE, ERR_FILE):
        if not _exists(path):
            continue
        raw, cut = _read_tail_bytes(path, window)
        if not raw:
            continue
        try:
            mtime = int(path.stat().st_mtime)
        except OSError:
            mtime = int(time.time())
        recs = _merge(recs, _parse_chunk(raw, path.name, drop_first=cut,
                                         default_ts=mtime))
    if grep:
        g = str(grep).lower()
        recs = [r for r in recs if g in r["text"].lower()]
    recs = recs[-MAX_MEM_LINES:]
    BUFFER.extend(recs)
    return recs[-limit:]


def _read_from(path: Path, pos: int) -> tuple[list[dict], int, bool]:
    """读 pos 之后的新内容；返回 (记录, 新 offset, 是否有截断)。

    offset 只在完整行之后推进：写到一半的最后一行留到下次再读。
    """
    size = _size(path)
    mid = False                                  # 是否从「半个行」中间开始读
    if pos < 0 or pos > size:
        pos = max(0, size - TAIL_BYTES)          # offset 失效（文件被截断）-> 退回尾部
        mid = True
    if pos == size:
        return [], size, False
    dropped = False
    if size - pos > TAIL_BYTES:                  # 落后太多：只取尾部，避免读超大文件
        pos = size - TAIL_BYTES
        dropped = True
        mid = True
    with open(path, "rb") as f:
        f.seek(pos)
        data = f.read(size - pos)
    nl = data.rfind(b"\n")
    if nl < 0:                                   # 还没有完整行
        return [], pos, dropped
    consumed = pos + nl + 1
    try:
        mtime = int(path.stat().st_mtime)
    except OSError:
        mtime = int(time.time())
    # 只有从「行中间」开始时才丢首行；按已消费 offset 续读时首行是完整行，不能丢
    recs = _parse_chunk(data[:nl + 1], path.name, drop_first=mid, default_ts=mtime)
    return recs, consumed, dropped


def _warn(size_log: int, size_err: int) -> str | None:
    big = [(n, s) for n, s in (("run.log", size_log), ("run.err", size_err)) if s > WARN_BYTES]
    if not big:
        return None
    parts = ", ".join(f"{n} 已 {s / 1048576:.1f} MB" for n, s in big)
    return f"框架日志过大（{parts}，阈值 {WARN_BYTES // 1048576} MB），建议手动清理/重启框架"


def read_log(limit: int = 300, since_offset=None, since_offset_err=None,
             grep: str | None = None) -> dict:
    """读取框架终端日志。

    since_offset=None（或 0）→ 初始加载：只读尾部 limit 行，offset 返回文件末尾。
    since_offset=N            → 增量：只返回 run.log 第 N 字节之后的新行。
    since_offset_err 可选（run.err 的游标）；不传时增量模式不会再回放旧 stderr。
    grep 可选：关键字过滤（大小写不敏感）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 300
    limit = max(1, min(limit, MAX_MEM_LINES))

    size_log, size_err = _size(LOG_FILE), _size(ERR_FILE)
    warn = _warn(size_log, size_err)
    running = _is_running()

    try:
        off_in = int(since_offset) if since_offset is not None else 0
    except (TypeError, ValueError):
        off_in = 0

    if off_in <= 0:                              # ── 初始加载：只读尾部
        lines = tail(limit, grep=grep)
        window = SEARCH_BYTES if grep else TAIL_BYTES
        return {
            "lines": lines, "offset": size_log, "offset_err": size_err,
            "size": size_log, "size_err": size_err, "file": LOG_FILE.name,
            "running": running,
            "truncated": bool(size_log > window or len(lines) >= limit),
            "warn": warn, "grep": grep or None,
            "server_ts": int(time.time()),
        }

    # ── 增量
    recs: list[dict] = []
    truncated = False
    new_off = off_in
    if _exists(LOG_FILE):
        r, new_off, dropped = _read_from(LOG_FILE, off_in)
        recs = _merge(recs, r)
        truncated = truncated or dropped
    else:
        new_off = size_log

    new_off_err = size_err
    if _exists(ERR_FILE):
        if since_offset_err is None:
            new_off_err = size_err               # 客户端没带 err 游标：不回放旧内容
            r = []
        else:
            try:
                p = int(since_offset_err)
            except (TypeError, ValueError):
                p = 0
            r, new_off_err, dropped_e = _read_from(ERR_FILE, p)
            truncated = truncated or dropped_e
        recs = _merge(recs, r)

    if len(recs) > MAX_MEM_LINES:                # 内存上限：只留最近 2000 行
        truncated = True
        recs = recs[-MAX_MEM_LINES:]
    BUFFER.extend(recs)
    if grep:
        g = str(grep).lower()
        recs = [r for r in recs if g in r["text"].lower()]
    lines = recs[-limit:]
    if len(recs) > limit:
        truncated = True
    return {
        "lines": lines, "offset": new_off, "offset_err": new_off_err,
        "size": size_log, "size_err": size_err, "file": LOG_FILE.name,
        "running": running, "truncated": truncated, "warn": warn,
        "grep": grep or None,
        "server_ts": int(time.time()),
    }


# ── 框架状态 ────────────────────────────────────────────────────────────────
def load_framework_cfg() -> dict:
    try:
        return json.loads(ONEBOT_JSON.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


_PORT_CACHE: dict = {"at": 0.0, "open": {}}
_LISTEN_CACHE: dict = {"at": 0.0, "data": {}}
_LISTEN_TTL = 2.0


def _port_open(host: str, port: int, timeout: float = 0.15) -> bool:
    """TCP 探测端口是否在监听（比 netstat 快得多，用于每轮日志轮询）。"""
    key = (host, int(port))
    now = time.time()
    hit = _PORT_CACHE["open"].get(key)
    if hit is not None and now - _PORT_CACHE["at"] < 1.0:
        return hit
    ok = False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            ok = True
    except OSError:
        ok = False
    _PORT_CACHE["open"][key] = ok
    _PORT_CACHE["at"] = now
    return ok


def _host_of(url: str, default: str = "127.0.0.1") -> str:
    m = re.search(r"//([^:/]+)", str(url or ""))
    return m.group(1) if m else default


def _netstat_listen(force: bool = False) -> dict[int, int]:
    """返回 {端口: 监听进程 PID}（Windows netstat -ano，纯标准库，2 秒缓存）。"""
    now = time.time()
    if not force and _LISTEN_CACHE["data"] and now - _LISTEN_CACHE["at"] < _LISTEN_TTL:
        return _LISTEN_CACHE["data"]
    out: dict[int, int] = {}
    try:
        p = subprocess.run(["netstat", "-ano"], capture_output=True, timeout=6)
    except Exception:  # noqa: BLE001
        return _LISTEN_CACHE["data"] or out
    raw = (p.stdout or b"").decode("utf-8", "replace")
    for ln in raw.splitlines():
        parts = ln.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local = parts[1]
        if ":" not in local:
            continue
        try:
            port = int(local.rsplit(":", 1)[1])
            pid = int(parts[-1])
        except ValueError:
            continue
        if port not in out or pid:
            out.setdefault(port, pid)
    _LISTEN_CACHE["at"] = now
    _LISTEN_CACHE["data"] = out
    return out


def _netstat_peek() -> dict[int, int]:
    """只读 netstat 缓存（不 spawn 进程），快路径用。"""
    return dict(_LISTEN_CACHE["data"]) if _LISTEN_CACHE["data"] else {}


def _onebot(cfg: dict, path: str, timeout: float = 0.8) -> dict:
    base = str(cfg.get("base") or "").rstrip("/")
    if not base:
        raise ValueError("没有配置 OneBot 地址")
    req = urllib.request.Request(
        base + path, headers={"Authorization": "Bearer " + str(cfg.get("token") or "")})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _log_first_ts() -> int | None:
    """run.log 第一条带时间戳的行（≈ 本次框架启动时间），用于算 uptime。"""
    if not _exists(LOG_FILE):
        return None
    try:
        with open(LOG_FILE, "rb") as f:
            head = f.read(64 * 1024)
    except OSError:
        return None
    for ln in head.decode("utf-8", "replace").split("\n")[:200]:
        m = LINE_RE.match(_clean_raw(ln))
        if m:
            mm, dd, hh, mi, ss = (int(x) for x in m.groups()[:5])
            return _to_epoch(mm, dd, hh, mi, ss)
    return None


_VERSION_CACHE: dict = {"at": 0.0, "ver": None}


def _log_version(ttl: float = 600.0) -> str | None:
    """从 run.log 头部抓 NapCat 版本号（横幅在启动头部，不在尾部）。

    只读前 32KB + 600s 缓存：状态接口不能为了版本号去扫 256KB 尾部。
    """
    now = time.time()
    if _VERSION_CACHE["at"] > 0 and now - _VERSION_CACHE["at"] < ttl:
        return _VERSION_CACHE["ver"]
    ver = None
    try:
        with open(LOG_FILE, "rb") as f:
            head = f.read(32 * 1024)
        for ln in head.decode("utf-8", "replace").split("\n")[:200]:
            m = re.search(r"NapCat\.Core Version:\s*([0-9][0-9A-Za-z\.\-]*)", _clean_raw(ln))
            if m:
                ver = m.group(1)
                break
    except Exception:  # noqa: BLE001
        ver = None
    _VERSION_CACHE.update({"at": now, "ver": ver})
    return ver

def _is_running() -> bool:
    """框架是否在跑：探 6099/3000 端口（socket，快）。"""
    cfg = load_framework_cfg()
    for k, dflt in (("webui", 6099), ("base", 3000)):
        u = str(cfg.get(k) or "")
        m = re.search(r":(\d+)(?:/|$)", u)
        port = int(m.group(1)) if m else dflt
        if _port_open(_host_of(u), port):
            return True
    return False


def framework_status() -> dict:
    """框架（NapCat）进程 / 端口 / 登录态。"""
    cfg = load_framework_cfg()
    listen = _netstat_listen()

    def _port_of(url: str, default: int) -> int:
        m = re.search(r":(\d+)(?:/|$)", str(url or ""))
        return int(m.group(1)) if m else default

    p_onebot = _port_of(cfg.get("base"), 3000)
    p_webui = _port_of(cfg.get("webui"), 6099)
    ports = {str(p): (p in listen) for p in sorted({p_onebot, p_webui})}
    pid = int(listen.get(p_webui) or listen.get(p_onebot) or 0)
    running = bool(pid) or any(ports.values())

    logged_in, account, nickname = False, 0, ""
    try:
        data = (_onebot(cfg, "/get_login_info", 0.8) or {}).get("data") or {}
        uid = int(data.get("user_id") or 0)
        if uid:
            logged_in, account, nickname = True, uid, str(data.get("nickname") or "")
    except Exception:  # noqa: BLE001
        pass
    if not logged_in:                            # 兜底：从最近的日志里找登录痕迹
        for rec in tail(400):
            t = rec["text"]
            if "登录成功" in t or "已登录" in t or "数据库辅助支持能力" in t:
                logged_in = True
            if not account:
                m = re.match(r"^\d+\.\s*([1-9]\d{4,11})\s+(\S.*)$", t.strip())
                if m:
                    account, nickname = int(m.group(1)), m.group(2).strip()
    if not account:
        try:
            account = int(cfg.get("account_qq") or 0)
        except (TypeError, ValueError):
            account = 0

    start = _log_first_ts()
    uptime = int(time.time() - start) if start else 0
    return {
        "running": running,
        "pid": pid,
        "framework": str(cfg.get("framework") or "napcat"),
        "version": _log_version() or str(cfg.get("version") or ""),
        "logged_in": logged_in,
        "account": account,
        "nickname": nickname,
        "onebot_base": str(cfg.get("base") or ""),
        "webui": str(cfg.get("webui") or ""),
        "uptime": max(0, uptime),
        "ports": ports,
        "log": {"file": LOG_FILE.name, "size": _size(LOG_FILE),
                "size_err": _size(ERR_FILE), "warn": _warn(_size(LOG_FILE), _size(ERR_FILE))},
        "server_ts": int(time.time()),
    }


# ── 二维码 / 进程 / 登录门（task-34） ───────────────────────────────────────
def qrcode_path() -> Path:
    """NapCat 未登录时把二维码写到这里。"""
    return QRCODE_FILE


def qrcode_info() -> dict:
    """二维码文件状态（只 stat，不读图片内容）。available=False 时前端应提示去启动框架。"""
    url = "/api/framework/qrcode"
    try:
        st = QRCODE_FILE.stat()
    except OSError:
        return {"available": False, "mtime": 0, "size": 0, "url": url}
    return {"available": bool(st.st_size > 0), "mtime": int(st.st_mtime),
            "size": int(st.st_size), "url": url}


def _host_port(url: str, default_port: int) -> tuple[str, int]:
    """从 http://host:port/xxx 里取 host/port。"""
    host, port = "127.0.0.1", int(default_port)
    s = str(url or "")
    m = re.search(r"//([^/:]+)(?::(\d+))?", s)
    if m:
        host = m.group(1) or host
        if m.group(2):
            port = int(m.group(2))
    elif s.count(":") == 1 and s.split(":")[-1].strip().isdigit():
        port = int(s.split(":")[-1])
    return host, port


_NAP_PROC_CACHE: dict = {"at": 0.0, "procs": []}


def napcat_procs(force: bool = False) -> list[dict]:
    """列出「node.exe 且命令行含 napcat」的进程：[{pid,name,cmdline}]。

    用于两件事：① 判断框架是不是在跑（不依赖端口）；② 校验某个 PID 确实是
    框架进程（stop 前必须校验，绝不能误杀用户自己的进程）。best-effort，3 秒缓存。
    """
    now = time.time()
    if not force and now - _NAP_PROC_CACHE["at"] < 3.0:
        return _NAP_PROC_CACHE["procs"]
    procs: list[dict] = []
    if os.name == "nt":
        try:
            cmd = ("Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
                   "Where-Object { $_.CommandLine -match 'napcat' } | "
                   "ForEach-Object { '{0}|{1}' -f $_.ProcessId, $_.CommandLine }")
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                capture_output=True, timeout=10)
            out = (r.stdout or b"").decode("utf-8", "replace")
            for ln in out.replace("\r", "").split("\n"):
                ln = ln.strip()
                if "|" not in ln:
                    continue
                pid_s, _, cmdline = ln.partition("|")
                if pid_s.strip().isdigit():
                    procs.append({"pid": int(pid_s), "name": "node.exe",
                                  "cmdline": cmdline.strip()})
        except Exception:  # noqa: BLE001
            procs = []
    _NAP_PROC_CACHE["at"] = now
    _NAP_PROC_CACHE["procs"] = procs
    return procs


_PROC_CACHE: dict = {}


def proc_info(pid: int, force: bool = False, ttl: float = 5.0) -> dict | None:
    """单个进程的 {pid,name,cmdline}；进程不存在返回 None（5 秒缓存）。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    now = time.time()
    if not force:
        hit = _PROC_CACHE.get(pid)
        if hit and now - hit["at"] < ttl:
            return hit["info"]
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            info = {"pid": pid, "name": "", "cmdline": ""}
            _PROC_CACHE[pid] = {"at": now, "info": info}
            return info
        except OSError:
            _PROC_CACHE[pid] = {"at": now, "info": None}
            return None
    try:
        cmd = ("$p=Get-CimInstance Win32_Process -Filter \"ProcessId=%d\"; "
               "if($p){ '{0}|{1}' -f $p.Name, $p.CommandLine }") % pid
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                           capture_output=True, timeout=10)
        out = (r.stdout or b"").decode("utf-8", "replace").strip()
        if "|" not in out:
            return None
        name, _, cmdline = out.partition("|")
        info = {"pid": pid, "name": name.strip(), "cmdline": cmdline.strip()}
        _PROC_CACHE[pid] = {"at": now, "info": info}
        return info
    except Exception:  # noqa: BLE001
        return None


def is_framework_proc(pid: int, force: bool = False) -> bool:
    """该 PID 现在还在跑、且确实是 node.exe 且命令行含 napcat。

    force=True 绕开 5 秒进程缓存（刚 taskkill 完必须用，否则会读到旧结果）。
    """
    info = proc_info(pid, force=force)
    if not info:
        return False
    name = str(info.get("name") or "").lower()
    cmdline = str(info.get("cmdline") or "").lower()
    return name == "node.exe" and "napcat" in cmdline


_LOGIN_CACHE: dict = {"at": 0.0, "uid": None, "nick": "", "key": ""}


def _onebot_login_cached(cfg: dict, timeout: float = 0.4,
                         ttl: float = 2.0) -> tuple[int | None, str]:
    """轻量 get_login_info：0.4s 超时 + 2s 缓存（前端首屏不能等；
    实测 OneBot 正常 2~30ms，0.4s 足够，超时立刻退回端口探测）。

    返回 (uid, nick)：uid>0 已登录；uid==0 明确未登录；
    uid=None 超时/失败 —— 调用方用「OneBot 端口是否在听」兜底。
    """
    key = str(cfg.get("base") or "") + "|" + str(cfg.get("token") or "")
    now = time.time()
    if _LOGIN_CACHE["key"] == key and now - _LOGIN_CACHE["at"] < ttl:
        return _LOGIN_CACHE["uid"], _LOGIN_CACHE["nick"]
    uid: int | None = None
    nick = ""
    try:
        data = (_onebot(cfg, "/get_login_info", timeout) or {}).get("data") or {}
        uid = _to_int(data.get("user_id"))
        nick = str(data.get("nickname") or "")
    except Exception:  # noqa: BLE001
        uid = None
    _LOGIN_CACHE.update({"at": now, "uid": uid, "nick": nick, "key": key})
    return uid, nick


_IDENTITY_CACHE: dict = {"at": 0.0, "acct": 0, "nick": ""}


def _log_identity(ttl: float = 30.0) -> tuple[int, str]:
    """从 run.log 里认账号/昵称（纯读文件，不发任何 OneBot 请求；30s 缓存）。

    tail() 要读 256KB 并把上千行过一遍 scrub，放在首屏热路径上会拖到几百毫秒，
    所以必须缓存 —— 昵称在 30 秒内基本不会变。
    """
    now = time.time()
    if _IDENTITY_CACHE["at"] > 0 and now - _IDENTITY_CACHE["at"] < ttl:
        return int(_IDENTITY_CACHE["acct"]), str(_IDENTITY_CACHE["nick"])
    acct, nick = 0, ""
    try:
        recs = tail(400)
    except Exception:  # noqa: BLE001
        return 0, ""
    for rec in recs:
        t = str(rec.get("text") or "").strip()
        if not nick:
            m = re.match(r"^(\S{1,32})\s*\|\s*(?:接收|发送)", t)
            if m:
                nick = m.group(1).strip()
        if not acct:
            m = re.match(r"^\d+\.\s*([1-9]\d{4,11})\s+(\S.*)$", t)
            if m:
                acct = int(m.group(1))
                if not nick:
                    nick = m.group(2).strip()
    _IDENTITY_CACHE.update({"at": now, "acct": acct, "nick": nick})
    return acct, nick


def login_gate(cfg: dict | None = None, deep: bool = False,
               probe_onebot: bool = False) -> dict:
    """登录门状态机（**默认不发任何 OneBot 请求**；快路径 < 50ms）。

    sync_allowed = 框架在跑 且 已登录 且 OneBot 端口在听。

    判定依据（不猜）：
      · 6099 在听 / 进程存在  -> 框架活着
      · 3000 在听              -> NapCat 登录成功后才监听 OneBot HTTP
      · probe_onebot=True      -> 再问一次 get_login_info 实查（1s 超时 + 1.5s 缓存），
                                 只有端口在听时才问，未登录绝不发请求
    deep=True 才查 WMI 进程列表 / netstat（给 /start、/stop 这类非首屏路径用）。
    """
    cfg = dict(cfg or load_framework_cfg())
    ob_host, ob_port = _host_port(cfg.get("base"), 3000)
    wu_host, wu_port = _host_port(cfg.get("webui"), 6099)
    ob_open = _port_open(ob_host, ob_port)
    wu_open = _port_open(wu_host, wu_port)

    procs: list[dict] = []
    if deep:
        procs = napcat_procs()
    running = bool(wu_open or ob_open or procs)
    logged_in = bool(ob_open)

    account, nickname = 0, ""
    verified = False
    if probe_onebot and ob_open:
        uid, nick = _onebot_login_cached(cfg)
        if uid:                                   # 实查成功：权威
            account, nickname, logged_in, verified = uid, nick, True, True
        elif uid == 0:                            # 明确未登录
            logged_in = False
        # uid is None（超时/失败）-> 用端口兜底，宁可当已登录也不卡首屏

    if not account and ob_open:                   # 只在端口在听时才读日志（省 IO）
        a, n = _log_identity()
        account, nickname = a or account, nickname or n
    if not account:
        account = _to_int(cfg.get("account_qq"))
    if not running:                               # 框架都没跑，身份信息没有意义
        account, nickname = 0, ""

    pid = int(procs[0]["pid"]) if procs else 0
    if deep:
        listen = _netstat_listen()
        pid = pid or int(listen.get(wu_port) or listen.get(ob_port) or 0)
    else:
        listen = _netstat_peek()
        pid = int(listen.get(wu_port) or listen.get(ob_port) or 0)

    qr = qrcode_info()
    # NapCat 只在启动时写一次二维码文件，过期后不会自己重写 —— 这里标出「已过期」，
    # 前端据此提示用户点「刷新二维码」（= 重启框架拿新码），而不是重复显示同一张死码。
    try:
        _age = max(0, int(time.time()) - int(qr.get("mtime") or 0))
    except Exception:
        _age = 0
    qr["age_s"] = _age
    qr["stale"] = bool(qr.get("available") and not logged_in and _age > 110)
    if not running:
        msg = "框架未运行：请点「启动框架」后再扫码登录"
    elif not logged_in:
        msg = ("等待扫码登录…（二维码已就绪）" if qr["available"]
               else "等待扫码登录…（暂无二维码，框架可能还在启动）")
    else:
        who = nickname or (str(account) if account else "")
        msg = f"已登录：{who}" if who else "已登录"

    allowed = bool(running and logged_in and ob_open)
    return {
        "framework_running": running,
        "logged_in": logged_in,
        "account": account,
        "nickname": nickname,
        "pid": pid,
        "ports": {str(ob_port): ob_open, str(wu_port): wu_open},
        "qr": qr,
        "sync_allowed": allowed,
        "waiting_login": not allowed,
        "verified": verified,
        "message": msg,
    }
