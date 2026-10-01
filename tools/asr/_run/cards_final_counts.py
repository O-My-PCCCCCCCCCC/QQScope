# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r"E:\01-项目\QQScope")
from core import store
con = store.connect()
def c(sql):
    return con.execute(sql).fetchone()["n"]
base = "SELECT COUNT(*) n FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.kind')='card'"
print("card total       :", c(base))
for lab, cond in (("[名片] 光秃", "json_extract(media,'$.fallback')='[名片]'"),
                  ("[名片] 有名", "json_extract(media,'$.fallback') LIKE '[名片] %'"),
                  ("[戳一戳]", "json_extract(media,'$.fallback') LIKE '[戳一戳]%'"),
                  ("[群提醒]", "json_extract(media,'$.fallback') LIKE '[群提醒]%'"),
                  ("[小程序]", "json_extract(media,'$.fallback') LIKE '[小程序]%'")):
    print("%-14s:" % lab, c(base + " AND " + cond))
print("--- 剩余光秃 [名片] 的 msg_type 分布 ---")
for r in con.execute(base + " AND json_extract(media,'$.fallback')='[名片]' GROUP BY msg_type ORDER BY n DESC"):
    pass
for r in con.execute("SELECT msg_type mt, COUNT(*) n FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.kind')='card' AND json_extract(media,'$.fallback')='[名片]' GROUP BY msg_type ORDER BY n DESC"):
    print("   mt=%s  n=%s" % (r["mt"], r["n"]))
con.close()

