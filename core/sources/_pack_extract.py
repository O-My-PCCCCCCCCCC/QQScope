#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QQScope · 窗口A 解析助手（必须在 tools/nt_msg_db_util/.venv 里运行）

仅供 core/sources/pack_source.py 起子进程调用，职责：
  1. 剥离 nt_msg.db 前 1024 字节 NTQQ 自定义头 → nt_msg_clear.db
  2. 带上 -wal / -shm 交给 sqlcipher3 解密（WAL 打不开就自动退回不看 WAL）
  3. 复用 tools/nt_msg_db_util/msgdb 的 c2c/parser.py、group/exporter.py 解析文本
  4. 输出 JSONL（父进程负责写 core.store）；--probe-only 时只统计不写文件

stdout 行协议（父进程逐行解析）：
  @@PROG@@  {"stage","pct","msg"}        进度
  @@KEYOK@@ {"index","total_keys"}       哪个密钥成功
  @@CONVS@@ {"account_qq","conversations"}  probe-only 的会话清单
  @@DONE@@  {...}                        汇总
  @@ERROR@@ {"code","msg"}               失败（中文）

退出码：0 成功 / 3 解密失败 / 4 输入或剥头失败 / 5 其他致命错误。
本文件不修改 tools/ 下任何内容。
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import sys
import time
from pathlib import Path

try:  # MessageToDict 只在 venv 里可用；模块被别处 import 时不至于炸
    from google.protobuf.json_format import MessageToDict
except Exception:  # noqa: BLE001
    MessageToDict = None  # type: ignore

HEADER_SIZE = 1024
CHUNK = 64 << 20

PROG_PREFIX = "@@PROG@@ "
KEYOK_PREFIX = "@@KEYOK@@ "
CONVS_PREFIX = "@@CONVS@@ "
DONE_PREFIX = "@@DONE@@ "
ERR_PREFIX = "@@ERROR@@ "


class HelperError(Exception):
    """带退出码与中文说明的致命错误。"""

    def __init__(self, code: str, msg: str):
        super().__init__(msg)
        self.code = code
        self.msg = msg


def emit(prefix: str, payload: dict) -> None:
    print(prefix + json.dumps(payload, ensure_ascii=False), flush=True)


def strip_header(src: Path, dst: Path) -> None:
    """读取 src 跳过前 1024 字节写出 dst（每次重建，保证跟最新副本一致）。"""
    size = src.stat().st_size
    if size <= HEADER_SIZE:
        raise RuntimeError(f"数据库文件太小（{size} 字节），不是有效的 nt_msg.db")
    expected = size - HEADER_SIZE
    with open(src, "rb") as f:
        f.seek(HEADER_SIZE)
        with open(dst, "wb") as out:
            while True:
                buf = f.read(CHUNK)
                if not buf:
                    break
                out.write(buf)
    actual = dst.stat().st_size
    if actual != expected:
        raise RuntimeError(f"剥离头部后大小不符：期望 {expected}，实际 {actual}")


def sync_wal(work: Path, clear: Path) -> None:
    """把 nt_msg.db-wal / -shm 复制成 nt_msg_clear.db-wal / -shm。"""
    for suf in ("-wal", "-shm"):
        s = work / f"nt_msg.db{suf}"
        d = Path(str(clear) + suf)
        if d.exists():
            try:
                d.unlink()
            except OSError:
                pass
        if s.exists():
            shutil.copy2(s, d)


def drop_wal(clear: Path) -> None:
    for suf in ("-wal", "-shm"):
        d = Path(str(clear) + suf)
        if d.exists():
            try:
                d.unlink()
            except OSError:
                pass


