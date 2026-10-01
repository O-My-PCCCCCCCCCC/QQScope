# -*- coding: utf-8 -*-
import sys, json, httpx
sys.path.insert(0, r"E:\01-项目\QQScope")
from core import store, voice
api = httpx.Client(trust_env=False, timeout=20).get("http://127.0.0.1:15555/api/voice/stats?account=1605289411").json()
print("API   :", json.dumps(api, ensure_ascii=False))
print("LIB   :", json.dumps(voice.stats(1605289411), ensure_ascii=False))
con = store.connect()
print("CARD bare=[名片]:", con.execute("SELECT COUNT(*) n FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.kind')='card' AND json_extract(media,'$.fallback')='[名片]'").fetchone()["n"])
print("CARD total    :", con.execute("SELECT COUNT(*) n FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.kind')='card'").fetchone()["n"])
print("CARD named    :", con.execute("SELECT COUNT(*) n FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.kind')='card' AND json_extract(media,'$.fallback') LIKE '[名片] %'").fetchone()["n"])
print("MSG total     :", con.execute("SELECT COUNT(*) n FROM messages WHERE account_qq=1605289411").fetchone()["n"])
con.close()
