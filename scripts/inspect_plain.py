"""QQScope · 检查明文库里是否有联系人/昵称数据"""
import sqlite3

con = sqlite3.connect(r"E:\01-项目\QQScope\data\decrypt\nt_msg_plain.db")
cur = con.cursor()
tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
print("=== nt_msg_plain.db 全部表 ===")
for t in tables:
    try:
        n = cur.execute(f'SELECT count(*) FROM [{t}]').fetchone()[0]
    except Exception:
        n = "?"
    print(f"  {t}: {n} 行")

print("\n=== 疑似联系人/资料表结构 ===")
for t in tables:
    tl = t.lower()
    if any(k in tl for k in ["buddy", "contact", "friend", "profile", "uid", "roster", "group_info", "friend"]):
        print(f"--- [{t}] ---")
        for row in cur.execute(f"PRAGMA table_info([{t}])"):
            print("   ", row[1], row[2])
con.close()
