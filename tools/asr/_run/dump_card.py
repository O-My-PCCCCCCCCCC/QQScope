# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r"E:\01-项目\QQScope")
from core import store
con = store.connect()
for mid in (3340, 6654, 4978):
    r = con.execute("SELECT id, msg_type, text, content, media FROM messages WHERE id=?", (mid,)).fetchone()
    print("="*20, mid, "msg_type", r["msg_type"])
    print("TEXT:", (r["text"] or "")[:120])
    c = r["content"] or ""
    print("CONTENT len", len(c), "has nc_nickname:", "nc_nickname" in c, "has nc_uid:", "nc_uid" in c)
    print(c[:2500])
con.close()
