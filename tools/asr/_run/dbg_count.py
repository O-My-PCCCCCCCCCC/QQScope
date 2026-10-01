# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r"E:\01-项目\QQScope")
from core import store
BK = r"E:\01-项目\QQScope\data\backup\cards_ct8_20261001.jsonl"
ids = []
for line in open(BK, encoding="utf-8"):
    if line.strip():
        ids.append(json.loads(line)["id"])
print("backup targets:", len(ids), "first:", ids[:3])
sq = store.connect()
for n in (3, 900, 5000, len(ids)):
    sub = ids[:n]
    ph = ",".join("?" * len(sub))
    try:
        r = sq.execute("SELECT COUNT(*) n FROM messages WHERE id IN (%s) AND kind='card' AND json_extract(media,'$.fallback')='[名片]'" % ph, sub).fetchone()
        print("n=%d -> %s" % (n, r["n"]))
    except Exception as e:
        print("n=%d -> ERR %s: %s" % (n, type(e).__name__, e))
# 单条直查
r = sq.execute("SELECT id, kind, json_extract(media,'$.fallback') fb FROM messages WHERE id=?", (ids[0],)).fetchone()
print("single:", dict(r) if r else None)
print("var limit:", sq.execute("SELECT 1").fetchone() and "ok")
import sqlite3
print("sqlite3 version:", sqlite3.sqlite_version)
con = sq
try:
    con.execute("SELECT ?" + ",?"*1500, list(range(1501)))
    print("1501 vars OK")
except Exception as e:
    print("1501 vars ERR", e)
sq.close()
