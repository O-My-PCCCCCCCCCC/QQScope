# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r"E:\01-项目\QQScope")
from core import store
from collections import Counter
con = store.connect()
print("by source:", dict(con.execute("SELECT source, COUNT(*) n FROM messages GROUP BY source").fetchall() and {r["source"]:r["n"] for r in con.execute("SELECT source, COUNT(*) n FROM messages GROUP BY source")}))
print("card by source:", {r["source"]: r["n"] for r in con.execute("SELECT source, COUNT(*) n FROM messages WHERE json_extract(media,'$.kind')='card' GROUP BY source")})
print("bare card by source:", {r["source"]: r["n"] for r in con.execute("SELECT source, COUNT(*) n FROM messages WHERE json_extract(media,'$.kind')='card' AND json_extract(media,'$.fallback')='[名片]' GROUP BY source")})
print("bare card by kind:", {r["kind"]: r["n"] for r in con.execute("SELECT kind, COUNT(*) n FROM messages WHERE json_extract(media,'$.kind')='card' AND json_extract(media,'$.fallback')='[名片]' GROUP BY kind")})
print("named card by source:", {r["source"]: r["n"] for r in con.execute("SELECT source, COUNT(*) n FROM messages WHERE json_extract(media,'$.kind')='card' AND json_extract(media,'$.fallback') LIKE '[名片] %' GROUP BY source")})
con.close()
