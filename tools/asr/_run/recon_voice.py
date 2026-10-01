# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r"E:\01-项目\QQScope")
from core import store
from collections import Counter
con = store.connect()
rows = con.execute("SELECT id, ts, json_extract(media,'$.voice_status') st, json_extract(media,'$.duration') dur, json_extract(media,'$.name') nm, json_extract(media,'$.file') f FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.kind')='voice'").fetchall()
print("status counter:", dict(Counter([r["st"] or "pending" for r in rows])), "total", len(rows))
pend = [r for r in rows if (r["st"] or "pending") not in ("ok","empty")]
print("non-ok/empty:", len(pend))
for r in sorted(pend, key=lambda x: -(x["dur"] or 0)):
    print("%9s %-8s %7.1fs %s" % (r["id"], r["st"], r["dur"] or 0, (r["nm"] or "")[:24]))
print("--- ok samples (10, longest) ---")
ok = [r for r in rows if r["st"]=="ok"]
for r in sorted(ok, key=lambda x: -(x["dur"] or 0))[:10]:
    print("%9s %7.1fs %s" % (r["id"], r["dur"] or 0, (r["nm"] or "")[:24]))
print("--- empty samples (10, longest) ---")
em = [r for r in rows if r["st"]=="empty"]
for r in sorted(em, key=lambda x: -(x["dur"] or 0))[:10]:
    print("%9s %7.1fs %s" % (r["id"], r["dur"] or 0, (r["nm"] or "")[:24]))
print("--- suspect (all) ---")
for r in rows:
    if r["st"]=="suspect":
        print("%9s %7.1fs %s" % (r["id"], r["dur"] or 0, (r["nm"] or "")[:24]))
con.close()
