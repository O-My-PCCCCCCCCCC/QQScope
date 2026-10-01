# -*- coding: utf-8 -*-
"""只更新 card 相关行的 media：名片昵称补全 + 小程序包名退化（task-17）

- 名片：原始 protobuf 里很多 ct=8 只有 nc_uid_1、没有昵称字段（wire dump 已验证），
  所以用 uid -> 昵称 映射补：来源 ① names.fetch_names() 的 people（profile/group 库，1.7 万条）
  ② 其他带昵称的 card 行。补不到且无 uid 的保持原样（不编造）。
- 小程序：desc/prompt/title 为空时不再直接显示 com.tencent.xxx 包名。
- 只 UPDATE messages.media，不重新导入、不碰其他 kind。

用法：
    python tools/asr/fix_cards.py --account 1605289411 [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import store                      # noqa: E402
from core.sources import names              # noqa: E402
from core.sources._pack_extract import _classify  # noqa: E402


def find_segment(content, ct: int):
    """从 content JSON 里找出 content_type=ct 的那一段。"""
    if not content:
        return None
    try:
        d = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    if d.get("type") == "msg_body":
        for seg in d.get("segments") or []:
            if isinstance(seg, dict) and seg.get("content_type") == ct:
                return seg
        return None
    if ct == 8 and d.get("type") in ("contact", "reply"):
        return d
    if ct == 10 and d.get("type") in ("forward", "mini_app"):
        return d
    if ct == 10 and ("fwd_meta" in d or "meta" in d):
        return d
    return None


def uid_pairs(content):
    """从 content 里抠所有 (uid, nickname)，用于建索引。"""
    if not content:
        return []
    try:
        d = json.loads(content)
    except (TypeError, ValueError):
        return []
    out: list[tuple[str, str]] = []

    def take(x):
        if not isinstance(x, dict):
            return
        uid = (x.get("nc_uid_1") or x.get("nc_uid_2") or x.get("uid") or x.get("ref_uid") or "")
        nick = (x.get("nc_nickname_1") or x.get("nc_nickname_2") or x.get("nickname")
                or x.get("ref_nickname") or "")
        if uid and nick:
            out.append((str(uid).strip(), str(nick).strip()))

    if isinstance(d, dict):
        if d.get("type") == "msg_body":
            for seg in d.get("segments") or []:
                take(seg)
        else:
            take(d)
        if isinstance(d.get("ref_msg"), dict):
            take(d["ref_msg"])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="修复 card 行 media（名片昵称 / 小程序包名）")
    ap.add_argument("--account", type=int, required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    qq = int(args.account)

    # 1) uid -> 昵称：names.fetch_names（profile/group 库）
    name_map: dict[str, str] = {}
    try:
        got = names.fetch_names(qq)
        for uid, info in (got.get("people") or {}).items():
            if isinstance(info, dict):
                nm = str(info.get("remark") or info.get("name") or "").strip()
                if nm:
                    name_map[str(uid)] = nm
        print("names.fetch_names: people=%d ok=%s" % (len(got.get("people") or {}), got.get("ok")))
    except Exception as exc:  # noqa: BLE001
        print("names.fetch_names 失败（将继续用 card 内部索引）：%s" % exc)

    con = store.connect()
    rows = con.execute(
        "SELECT id, kind, msg_type, content, media FROM messages "
        "WHERE account_qq=? AND json_extract(media,'$.kind')='card'", (qq,)).fetchall()

    # 2) 再合并 card 内部 uid->昵称
    for r in rows:
        for uid, nick in uid_pairs(r["content"]):
            name_map.setdefault(uid, nick)
    print("uid->昵称 索引: %d" % len(name_map))

    before_bare = 0
    fixed_name = 0
    fixed_mini = 0
    updated = 0
    changed = []
    for r in rows:
        try:
            m = json.loads(r["media"] or "{}")
        except (TypeError, ValueError):
            continue
        fb = str(m.get("fallback") or "")
        new = None

        if fb.startswith("[名片") or fb == "[名片]":
            seg = find_segment(r["content"], 8)
            if seg is None:
                continue
            if fb == "[名片]":
                before_bare += 1
            new = _classify(8, seg)
            if not new:
                continue
            uid = str(new.pop("uid", "") or "")
            name = str(new.get("name") or "").strip()
            if not name and uid:
                name = name_map.get(uid, "")
            new["name"] = name
            new["fallback"] = "[名片] %s" % name if name else "[名片]"
            if name and fb == "[名片]":
                fixed_name += 1

        elif fb.startswith("[小程序"):
            seg = find_segment(r["content"], 10)
            if seg is None:
                continue
            new = _classify(10, seg)
            if not new:
                continue
            if new.get("fallback") != fb and fb:
                fixed_mini += 1

        if not new:
            continue
        new.setdefault("md5", None)
        new.setdefault("size", 0)
        new.setdefault("duration", None)
        new.setdefault("file", m.get("file"))
        merged = dict(m)
        merged.update({k: v for k, v in new.items() if k != "uid"})
        if merged == m:
            continue
        changed.append((r["id"], json.dumps(merged, ensure_ascii=False)))
        if len(changed) <= 5:
            print("  例: id=%s %s -> %s" % (r["id"], fb, merged.get("fallback")))

    if not args.dry_run:
        for mid, js in changed:
            con.execute("UPDATE messages SET media=? WHERE id=?", (js, mid))
        con.commit()
    updated = len(changed)

    after_bare = 0
    for r in con.execute(
            "SELECT media FROM messages WHERE account_qq=? AND json_extract(media,'$.kind')='card'", (qq,)):
        try:
            m = json.loads(r["media"] or "{}")
        except (TypeError, ValueError):
            continue
        if str(m.get("fallback") or "") == "[名片]":
            after_bare += 1
    con.close()
    print(json.dumps({
        "account": qq, "dry_run": bool(args.dry_run),
        "card_rows": len(rows), "bare_before": before_bare, "bare_after": after_bare,
        "name_fixed": fixed_name, "mini_fixed": fixed_mini, "updated": updated,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())