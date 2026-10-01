# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r"E:\01-项目\QQScope")
from core import store
con = store.connect()
bare = con.execute("SELECT COUNT(*) n FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.kind')='card' AND json_extract(media,'$.fallback')='[名片]'").fetchone()["n"]
named = con.execute("SELECT COUNT(*) n FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.kind')='card' AND json_extract(media,'$.fallback') LIKE '[名片] %'").fetchone()["n"]
uidf = con.execute("SELECT COUNT(*) n FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.fallback') LIKE '[名片] uid:%'").fetchone()["n"]
tot = con.execute("SELECT COUNT(*) n FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.kind')='card'").fetchone()["n"]
print("card_total=%d bare=[名片] %d | [名片] name=%d | uid-form=%d" % (tot, bare, named, uidf))
print("--- 5 fixed samples (uid-form) ---")
for r in con.execute("SELECT id, json_extract(media,'$.fallback') fb FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.fallback') LIKE '[名片] uid:%' LIMIT 5"):
    print("  id=%s  %s" % (r["id"], r["fb"]))
print("--- 5 fixed samples (new layout / nick) ---")
for r in con.execute("SELECT id, msg_type, json_extract(media,'$.fallback') fb, json_extract(media,'$.name') nm FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.kind')='card' AND json_extract(media,'$.fallback') LIKE '[名片] %' ORDER BY id DESC LIMIT 5"):
    print("  id=%s mt=%s fallback=%r name=%r" % (r["id"], r["msg_type"], r["fb"], r["nm"]))
print("--- 5 examples of fixed new-layout (id 的选取见上方 samples) ---")
for r in con.execute("SELECT id, media FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.fallback') LIKE '[名片] %' AND json_extract(media,'$.voice_status') IS NULL LIMIT 0"):
    pass
con.close()
