"""QQScope · 导出引擎（HTML / TXT / Markdown + 媒体 + zip）。

契约见 docs/SPEC-重构接口.md 第 4 / 10.5 节：

    export(account_qq, targets, formats, mode="per_peer", opts=None) -> dict

设计：
  * 纯标准库，无第三方依赖；
  * 通过 core.store.list_messages 分页拉取、边读边写，超长会话也不会撑爆内存；
  * HTML 单文件自包含（内联 CSS / 内联 JS）+ 同级相对 media/ 目录，零外部网络请求；
  * 媒体（图片/语音/视频缩略图/文件/表情）复制到 <job>/media/，HTML 用相对路径引用；
    本地缺失的媒体渲染成占位徽章，绝不出现破图；
  * TXT 用 utf-8-sig（带 BOM）；Markdown 带统计表 + 按天分节 + 引用块；
  * 文件名做 Windows 非法字符净化、≤120 字符截断、重名去重。

媒体来源（防御式，按优先级）：
  1) opts["media_resolver"]（可调用，测试/注入用）
  2) core.media.resolve(account_qq, media)（task-11 提供，存在时优先）
  3) 内置回退：把 media["file"]（相对 nt_data 的路径）拼到
     <data_root>/<qq>/nt_qq/nt_data 下（或用 opts["media_root"] 覆盖根目录）
"""
from __future__ import annotations

import hashlib
import heapq
import json
import os
import re
import shutil
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from . import paths, store

try:  # 防御式：core/media.py 由 task-11 提供；未就绪时用内置回退，绝不崩
    from . import media as media_mod  # type: ignore
except Exception:  # pragma: no cover
    media_mod = None

__all__ = ["export", "FORMATS", "MODES"]

FORMATS: tuple[str, ...] = ("html", "txt", "md")
MODES: tuple[str, ...] = ("per_peer", "merged")
MAX_FILENAME = 120
DEFAULT_BATCH = 2000
DEFAULT_MEDIA_MAX_MB = 500.0
MEDIA_DIR = "media"

# 需要本地文件的媒体类型；card 只展示语义，不需要文件
FILE_KINDS = ("image", "voice", "video", "file", "sticker")
ALL_KINDS = FILE_KINDS + ("card",)

_MISSING_BADGE = {
    "image": "[图片·未缓存]",
    "voice": "[语音·未缓存]",
    "video": "[视频·未缓存]",
    "file": "[文件·未缓存]",
    "sticker": "[表情·未缓存]",
    "card": "[卡片]",
}
_MD_HEAD = {
    "image": "\U0001F5BC 图片",
    "voice": "\U0001F3A4 语音",
    "video": "\U0001F3AC 视频",
    "sticker": "\U0001F5BC 表情",
    "file": "\U0001F4CE 文件",
    "card": "\U0001F4CE 卡片",
}
_PLAY_ICON = "\u25b6"
_PIN_ICON = "\U0001F4CE"

