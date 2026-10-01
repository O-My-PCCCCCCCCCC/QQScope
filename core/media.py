# -*- coding: utf-8 -*-
"""QQScope · 本地媒体索引与定位（SPEC 第 10 章）

职责
----
* 扫描 <data_root>/<qq>/nt_qq/nt_data 下的 Ptt / Pic / Video / File / Emoji，
  建立 md5 -> 绝对路径 索引（进程内缓存，默认 5 分钟）
* 把 messages.media 里的 md5 / file(相对 nt_data 的路径) 解析成真实文件
* 统计 store 里各类媒体的命中率、列出本地缺失清单
* 估算 AMR 语音时长

约定
----
* 文件名去扩展名后小写比对；`_0` / `_720` / `_750` / `_thumb` 后缀先去掉
* 同名多份时优先 Ori（原文件），其次 Thumb
* media.file 一律写相对 nt_data 的 POSIX 路径，例如 Ptt/2026-08/Ori/xxx.amr
* 纯标准库实现，不引入第三方依赖
"""
from __future__ import annotations

import json
import mimetypes
import re
import threading
import time
from pathlib import Path

from core import paths, store

DATA_ROOT: Path = paths.DEFAULT_DATA_ROOT
_MEDIA_DIRS = ("Ptt", "Pic", "Video", "File", "Emoji")
_SUFFIX_RE = re.compile(r"_(0|720|750|thumb)$", re.I)
_MD5_RE = re.compile(r"^[0-9a-f]{32}$")
_CACHE_TTL = 300.0

_cache: dict[str, dict] = {}
_lock = threading.RLock()
_data_root: Path | None = None


# ── 基础 ────────────────────────────────────────────────────────────────────
def set_data_root(root) -> None:
    """覆盖 Tencent Files 根目录（pack_source 的 opts["data_root"] 会调）。"""
    global _data_root
    new = Path(root) if root else None
    if new != _data_root:
        _data_root = new
        clear_cache()


def data_root() -> Path:
    return _data_root or DATA_ROOT


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def nt_data_root(account_qq) -> Path | None:
    """返回 <root>/<qq>/nt_qq/nt_data；不存在返回 None。"""
    p = data_root() / str(account_qq) / "nt_qq" / "nt_data"
    try:
        return p if p.is_dir() else None
    except OSError:
        return None


def _score(path: Path) -> tuple:
    """同名文件打分：Ori > Thumb，原文件优先。"""
    parts = {x.lower() for x in path.parts}
    return (1 if "ori" in parts else 0,
            0 if "thumb" in parts else 1,
            -len(path.parts))


def _scan(root: Path) -> tuple[dict, dict]:
    index: dict[str, tuple] = {}
    per_dir: dict[str, int] = {}
    for d in _MEDIA_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        try:
            files = list(base.rglob("*"))
        except OSError:
            continue
        for f in files:
            try:
                if not f.is_file():
                    continue
            except OSError:
                continue
            stem = _SUFFIX_RE.sub("", f.stem).lower()
            if not _MD5_RE.match(stem):
                continue
            per_dir[d] = per_dir.get(d, 0) + 1
            score = _score(f)
            prev = index.get(stem)
            if prev is None or score > prev[0]:
                index[stem] = (score, str(f))
    return {k: v[1] for k, v in index.items()}, per_dir


def get_index(account_qq, force: bool = False) -> dict:
    """md5 -> 绝对路径（进程内缓存）。"""
    key = str(account_qq)
    now = time.time()
    with _lock:
        ent = _cache.get(key)
        if ent and not force and now - ent["at"] < _CACHE_TTL:
            return ent["index"]
    root = nt_data_root(account_qq)
    if root is None:
        index, per_dir = {}, {}
    else:
        index, per_dir = _scan(root)
    with _lock:
        _cache[key] = {"index": index, "per_dir": per_dir,
                       "root": str(root) if root else None,
                       "count": len(index), "at": now}
    return index


def _entry(account_qq) -> dict:
    get_index(account_qq)
    with _lock:
        return dict(_cache.get(str(account_qq)) or {})


