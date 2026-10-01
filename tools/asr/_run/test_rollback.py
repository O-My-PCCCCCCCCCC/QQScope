# -*- coding: utf-8 -*-
import json, sqlite3, sys
db = sys.argv[1]
bk = r"E:\01-项目\QQScope\data\backup\cards_ct8_20261001.jsonl"
con = sqlite3.connect(db)
n = 0
for line in open(bk, encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    o = json.loads(line)
    con.execute("UPDATE messages SET media=? WHERE id=?", (o["before_media"], o["id"]))
    n += 1
con.commit()
bare = con.execute("SELECT COUNT(*) FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.kind')='card' AND json_extract(media,'$.fallback')='[名片]'").fetchone()[0]
nudge = con.execute("SELECT COUNT(*) FROM messages WHERE account_qq=1605289411 AND json_extract(media,'$.fallback') LIKE '[戳一戳]%'").fetchone()[0]
print("回滚行数=%d  回滚后 bare=[名片] %d  [戳一戳] %d" % (n, bare, nudge))
con.close()
