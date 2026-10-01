# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r"E:\01-项目\QQScope")
from core import store
from collections import Counter
con = store.connect()
rows = con.execute("SELECT id, msg_type, json_extract(media,'$.kind') k, json_extract(media,'$.fallback') fb, media FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.kind')='card'").fetchall()
print("card rows:", len(rows), "msg_type counter:", dict(Counter([r["msg_type"] for r in rows])))
bare = [r for r in rows if str(r["fb"] or "") == "[名片]"]
print("bare [名片]:", len(bare))
print("fallback prefix counter:", dict(Counter([str(r["fb"] or "")[:6] for r in rows]).most_common(12)))
print("--- 5 bare examples ---")
for r in bare[:5]:
    print(r["id"], r["msg_type"], (r["media"] or "")[:200])
print("--- 5 named card examples ---")
named=[r for r in rows if str(r["fb"] or "").startswith("[名片] ")][:5]
for r in named:
    print(r["id"], r["msg_type"], r["fb"])
print("--- mini_app fallbacks touching com.tencent ---")
mini=[r for r in rows if "com.tencent" in str(r["fb"] or "")]
print("mini with com.tencent in fallback:", len(mini))
for r in mini[:5]:
    print(r["id"], r["msg_type"], r["fb"])
con.close()
