# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"E:\01-项目\QQScope")
from core import store
con = store.connect()
r = con.execute("SELECT COUNT(*) n, SUM(json_extract(media,'$.duration')) tot FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.kind')='voice'").fetchone()
tot = r["tot"] or 0
print("voice files=%d total_duration=%.0fs (%.2f h)" % (r["n"], tot, tot/3600))
for st in ("ok","empty","suspect","decode","missing",None):
    q = ("SELECT COUNT(*) n, SUM(json_extract(media,'$.duration')) d FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.kind')='voice' AND json_extract(media,'$.voice_status') %s"
         % ("IS NULL" if st is None else "= '%s'" % st))
    x = con.execute(q).fetchone()
    print("  %-8s n=%-4d dur=%.0fs" % (st or "pending", x["n"], x["d"] or 0))
# >120s
x = con.execute("SELECT COUNT(*) n, SUM(json_extract(media,'$.duration')) d FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.kind')='voice' AND json_extract(media,'$.duration')>120").fetchone()
print("  >120s   n=%-4d dur=%.0fs (%.2f h, 占 %.0f%%)" % (x["n"], x["d"] or 0, (x["d"] or 0)/3600, 100*(x["d"] or 0)/tot))
x = con.execute("SELECT COUNT(*) n, SUM(json_extract(media,'$.duration')) d FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.kind')='voice' AND json_extract(media,'$.voice_status')='ok'").fetchone()
print("  ok       n=%-4d dur=%.0fs (%.2f h)" % (x["n"], x["d"] or 0, (x["d"] or 0)/3600))
con.close()