_ILLEGAL = set('\\/:*?"<>|')
# 零宽空格：只在「渲染出来的纯文本」里打断 ://、src= 等字样，
# 保证任何聊天内容都不会让自包含文件被误判成「有外链」。浏览器显示不可见。
_ZW = "\u200b"
_RE_URLSCHEME = re.compile(r"(?i)://")
_RE_ATTR = re.compile(r"(?i)\b(src|href|action|formaction|srcset)\s*=")
_RESERVED = {"CON", "PRN", "AUX", "NUL",
             *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


# -- 通用工具 ---------------------------------------------------------------
def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _fmt_time(ts: Any) -> str:
    """Unix 秒 -> 本地时间 YYYY-MM-DD HH:MM。"""
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "-"


def _fmt_day(ts: Any) -> str:
    return _fmt_time(ts)[:10]


def _split_lines(text: Any) -> list[str]:
    s = "" if text is None else str(text)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return s.split("\n")


def _fmt_size(n: Any) -> str:
    """1234567 -> 1.2 MB。"""
    try:
        n = int(n)
    except Exception:
        return ""
    if n <= 0:
        return ""
    units = ("B", "KB", "MB", "GB", "TB")
    v = float(n)
    i = 0
    while v >= 1024 and i < len(units) - 1:
        v /= 1024.0
        i += 1
    return f"{n} B" if i == 0 else f"{v:.1f} {units[i]}"


def _h(s: Any) -> str:
    """HTML 转义 + 打断可能被误判为「外链」的字样。"""
    s = "" if s is None else str(s)
    s = (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
           .replace('"', "&quot;").replace("'", "&#39;"))
    s = _RE_URLSCHEME.sub(":" + _ZW + "//", s)
    s = _RE_ATTR.sub(lambda m: m.group(0).replace("=", _ZW + "="), s)
    return s


def _md(s: Any) -> str:
    """Markdown 特殊字符转义（反斜杠 / 反引号 / 星号 / 下划线 / 竖线）。"""
    s = "" if s is None else str(s)
    s = s.replace("\\", "\\\\")
    for ch in ("`", "*", "_", "|"):
        s = s.replace(ch, "\\" + ch)
    return s

# -- 文件名净化 -------------------------------------------------------------
def _sanitize(value: Any) -> str:
    s = "" if value is None else str(value)
    s = re.sub(r"[\x00-\x1f\x7f]", "", s)
    s = "".join("_" if ch in _ILLEGAL else ch for ch in s)
    s = re.sub(r"\s+", " ", s).strip().rstrip(" .")
    if not s:
        s = "_"
    stem = s.split(".")[0].upper()
    if stem in _RESERVED:
        s = "_" + s
    return s


def _unique_filename(display_name: Any, kind: Any, peer_id: Any, ext: str,
                     used: set[str]) -> str:
    """生成 <安全名字>_<kind>_<peer_id>.<ext>，≤120 字符，重名加 _2/_3。"""
    base = _sanitize(display_name) or "会话"
    kind_s = _sanitize(kind) or "peer"
    peer_s = _sanitize(peer_id) or "0"
    suffix = f"_{kind_s}_{peer_s}.{ext}"
    if len(suffix) > MAX_FILENAME - 12:
        h = hashlib.md5(str(peer_id).encode("utf-8")).hexdigest()[:8]
        peer_s = ((peer_s[:40] + "_" + h) if peer_s else h)
        suffix = f"_{kind_s[:16]}_{peer_s}.{ext}"
    room = max(1, MAX_FILENAME - len(suffix))
    base = base[:room] or "_"
    name = base + suffix
    i = 2
    while name.lower() in used:
        tag = f"_{i}"
        b = (base[: max(1, room - len(tag))] or "_") + tag
        name = b + suffix
        i += 1
    if len(name) > MAX_FILENAME:
        name = name[:MAX_FILENAME]
    used.add(name.lower())
    return name


def _media_name(msg_id: Any, orig: Any, used: set[str]) -> str:
    """媒体文件命名：m<msg_id>_<安全原名>，≤120 字符，重名去重。"""
    safe = _sanitize(Path(str(orig or "")).name) or "media"
    base = f"m{msg_id}_{safe}"
    if len(base) > MAX_FILENAME:
        stem, dot, ext = base.rpartition(".")
        if dot and len(ext) <= 8:
            base = stem[:MAX_FILENAME - len(ext) - 1] + "." + ext
        else:
            base = base[:MAX_FILENAME]
    name = base
    i = 2
    while name.lower() in used:
        stem, dot, ext = name.rpartition(".")
        tag = f"_{i}"
        if dot:
            stem2, dot2, ext2 = base.rpartition(".")
            name = stem2[: max(1, MAX_FILENAME - len(ext2) - 1 - len(tag))] + tag + "." + ext2
        else:
            name = base[: max(1, MAX_FILENAME - len(tag))] + tag
        i += 1
    used.add(name.lower())
    return name

# -- 会话信息 ---------------------------------------------------------------
def _contact_info(account_qq: int, kind: str, peer_id: str) -> dict:
    """名字规则：remark or name or str(peer_qq) or peer_id；顺带带上消息数/最后时间。"""
    contact: dict = {}
    try:
        contact = store.get_contact(account_qq, kind, peer_id) or {}
    except Exception:
        contact = {}
    peer_qq = contact.get("peer_qq")
    if not peer_qq:
        try:
            rows = store.list_messages(account_qq, kind, peer_id, limit=1, order="DESC")
        except Exception:
            rows = []
        if rows:
            peer_qq = rows[0].get("peer_qq")
    name = contact.get("remark") or contact.get("name")
    if not name and peer_qq:
        name = str(peer_qq)
    if not name:
        name = str(peer_id)
    nick = contact.get("name")
    if not nick and peer_qq:
        nick = str(peer_qq)
    if not nick:
        nick = str(peer_id)
    msg_count = _to_int(contact.get("msg_count"), 0)
    if not msg_count:
        try:
            msg_count = int(store.count_messages(account_qq, kind, peer_id) or 0)
        except Exception:
            msg_count = 0
    return {
        "name": str(name),
        "nick": str(nick),
        "peer_qq": peer_qq,
        "remark": contact.get("remark"),
        "source": contact.get("source"),
        "msg_count": msg_count,
        "last_ts": _to_int(contact.get("last_ts"), 0),
    }


# -- 消息迭代（分页，内存安全） ----------------------------------------------
def _iter_peer(account_qq: int, kind: str, peer_id: str, batch: int,
               since: Any, until: Any) -> Iterator[dict]:
    offset = 0
    while True:
        rows = store.list_messages(account_qq, kind, peer_id, limit=batch,
                                   offset=offset, order="ASC",
                                   since=since, until=until)
        if not rows:
            return
        yield from rows
        if len(rows) < batch:
            return
        offset += len(rows)


def _iter_session(sess: dict, batch: int, since: Any, until: Any) -> Iterator[dict]:
    aq = sess["account_qq"]
    targets = sess["targets"]
    key = lambda m: (int(m.get("ts") or 0), str(m.get("kind") or ""),
                     str(m.get("peer_id") or ""))
    if len(targets) == 1:
        t = targets[0]
        yield from _iter_peer(aq, t["kind"], t["peer_id"], batch, since, until)
        return
    if sess.get("sectioned"):
        # merged：私聊整体在前、群聊整体在后，中间插分节标记
        c2c = [t for t in targets if t["kind"] == "c2c"]
        grp = [t for t in targets if t["kind"] != "c2c"]
        for label, group in (("私聊", c2c), ("群聊", grp)):
            if not group:
                continue
            yield {"_section": label}
            gens = [_iter_peer(aq, t["kind"], t["peer_id"], batch, since, until)
                    for t in group]
            yield from heapq.merge(*gens, key=key)
        return
    gens = [_iter_peer(aq, t["kind"], t["peer_id"], batch, since, until)
            for t in targets]
    yield from heapq.merge(*gens, key=key)


def _label_for(m: dict, sess: dict) -> str:
    if int(m.get("direction") or 0) == 1:
        return "我"
    sender = m.get("sender_name")
    if sender:
        return str(sender)
    key = (str(m.get("kind") or ""), str(m.get("peer_id") or ""))
    return sess["names"].get(key) or "对方"

# -- 媒体解析 / 定位 --------------------------------------------------------
def _norm_kind(kind: Any) -> str:
    k = str(kind or "").strip().lower()
    alias = {
        "ptt": "voice", "audio": "voice", "voice": "voice",
        "pic": "image", "photo": "image", "image": "image", "img": "image",
        "video": "video", "mp4": "video",
        "file": "file", "doc": "file", "document": "file",
        "emoji": "sticker", "face": "sticker", "sticker": "sticker",
        "card": "card", "link": "card", "json": "card", "mini": "card",
    }
    return alias.get(k, "")


def _parse_media(raw: Any) -> dict | None:
    if not raw:
        return None
    obj = raw
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
        except Exception:
            return None
    if not isinstance(obj, dict):
        return None
    return obj if _norm_kind(obj.get("kind") or obj.get("type")) else None


def _media_rel(md: dict) -> str:
    for key in ("file", "path", "abs_path", "local", "cache"):
        v = md.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _media_root(account_qq: Any, media_root: Any = None) -> Path:
    if media_root:
        return Path(str(media_root))
    return paths.DEFAULT_DATA_ROOT / str(int(account_qq or 0)) / "nt_qq" / "nt_data"


def _norm_file(p: Any) -> str | None:
    if not p:
        return None
    try:
        path = Path(str(p))
        if path.is_file():
            return str(path.resolve())
    except Exception:
        pass
    return None


def _resolve_media(account_qq: Any, md: dict, media_root: Any = None,
                   resolver=None) -> str | None:
    """三级解析：显式 resolver -> core.media.resolve -> 内置相对路径回退。"""
    if resolver is not None:
        try:
            return _norm_file(resolver(int(account_qq or 0), md))
        except Exception:
            return None
    if media_mod is not None and hasattr(media_mod, "resolve"):
        try:
            return _norm_file(media_mod.resolve(int(account_qq or 0), md))
        except Exception:
            return None
    rel = _media_rel(md)
    if not rel:
        return None
    p = Path(rel)
    if not p.is_absolute():
        p = _media_root(account_qq, media_root) / rel
    return _norm_file(p)


def _to_int(v: Any, default: int = 0) -> int:
    try:
        if v in (None, ""):
            return default
        return int(float(v))
    except Exception:
        return default


def _opt_int(v: Any):
    """可选整数参数：空/非法 -> None。"""
    if v in (None, ""):
        return None
    try:
        return int(float(v))
    except Exception:
        return None


def _build_media_entry(md: dict, local: str | None) -> dict:
    kind = _norm_kind(md.get("kind") or md.get("type"))
    name = str(md.get("name") or md.get("filename") or "").strip()
    if not name:
        name = Path(_media_rel(md)).name or ""
    return {
        "kind": kind,
        "name": name,
        "size": _to_int(md.get("size"), 0),
        "duration": _to_int(md.get("duration"), 0),
        "local": local,               # 例如 "media/m12_pic.jpg"；None = 本地缺失
        "missing": local is None,
        "fallback": str(md.get("fallback") or "").strip(),
        "voice_text": str(md.get("voice_text") or "").strip(),
        "voice_lang": str(md.get("voice_lang") or "").strip(),
        "voice_engine": str(md.get("voice_engine") or "").strip(),
    }

def _scan(sess: dict, batch: int, since: Any, until: Any,
          media_enabled: bool = True) -> dict:
    total = rendered = text_n = media_n = self_n = peer_n = skipped = 0
    first_ts = last_ts = None
    source = None
    kinds: dict = {"c2c": 0, "group": 0}
    for m in _iter_session(sess, batch, since, until):
        if m.get("_section"):
            continue
        total += 1
        ts = _to_int(m.get("ts"), 0)
        if first_ts is None or ts < first_ts:
            first_ts = ts
        if last_ts is None or ts > last_ts:
            last_ts = ts
        if source is None:
            source = m.get("source")
        txt = m.get("text")
        has_text = bool(txt is not None and str(txt).strip())
        md = _parse_media(m.get("media")) if media_enabled else None
        has_media = md is not None
        if has_text:
            text_n += 1
        if has_media:
            media_n += 1
        if has_text or has_media:
            rendered += 1
            k = str(m.get("kind") or "c2c")
            kinds[k] = kinds.get(k, 0) + 1
            if int(m.get("direction") or 0) == 1:
                self_n += 1
            else:
                peer_n += 1
        else:
            skipped += 1
    return {"total": total, "rendered": rendered, "text": text_n, "media": media_n,
            "self": self_n, "peer": peer_n, "skipped": skipped,
            "first_ts": first_ts, "last_ts": last_ts, "source": source,
            "kinds": kinds}


def _build_ctx(sess: dict, stats: dict, media_enabled: bool = True) -> dict:
    info = sess.get("info") or {}
    src = info.get("source") or stats.get("source") or "-"
    rng = "-"
    if stats["first_ts"] is not None:
        rng = f'{_fmt_time(stats["first_ts"])} ~ {_fmt_time(stats["last_ts"])}'
    kind = sess["file_kind"]
    if kind == "merged":
        k = stats.get("kinds") or {}
        type_label = f'合并（私聊 {k.get("c2c", 0)} / 群聊 {k.get("group", 0)}）'
    else:
        type_label = "私聊" if kind == "c2c" else "群聊"
    return {
        "name": sess["display"],
        "nick": info.get("nick") or sess["display"],
        "remark": info.get("remark"),
        "type_label": type_label,
        "peer_qq": info.get("peer_qq"),
        "kind": kind,
        "peer_id": sess["file_peer"],
        "account_qq": sess["account_qq"],
        "msg_text": stats["text"],
        "msg_media": stats["media"],
        "msg_rendered": stats["rendered"],
        "msg_all": stats["total"],
        "self_count": stats["self"],
        "peer_count": stats["peer"],
        "skipped": stats["skipped"],
        "range": rng,
        "export_time": _now_str(),
        "source": src,
        "media_enabled": media_enabled,
        "has_media": stats["media"] > 0,
        "kinds": stats.get("kinds") or {},
    }

# -- 三种格式的渲染器 --------------------------------------------------------
class _HtmlWriter:
    fmt = "html"

    def __init__(self, fh, ctx: dict):
        self.fh = fh
        self.ctx = ctx
        self._day = None

    def message(self, m: dict, label: str, media: dict | None = None) -> None:
        txt = m.get("text")
        text_html = _h(txt) if (txt is not None and str(txt).strip()) else ""
        media_html = _media_html(media) if media else ""
        if not text_html and not media_html:
            return
        day = _fmt_day(m.get("ts"))
        if day != self._day:
            self._day = day
            self.fh.write(f'<div class="day"><span>{_h(day)}</span></div>\n')
        cls = "self" if int(m.get("direction") or 0) == 1 else "peer"
        inner = ""
        if text_html:
            inner += f'<div class="text">{text_html}</div>'
        if media_html:
            inner += media_html
        self.fh.write(
            f'<div class="msg {cls}"><div class="bubble">'
            f'<div class="meta"><span class="who">{_h(label)}</span>'
            f'<span class="time">{_fmt_time(m.get("ts"))}</span></div>'
            f'{inner}</div></div>\n')

    def section(self, title: str) -> None:
        self.fh.write(f'<div class="section"><span>===== {_h(title)} =====</span></div>\n')

    def header_text(self) -> str:
        return _html_header(self.ctx)

    def footer_text(self) -> str:
        return _html_footer(self.ctx)


class _TxtWriter:
    fmt = "txt"

    def __init__(self, fh, ctx: dict):
        self.fh = fh
        self.ctx = ctx

    def message(self, m: dict, label: str, media: dict | None = None) -> None:
        t = _fmt_time(m.get("ts"))
        txt = m.get("text")
        has_text = bool(txt is not None and str(txt).strip())
        if has_text:
            lines = _split_lines(txt)
            self.fh.write(f"{t}  {label}：{lines[0]}\n")
            for cont in lines[1:]:
                self.fh.write(f"    {cont}\n")
        if media:
            ph = _txt_media(media)
            if has_text:
                self.fh.write(f"    {ph}\n")
            else:
                self.fh.write(f"{t}  {label}：{ph}\n")

    def section(self, title: str) -> None:
        self.fh.write(f"\n===== {title} =====\n")

    def header_text(self) -> str:
        return _txt_header(self.ctx)

    def footer_text(self) -> str:
        return ""


class _MdWriter:
    fmt = "md"

    def __init__(self, fh, ctx: dict):
        self.fh = fh
        self.ctx = ctx
        self._day = None

    def message(self, m: dict, label: str, media: dict | None = None) -> None:
        txt = m.get("text")
        has_text = bool(txt is not None and str(txt).strip())
        if not has_text and not media:
            return
        day = _fmt_day(m.get("ts"))
        if day != self._day:
            self._day = day
            self.fh.write(f"\n## {day}\n\n")
        t = _fmt_time(m.get("ts"))
        hhmm = t[11:16] if len(t) >= 16 else t
        if media and media.get("kind") == "voice":
            self.fh.write("> " + _md_voice_header(label, hhmm, media) + "\n")
            if has_text:
                self.fh.write("> " + "\n> ".join(_md(x) for x in _split_lines(txt)) + "\n")
            vt = str(media.get("voice_text") or "").strip()
            if vt:
                self.fh.write("> " + "\n> ".join(_md(x) for x in _split_lines(vt)) + "\n")
            self.fh.write("\n")
            return
        self.fh.write(f"> **{_md(label)}** · {hhmm}\n")
        if has_text:
            content = "\n> ".join(_md(x) for x in _split_lines(txt))
            self.fh.write(f"> {content}\n")
        if media:
            self.fh.write("> " + _md_media(media) + "\n")
        self.fh.write("\n")

    def section(self, title: str) -> None:
        self.fh.write(f"\n## ===== {_md(title)} =====\n\n")

    def header_text(self) -> str:
        return _md_header(self.ctx)

    def footer_text(self) -> str:
        return ""


_WRITERS = {"html": _HtmlWriter, "txt": _TxtWriter, "md": _MdWriter}

# -- 媒体在三种格式里的呈现 --------------------------------------------------
def _voice_dur(md: dict) -> str:
    return f'{md["duration"]}"' if md.get("duration") else ""


def _voice_html(md: dict) -> str:
    """语音条目：识别文字是主体，播放器缩小为辅。

    - 有 voice_text：正常字号、可选中；voice_lang 非中文时加小标记
    - 无 voice_text：[语音 3"·未转文字] 占位徽章
    - 音频本地缺失：不渲染 <audio>，但文字照常显示
    """
    dur = _voice_dur(md)
    text = str(md.get("voice_text") or "").strip()
    lang = str(md.get("voice_lang") or "").strip().lower()
    parts = []
    if text:
        parts.append(f'<span class="voice-text">{_h(text)}</span>')
        if lang and lang not in ("zh", "zh-cn", "zh-hans", "zh-hant", "chinese", "cmn"):
            parts.append('<span class="voice-lang">可能非中文</span>')
    else:
        label = f'[语音 {dur}·未转文字]' if dur else '[语音·未转文字]'
        parts.append(f'<span class="voice-badge">{_h(label)}</span>')
    local = md.get("local")
    if local:
        parts.append('<div class="voice-player">'
                     f'<audio controls preload="none" src="{_h(local)}"></audio>'
                     f'<span class="voice-dur">{_h(dur)}</span></div>')
    return '<div class="media voice">' + "".join(parts) + '</div>'


def _media_html(md: dict) -> str:
    kind = md.get("kind") or ""
    if kind == "voice":
        return _voice_html(md)
    local = md.get("local")
    if not local:
        badge = _MISSING_BADGE.get(kind, "[媒体·未缓存]")
        extra = ""
        if kind == "card" and md.get("fallback"):
            extra = " " + _h(md["fallback"])
        return f'<div class="media"><span class="m-badge">{_h(badge)}</span>{extra}</div>'
    url = _h(local)
    name = _h(md.get("name") or "")
    if kind in ("image", "sticker"):
        cls = "m-img m-sticker" if kind == "sticker" else "m-img"
        return f'<div class="media"><img class="{cls}" src="{url}" loading="lazy" alt="{name}"></div>'
    if kind == "video":
        return ('<div class="media m-video-wrap">'
                f'<img class="m-img m-video" src="{url}" loading="lazy" alt="{name}">'
                f'<span class="m-play">{_PLAY_ICON}</span></div>')
    if kind == "file":
        return ('<div class="media"><a class="m-file" href="' + url + '" download>'
                f'<span class="m-ico">{_PIN_ICON}</span>'
                f'<span class="m-name">{name or "文件"}</span>'
                f'<span class="m-size">{_fmt_size(md.get("size"))}</span></a></div>')
    if kind == "card":
        fb = _h(md.get("fallback") or "卡片")
        return f'<div class="media"><span class="m-badge">{fb}</span></div>'
    return f'<div class="media"><span class="m-badge">{_h(_MISSING_BADGE.get(kind, "[媒体]"))}</span></div>'


def _txt_media(md: dict) -> str:
    kind = md.get("kind") or ""
    if kind == "voice":
        dur = f' {md["duration"]}"' if md.get("duration") else ""
        head = f"[语音{dur}]"
        vt = str(md.get("voice_text") or "").strip()
        return f"{head} {vt}" if vt else head
    if kind == "image":
        return "[图片]"
    if kind == "sticker":
        return "[表情]"
    if kind == "video":
        return "[视频]"
    if kind == "file":
        name = md.get("name") or "文件"
        size = _fmt_size(md.get("size"))
        return f"[文件: {name} ({size})]" if size else f"[文件: {name}]"
    return md.get("fallback") or "[卡片]"


def _md_media(md: dict) -> str:
    """Markdown 媒体行（已是 blockquote 内容，调用方补 > 前缀）。"""
    kind = md.get("kind") or ""
    name = md.get("name") or ""
    head = _MD_HEAD.get(kind, "媒体")
    if kind == "voice":
        if md.get("duration"):
            head += f' {md["duration"]}"'
        vt = str(md.get("voice_text") or "").strip()
        if vt:
            head += " " + _md(vt)
    elif kind == "file" and name:
        head += ": " + _md(name)
        size = _fmt_size(md.get("size"))
        if size:
            head += f" ({size})"
    elif kind == "card":
        head = _md(md.get("fallback") or "卡片")
    if md.get("local"):
        head += f' [打开]({str(md["local"]).replace(" ", "%20")})'
    return head

def _md_voice_header(label: str, hhmm: str, md: dict) -> str:
    """语音的 Markdown 行：说话人 / 时间 / 时长一行，识别文字另起引用块。"""
    dur = f' {md["duration"]}"' if md.get("duration") else ""
    return f"\U0001F3A4 **{_md(label)}** · {hhmm} · 语音{dur}"

# -- 模板 -------------------------------------------------------------------
_CSS = """
:root{
  --bg:#f2f2f7; --fg:#1c1c1e; --card:#ffffff; --muted:#6b6b70; --line:#d8d8dc;
  --peer-bg:#e9e9eb; --peer-fg:#1c1c1e; --self-bg:#0a7cff; --self-fg:#ffffff;
  --accent:#0a7cff;
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#0b0b0d; --fg:#f2f2f7; --card:#1c1c1e; --muted:#9a9aa0; --line:#38383a;
    --peer-bg:#2c2c2e; --peer-fg:#f2f2f7; --self-bg:#0a84ff; --self-fg:#ffffff;
    --accent:#0a84ff;
  }
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:var(--bg);color:var(--fg);font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",system-ui,sans-serif}
header.info{position:sticky;top:0;z-index:5;background:var(--card);border-bottom:1px solid var(--line);padding:12px 16px;box-shadow:0 1px 4px rgba(0,0,0,.06)}
header.info h1{margin:0 0 6px;font-size:16px;font-weight:600}
.meta-line{display:flex;flex-wrap:wrap;gap:4px 16px;color:var(--muted);font-size:12px}
.meta-line b{color:var(--fg);font-weight:600}
.toolbar{max-width:900px;margin:0 auto;display:flex;align-items:center;gap:10px;padding:10px 16px 0}
#btn-self{cursor:pointer;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:999px;padding:5px 14px;font-size:13px}
#btn-self[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);color:#fff}
.hint{color:var(--muted);font-size:12px}
.chat{max-width:900px;margin:0 auto;padding:6px 16px 40px}
.day{text-align:center;color:var(--muted);font-size:12px;margin:18px 0 10px}
.day span{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:2px 12px}
.section{text-align:center;margin:20px 0 8px;color:var(--muted);font-size:13px;font-weight:600;letter-spacing:1px}
.section span{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:3px 16px}
.msg{display:flex;margin:6px 0}
.msg.self{justify-content:flex-end}
.msg.peer{justify-content:flex-start}
.bubble{max-width:72%;padding:8px 12px;border-radius:14px;background:var(--peer-bg);color:var(--peer-fg);box-shadow:0 1px 2px rgba(0,0,0,.05)}
.msg.self .bubble{background:var(--self-bg);color:var(--self-fg)}
.bubble .meta{display:flex;gap:8px;font-size:11px;opacity:.78;margin-bottom:3px}
.msg.self .bubble .meta{justify-content:flex-end}
.text{white-space:pre-wrap;word-break:break-word;overflow-wrap:anywhere}
.media{margin-top:6px}
.m-img{display:block;max-width:100%;max-height:320px;border-radius:10px;cursor:zoom-in}
.m-sticker{max-height:140px;cursor:default}
.m-video-wrap{position:relative;display:inline-block}
.m-play{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);font-size:30px;color:#fff;text-shadow:0 1px 5px rgba(0,0,0,.65);pointer-events:none}
.m-voice{display:flex;align-items:center;gap:8px}
.m-voice audio{max-width:240px;height:34px}
.voice{display:flex;flex-direction:column;gap:6px}
.voice-text{font-size:14px;line-height:1.55;white-space:pre-wrap;word-break:break-word;overflow-wrap:anywhere;user-select:text;cursor:text}
.voice-lang{display:inline-block;align-self:flex-start;margin-left:6px;font-size:11px;color:var(--muted);border:1px solid var(--line);border-radius:999px;padding:0 6px}
.msg.self .voice-lang{color:#fff;border-color:rgba(255,255,255,.5)}
.voice-player{display:flex;align-items:center;gap:8px}
.voice-player audio{max-width:220px;height:32px}
.voice-dur{font-size:12px;opacity:.75}
.voice-badge{display:inline-block;align-self:flex-start;padding:3px 10px;border-radius:999px;background:var(--peer-bg);color:var(--muted);border:1px dashed var(--line);font-size:12px}
.msg.self .voice-badge{background:rgba(255,255,255,.18);color:#fff;border-color:rgba(255,255,255,.45)}
.m-meta{font-size:12px;opacity:.8}
.m-file{display:inline-flex;align-items:center;gap:8px;padding:8px 12px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--fg);text-decoration:none}
.m-file:hover{border-color:var(--accent)}
.m-ico{font-size:18px}
.m-name{max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.m-size{color:var(--muted);font-size:12px}
.m-badge{display:inline-block;padding:3px 10px;border-radius:999px;background:var(--peer-bg);color:var(--muted);border:1px dashed var(--line);font-size:12px}
.msg.self .m-badge{background:rgba(255,255,255,.18);color:#fff;border-color:rgba(255,255,255,.45)}
#qs-lightbox{position:fixed;top:0;left:0;right:0;bottom:0;z-index:99;background:rgba(0,0,0,.86);display:none;align-items:center;justify-content:center;cursor:zoom-out}
#qs-lightbox.on{display:flex}
#qs-lightbox img{max-width:92vw;max-height:92vh;border-radius:10px}
footer.foot{max-width:900px;margin:0 auto;padding:8px 16px 40px;color:var(--muted);font-size:12px;text-align:center}
.chat.self-only .msg.peer{display:none}
@media (max-width:640px){.bubble{max-width:86%}header.info h1{font-size:15px}}
"""

_JS = """
(function(){
  var btn = document.getElementById('btn-self');
  var chat = document.getElementById('chat');
  var hint = document.getElementById('hint');
  if (btn && chat) {
    btn.addEventListener('click', function () {
      var on = chat.classList.toggle('self-only');
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
      btn.textContent = on ? '显示全部消息' : '仅看自己发的';
      if (hint) {
        hint.textContent = on ? ('只显示「我」发送的 ' + chat.querySelectorAll('.msg.self').length + ' 条消息') : '';
      }
    });
  }
  var lb = document.getElementById('qs-lightbox');
  var lbImg = document.getElementById('qs-lightbox-img');
  if (lb && lbImg) {
    document.addEventListener('click', function (e) {
      var t = e.target;
      if (t && t.classList && t.classList.contains('m-img')) {
        var u = t.getAttribute('src');
        if (u) { lbImg.setAttribute('src', u); lb.classList.add('on'); }
      } else if (t === lb || t === lbImg) {
        lb.classList.remove('on');
      }
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { lb.classList.remove('on'); }
    });
  }
})();
"""

def _info_html(ctx: dict) -> str:
    name_txt = ctx.get("nick") or ctx["name"]
    if ctx.get("remark"):
        name_txt = f'{name_txt}（备注：{ctx["remark"]}）'
    spans = [
        f'<span>对象：<b>{_h(name_txt)}</b></span>',
        f'<span>类型：<b>{_h(ctx["type_label"])}</b></span>',
        f'<span>QQ号/群号：<b>{_h(ctx["peer_qq"] or "-")}</b></span>',
        f'<span>会话：<b>{_h(ctx["kind"])} / {_h(ctx["peer_id"])}</b></span>',
        f'<span>消息数：<b>{ctx["msg_rendered"]}</b> 条（其中你发的 <b>{ctx["self_count"]}</b> 条；'
        f'文本 {ctx["msg_text"]} · 媒体 {ctx["msg_media"]} · '
        f'另有 {ctx["skipped"]} 条非文本消息被略过）</span>',
        f'<span>时间范围：<b>{_h(ctx["range"])}</b></span>',
        f'<span>导出来源：<b>{_h(ctx["source"])}</b></span>',
        f'<span>导出时间：<b>{_h(ctx["export_time"])}</b></span>',
    ]
    return ('<header class="info">\n'
            f'  <h1>与 {_h(ctx["name"])} 的聊天记录</h1>\n'
            '  <div class="meta-line">\n    ' + "\n    ".join(spans)
            + '\n  </div>\n</header>\n')


def _html_header(ctx: dict) -> str:
    comment = (
        "<!-- QQScope 导出：单文件 HTML + 同级 media/ 目录。\n"
        "     图片/语音/文件都用相对路径 media/xxx 引用，没有任何外部网络请求；\n"
        "     请连同 media/ 文件夹一起拷贝或解压，否则本地缺失的媒体会显示占位徽章。 -->\n"
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>与 {_h(ctx['name'])} 的聊天记录</title>\n"
        + comment
        + "<style>\n" + _CSS + "\n</style>\n</head>\n<body>\n"
        + _info_html(ctx)
        + '<div class="toolbar">'
          '<button id="btn-self" type="button" aria-pressed="false">仅看自己发的</button>'
          '<span class="hint" id="hint"></span>'
          "</div>\n"
          '<div class="chat" id="chat">\n'
    )


def _html_footer(ctx: dict | None = None) -> str:
    lightbox = ""
    if (ctx or {}).get("has_media"):
        lightbox = '<div id="qs-lightbox"><img id="qs-lightbox-img" alt=""></div>\n'
    return ("</div>\n"
            '<footer class="foot">由 QQScope 导出 · 单文件自包含 · 媒体走同级 media/ 目录 · 无外部请求</footer>\n'
            + lightbox
            + "<script>\n" + _JS + "\n</script>\n</body>\n</html>\n")

def _txt_header(ctx: dict) -> str:
    name_txt = ctx.get("nick") or ctx["name"]
    if ctx.get("remark"):
        name_txt = f'{name_txt}（备注：{ctx["remark"]}）'
    lines = [
        "QQScope 聊天记录导出",
        f'对象：{name_txt}',
        f'类型：{ctx["type_label"]}',
        f'QQ号/群号：{ctx["peer_qq"] or "-"}',
        f'会话：{ctx["kind"]} / {ctx["peer_id"]}',
        f'账号：{ctx["account_qq"]}',
        f'消息数：{ctx["msg_rendered"]} 条（其中你发的 {ctx["self_count"]} 条；'
        f'文本 {ctx["msg_text"]}，媒体 {ctx["msg_media"]}；另有 {ctx["skipped"]} 条非文本消息被略过）',
        f'时间范围：{ctx["range"]}',
        f'导出来源：{ctx["source"]}',
        f'导出时间：{ctx["export_time"]}',
        "=" * 60,
        "",
    ]
    return "\n".join(lines) + "\n"


def _md_header(ctx: dict) -> str:
    rows = [("对象", ctx.get("nick") or ctx["name"])]
    if ctx.get("remark"):
        rows.append(("备注", ctx["remark"]))
    rows += [
        ("类型", ctx["type_label"]),
        ("QQ号/群号", ctx["peer_qq"] or "-"),
        ("会话", f'{ctx["kind"]} / {ctx["peer_id"]}'),
        ("账号", ctx["account_qq"]),
        ("消息总数", ctx["msg_all"]),
        ("其中我发送", ctx["self_count"]),
        ("对方发送", ctx["peer_count"]),
        ("文本消息", ctx["msg_text"]),
        ("媒体消息", ctx["msg_media"]),
        ("非文本略过", f'{ctx["skipped"]}（图片/文件等）'),
        ("时间范围", ctx["range"]),
        ("导出来源", ctx["source"]),
        ("导出时间", ctx["export_time"]),
    ]
    lines = [f"# 与 {_md(ctx['name'])} 的聊天记录", "", "| 项目 | 值 |", "| --- | --- |"]
    for k, v in rows:
        lines.append(f"| {_md(k)} | {_md(v)} |")
    lines.append("")
    return "\n".join(lines) + "\n"

# -- 媒体收集 / 渲染 + 落盘 --------------------------------------------------
class _MediaCollector:
    """把命中的媒体复制到 <job>/media/，并汇总统计。"""

    def __init__(self, jobdir: Path, enabled: bool, max_bytes: int | None,
                 media_root: Any = None, resolver=None):
        self.dir = Path(jobdir) / MEDIA_DIR
        self.enabled = bool(enabled)
        self.max_bytes = max_bytes
        self.media_root = media_root
        self.resolver = resolver
        self.used: set[str] = set()
        self.total = 0        # 需要本地文件的媒体条数（不含 card）
        self.cards = 0
        self.voice_total = 0
        self.voice_transcribed = 0
        self.voice_no_text = 0
        self.copied = 0
        self.missing = 0
        self.bytes = 0
        self.truncated = False
        self.files: list[str] = []

    def process(self, account_qq: Any, m: dict) -> dict | None:
        md = _parse_media(m.get("media"))
        if not md:
            return None
        kind = _norm_kind(md.get("kind") or md.get("type"))
        if not kind:
            return None
        if kind == "card":
            self.cards += 1
            return _build_media_entry(md, None)
        if kind not in FILE_KINDS:
            return _build_media_entry(md, None)

        self.total += 1
        if kind == "voice":
            self.voice_total += 1
            if str(md.get("voice_text") or "").strip():
                self.voice_transcribed += 1
            else:
                self.voice_no_text += 1
        path = _resolve_media(account_qq, md, self.media_root, self.resolver)
        rel = None
        if path and self.enabled:
            size = 0
            try:
                size = int(os.path.getsize(path))
            except Exception:
                size = 0
            if self.max_bytes is None or self.bytes + size <= self.max_bytes:
                name = self._copy(m, path, md)
                if name:
                    rel = f"{MEDIA_DIR}/{name}"
                    self.copied += 1
                    self.bytes += size
                    self.files.append(name)
            else:
                self.truncated = True
        if rel is None:
            self.missing += 1
        return _build_media_entry(md, rel)

    def _copy(self, m: dict, path: str, md: dict) -> str | None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return None
        msg_id = m.get("id")
        if msg_id in (None, ""):
            msg_id = _to_int(m.get("ts"), 0)
        orig = md.get("name") or md.get("filename") or Path(path).name
        name = _media_name(msg_id, orig, self.used)
        try:
            shutil.copyfile(path, self.dir / name)
            return name
        except Exception:
            return None


def _render_session(sess: dict, fmts: list[str], jobdir, names_map: dict,
                    ctx: dict, batch: int, since: Any, until: Any,
                    collector: "_MediaCollector | None" = None) -> list:
    """把一次会话渲染成若干格式：正文先写 .part 临时文件，再拼上表头/表尾。"""
    parts: dict = {}
    writers: dict = {}
    handles = []
    try:
        for fmt in fmts:
            pp = jobdir / (names_map[fmt] + ".part")
            fh = open(pp, "w", encoding="utf-8", newline="\n")
            handles.append(fh)
            parts[fmt] = pp
            writers[fmt] = _WRITERS[fmt](fh, ctx)
        for m in _iter_session(sess, batch, since, until):
            if m.get("_section"):
                for fmt in fmts:
                    writers[fmt].section(m["_section"])
                continue
            txt = m.get("text")
            has_text = bool(txt is not None and str(txt).strip())
            media = None
            if collector is not None and collector.enabled:
                media = collector.process(sess["account_qq"], m)
            if not has_text and not media:
                continue
            label = _label_for(m, sess)
            for fmt in fmts:
                writers[fmt].message(m, label, media)
    finally:
        for fh in handles:
            try:
                fh.close()
            except Exception:
                pass

    out: list = []
    for fmt in fmts:
        final = jobdir / names_map[fmt]
        enc = "utf-8-sig" if fmt == "txt" else "utf-8"
        with open(final, "w", encoding=enc, newline="\n") as f:
            f.write(writers[fmt].header_text())
            with open(parts[fmt], "r", encoding="utf-8", newline="") as bf:
                shutil.copyfileobj(bf, f, length=262144)
            f.write(writers[fmt].footer_text())
        parts[fmt].unlink(missing_ok=True)
        out.append((fmt, final))
    return out


def _new_job_id() -> str:
    return "exp_" + time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]


