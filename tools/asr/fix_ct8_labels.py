# -*- coding: utf-8 -*-
"""ct8 语义修正（Lead 已批准）：把光秃 [名片] 里的「戳一戳 / 群提醒」改写为正确标签。

判据（与 core/sources/_pack_extract.py 内的 _nudge_label / _gtip_label 完全同源）：
  - reply_f48271 (pb2 字段 48271) JSON 的 items[].txt -> "[戳一戳] <文案>"
  - 未知字段 48214 的 gtip XML 的 <nor txt="...">   -> "[群提醒] <文案>"

硬条件（Lead 批准范围）：
  - 只改渲染文案，kind 仍是 'card'，不动 schema / store.py；
  - 只命中「当前 fallback == '[名片]'」的 card 行；WHERE 写窄；
  - 改库前把目标行快照到 data/backup/cards_ct8_20261001.jsonl，报告里给回滚步骤。

用法：
    python tools/asr/fix_ct8_labels.py --account 1605289411 [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (str(ROOT), str(ROOT / "tools" / "nt_msg_db_util")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

from core import store                                        # noqa: E402
from core.sources._pack_extract import build_media, open_enc  # noqa: E402
from msgdb.c2c import parser as c2c_parser                    # noqa: E402
from msgdb.group import exporter as group_exporter            # noqa: E402
from msgdb.proto.c2c_40800_parser import parse_40800          # noqa: E402

RICH_PREFIXES = ("[戳一戳]", "[群提醒]")
BACKUP = ROOT / "data" / "backup" / "cards_ct8_20261001.jsonl"


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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ct8 戳一戳/群提醒 标签修正（只动 fallback=='[名片]'）")
    ap.add_argument("--account", type=int, required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    qq = int(args.account)

    clear = ROOT / "data" / "decrypt" / str(qq) / "nt_msg_clear.db"
    if not clear.exists():
        print(json.dumps({"ok": False, "error": f"没有解密缓存 {clear}"}, ensure_ascii=False))
        return 4
    con, key_idx = open_enc(clear, candidate_keys(qq))

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

    targets: dict[int, tuple[str, str]] = {}     # mid -> (new_fallback, criterion)
    already: dict[str, int] = {}

    def handle(kind, peer_id, ts, direction, text, contents):
        key = (kind, peer_id, int(ts), int(direction), text or "")
        row = ux2row.get(key)
        if row is None:
            return
        mid, fb, _media_json = row
        media = build_media(contents)
        if not media or media.get("kind") != "card":
            return
        nfb = str(media.get("fallback") or "")
        if nfb.startswith(RICH_PREFIXES):
            crit = "nudge" if nfb.startswith("[戳一戳]") else "gtip"
            already[crit] = already.get(crit, 0) + 1
            if fb == "[名片]" and mid not in targets:
                targets[mid] = (nfb, crit)

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
                    text = getattr(c2c_parser.parse_row(r), "text", None)
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

    n_nudge = sum(1 for _i, (_f, c) in targets.items() if c == "nudge")
    n_gtip = sum(1 for _i, (_f, c) in targets.items() if c == "gtip")
    med_by_id = {mid: mj for (mid, _fb, mj) in ux2row.values()}

    # 1) 改库前：目标行快照（回滚用）
    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    with open(BACKUP, "w", encoding="utf-8", newline="\n") as fh:
        for mid, (nfb, crit) in targets.items():
            fh.write(json.dumps({"id": mid, "criterion": crit, "after_fallback": nfb,
                                 "before_media": med_by_id.get(mid)}, ensure_ascii=False) + "\n")

    # 2) SELECT COUNT(*) 预期
    ids = list(targets)
    ph = ",".join("?" * len(ids)) if ids else "NULL"
    expected = sq.execute(
        # 注意：messages.kind 是 c2c/group；名片类型在 media JSON 里（$.kind='card'）。
        "SELECT COUNT(*) n FROM messages WHERE id IN (%s) AND account_qq=? "
        "AND json_extract(media,'$.kind')='card' "
        "AND json_extract(media,'$.fallback')='[名片]'" % ph,
        ids + [qq]).fetchone()["n"] if ids else 0

    # 3) UPDATE（WHERE 写窄）
    changed = 0
    if not args.dry_run and ids:
        sql = ("UPDATE messages SET media=? WHERE id=? AND account_qq=? "
               "AND json_extract(media,'$.kind')='card' "
               "AND json_extract(media,'$.fallback')='[名片]'")
        for i in range(0, len(ids), 500):
            batch = ids[i:i + 500]
            before = sq.total_changes
            rows = []
            for mid in batch:
                nfb = targets[mid][0]
                try:
                    m = json.loads(med_by_id.get(mid) or "{}")
                except (TypeError, ValueError):
                    m = {}
                m["name"] = ""
                m["fallback"] = nfb
                rows.append((json.dumps(m, ensure_ascii=False), mid, qq))
            sq.executemany(sql, rows)
            sq.commit()
            changed += sq.total_changes - before

    bare_after = sq.execute(
        "SELECT COUNT(*) n FROM messages WHERE account_qq=? AND json_extract(media,'$.kind')='card' "
        "AND json_extract(media,'$.fallback')='[名片]'", (qq,)).fetchone()["n"]
    print(json.dumps({
        "ok": True, "account": qq, "dry_run": bool(args.dry_run), "key_index": key_idx,
        "bare_before": bare_before,
        "targets": len(targets), "nudge": n_nudge, "gtip": n_gtip,
        "select_count_expected": expected, "update_changed": changed,
        "counts_match": expected == len(targets) == (changed if not args.dry_run else len(targets)),
        "bare_after": bare_after if not args.dry_run else bare_before - len(targets),
        "backup": str(BACKUP),
        "already_rich_rows_seen": already,
    }, ensure_ascii=False, indent=1))
    sq.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