def open_enc(path: Path, keys: list[str]):
    """按 SPEC 固定 PRAGMA 顺序逐个尝试密钥，返回 (连接, 命中的密钥下标)。

    顺序错了会直接报 'file is not a database'，所以顺序不能改：
        cipher_page_size=4096 → key → kdf_iter=4000
        → cipher_hmac_algorithm=HMAC_SHA1 → cipher_kdf_algorithm=PBKDF2_HMAC_SHA512
    """
    try:
        import sqlcipher3.dbapi2 as sc
    except Exception as exc:  # noqa: BLE001
        raise HelperError("novenv", f"venv 里没有 sqlcipher3：{exc}") from exc

    last = ""
    for i, key in enumerate(keys):
        con = None
        try:
            con = sc.connect(str(path), isolation_level=None)
            con.execute("PRAGMA cipher_page_size = 4096;")
            safe = key.replace("'", "''")
            con.execute(f"PRAGMA key = '{safe}';")
            con.execute("PRAGMA kdf_iter = 4000;")
            con.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA1;")
            con.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512;")
            con.execute("SELECT COUNT(*) FROM sqlite_master;").fetchone()
            return con, i
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass
    raise HelperError(
        "decrypt",
        f"{len(keys)} 个密钥都打不开数据库（最后一个错误：{last}）。"
        "请确认密钥是否与当前 QQ 版本匹配。",
    )


def iter_dict_rows(con, sql: str):
    """sqlite3.Row 与 sqlcipher3 游标不兼容，这里统一转成 dict 给 msgdb 用。"""
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    for row in cur:
        yield dict(zip(cols, row))


def collect_conversations(con, account_qq: str) -> list[dict]:
    """只读统计，不解析 protobuf：列出真实库里有哪些会话。"""
    out: list[dict] = []
    for r in con.execute(
        'SELECT "40021" AS peer_uid, MAX("40030") AS peer_qq, COUNT(*) AS n, '
        'MAX("40050") AS last_ts FROM c2c_msg_table GROUP BY "40021"'
    ):
        uid = (r[0] or "").strip()
        pq = int(r[1] or 0)
        out.append({
            "account_qq": int(account_qq) if str(account_qq).isdigit() else 0,
            "kind": "c2c", "peer_id": uid or str(pq), "peer_qq": pq,
            "name": None, "remark": None, "avatar": None,
            "msg_count": int(r[2] or 0), "last_ts": int(r[3] or 0),
        })
    for r in con.execute(
        'SELECT "40021" AS group_id, MAX("40030") AS group_qq, COUNT(*) AS n, '
        'MAX("40050") AS last_ts FROM group_msg_table WHERE "40050">0 GROUP BY "40021"'
    ):
        gid = (r[0] or "").strip()
        gq = int(r[1] or 0)
        if gq <= 0 and gid.isdigit():
            gq = int(gid)
        out.append({
            "account_qq": int(account_qq) if str(account_qq).isdigit() else 0,
            "kind": "group", "peer_id": gid or str(gq), "peer_qq": gq,
            "name": None, "remark": None, "avatar": None,
            "msg_count": int(r[2] or 0), "last_ts": int(r[3] or 0),
        })
    return out


# ── 媒体分类（SPEC 10.2 / 10.3）────────────────────────────────────────────
_MD5_HEX_RE = re.compile(r"^[0-9a-f]{32}$")
_SIZE_UNITS = (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10))
_MEDIA_CT = (2, 3, 4, 5, 6, 30)
_CARD_CT = (8, 10, 11, 16, 21)


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _b64_to_hex(v):
    """MessageToDict 把 bytes 编码成 base64；16 字节 -> 32 位 hex。"""
    if not v or not isinstance(v, str):
        return None
    try:
        raw = base64.b64decode(v, validate=False)
    except Exception:  # noqa: BLE001
        return None
    return raw.hex() if len(raw) == 16 else None


def _b64_to_hexstr(v):
    """f45424 这类：base64 解出来本身就是 32 位 hex 字符串。"""
    if not v or not isinstance(v, str):
        return None
    try:
        s = base64.b64decode(v, validate=False).decode("ascii", "ignore").strip().lower()
    except Exception:  # noqa: BLE001
        return None
    return s if _MD5_HEX_RE.match(s) else None


