"""QQScope · 统一主库（两个数据源汇聚到这里）

约定（所有模块必须遵守）：
  kind      : 'c2c'（私聊） | 'group'（群聊）
  peer_id   : 会话标识的字符串形式。私聊=对方 uid（没有 uid 就用 QQ 号），群聊=群号字符串
  peer_qq   : 会话的 QQ 号（int，可能为 0 / None）
  direction : 1 = 自己发出，0 = 收到
  ts        : Unix 秒
  source    : 'pack'（窗口A 数据包） | 'bot'（窗口B 机器人框架）
"""
from __future__ import annotations

import pathlib
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterable

from . import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  account_qq INTEGER PRIMARY KEY,
  label      TEXT,
  source     TEXT,
  updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS contacts (
  account_qq INTEGER NOT NULL,
  kind       TEXT    NOT NULL,
  peer_id    TEXT    NOT NULL,
  peer_qq    INTEGER,
  name       TEXT,
  remark     TEXT,
  avatar     TEXT,
  msg_count  INTEGER DEFAULT 0,
  self_count INTEGER DEFAULT 0,
  first_ts   INTEGER,
  last_ts    INTEGER,
  last_text  TEXT,
  source     TEXT,
  PRIMARY KEY (account_qq, kind, peer_id)
);
CREATE TABLE IF NOT EXISTS messages (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  account_qq  INTEGER NOT NULL,
  kind        TEXT    NOT NULL,
  peer_id     TEXT    NOT NULL,
  peer_qq     INTEGER,
  ts          INTEGER NOT NULL,
  direction   INTEGER NOT NULL,
  sender_qq   INTEGER,
  sender_name TEXT,
  msg_type    INTEGER,
  text        TEXT,
  source      TEXT,
  content     TEXT,          -- 原始结构化 JSON（来自 3.export.py 的 content 列）
  media       TEXT           -- 归一化媒体 JSON: {"kind","file","name","size","duration","md5"}
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_msg ON messages(
  account_qq, kind, peer_id, ts, direction, COALESCE(text,'')
);
CREATE INDEX IF NOT EXISTS ix_msg_peer ON messages(account_qq, kind, peer_id, ts);
CREATE INDEX IF NOT EXISTS ix_msg_ts   ON messages(ts);
"""


_MIGRATED = False


def _ensure_compatible() -> None:
    """旧版 data/qqscope.db 用的是完全不同的 schema（无 account_qq）。
    首次访问时自动把它归档到 data/legacy/，再建新库，绝不删数据。"""
    global _MIGRATED
    if _MIGRATED:
        return
    _MIGRATED = True
    if not paths.STORE_DB.exists():
        return
    try:
        con = sqlite3.connect(paths.STORE_DB)
        cols = [r[1] for r in con.execute("PRAGMA table_info(messages)")]
        con.close()
    except Exception:
        return
    if cols and "account_qq" not in cols:
        legacy = paths.DATA / "legacy"
        legacy.mkdir(parents=True, exist_ok=True)
        dst = legacy / f"qqscope_legacy_{int(time.time())}.db"
        paths.STORE_DB.rename(dst)
        for suf in ("-wal", "-shm"):
            f = pathlib.Path(str(paths.STORE_DB) + suf)
            if f.exists():
                f.rename(pathlib.Path(str(dst) + suf))


def connect() -> sqlite3.Connection:
    _ensure_compatible()
    con = sqlite3.connect(paths.STORE_DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def _migrate_columns(con) -> None:
    """老库补列：messages.content / messages.media（幂等）。"""
    cols = {r[1] for r in con.execute("PRAGMA table_info(messages)")}
    for name, ddl in (("content", "TEXT"), ("media", "TEXT")):
        if cols and name not in cols:
            con.execute(f"ALTER TABLE messages ADD COLUMN {name} {ddl}")


def init() -> None:
    con = connect()
    try:
        con.executescript(SCHEMA)
        _migrate_columns(con)
        con.commit()
    finally:
        con.close()


@contextmanager
def tx():
    con = connect()
    try:
        con.executescript(SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


# ── 账号 ────────────────────────────────────────────────────────────────────
def upsert_account(account_qq: int, label: str | None = None,
                   source: str | None = None) -> None:
    with tx() as con:
        con.execute(
            "INSERT INTO accounts(account_qq,label,source,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(account_qq) DO UPDATE SET "
            "label=COALESCE(excluded.label,accounts.label), "
            "source=COALESCE(excluded.source,accounts.source), updated_at=excluded.updated_at",
            (int(account_qq), label, source, int(time.time())))


def list_accounts() -> list[dict]:
    con = connect()
    try:
        rows = con.execute(
            "SELECT a.*, "
            "(SELECT COUNT(*) FROM contacts c WHERE c.account_qq=a.account_qq) AS contacts, "
            "(SELECT COUNT(*) FROM messages m WHERE m.account_qq=a.account_qq) AS messages, "
            "(SELECT COUNT(*) FROM messages m WHERE m.account_qq=a.account_qq AND m.direction=1) AS self_messages, "
            "(SELECT MIN(ts) FROM messages m WHERE m.account_qq=a.account_qq) AS first_ts, "
            "(SELECT MAX(ts) FROM messages m WHERE m.account_qq=a.account_qq) AS last_ts "
            "FROM accounts a ORDER BY a.account_qq").fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def delete_account(account_qq: int) -> None:
    with tx() as con:
        con.execute("DELETE FROM messages WHERE account_qq=?", (int(account_qq),))
        con.execute("DELETE FROM contacts WHERE account_qq=?", (int(account_qq),))
        try:  # feeds 由 qzone schema 建，可能不存在；删除账号时必须一并清掉
            con.execute("DELETE FROM feeds WHERE account_qq=?", (int(account_qq),))
        except sqlite3.OperationalError:
            pass
        con.execute("DELETE FROM accounts WHERE account_qq=?", (int(account_qq),))


# ── 联系人/会话 ─────────────────────────────────────────────────────────────
CONTACT_FIELDS = ("account_qq", "kind", "peer_id", "peer_qq", "name", "remark",
                  "avatar", "msg_count", "self_count", "first_ts", "last_ts",
                  "last_text", "source")


def upsert_contacts(rows: Iterable[dict]) -> int:
    """只更新非 None 字段，避免覆盖已有昵称/备注。"""
    rows = list(rows)
    if not rows:
        return 0
    with tx() as con:
        for r in rows:
            cols = [f for f in CONTACT_FIELDS if r.get(f) is not None]
            vals = [r[f] for f in cols]
            if not {"account_qq", "kind", "peer_id"} <= set(cols):
                continue
            sets = ",".join(f"{c}=COALESCE(excluded.{c},{c})" for c in cols
                            if c not in ("account_qq", "kind", "peer_id"))
            con.execute(
                f"INSERT INTO contacts({','.join(cols)}) VALUES({','.join('?'*len(cols))}) "
                f"ON CONFLICT(account_qq,kind,peer_id) DO UPDATE SET {sets}",
                vals)
    return len(rows)


def list_contacts(account_qq: int | None = None, kind: str | None = None,
                  query: str | None = None, order: str = "last_ts DESC",
                  limit: int = 1000) -> list[dict]:
    if not account_qq:
        raise ValueError("list_contacts 必须指定 account_qq（多账号隔离要求）")
    sql = "SELECT * FROM contacts WHERE account_qq=?"
    args: list[Any] = [int(account_qq)]
    if kind:
        sql += " AND kind=?"; args.append(kind)
    if query:
        sql += " AND (name LIKE ? OR remark LIKE ? OR CAST(peer_qq AS TEXT) LIKE ?)"
        args += [f"%{query}%"] * 3
    sql += f" ORDER BY {order} LIMIT ?"; args.append(int(limit))
    con = connect()
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def get_contact(account_qq: int, kind: str, peer_id: str) -> dict | None:
    con = connect()
    try:
        r = con.execute("SELECT * FROM contacts WHERE account_qq=? AND kind=? AND peer_id=?",
                        (int(account_qq), kind, str(peer_id))).fetchone()
        return dict(r) if r else None
    finally:
        con.close()


def set_contact_meta(account_qq: int, kind: str, peer_id: str,
                     name: str | None = None, remark: str | None = None,
                     avatar: str | None = None) -> None:
    with tx() as con:
        con.execute(
            "INSERT INTO contacts(account_qq,kind,peer_id,name,remark,avatar) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(account_qq,kind,peer_id) DO UPDATE SET "
            "name=COALESCE(excluded.name,name), remark=COALESCE(excluded.remark,remark), "
            "avatar=COALESCE(excluded.avatar,avatar)",
            (int(account_qq), kind, str(peer_id), name, remark, avatar))


# ── 消息 ────────────────────────────────────────────────────────────────────
MSG_FIELDS = ("account_qq", "kind", "peer_id", "peer_qq", "ts", "direction",
              "sender_qq", "sender_name", "msg_type", "text", "source",
              "content", "media")


def insert_messages(rows: Iterable[dict]) -> int:
    """去重导入（唯一索引 ux_msg）。返回本次新增条数。"""
    rows = list(rows)
    if not rows:
        return 0
    con = connect()
    try:
        con.executescript(SCHEMA)
        _migrate_columns(con)
        before = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        con.executemany(
            f"INSERT OR IGNORE INTO messages({','.join(MSG_FIELDS)}) "
            f"VALUES({','.join('?'*len(MSG_FIELDS))})",
            [tuple(r.get(f) for f in MSG_FIELDS) for r in rows])
        con.commit()
        after = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        return after - before
    finally:
        con.close()


def list_messages(account_qq: int, kind: str, peer_id: str, limit: int = 200,
                  offset: int = 0, order: str = "ASC",
                  since: int | None = None, until: int | None = None) -> list[dict]:
    sql = ("SELECT * FROM messages WHERE account_qq=? AND kind=? AND peer_id=?")
    args: list[Any] = [int(account_qq), kind, str(peer_id)]
    if since:
        sql += " AND ts>=?"; args.append(int(since))
    if until:
        sql += " AND ts<=?"; args.append(int(until))
    sql += f" ORDER BY ts {order} LIMIT ? OFFSET ?"
    args += [int(limit), int(offset)]
    con = connect()
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def count_messages(account_qq: int | None = None, kind: str | None = None,
                   peer_id: str | None = None) -> int:
    if not account_qq:
        raise ValueError("count_messages 必须指定 account_qq（多账号隔离要求）")
    sql = "SELECT COUNT(*) FROM messages WHERE 1=1"
    args: list[Any] = []
    if account_qq:
        sql += " AND account_qq=?"; args.append(int(account_qq))
    if kind:
        sql += " AND kind=?"; args.append(kind)
    if peer_id:
        sql += " AND peer_id=?"; args.append(str(peer_id))
    con = connect()
    try:
        return con.execute(sql, args).fetchone()[0]
    finally:
        con.close()


def search_messages(query: str, limit: int = 200,
                    account_qq: int | None = None) -> list[dict]:
    """全文搜索。必须显式指定 account_qq，绝不跨账号返回（多账号隔离硬约束）。"""
    if not account_qq:
        raise ValueError("search_messages 必须指定 account_qq（多账号隔离要求）")
    con = connect()
    try:
        return [dict(r) for r in con.execute(
            "SELECT * FROM messages WHERE account_qq=? AND text LIKE ? "
            "ORDER BY ts DESC LIMIT ?",
            (int(account_qq), f"%{query}%", int(limit))).fetchall()]
    finally:
        con.close()


def refresh_contact_stats(account_qq: int) -> int:
    """根据 messages 重算 contacts 的统计字段（导入后调用）。"""
    with tx() as con:
        con.execute("""
            UPDATE contacts SET
              msg_count  = COALESCE((SELECT COUNT(*) FROM messages m
                            WHERE m.account_qq=contacts.account_qq AND m.kind=contacts.kind
                              AND m.peer_id=contacts.peer_id),0),
              self_count = COALESCE((SELECT COUNT(*) FROM messages m
                            WHERE m.account_qq=contacts.account_qq AND m.kind=contacts.kind
                              AND m.peer_id=contacts.peer_id AND m.direction=1),0),
              first_ts   = (SELECT MIN(ts) FROM messages m
                            WHERE m.account_qq=contacts.account_qq AND m.kind=contacts.kind
                              AND m.peer_id=contacts.peer_id),
              last_ts    = (SELECT MAX(ts) FROM messages m
                            WHERE m.account_qq=contacts.account_qq AND m.kind=contacts.kind
                              AND m.peer_id=contacts.peer_id)
            WHERE account_qq=?""", (int(account_qq),))
        n = con.execute("SELECT changes()").fetchone()[0]
    return n


def overview(account_qq: int | None = None) -> dict:
    if not account_qq:
        raise ValueError("overview 必须指定 account_qq（多账号隔离要求）")
    con = connect()
    try:
        w = "WHERE account_qq=?"
        a: list[Any] = [int(account_qq)]
        g = lambda s: con.execute(s, a).fetchone()[0]
        total = g(f"SELECT COUNT(*) FROM messages {w}")
        if not total:
            return {"total": 0, "self": 0, "c2c": 0, "group": 0,
                    "contacts": 0, "first_ts": None, "last_ts": None, "kinds": []}
        w2 = (w + " AND") if w else "WHERE"
        return {
            "total": total,
            "self": g(f"SELECT COUNT(*) FROM messages {w} {'AND' if w else 'WHERE'} direction=1"),
            "c2c": g(f"SELECT COUNT(*) FROM messages {w} {'AND' if w else 'WHERE'} kind='c2c'"),
            "group": g(f"SELECT COUNT(*) FROM messages {w} {'AND' if w else 'WHERE'} kind='group'"),
            "contacts": g(f"SELECT COUNT(*) FROM contacts {w}"),
            "first_ts": g(f"SELECT MIN(ts) FROM messages {w}"),
            "last_ts": g(f"SELECT MAX(ts) FROM messages {w}"),
            "kinds": [dict(r) for r in con.execute(
                f"SELECT kind, COUNT(*) n FROM messages {w} GROUP BY kind", a).fetchall()],
        }
    finally:
        con.close()

# ── 聚合统计（前端总览页用） ────────────────────────────────────────────────
def daily_stats(account_qq: int) -> list[dict]:
    con = connect()
    try:
        rows = con.execute(
            "SELECT date(ts,'unixepoch','localtime') AS d, "
            "COUNT(*) AS count, "
            "SUM(CASE WHEN direction=1 THEN 1 ELSE 0 END) AS self, "
            "SUM(CASE WHEN direction=1 AND (CAST(strftime('%H',ts,'unixepoch','localtime') AS INT)>=23 "
            "      OR CAST(strftime('%H',ts,'unixepoch','localtime') AS INT)<5) THEN 1 ELSE 0 END) AS night "
            "FROM messages WHERE account_qq=? GROUP BY d ORDER BY d", (int(account_qq),)).fetchall()
        return [{"date": r["d"], "count": r["count"], "self": r["self"] or 0,
                 "night": r["night"] or 0} for r in rows]
    finally:
        con.close()


def hour_hist(account_qq: int, direction: int | None = 1) -> list[list[int]]:
    con = connect()
    try:
        sql = ("SELECT CAST(strftime('%H',ts,'unixepoch','localtime') AS INT) AS h, COUNT(*) AS n "
               "FROM messages WHERE account_qq=?")
        args = [int(account_qq)]
        if direction is not None:
            sql += " AND direction=?"
            args.append(int(direction))
        sql += " GROUP BY h ORDER BY h"
        got = {r["h"]: r["n"] for r in con.execute(sql, args).fetchall()}
        return [[h, got.get(h, 0)] for h in range(24)]
    finally:
        con.close()


def top_contacts(account_qq: int, limit: int = 10) -> list[dict]:
    con = connect()
    try:
        rows = con.execute(
            "SELECT kind, peer_id, peer_qq, name, remark, msg_count, self_count, last_ts "
            "FROM contacts WHERE account_qq=? ORDER BY msg_count DESC LIMIT ?",
            (int(account_qq), int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()