# ── 解析 ────────────────────────────────────────────────────────────────────
def resolve(account_qq, media: dict | None) -> str | None:
    """media -> 本地绝对路径；找不到返回 None。"""
    if not media:
        return None
    root = nt_data_root(account_qq)
    if root is None:
        return None
    rel = media.get("file")
    if rel:
        try:
            cand = Path(str(rel))
            if not cand.is_absolute():
                cand = root / str(rel)
            if cand.is_file():
                return str(cand)
        except OSError:
            pass
    md5 = str(media.get("md5") or "").strip().lower()
    if _MD5_RE.match(md5):
        p = get_index(account_qq).get(md5)
        if p:
            try:
                if Path(p).is_file():
                    return p
            except OSError:
                pass
    return None


def rel_path(account_qq, abs_path) -> str | None:
    """绝对路径 -> 相对 nt_data 的 POSIX 路径。"""
    root = nt_data_root(account_qq)
    if not root or not abs_path:
        return None
    try:
        return Path(abs_path).relative_to(root).as_posix()
    except (ValueError, OSError):
        return None


def fill_media(account_qq, media: dict | None) -> dict | None:
    """补齐 media.file（相对路径）与语音时长，返回新 dict。"""
    if not media:
        return None
    m = dict(media)
    path = resolve(account_qq, m)
    m["file"] = rel_path(account_qq, path) if path else None
    if m.get("kind") == "voice":
        sec = m.get("duration")
        if path:
            d = voice_duration(path)
            if d:
                sec = d
        if not sec:
            sec = estimate_voice_seconds(m.get("size"))
        m["duration"] = sec
    return m


# ── 语音时长 ────────────────────────────────────────────────────────────────
_AMR_NB = (13, 14, 16, 18, 20, 21, 27, 32, 6, 0, 0, 0, 0, 0, 0, 1)
_AMR_WB = (18, 24, 33, 37, 41, 47, 51, 59, 61, 6, 6, 0, 0, 0, 0, 1)


def amr_duration(path) -> int | None:
    """按 AMR 帧头精确计算时长（秒）；不是 AMR 或解析失败返回 None。"""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if data.startswith(b"#!AMR-WB\n"):
        off, table = 9, _AMR_WB
    elif data.startswith(b"#!AMR\n"):
        off, table = 6, _AMR_NB
    else:
        return None
    frames = 0
    total = len(data)
    while off < total:
        ft = (data[off] >> 3) & 0x0F
        size = table[ft]
        if size <= 0 or off + size > total:
            break
        off += size
        frames += 1
    if frames <= 0:
        return None
    return max(1, int(round(frames * 0.02)))


def silk_duration(path) -> int | None:
    """SILK v3（QQ 语音的真实格式，扩展名常写作 .amr）：每帧 20ms，前有 2 字节小端长度。"""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    off = 1 if data[:1] == b"\x02" else 0
    if data[off:off + 9] != b"#!SILK_V3":
        return None
    off += 9
    frames = 0
    total = len(data)
    while off + 2 <= total:
        size = int.from_bytes(data[off:off + 2], "little")
        if size <= 0 or off + 2 + size > total:
            break
        off += 2 + size
        frames += 1
    if frames <= 0:
        return None
    return max(1, int(round(frames * 0.02)))


def voice_duration(path) -> int | None:
    """SILK / AMR 通用时长（秒）。"""
    return silk_duration(path) or amr_duration(path)

def estimate_voice_seconds(size) -> int | None:
    """本地没有文件时按 AMR-NB 约 50 字节/20ms 估算。"""
    try:
        size = int(size or 0)
    except (TypeError, ValueError):
        return None
    if size < 100:
        return None
    return max(1, int(round((size - 6) / 50.0 * 0.02)))


_EXTRA_MIME = {
    ".amr": "audio/amr",
    ".silk": "audio/silk",
    ".mp4": "video/mp4",
    ".m4a": "audio/mp4",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".7z": "application/x-7z-compressed",
    ".rar": "application/vnd.rar",
    ".apk": "application/vnd.android.package-archive",
    ".amr-wb": "audio/amr-wb",
}