_APP_LABELS = {
    "com.tencent.miniapp_01": "",
    "com.tencent.tuwen.lua": "图文",
    "com.tencent.feed.lua": "动态",
    "com.tencent.music.lua": "音乐",
    "com.tencent.gamecenter.mall": "QQ手游消息",
    "com.tencent.tmdownloader": "应用下载",
}


def _app_label(app) -> str:
    """把 com.tencent.xxx 这种包名换成友好名；换不了就返回空串（避免展示包名）。"""
    app = str(app or "").strip()
    if not app:
        return ""
    if app in _APP_LABELS:
        return _APP_LABELS[app]
    if app.startswith("com.tencent") or "." in app:
        return ""
    return app


def _size_text(n):
    n = _int(n)
    for unit, div in _SIZE_UNITS:
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} B"


def _nudge_label(d) -> str:
    """ct=8 的「戳一戳 / 拍了拍」载荷 -> "[戳一戳] <动作文案>"。

    判据来源（NT QQ 9.9.36 实测）：
      MsgContent.reply_f48271  （pb2 已定义字段号 48271，JSON 字符串）
      JSON 形如 {"items":[{"type":"qq","uid":"u_..."}, {"txt":"揉了揉","type":"nor"}, ...]}
      -> 把 items[].txt 拼起来就是「揉了揉 的头」这类动作文案。
    仅在 nc_nickname_*/nc_uid_* 都为空（即不是名片）时才使用。
    """
    raw = d.get("reply_f48271")
    if not isinstance(raw, str) or not raw.strip():
        return ""
    try:
        j = json.loads(raw)
    except (TypeError, ValueError):
        return ""
    if not isinstance(j, dict):
        return ""
    parts = []
    for it in j.get("items") or []:
        if isinstance(it, dict):
            t = str(it.get("txt") or "").strip()
            if t:
                parts.append(t)
    text = " ".join(parts).strip()
    return f"[戳一戳] {text}" if text else ""


def _gtip_label(raw) -> str:
    """ct=8 的「群成员加入提醒」gtip XML -> "[群提醒] <文案>"。

    判据来源（NT QQ 9.9.36 实测）：
      MsgContent 的 **未知字段 48214**（pb2 里没有定义，只能从 wire 里读到；
      字段号是 48214 = 0xBC 0xF8 0x02）。值是 XML 字符串，形如
      <gtip align="center"><qq uin="u_..."/><nor txt="邀请"/><qq uin="u_..."/><nor txt="加入了群聊，并附带了30条聊天记录。"/></gtip>
      -> 把 <nor txt="..."> 拼起来。
    仅在 nc_nickname_*/nc_uid_* 都为空时才使用。
    """
    if not raw:
        return ""
    try:
        from msgdb.proto import wire as _wire

        fields = _wire.parse_wire(raw)
    except Exception:  # noqa: BLE001
        return ""
    for f in fields:
        if f.number != 48214 or f.wire_type != 2:
            continue
        try:
            s = f.raw_value.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        if "gtip" not in s and "<qq" not in s:
            continue
        txt = "".join(re.findall(r'txt="([^"]*)"', s)).strip()
        return f"[群提醒] {txt}" if txt else "[群提醒]"
    return ""


