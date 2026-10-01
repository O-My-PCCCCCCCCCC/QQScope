# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r"E:\01-项目\QQScope")
from core import store
from collections import Counter
con = store.connect()
rows = con.execute("SELECT id, msg_type, content FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.kind')='card' AND json_extract(media,'$.fallback')='[名片]' LIMIT 400").fetchall()
ctr = Counter()
ex = {}
for r in rows:
    try: d = json.loads(r["content"] or "{}")
    except Exception: d = {}
    t = d.get("type")
    segs = d.get("segments") if isinstance(d, dict) else None
    if segs:
        cts = tuple(sorted({s.get("content_type") for s in segs if isinstance(s, dict)}))
        key = (t, cts)
    else:
        key = (t, None)
    ctr[key]+=1
    ex.setdefault(key, r["id"])
for k,v in ctr.most_common(15):
    print(k, v, "ex_id", ex[k])
print("--- sample seg-bearing bare card ---")
for r in rows:
    d = json.loads(r["content"] or "{}")
    if isinstance(d, dict) and d.get("segments"):
        print(r["id"], r["msg_type"], json.dumps(d, ensure_ascii=False)[:900])
        break
con.close()
