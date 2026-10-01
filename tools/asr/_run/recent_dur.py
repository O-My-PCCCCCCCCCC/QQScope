# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"E:\01-项目\QQScope")
from core import store
con = store.connect()
for n in (50, 200):
    r = con.execute(
        "SELECT SUM(d) s, COUNT(*) n FROM (SELECT json_extract(media,'$.duration') d "
        "FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.kind')='voice' "
        "ORDER BY ts DESC LIMIT %d)" % n).fetchone()
    print("recent%-4d n=%s dur=%.0fs (%.2f h)" % (n, r["n"], r["s"] or 0, (r["s"] or 0) / 3600))
con.close()
