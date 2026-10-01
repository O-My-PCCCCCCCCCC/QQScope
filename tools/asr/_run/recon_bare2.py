# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r"E:\01-项目\QQScope")
from core import store
from collections import Counter
con = store.connect()
rows = con.execute("SELECT id, msg_type, content, media FROM messages WHERE json_extract(media,'$.kind')='card' AND json_extract(media,'$.fallback')='[名片]'").fetchall()
ctr = Counter(); has_nick = 0; has_uid = 0; has_seg8 = 0
ex_nick = []; ex_uid = []
for r in rows:
    try: d = json.loads(r["content"] or "{}")
    except Exception: d = {}
    t = d.get("type") if isinstance(d, dict) else None
    segs = d.get("segments") if isinstance(d, dict) else None
    cts = tuple(sorted({s.get("content_type") for s in segs})) if segs else None
    ctr[(t, cts)] += 1
    if cts and 8 in cts: has_seg8 += 1
    # search nickname anywhere
    s = r["content"] or ""
    if any(k in s for k in ("nc_nickname_1","nc_nickname_2")): has_nick += 1
    if "nc_uid" in s: has_uid += 1
print("segment-type counter (top 15):")
for k,v in ctr.most_common(15):
    print("  ", k, v)
print("has nc_nickname_*:", has_nick, "has nc_uid*:", has_uid, "has seg ct=8:", has_seg8, "total", len(rows))
print("--- sample msg_body bare card ---")
for r in rows:
    d = json.loads(r["content"] or "{}")
    if isinstance(d, dict) and d.get("segments"):
        print("id", r["id"], "mt", r["msg_type"])
        print(json.dumps(d, ensure_ascii=False)[:1200])
        print("media:", r["media"])
        break
con.close()
