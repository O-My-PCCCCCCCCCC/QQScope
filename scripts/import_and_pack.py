"""QQScope · 多账号统一消息库导入器 + 数据包生成器
输入：每个账号的结构化导出库（nt_msg_export.db）
输出：data/qqscope_<qq>.db（统一 schema，按账号分库）
     data/pack/<qq>/messages.json.gz + meta.json（安卓端数据包，按账号分目录）

账号列表：默认 ACCOUNTS；可用环境变量 QQSCOPE_ACCOUNTS 覆盖（JSON 数组，
后端自动发现账号时使用：QQSCOPE_ACCOUNTS='[{"qq":1605289411,"export_db":"...","default_nick":"..."}]'）
"""
import json
import gzip
import os
import sqlite3
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 账号配置：export_db 为 3.export.py 的结构化导出结果
ACCOUNTS = [
    {"qq": 1605289411, "export_db": ROOT / "data" / "decrypt" / "nt_msg_export.db", "default_nick": "主号"},
    {"qq": 3101168700, "export_db": ROOT / "data" / "decrypt" / "xiaohao" / "nt_msg_export.db", "default_nick": "小号"},
]

_env_accounts = os.getenv("QQSCOPE_ACCOUNTS")
if _env_accounts:
    ACCOUNTS = json.loads(_env_accounts)
    for a in ACCOUNTS:
        a["export_db"] = Path(a["export_db"])

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  direction INTEGER NOT NULL,
  peer_qq INTEGER,
  group_qq INTEGER,
  kind TEXT NOT NULL,
  msg_type INTEGER,
  text TEXT,
  content TEXT
);
CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(ts);
CREATE INDEX IF NOT EXISTS idx_msg_dir ON messages(direction, ts);
"""


def build_scope_db(acc):
    qq = acc["qq"]
    db = ROOT / "data" / f"qqscope_{qq}.db"
    if db.exists():
        db.unlink()
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    src = sqlite3.connect(acc["export_db"])
    cur = con.cursor()

    rows = []
    for row in src.execute(
        "SELECT timestamp, direction, peer_qq, msg_type, text, content FROM c2c_messages"
    ):
        ts, d, peer, mt, text, content = row
        if not ts or ts < 1000000000:
            continue
        rows.append((ts, d, peer, None, "c2c", mt, text, content))
    for row in src.execute(
        "SELECT timestamp, direction, group_qq, msg_type, text, content FROM group_messages"
    ):
        ts, d, g, mt, text, content = row
        if not ts or ts < 1000000000:
            continue
        rows.append((ts, d, None, g, "group", mt, text, content))

    cur.executemany(
        "INSERT INTO messages(ts,direction,peer_qq,group_qq,kind,msg_type,text,content) "
        "VALUES(?,?,?,?,?,?,?,?)", rows)
    con.commit()
    n = cur.execute("SELECT count(*) FROM messages").fetchone()[0]
    src.close()
    con.close()
    print(f"[{qq}] qqscope_{qq}.db 导入完成：{n} 条消息")
    return n


def build_pack(acc):
    qq = acc["qq"]
    db = ROOT / "data" / f"qqscope_{qq}.db"
    out_dir = ROOT / "data" / "pack" / str(qq)
    out_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    cur = con.cursor()

    msgs = []
    for row in cur.execute(
        "SELECT ts,direction,peer_qq,group_qq,kind,text FROM messages "
        "WHERE text IS NOT NULL AND text != '' ORDER BY ts"
    ):
        ts, d, peer, g, kind, text = row
        p = peer if peer else g
        msgs.append({"t": ts, "d": d, "p": p if p else None, "k": kind, "x": text})

    def q(sql, *a):
        return cur.execute(sql, a).fetchone()[0]

    total = q("SELECT count(*) FROM messages")
    self_n = q("SELECT count(*) FROM messages WHERE direction=1")
    c2c_n = q("SELECT count(*) FROM messages WHERE kind='c2c'")
    c2c_self = q("SELECT count(*) FROM messages WHERE kind='c2c' AND direction=1")
    grp_n = q("SELECT count(*) FROM messages WHERE kind='group'")
    grp_self = q("SELECT count(*) FROM messages WHERE kind='group' AND direction=1")
    tmin = q("SELECT min(ts) FROM messages")
    tmax = q("SELECT max(ts) FROM messages")

    meta = {
        "qq": qq,
        "label": acc["default_nick"],
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_messages": total,
        "self_messages": self_n,
        "c2c_total": c2c_n, "c2c_self": c2c_self,
        "group_total": grp_n, "group_self": grp_self,
        "time_start": tmin, "time_end": tmax,
    }

    with gzip.open(out_dir / "messages.json.gz", "wt", encoding="utf-8") as f:
        json.dump(msgs, f, ensure_ascii=False)
    with open(out_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    con.close()
    print(f"[{qq}] 数据包：{len(msgs)} 条带文本消息 → data/pack/{qq}/")
    return meta


def fmt_ts(t):
    return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d") if t else "?"


if __name__ == "__main__":
    print("===== QQScope 多账号导入 =====")
    for acc in ACCOUNTS:
        if not acc["export_db"].exists():
            print(f"[跳过] {acc['qq']}：{acc['export_db']} 不存在（先跑小号解密）")
            continue
        build_scope_db(acc)
        m = build_pack(acc)
        print(f"  → {m['qq']}（{m['label']}）：总 {m['total_messages']} 条，你发 {m['self_messages']} 条，"
              f"范围 {fmt_ts(m['time_start'])} ~ {fmt_ts(m['time_end'])}")