def job_meta(job: str, account_qq=None) -> dict | None:
    """读取导出任务元数据（含 account_qq）。

    task-11：导出目录已按账号拆分 data/accounts/<qq>/export/_meta/<job>.json；
    account_qq 给定 -> 只查该账号；不传 -> 遍历各账号目录（迁移前兼容全局 data/export）。"""
    roots = []
    if account_qq:
        try:
            roots.append(paths.account_export(account_qq, create=False))
        except Exception:  # noqa: BLE001
            pass
    else:
        try:
            roots = [d / "export" for d in paths.ACCOUNTS_DIR.iterdir() if (d / "export").is_dir()]
        except OSError:
            roots = []
        if paths.EXPORT_DIR.is_dir():
            roots.append(paths.EXPORT_DIR)   # 迁移前/未归属的旧任务
    for root in roots:
        try:
            p = root / "_meta" / f"{str(job)}.json"
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:  # noqa: BLE001
            continue
    return None

# -- 对外契约 ---------------------------------------------------------------
def export(account_qq, targets, formats, mode: str = "per_peer", opts: dict | None = None) -> dict:
    """导出会话到 data/export/<job>/ 并打包 zip（含 media/ 目录）。

    返回 {"ok","job","dir","files":[...],"zip","count","total","skipped",
          "media":{total,copied,missing,bytes,cards,truncated},"truncated", ...}
    """
    opts = dict(opts or {})
    account_qq = int(account_qq)
    media_enabled = bool(opts.get("media", True))
    try:
        media_max_mb = float(opts.get("media_max_mb", DEFAULT_MEDIA_MAX_MB))
    except Exception:
        media_max_mb = DEFAULT_MEDIA_MAX_MB
    max_bytes = None if media_max_mb < 0 else int(max(0.0, media_max_mb) * 1024 * 1024)
    media_root = opts.get("media_root")
    resolver = opts.get("media_resolver")
    if not callable(resolver):
        resolver = None

    if not targets:
        raise ValueError("targets 不能为空")
    if not formats:
        raise ValueError("formats 不能为空")

    fmts: list[str] = []
    for f in formats:
        f = str(f).lower().strip()
        if f not in FORMATS:
            raise ValueError(f"不支持的导出格式：{f}（可选 html/txt/md）")
        if f not in fmts:
            fmts.append(f)

    mode = str(mode or "per_peer")
    if mode not in MODES:
        raise ValueError(f"不支持的导出模式：{mode}（可选 per_peer/merged）")

    try:
        batch = int(opts.get("batch") or DEFAULT_BATCH)
    except Exception:
        batch = DEFAULT_BATCH
    batch = max(100, min(batch, 50000))
    all_time = bool(opts.get("all_time"))
    since = None if all_time else _opt_int(opts.get("since"))
    until = None if all_time else _opt_int(opts.get("until"))

    clean: list[dict] = []
    seen = set()
    for t in targets:
        if not isinstance(t, dict):
            raise ValueError("targets 里每一项都要是 {kind, peer_id} 字典")
        kind = str(t.get("kind") or "c2c").strip().lower()
        peer_id = t.get("peer_id")
        if peer_id is None or str(peer_id) == "":
            raise ValueError("targets 缺少 peer_id")
        if kind not in ("c2c", "group"):
            raise ValueError(f"不支持的会话类型：{kind}")
        key = (kind, str(peer_id))
        if key in seen:
            continue
        seen.add(key)
        clean.append({"kind": kind, "peer_id": str(peer_id)})
    if not clean:
        raise ValueError("targets 去重后为空")

    # 分类排序：私聊在前、群聊在后；同 kind 内按全量消息数降序（再 last_ts、peer_id）
    decorated = []
    for t in clean:
        info = _contact_info(account_qq, t["kind"], t["peer_id"])
        decorated.append((t, info))
    decorated.sort(key=lambda x: (
        0 if x[0]["kind"] == "c2c" else 1,
        -int(x[1].get("msg_count") or 0),
        -int(x[1].get("last_ts") or 0),
        str(x[0]["peer_id"]),
    ))
    clean = [t for t, _ in decorated]
    info_map = {(t["kind"], t["peer_id"]): info for t, info in decorated}

    store.init()

    root = paths.account_export(account_qq)          # task-11：导出产物按账号落目录
    job = str(opts.get("job") or _new_job_id())
    jobdir = root / job
    n = 1
    while jobdir.exists():
        job = f"{job}_{n}"
        jobdir = root / job
        n += 1
    jobdir.mkdir(parents=True, exist_ok=True)
    try:  # 多账号隔离：记录任务归属，供 /api/export/list 与 /download 校验
        meta_dir = root / "_meta"
        meta_dir.mkdir(parents=True, exist_ok=True)
        (meta_dir / f"{job}.json").write_text(
            json.dumps({"job": job, "account_qq": account_qq,
                        "created": int(time.time())}, ensure_ascii=False),
            encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    collector = _MediaCollector(jobdir, media_enabled, max_bytes,
                                media_root=media_root, resolver=resolver)

    if mode == "per_peer":
        sessions = []
        for t in clean:
            info = info_map[(t["kind"], t["peer_id"])]
            sessions.append({
                "account_qq": account_qq,
                "targets": [t],
                "names": {(t["kind"], t["peer_id"]): info["name"]},
                "info": info,
                "display": info["name"],
                "file_base": info["name"],
                "file_kind": t["kind"],
                "file_peer": t["peer_id"],
                "merged": False,
            })
    else:
        names = {k: v["name"] for k, v in info_map.items()}
        srcs = sorted({str(v.get("source")) for v in info_map.values() if v.get("source")})
        base = str(opts.get("merged_name") or "合并导出")
        sessions = [{
            "account_qq": account_qq,
            "targets": clean,
            "names": names,
            "info": {"name": "合并导出", "peer_qq": account_qq,
                     "source": " / ".join(srcs) or None},
            "display": f"合并导出（{len(clean)} 个会话）",
            "file_base": base,
            "file_kind": "merged",
            "file_peer": str(account_qq),
            "merged": True,
            "sectioned": True,
        }]
    used: set[str] = set()
    files: list[dict] = []
    total_all = total_text = total_media = total_rendered = total_skipped = 0
    range_first = range_last = None
    for sess in sessions:
        stats = _scan(sess, batch, since, until, media_enabled)
        total_all += stats["total"]
        total_text += stats["text"]
        total_media += stats["media"]
        total_rendered += stats["rendered"]
        total_skipped += stats["skipped"]
        if stats["first_ts"] is not None:
            range_first = (stats["first_ts"] if range_first is None
                           else min(range_first, stats["first_ts"]))
        if stats["last_ts"] is not None:
            range_last = (stats["last_ts"] if range_last is None
                          else max(range_last, stats["last_ts"]))
        ctx = _build_ctx(sess, stats, media_enabled)
        names_map = {fmt: _unique_filename(sess["file_base"], sess["file_kind"],
                                           sess["file_peer"], fmt, used)
                     for fmt in fmts}
        out = _render_session(sess, fmts, jobdir, names_map, ctx,
                              batch, since, until, collector)
        for fmt, path in out:
            files.append({
                "name": path.name,
                "size": path.stat().st_size,
                "peer": ctx["name"],
                "fmt": fmt,
                "kind": sess["file_kind"],
                "peer_id": sess["file_peer"],
                "target": f'{sess["file_kind"]}:{sess["file_peer"]}',
            })

    zip_path = root / f"{job}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(jobdir / f["name"], arcname=f["name"])
        for mname in collector.files:
            zf.write(collector.dir / mname, arcname=f"{MEDIA_DIR}/{mname}")

    media_stats = {
        "total": collector.total,
        "copied": collector.copied,
        "missing": collector.missing,
        "bytes": collector.bytes,
        "cards": collector.cards,
        "voice_total": collector.voice_total,
        "voice_transcribed": collector.voice_transcribed,
        "voice_no_text": collector.voice_no_text,
        "truncated": collector.truncated,
        "dir": MEDIA_DIR,
    }
    return {
        "ok": True,
        "job": job,
        "dir": str(jobdir.resolve()),
        "files": files,
        "zip": str(zip_path.resolve()),
        "count": total_rendered,
        "exported": total_rendered,
        "text_count": total_text,
        "media_count": total_media,
        "total": total_all,
        "skipped": total_skipped,
        "files_count": len(files),
        "media": media_stats,
        "truncated": collector.truncated,
        "range": {"since": since, "until": until, "first_ts": range_first,
                  "last_ts": range_last, "count": total_all},
        "mode": mode,
        "formats": fmts,
        "targets": clean,
        "media_enabled": media_enabled,
    }
