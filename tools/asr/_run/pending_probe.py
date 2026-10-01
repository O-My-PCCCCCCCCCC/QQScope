# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r"E:\01-项目\QQScope")
from core import store
con = store.connect()
rows = con.execute("SELECT id, ts, json_extract(media,'$.voice_status') st, media FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.kind')='voice' AND (json_extract(media,'$.voice_status') IS NULL OR json_extract(media,'$.voice_status')='')").fetchall()
print("voice_status 为空的行数:", len(rows))
for r in rows:
    print("  id=%s ts=%s media=%s" % (r["id"], r["ts"], (r["media"] or "")[:220]))
rows2 = con.execute("SELECT id, json_extract(media,'$.voice_status') st FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.kind')='voice' AND json_extract(media,'$.file') IS NULL").fetchall()
print("file 为 NULL 的语音行:", len(rows2), [(r["id"], r["st"]) for r in rows2])
con.close()