def _classify(ct, d, has_sticker=False, raw=b""):
    """一个 40800 content（MessageToDict 结果）-> SPEC 10.3 的 media dict。"""
    if ct == 4:
        md5 = _b64_to_hex(d.get("md5_raw"))
        return {"kind": "voice", "md5": md5,
                "name": d.get("filename") or (f"{md5}.amr" if md5 else ""),
                "size": _int(d.get("filesize")), "duration": None,
                "fallback": "[语音]"}
    if ct == 5:
        if has_sticker or d.get("sticker"):
            return {"kind": "sticker", "md5": None, "name": "", "size": 0,
                    "duration": None, "fallback": "[表情]"}
        md5 = _b64_to_hex(d.get("md5_raw"))
        ms = _int(d.get("f45415"))
        sec = int(round(ms / 1000.0)) if ms > 0 else None
        return {"kind": "video", "md5": md5,
                "name": d.get("filename") or (f"{md5}.mp4" if md5 else ""),
                "size": _int(d.get("filesize")), "duration": sec,
                "fallback": f"[视频 {sec}秒]" if sec else "[视频]"}
    if ct == 3:
        md5 = _b64_to_hex(d.get("md5_raw")) or _b64_to_hex(d.get("f45407"))
        name = d.get("filename") or ""
        size = _int(d.get("filesize"))
        return {"kind": "file", "md5": md5, "name": name, "size": size,
                "duration": None,
                "fallback": f"[文件: {name} ({_size_text(size)})]" if name else "[文件]"}
    if ct == 30:
        name = d.get("filename") or d.get("filepath") or ""
        return {"kind": "file", "md5": None, "name": name,
                "size": _int(d.get("filesize")), "duration": None,
                "fallback": f"[文件夹: {name}]" if name else "[文件夹]"}
    if ct == 6:
        txt = (d.get("video_text") or "").strip()
        return {"kind": "sticker", "md5": None, "name": "", "size": 0,
                "duration": None,
                "fallback": f"[视频表情] {txt}" if txt else "[视频表情]"}
    if ct == 2:
        md5 = _b64_to_hex(d.get("md5_raw")) or _b64_to_hexstr(d.get("f45424"))
        name = d.get("filename") or ""
        size = _int(d.get("filesize"))
        if has_sticker or d.get("sticker"):
            return {"kind": "sticker", "md5": md5, "name": name, "size": size,
                    "duration": None, "fallback": "[表情]"}
        return {"kind": "image", "md5": md5, "name": name, "size": size,
                "duration": None, "fallback": "[图片]"}
    if ct == 8:
        nick = (d.get("nc_nickname_1") or d.get("nc_nickname_2")
                or d.get("nickname") or d.get("ref_nickname") or "").strip()
        uid = (d.get("nc_uid_1") or d.get("nc_uid_2") or d.get("uid")
               or d.get("ref_uid") or "").strip()
        if not nick and not uid:
            # ct=8 不只承载名片：戳一戳(reply_f48271) 和群成员提醒(未知字段 48214 gtip)
            # 也是 ct=8。以前一律当名片 -> 光秃秃 [名片]（DB 里 11123 条中约 84% 属于这两类）。
            # 这里只改渲染文案，kind 仍是 'card'，不动 schema。
            rich = _nudge_label(d) or _gtip_label(raw)
            if rich:
                return {"kind": "card", "md5": None, "name": "", "size": 0,
                        "duration": None, "fallback": rich}
        out = {"kind": "card", "md5": None, "name": nick, "size": 0,
               "duration": None, "fallback": f"[名片] {nick}" if nick else "[名片]"}
        if uid:
            out["uid"] = uid
        return out
    if ct == 10:
        meta = None
        raw = d.get("fwd_meta")
        if isinstance(raw, str) and raw:
            try:
                meta = json.loads(raw)
            except Exception:  # noqa: BLE001
                meta = None
        elif isinstance(raw, dict):
            meta = raw
        if meta is None and isinstance(d.get("meta"), dict):
            meta = d["meta"]
        desc = ""
        app = ""
        if isinstance(meta, dict):
            desc = str(meta.get("desc") or meta.get("prompt") or meta.get("title") or "").strip()
            app = str(meta.get("app") or "").strip()
        if not desc:
            desc = _app_label(app)
        return {"kind": "card", "md5": None, "name": desc, "size": 0,
                "duration": None, "fallback": f"[小程序] {desc}" if desc else "[小程序]"}
    if ct == 11:
        txt = (d.get("sys_content") or "").strip()
        return {"kind": "card", "md5": None, "name": txt, "size": 0,
                "duration": None, "fallback": txt or "[系统消息]"}
    if ct == 16:
        return {"kind": "card", "md5": None, "name": "", "size": 0,
                "duration": None, "fallback": "[合并转发的聊天记录]"}
    if ct == 21:
        desc = (d.get("call_desc") or "").strip()
        return {"kind": "card", "md5": None, "name": desc, "size": 0,
                "duration": None, "fallback": f"[通话] {desc}" if desc else "[通话]"}
    return None


