# -*- coding: utf-8 -*-
"""只更新 card 行：从原始 NT blob 还原名片昵称/uid，补全光秃秃的 [名片]。

口径（Lead 2026-10-01 裁决）：
- 只 UPDATE 当前 media.kind='card' 且 fallback=='[名片]' 的行；不碰其它 kind、不碰已有非空名片。
- 能确定拿到昵称        -> fallback = "[名片] <昵称>"
- 只拿到 uid            -> fallback = "[名片] uid:<uid>"（仅在 uid 无撤回/ark/文件等标记时）
- 昵称/uid 都拿不到      -> 保持原样，计数。
- **不做** ct8 误分类（戳一戳/群提醒/撤回/文件）的语义修正，只在报告里给数字与建议。

昵称来源：
1) ct=8 老布局：nc_nickname_1(47705) / nc_nickname_2(47714)
2) ct=8 新布局 media_sub=4：未知字段 48504/48505（名片主人，实测与 profile 库一致率 1092/1213≈90%），
   48506/48507/48508 是分享者信息（一致率仅 438/1216），不作为显示名。
3) uid -> 昵称：core.sources.names.fetch_names()（本地 profile/group 库）。

用法：
    python tools/asr/update_cards.py --account 1605289411 [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (str(ROOT), str(ROOT / "tools" / "nt_msg_db_util")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

from core import store                                        # noqa: E402
from core.sources import names                                # noqa: E402
from core.sources._pack_extract import build_media, open_enc  # noqa: E402
from msgdb.c2c import parser as c2c_parser                    # noqa: E402
from msgdb.group import exporter as group_exporter            # noqa: E402
from msgdb.proto import wire as _wire                         # noqa: E402
from msgdb.proto.c2c_40800_parser import parse_40800          # noqa: E402

# ct=8 老布局映射字段号（pb2 已有定义）
F_NICK1, F_NICK2, F_UID1, F_UID2 = 47705, 47714, 47703, 47704
# ct=8 新布局（media_sub=4，pb2 未定义，只在 wire 里；48504/48505 一致 1320/1323）
F_NEW_NICK, F_NEW_NICK2, F_NEW_UID = 48504, 48505, 48503
F_NEW_UID2 = 48506
MARK_REF, MARK_ARK, MARK_GTIP, MARK_FILE = 47713, 48271, 48214, 45402


def candidate_keys(qq: int) -> list[str]:
    out: list[str] = []
    for p in (ROOT / "data" / "keys" / f"{qq}.key",
              ROOT / "data" / "keys" / "legacy.key",
              ROOT / "qq-export" / "db_key.txt"):
        try:
            if p.exists():
                t = p.read_text(encoding="utf-8", errors="ignore").strip()
                if t and t not in out:
                    out.append(t)
        except OSError:
            pass
    return out


def load_name_map(qq: int) -> dict[str, str]:
    m: dict[str, str] = {}
    try:
        got = names.fetch_names(qq)
        for uid, info in (got.get("people") or {}).items():
            if isinstance(info, dict):
                nm = str(info.get("remark") or info.get("name") or "").strip()
                if nm:
                    m[str(uid)] = nm
        print("names.fetch_names people=%d" % len(got.get("people") or {}))
    except Exception as exc:  # noqa: BLE001
        print("names.fetch_names 失败：%s" % exc)
    return m


def _wire_strs(raw: bytes) -> dict[int, str]:
    """wire 扫一遍：字段号 -> utf-8 字符串（重复字段保留第一个非空）。"""
    out: dict[int, str] = {}
    try:
        fields = _wire.parse_wire(raw)
    except Exception:  # noqa: BLE001
        return out
    for f in fields:
        if f.wire_type != 2 or f.number in out:
            continue
        try:
            s = f.raw_value.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        if s:
            out[f.number] = s
    return out


def resolve_card(contents, name_map):
    """返回 (fallback, name, tag) 或 (None, None, tag)。只看 ct==8。"""
    for c in contents:
        if int(c.content_type or 0) != 8:
            continue
        raw = c.SerializeToString()
        w = _wire_strs(raw)
        nick = (w.get(F_NICK1) or w.get(F_NICK2) or "").strip()
        uid = (w.get(F_UID1) or w.get(F_UID2) or "").strip()
        ark = (w.get(MARK_ARK) or "").strip()
        gtip = (w.get(MARK_GTIP) or "").strip()
        fname = (w.get(MARK_FILE) or "").strip()
        ref = (w.get(MARK_REF) or "").strip()
        new_nick = (w.get(F_NEW_NICK) or w.get(F_NEW_NICK2) or "").strip()
        if nick:
            return f"[名片] {nick}", nick, "nick"
        if int(c.media_sub or 0) == 4 and new_nick:
            return f"[名片] {new_nick}", new_nick, "new_layout"
        if uid and not ref and not ark and not gtip and not fname:
            nm = name_map.get(uid, "")
            if nm:
                return f"[名片] {nm}", nm, "uid_resolved"
            return f"[名片] uid:{uid}", uid, "uid_raw"
        if uid and ref:
            return None, None, "recall"
        if ark:
            return None, None, "ark"
        if gtip:
            return None, None, "gtip"
        if fname:
            return None, None, "file"
        return None, None, "empty_shell"
    return None, None, "no_ct8"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="补全光秃秃 [名片]（只动 fallback=='[名片]' 的 card 行）")
    ap.add_argument("--account", type=int, required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    qq = int(args.account)

    name_map = load_name_map(qq)
    clear = ROOT / "data" / "decrypt" / str(qq) / "nt_msg_clear.db"
    if not clear.exists():
        print(json.dumps({"ok": False, "error": f"没有解密缓存 {clear}"}, ensure_ascii=False))
        return 4
    keys = candidate_keys(qq)
    con, key_idx = open_enc(clear, keys)
    print("解密缓存打开成功（第 %d/%d 个密钥）" % (key_idx + 1, len(keys)))

    sq = store.connect()
    ux2row: dict[tuple, tuple] = {}
    for r in sq.execute(
            "SELECT id, kind, peer_id, ts, direction, text, media FROM messages "
            "WHERE account_qq=? AND json_extract(media,'$.kind')='card'", (qq,)):
        try:
            m = json.loads(r["media"] or "{}")
        except (TypeError, ValueError):
            m = {}
        ux2row[(r["kind"], r["peer_id"], int(r["ts"]), int(r["direction"]),
                r["text"] or "")] = (int(r["id"]), str(m.get("fallback") or ""), r["media"] or "{}")
    bare_before = sum(1 for _i, fb, _m in ux2row.values() if fb == "[名片]")
    print("qqscope card 行=%d，其中 fallback=='[名片]'=%d" % (len(ux2row), bare_before))

    tag_seen: dict[str, int] = {}
    updates: dict[int, tuple[str, str]] = {}   # mid -> (fallback, name)
    for ux, (mid, fb, media_json) in ux2row.items():
        if fb != "[名片]":
            continue
        media_json  # keep ref

    def handle(kind, peer_id, ts, direction, text, contents):
        key = (kind, peer_id, int(ts), int(direction), text or "")
        row = ux2row.get(key)
        if row is None:
            return
        mid, fb, media_json = row
        if fb != "[名片]" or mid in updates:
            return
        fb2, name, tag = resolve_card(contents, name_map)
        tag_seen[tag] = tag_seen.get(tag, 0) + 1
        if fb2:
            updates[mid] = (fb2, name)

    def scan(sql, kind):
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description]
        for raw in cur:
            r = dict(zip(cols, raw))
            blob = r.get("blob")
            if not blob:
                continue
            try:
                contents = list(parse_40800(blob).contents)
            except Exception:  # noqa: BLE001
                continue
            if not contents:
                continue
            if kind == "c2c":
                try:
                    m = c2c_parser.parse_row(r)
                    text = getattr(m, "text", None)
                except Exception:  # noqa: BLE001
                    text = None
                try:
                    sqq = int(r.get("sender_qq") or 0)
                except (TypeError, ValueError):
                    sqq = 0
                try:
                    pq = int(r.get("peer_qq") or 0)
                except (TypeError, ValueError):
                    pq = 0
                peer_id = str(pq) if pq else (str(r.get("peer_uid") or "").strip() or "0")
                handle("c2c", peer_id, int(r.get("timestamp") or 0),
                       1 if (sqq and sqq == qq) else 0, text, contents)
            else:
                try:
                    rec = group_exporter.parse_row(r)
                except Exception:  # noqa: BLE001
                    continue
                try:
                    sqq = int(rec.get("sender_qq") or 0)
                except (TypeError, ValueError):
                    sqq = 0
                gi = str(rec.get("group_id") or "").strip()
                try:
                    gq = int(rec.get("group_qq") or 0)
                except (TypeError, ValueError):
                    gq = 0
                if gq <= 0 and gi.isdigit():
                    gq = int(gi)
                handle("group", gi or str(gq), int(rec.get("timestamp") or 0),
                       1 if (sqq and sqq == qq) else 0, rec.get("text"), contents)

    scan(c2c_parser.SELECT_SQL, "c2c")
    scan(group_exporter.SELECT_SQL, "group")
    con.close()

    rows_to_write = []
    media_by_id = {mid: media_json for (mid, _fb, media_json) in ux2row.values()}
    for mid, (fb2, name) in updates.items():
        try:
            m = json.loads(media_by_id.get(mid) or "{}")
        except (TypeError, ValueError):
            m = {}
        m["name"] = name
        m["fallback"] = fb2
        m.pop("uid", None)
        rows_to_write.append((mid, json.dumps(m, ensure_ascii=False)))

    if not args.dry_run and rows_to_write:
        for i in range(0, len(rows_to_write), 500):
            batch = rows_to_write[i:i + 500]
            sq.executemany("UPDATE messages SET media=? WHERE id=?",
                           [(js, mid) for mid, js in batch])
        sq.commit()

    n_new = tag_seen.get("new_layout", 0)
    n_nick = tag_seen.get("nick", 0)
    n_uidr = tag_seen.get("uid_resolved", 0)
    n_uidraw = tag_seen.get("uid_raw", 0)
    fixed = n_nick + n_new + n_uidr + n_uidraw
    print(json.dumps({
        "ok": True, "account": qq, "dry_run": bool(args.dry_run), "key_index": key_idx,
        "card_rows": len(ux2row), "bare_before": bare_before, "bare_after_est": bare_before - fixed,
        "fixed_total": fixed, "fixed_nick": n_nick, "fixed_new_layout": n_new,
        "fixed_uid_resolved": n_uidr, "fixed_uid_raw": n_uidraw,
        "skipped_by_tag": {k: v for k, v in tag_seen.items()
                           if k not in ("nick", "new_layout", "uid_resolved", "uid_raw")},
        "samples_after": [fb2 for _mid, (fb2, _n) in list(updates.items())[:5]],
        "name_map_size": len(name_map),
    }, ensure_ascii=False, indent=1))
    sq.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
