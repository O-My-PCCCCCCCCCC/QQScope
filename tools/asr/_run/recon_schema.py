# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"E:\01-项目\QQScope")
from core import store
con = store.connect()
for r in con.execute("SELECT name, sql FROM sqlite_master WHERE type='table'"):
    print(r["name"])
print("--- messages ---")
print(con.execute("SELECT sql FROM sqlite_master WHERE name='messages'").fetchone()["sql"])
try:
    print("--- meta ---")
    print(con.execute("SELECT sql FROM sqlite_master WHERE name='meta'").fetchone()["sql"])
except Exception as e:
    print(e)
con.close()
