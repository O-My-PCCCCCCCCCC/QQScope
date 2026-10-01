# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r"E:\01-项目\QQScope")
from core import store
from collections import Counter
con = store.connect()
n = con.execute("SELECT COUNT(*) c, COUNT(DISTINCT account_qq) a FROM messages").fetchone()
print("messages total", n["c"], "accounts", n["a"])
for r in con.execute("SELECT account_qq, COUNT(*) n FROM messages GROUP BY account_qq"):
    print("  acct", r["account_qq"], r["n"])
# per-account bare card count
for r in con.execute("SELECT account_qq, COUNT(*) n FROM messages WHERE json_extract(media,'$.kind')='card' AND json_extract(media,'$.fallback')='[名片]' GROUP BY account_qq"):
    print("  bare cards acct", r["account_qq"], r["n"])
b = con.execute("SELECT id, ts, peer_id, msg_type, content FROM messages WHERE json_extract(media,'$.kind')='card' AND json_extract(media,'$.fallback')='[名片]'").fetchall()
print("bare total", len(b), "msg_type counter", dict(Counter([x["msg_type"] for x in b])))
print("content-type counter:", dict(Counter([ (json.loads(x["content"] or "{}") or {}).get("type") if x["content"] else None for x in b]).most_common()))
con.close()
