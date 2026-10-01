# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r"E:\01-项目\QQScope")
from core import store
con = store.connect()
rows = con.execute("SELECT id, msg_type, source, text, content, media FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.kind')='card' AND json_extract(media,'$.fallback') LIKE '[名片] %' LIMIT 6").fetchall()
for r in rows:
    print("="*60)
    print("id", r["id"], "mt", r["msg_type"], "src", r["source"], "text", repr(r["text"])[:40])
    print("media:", r["media"][:160])
    print("content:", (r["content"] or "")[:220])
con.close()