def guess_content_type(path) -> str:
    ext = Path(str(path)).suffix.lower()
    if ext in _EXTRA_MIME:
        return _EXTRA_MIME[ext]
    mt, _ = mimetypes.guess_type(str(path))
    return mt or "application/octet-stream"


# ── 统计 ────────────────────────────────────────────────────────────────────
def index_stats(account_qq=None) -> dict:
    """媒体索引汇总。传 account_qq 时只统计该账号，避免跨账号目录/文件数泄漏。"""
    root = data_root()
    accounts: list[str] = []
    if account_qq:
        name = str(int(account_qq))
        try:
            if (root / name / "nt_qq" / "nt_data").is_dir():
                accounts.append(name)
        except OSError:
            accounts = []
    else:
        try:
            for d in sorted(root.iterdir()):
                if d.is_dir() and (d / "nt_qq" / "nt_data").is_dir():
                    accounts.append(d.name)
        except OSError:
            accounts = []
    detail: dict[str, dict] = {}
    per_dir: dict[str, int] = {}
    total = 0
    for name in accounts:
        ent = _entry(name)
        n = int(ent.get("count") or 0)
        detail[name] = {"files": n, "per_dir": ent.get("per_dir") or {},
                        "nt_data": ent.get("root")}
        total += n
        for k, v in (ent.get("per_dir") or {}).items():
            per_dir[k] = per_dir.get(k, 0) + int(v)
    return {"data_root": str(root), "accounts": detail,
            "files": total, "per_dir": per_dir, "cached_at": int(time.time())}


def media_stats(account_qq) -> dict:
    """按 kind 统计 store 里 media 的本地命中情况。"""
    qq = int(account_qq)
    out: dict[str, dict] = {}
    con = store.connect()
    try:
        try:
            rows = con.execute(
                "SELECT json_extract(media,'$.kind') AS k, "
                "SUM(CASE WHEN json_extract(media,'$.file') IS NOT NULL THEN 1 ELSE 0 END) AS hit, "
                "COUNT(*) AS total "
                "FROM messages WHERE account_qq=? AND media IS NOT NULL GROUP BY k",
                (qq,)).fetchall()
        except Exception:  # JSON1 不可用时的兜底
            rows = []
            cur = con.execute("SELECT media FROM messages WHERE account_qq=? AND media IS NOT NULL", (qq,))
            for (raw,) in cur:
                try:
                    m = json.loads(raw)
                except Exception:
                    continue
                k = m.get("kind") or "unknown"
                e = out.setdefault(k, {"hit": 0, "miss": 0, "total": 0})
                e["total"] += 1
                if m.get("file"):
                    e["hit"] += 1
                else:
                    e["miss"] += 1
        for r in rows:
            k = r["k"] or "unknown"
            hit = int(r["hit"] or 0)
            total = int(r["total"] or 0)
            out[k] = {"hit": hit, "miss": total - hit, "total": total}
    finally:
        con.close()
    return out


def pending(account_qq, kind: str | None = None, limit: int = 200) -> list[dict]:
    """本地缺失的媒体清单（供以后补下载用）。"""
    qq = int(account_qq)
    try:
        limit = max(1, min(int(limit or 200), 2000))
    except (TypeError, ValueError):
        limit = 200
    sql = ("SELECT id, kind, peer_id, peer_qq, ts, text, media FROM messages "
           "WHERE account_qq=? AND media IS NOT NULL "
           "AND json_extract(media,'$.file') IS NULL")
    args: list = [qq]
    if kind:
        sql += " AND json_extract(media,'$.kind')=?"
        args.append(str(kind))
    sql += " ORDER BY ts DESC LIMIT ?"
    args.append(limit)
    con = store.connect()
    try:
        rows = con.execute(sql, args).fetchall()
    finally:
        con.close()
    out: list[dict] = []
    for r in rows:
        try:
            m = json.loads(r["media"] or "{}")
        except Exception:
            m = {}
        out.append({
            "id": r["id"], "kind": m.get("kind") or "unknown",
            "peer_id": r["peer_id"], "peer_qq": r["peer_qq"], "ts": r["ts"],
            "md5": m.get("md5"), "name": m.get("name"),
            "fallback": m.get("fallback"), "text": r["text"],
        })
    return out