def build_media(contents):
    """从 repeated MsgContent 里挑主媒体（真媒体优先，其次卡片）；没有返回 None。"""
    if MessageToDict is None:
        return None
    card = None
    for c in contents:
        try:
            ct = _int(getattr(c, "content_type", 0))
            has_sticker = bool(getattr(c, "sticker", b""))
        except Exception:  # noqa: BLE001
            continue
        if ct in _MEDIA_CT or has_sticker:
            try:
                d = MessageToDict(c, preserving_proto_field_name=True)
            except Exception:  # noqa: BLE001
                continue
            m = _classify(ct, d, has_sticker)
            if m:
                return m
        elif ct in _CARD_CT and card is None:
            try:
                card = _classify(ct, MessageToDict(c, preserving_proto_field_name=True), False,
                                 c.SerializeToString())
            except Exception:  # noqa: BLE001
                card = None
    return card


def main() -> int:
    ap = argparse.ArgumentParser(description="QQScope pack 源解析助手（venv 子进程）")
    ap.add_argument("--work", required=True, help="工作目录（含已复制的 nt_msg.db）")
    ap.add_argument("--qq", default="", help="账号 QQ（写进 JSONL 供父进程参考）")
    ap.add_argument("--key", action="append", default=[], help="候选密钥，可重复")
    ap.add_argument("--out", default="", help="JSONL 输出路径（--probe-only 时不写）")
    ap.add_argument("--probe-only", action="store_true", help="只探测统计，不输出 JSONL")
    ap.add_argument("--no-c2c", action="store_true", help="跳过私聊表")
    ap.add_argument("--no-group", action="store_true", help="跳过群聊表")
    ap.add_argument("--limit", type=int, default=0, help="每张表最多解析多少行（调试用）")
    ap.add_argument("--reuse-clear", action="store_true",
                    help="复用已有的 nt_msg_clear.db，不重新复制/剥头（重扫媒体用）")
    args = ap.parse_args()

    work = Path(args.work)
    src = work / "nt_msg.db"
    clear = work / "nt_msg_clear.db"

    if not src.exists():
        emit(ERR_PREFIX, {"code": "nodb", "msg": f"找不到已复制的数据库：{src}"})
        return 4
    if not args.key:
        emit(ERR_PREFIX, {"code": "nokey", "msg": "没有可用的数据库密钥"})
        return 3

    t_start = time.monotonic()

    if args.reuse_clear:
        # 复用上一次 sync 留下的 nt_msg_clear.db（仍是加密库，只需重新打开）
        if not clear.exists():
            emit(ERR_PREFIX, {"code": "nodb",
                              "msg": f"没有可复用的解密缓存 {clear}，请先跑一次完整同步"})
            return 4
        strip_sec = 0.0
        emit(PROG_PREFIX, {"stage": "解密", "pct": 1, "msg": "复用已有解密缓存，重扫媒体列…"})
        t_dec = time.monotonic()
        con = None
        key_index = -1
        used_wal = False
        last_err = ""
        for use_wal in (True, False):
            if not use_wal:
                drop_wal(clear)
            try:
                con, key_index = open_enc(clear, args.key)
                break
            except HelperError as exc:
                last_err = exc.msg
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)
        if con is None:
            emit(ERR_PREFIX, {"code": "decrypt", "msg": f"解密失败：{last_err}"})
            return 3
        decrypt_sec = time.monotonic() - t_dec
    else:
        # ── 1. 剥头 ──────────────────────────────────────────────────────
        emit(PROG_PREFIX, {"stage": "剥头", "pct": 0, "msg": "剥离 1024 字节自定义头…"})
        try:
            strip_header(src, clear)
        except Exception as exc:  # noqa: BLE001
            emit(ERR_PREFIX, {"code": "strip", "msg": f"剥离数据库头部失败：{exc}"})
            return 4
        strip_sec = time.monotonic() - t_start

        # ── 2. 解密（先带 WAL，再退回不带） ────────────────────────────
        emit(PROG_PREFIX, {"stage": "解密", "pct": 1, "msg": "用 sqlcipher3 校验密钥…"})
        t_dec = time.monotonic()
        con = None
        key_index = -1
        used_wal = False
        last_err = ""
        for use_wal in (True, False):
            if use_wal:
                sync_wal(work, clear)
            else:
                drop_wal(clear)
            try:
                con, key_index = open_enc(clear, args.key)
                used_wal = use_wal
                break
            except HelperError as exc:
                last_err = exc.msg
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)
        if con is None:
            emit(ERR_PREFIX, {"code": "decrypt", "msg": f"解密失败：{last_err}"})
            return 3
        decrypt_sec = time.monotonic() - t_dec
    emit(KEYOK_PREFIX, {"index": key_index, "total_keys": len(args.key), "used_wal": used_wal})
    emit(PROG_PREFIX, {"stage": "解密", "pct": 2, "msg": f"密钥正确（第 {key_index + 1}/{len(args.key)} 个）"})

    # ── 3. 统计 ──────────────────────────────────────────────────────────
    try:
        c2c_total = (
            con.execute("SELECT COUNT(*) FROM c2c_msg_table").fetchone()[0]
            if not args.no_c2c else 0
        )
        group_total = (
            con.execute("SELECT COUNT(*) FROM group_msg_table").fetchone()[0]
            if not args.no_group else 0
        )
    except Exception as exc:  # noqa: BLE001
        emit(ERR_PREFIX, {"code": "schema", "msg": f"源库缺少 c2c_msg_table / group_msg_table：{exc}"})
        return 4
    if args.limit > 0:
        c2c_total = min(c2c_total, args.limit)
        group_total = min(group_total, args.limit)
    total = c2c_total + group_total

    if args.probe_only:
        try:
            convs = collect_conversations(con, args.qq)
        except Exception as exc:  # noqa: BLE001
            emit(ERR_PREFIX, {"code": "convs", "msg": f"统计会话失败：{exc}"})
            return 5
        emit(CONVS_PREFIX, {"account_qq": args.qq, "conversations": convs})
        emit(DONE_PREFIX, {
            "qq": args.qq, "probe_only": True, "key_index": key_index,
            "c2c_total": c2c_total, "group_total": group_total,
            "conversations": len(convs),
            "strip_sec": round(strip_sec, 2), "decrypt_sec": round(decrypt_sec, 2),
            "used_wal": used_wal, "elapsed": round(time.monotonic() - t_start, 2),
        })
        con.close()
        return 0

    # ── 4. 解析 → JSONL ──────────────────────────────────────────────────
    try:
        tool_dir = Path(__file__).resolve().parents[2] / "tools" / "nt_msg_db_util"
        if str(tool_dir) not in sys.path:
            sys.path.insert(0, str(tool_dir))
        from msgdb.c2c import parser as c2c_parser
        from msgdb.group import exporter as group_exporter
        from msgdb.proto.c2c_40800_parser import parse_40800
        from google.protobuf.json_format import MessageToDict
    except Exception as exc:  # noqa: BLE001
        emit(ERR_PREFIX, {"code": "import", "msg": f"加载 msgdb 解析器失败：{exc}"})
        return 5

    t_parse = time.monotonic()
    processed = 0
    skipped_ts = 0
    parse_errors = 0
    c2c_out = 0
    group_out = 0
    media_out = 0
    media_kinds: dict = {}
    last_emit = time.monotonic()

    def tick(stage: str) -> None:
        nonlocal last_emit
        if processed >= total or time.monotonic() - last_emit >= 0.5:
            pct = 2 + int(93 * processed / total) if total else 95
            emit(PROG_PREFIX, {
                "stage": stage, "pct": min(pct, 95),
                "msg": f"解析 {processed}/{total} 行",
            })
            last_emit = time.monotonic()

    out_path = Path(args.out) if args.out else (work / "messages.jsonl")
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        if not args.no_c2c:
            sql = c2c_parser.SELECT_SQL
            if args.limit > 0:
                sql = sql.rstrip().rstrip(";") + f" LIMIT {int(args.limit)}"
            for raw in iter_dict_rows(con, sql):
                ts = raw.get("timestamp") or 0
                if ts <= 0:
                    skipped_ts += 1
                    processed += 1
                    tick("解析")
                    continue
                try:
                    msg = c2c_parser.parse_row(raw)
                    media_obj = build_media(parse_40800(raw.get("blob")).contents)
                    fh.write(json.dumps({
                        "k": "c", "id": msg.msg_id, "ts": msg.timestamp, "d": msg.direction,
                        "su": msg.sender_uid, "sq": msg.sender_qq,
                        "pu": msg.peer_uid, "pq": msg.peer_qq,
                        "mt": msg.msg_type, "tx": msg.text,
                        "content": msg.to_db_row().get("content"),
                        "media": json.dumps(media_obj, ensure_ascii=False) if media_obj else None,
                    }, ensure_ascii=False, separators=(",", ":")) + "\n")
                    c2c_out += 1
                    if media_obj:
                        media_out += 1
                        media_kinds[media_obj["kind"]] = media_kinds.get(media_obj["kind"], 0) + 1
                except Exception:  # noqa: BLE001
                    parse_errors += 1
                processed += 1
                tick("解析")

        if not args.no_group:
            sql = group_exporter.SELECT_SQL
            if args.limit > 0:
                sql = sql.rstrip().rstrip(";") + f" LIMIT {int(args.limit)}"
            for raw in iter_dict_rows(con, sql):
                ts = raw.get("timestamp") or 0
                if ts <= 0:
                    skipped_ts += 1
                    processed += 1
                    tick("解析")
                    continue
                try:
                    rec = group_exporter.parse_row(raw)
                    media_obj = build_media(parse_40800(raw.get("blob")).contents)
                    fh.write(json.dumps({
                        "k": "g", "id": rec["msg_id"], "ts": rec["timestamp"],
                        "d": rec["direction"], "su": rec["sender_uid"],
                        "sq": rec["sender_qq"], "gi": rec["group_id"],
                        "gq": rec["group_qq"], "mt": rec["msg_type"],
                        "tx": rec["text"],
                        "content": rec.get("content"),
                        "media": json.dumps(media_obj, ensure_ascii=False) if media_obj else None,
                    }, ensure_ascii=False, separators=(",", ":")) + "\n")
                    group_out += 1
                    if media_obj:
                        media_out += 1
                        media_kinds[media_obj["kind"]] = media_kinds.get(media_obj["kind"], 0) + 1
                except Exception:  # noqa: BLE001
                    parse_errors += 1
                processed += 1
                tick("解析")

    parse_sec = time.monotonic() - t_parse
    con.close()

    emit(DONE_PREFIX, {
        "qq": args.qq, "probe_only": False, "key_index": key_index,
        "used_wal": used_wal, "out": str(out_path),
        "c2c_total": c2c_total, "group_total": group_total,
        "c2c_out": c2c_out, "group_out": group_out,
        "scanned": c2c_out + group_out,
        "media_out": media_out, "media_kinds": media_kinds,
        "skipped_ts": skipped_ts, "parse_errors": parse_errors,
        "strip_sec": round(strip_sec, 2), "decrypt_sec": round(decrypt_sec, 2),
        "parse_sec": round(parse_sec, 2),
        "elapsed": round(time.monotonic() - t_start, 2),
    })
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except HelperError as exc:
        emit(ERR_PREFIX, {"code": exc.code, "msg": exc.msg})
        sys.exit(5)
    except KeyboardInterrupt:
        sys.exit(130)