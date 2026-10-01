"""QQScope · 统一主库（两个数据源汇聚到这里）

task-11：物理分账号。每个账号一个库：data/accounts/<qq>/qqscope.db；
data/accounts.db 只存 accounts 注册表。connect() 不带参 -> master；带参 -> 该账号库。
迁移前（data/accounts/ 为空且 data/qqscope.db 存在）读接口回退到旧单库（只读），
迁移由 server/app.py 启动时调用 migrate_legacy_split() 完成（先备份、旧库改名保留）。

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
import shutil
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterable

from . import paths

REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  account_qq INTEGER PRIMARY KEY,
  label      TEXT,
  source     TEXT,
  updated_at INTEGER
);
"""

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
  content     TEXT,
  media       TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_msg ON messages(
  account_qq, kind, peer_id, ts, direction, COALESCE(text,'')
);
CREATE INDEX IF NOT EXISTS ix_msg_peer ON messages(account_qq, kind, peer_id, ts);
CREATE INDEX IF NOT EXISTS ix_msg_ts   ON messages(ts);
"""


# ── 连接 ────────────────────────────────────────────────────────────────────
def _open(p, full: bool = True) -> sqlite3.Connection:
    p = pathlib.Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=30, uri=True)   # uri=True：让 ATTACH 也识别 file: URI
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    if full:
        con.executescript(SCHEMA)
        _migrate_columns(con)
    else:
        con.executescript(REGISTRY_SCHEMA)
    return con


def _open_legacy_ro() -> sqlite3.Connection:
    con = sqlite3.connect("file:" + paths.LEGACY_DB.as_posix() + "?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(SCHEMA)
    except Exception:  # noqa: BLE001
        pass
    return con


def _migrate_columns(con) -> None:
    """老库补列：messages.content / messages.media（幂等）。"""
    cols = {r[1] for r in con.execute("PRAGMA table_info(messages)")}
    for name, ddl in (("content", "TEXT"), ("media", "TEXT")):
        if cols and name not in cols:
            con.execute(f"ALTER TABLE messages ADD COLUMN {name} {ddl}")


def _seed_registry(con) -> None:
    """master 为空且旧单库存在时，把旧库 accounts 注册表读到 master（只读 ATTACH）。"""
    if not paths.legacy_present():
        return
    try:
        if con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]:
            return
        con.execute("ATTACH DATABASE ? AS legacy",
                    ("file:" + paths.LEGACY_DB.as_posix() + "?mode=ro",))
        con.execute("INSERT OR REPLACE INTO accounts SELECT * FROM legacy.accounts")
        con.commit()
        con.execute("DETACH DATABASE legacy")
    except Exception:  # noqa: BLE001
        try:
            con.execute("DETACH DATABASE legacy")
        except Exception:  # noqa: BLE001
            pass


def connect(account_qq=None) -> sqlite3.Connection:
    """带 account_qq -> 该账号库；不带 -> master 注册表。

    迁移前（还没有 data/accounts/ 且旧单库存在）读接口回退到旧单库（只读），
    保证迁移前 verify / build / 只读探测仍能工作，绝不会写旧库。
    """
    if account_qq:
        qq = int(account_qq)
        p = paths.account_db(qq)
        if p.exists():
            return _open(p, full=True)
        if paths.legacy_present() and not paths.accounts_split_done():
            return _open_legacy_ro()
        return _open(p, full=True)
    con = _open(paths.MASTER_DB, full=False)
    _seed_registry(con)
    return con


def init() -> None:
    """确保 master 注册表与目录存在（**不**自动迁移；迁移在 app 启动时做）。"""
    paths.ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    con = connect(None)
    con.close()


@contextmanager
def tx(account_qq=None):
    con = connect(account_qq)
    try:
        if account_qq:
            con.executescript(SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


# ── 迁移：旧单库 -> 每账号一个库 ────────────────────────────────────────────
def _table_cols(con, table: str, schema: str = "main") -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA {schema}.table_info({table})")]


def migrate_legacy_split(force: bool = False) -> dict:
    """把旧单库按 account_qq 拆分到 data/accounts/<qq>/qqscope.db。

    - 先备份到 data/backup/legacy_split_<ts>.db（sqlite backup，保证一致）；
    - 旧库迁移后改名 data/qqscope.db.migrated（连同 -wal/-shm），绝不删除；
    - 幂等：已经拆过或没有旧库时直接返回。
    """
    if not paths.LEGACY_DB.exists():
        return {"ok": False, "reason": "no_legacy", "message": "没有旧单库，无需迁移"}
    if paths.accounts_split_done() and not force:
        return {"ok": False, "reason": "already_split", "message": "data/accounts/ 已存在，跳过迁移"}

    src = sqlite3.connect("file:" + paths.LEGACY_DB.as_posix() + "?mode=ro", uri=True, timeout=60)
    src.row_factory = sqlite3.Row
    try:
        msg_cols = [r[1] for r in src.execute("PRAGMA table_info(messages)")]
        if msg_cols and "account_qq" not in msg_cols:
            legacy_dir = paths.DATA / "legacy"
            legacy_dir.mkdir(parents=True, exist_ok=True)
            dst = legacy_dir / f"qqscope_legacy_{int(time.time())}.db"
            paths.LEGACY_DB.rename(dst)
            for suf in ("-wal", "-shm"):
                f = pathlib.Path(str(paths.LEGACY_DB) + suf)
                if f.exists():
                    f.rename(pathlib.Path(str(dst) + suf))
            return {"ok": False, "reason": "legacy_schema", "archived": str(dst),
                    "message": "旧库没有 account_qq 列，已归档到 data/legacy/（未拆分）"}
        accounts = [int(r[0]) for r in src.execute("SELECT account_qq FROM accounts")]

        paths.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup = paths.BACKUP_DIR / f"legacy_split_{ts}.db"
        dstc = sqlite3.connect(str(backup))
        src.backup(dstc)
        dstc.close()

        # master 注册表
        m = _open(paths.MASTER_DB, full=False)
        m.execute("ATTACH DATABASE ? AS legacy",
                  ("file:" + paths.LEGACY_DB.as_posix() + "?mode=ro",))
        m.execute("INSERT OR REPLACE INTO accounts SELECT * FROM legacy.accounts")
        m.commit()
        m.execute("DETACH DATABASE legacy")
        m.close()

        stats: dict = {}
        for qq in accounts:
            con = _open(paths.account_db(qq), full=True)
            con.execute("ATTACH DATABASE ? AS legacy",
                        ("file:" + paths.LEGACY_DB.as_posix() + "?mode=ro",))
            for tbl in ("contacts", "messages", "feeds"):
                try:
                    src_cols = _table_cols(src, tbl, schema="main")
                except Exception:  # noqa: BLE001
                    src_cols = []
                if not src_cols:
                    continue
                dst_cols = _table_cols(con, tbl, schema="main")
                if not dst_cols:
                    # 目标库还没有该表（如 feeds 由 qzone 建）：按旧库 DDL 建一份
                    try:
                        ddl = src.execute(
                            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                            (tbl,)).fetchone()
                        if ddl and ddl[0]:
                            con.execute(ddl[0])
                            dst_cols = _table_cols(con, tbl, schema="main")
                    except Exception:  # noqa: BLE001
                        pass
                cols = [c for c in dst_cols if c in src_cols]
                if not cols:
                    continue
                cl = ",".join(cols)
                try:
                    con.execute(
                        f"INSERT OR IGNORE INTO main.{tbl}({cl}) "
                        f"SELECT {cl} FROM legacy.{tbl} WHERE account_qq=?", (qq,))
                except Exception:  # noqa: BLE001
                    pass
            con.execute("INSERT OR REPLACE INTO accounts SELECT * FROM legacy.accounts "
                        "WHERE account_qq=?", (qq,))
            con.commit()
            try:
                con.execute("DETACH DATABASE legacy")
            except Exception:  # noqa: BLE001
                pass
            stats[qq] = {
                "contacts": con.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],
                "messages": con.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                "feeds": con.execute("SELECT COUNT(*) FROM feeds").fetchone()[0]
                if _table_exists(con, "feeds") else 0,
            }
            con.close()

        # 旧库改名保留（绝不删除）；Windows 下先关掉只读连接再改名
        try:
            src.close()
        except Exception:  # noqa: BLE001
            pass
        moved = paths.LEGACY_DB.with_name(paths.LEGACY_DB.name + ".migrated")
        n = 1
        while moved.exists():
            moved = paths.LEGACY_DB.with_name(paths.LEGACY_DB.name + f".migrated{n}")
            n += 1
        paths.LEGACY_DB.rename(moved)
        for suf in ("-wal", "-shm"):
            f = pathlib.Path(str(paths.LEGACY_DB) + suf)
            if f.exists():
                try:
                    f.rename(pathlib.Path(str(moved) + suf))
                except OSError:
                    pass
        return {"ok": True, "accounts": accounts, "stats": stats,
                "backup": str(backup), "moved": str(moved),
                "message": f"已拆分 {len(accounts)} 个账号；旧库备份 {backup.name}，原名改为 {moved.name}"}
    finally:
        src.close()


def _table_exists(con, table: str) -> bool:
    try:
        return bool(con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())
    except Exception:  # noqa: BLE001
        return False


# ── 账号 ────────────────────────────────────────────────────────────────────
def upsert_account(account_qq: int, label: str | None = None,
                   source: str | None = None) -> None:
    qq = int(account_qq)
    now = int(time.time())
    with tx(None) as con:
        con.execute(
            "INSERT INTO accounts(account_qq,label,source,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(account_qq) DO UPDATE SET "
            "label=COALESCE(excluded.label,accounts.label), "
            "source=COALESCE(excluded.source,accounts.source), updated_at=excluded.updated_at",
            (qq, label, source, now))
    if paths.account_db(qq).exists():
        with tx(qq) as con:
            con.execute(
                "INSERT INTO accounts(account_qq,label,source,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(account_qq) DO UPDATE SET "
                "label=COALESCE(excluded.label,accounts.label), "
                "source=COALESCE(excluded.source,accounts.source), updated_at=excluded.updated_at",
                (qq, label, source, now))


def list_accounts() -> list[dict]:
    con = connect(None)
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM accounts ORDER BY account_qq")]
    finally:
        con.close()
    out: list[dict] = []
    for a in rows:
        qq = int(a.get("account_qq") or 0)
        if not qq:
            continue
        counts = {"contacts": 0, "messages": 0, "self_messages": 0,
                  "first_ts": None, "last_ts": None}
        try:
            c2 = connect(qq)
            try:
                counts["contacts"] = c2.execute(
                    "SELECT COUNT(*) FROM contacts WHERE account_qq=?", (qq,)).fetchone()[0]
                counts["messages"] = c2.execute(
                    "SELECT COUNT(*) FROM messages WHERE account_qq=?", (qq,)).fetchone()[0]
                counts["self_messages"] = c2.execute(
                    "SELECT COUNT(*) FROM messages WHERE account_qq=? AND direction=1",
                    (qq,)).fetchone()[0]
                counts["first_ts"] = c2.execute(
                    "SELECT MIN(ts) FROM messages WHERE account_qq=?", (qq,)).fetchone()[0]
                counts["last_ts"] = c2.execute(
                    "SELECT MAX(ts) FROM messages WHERE account_qq=?", (qq,)).fetchone()[0]
            finally:
                c2.close()
        except Exception:  # noqa: BLE001
            pass
        out.append({"account_qq": qq, "label": a.get("label"), "source": a.get("source"),
                    "updated_at": a.get("updated_at"), **counts})
    return out


def delete_account(account_qq: int) -> None:
    """删该账号目录（含库与文件），并从注册表移除。"""
    qq = int(account_qq)
    with tx(None) as con:
        con.execute("DELETE FROM accounts WHERE account_qq=?", (qq,))
    d = paths.account_dir(qq, create=False)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


# ── 联系人/会话 ─────────────────────────────────────────────────────────────
CONTACT_FIELDS = ("account_qq", "kind", "peer_id", "peer_qq", "name", "remark",
                  "avatar", "msg_count", "self_count", "first_ts", "last_ts",
                  "last_text", "source")


def _group_by_account(rows: Iterable[dict]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for r in rows:
        try:
            qq = int(r.get("account_qq") or 0)
        except (TypeError, ValueError):
            qq = 0
        if not qq:
            continue
        out.setdefault(qq, []).append(r)
    return out


def upsert_contacts(rows: Iterable[dict]) -> int:
    """只更新非 None 字段，避免覆盖已有昵称/备注；按 account_qq 分库写入。"""
    rows = list(rows)
    if not rows:
        return 0
    n = 0
    for qq, rs in _group_by_account(rows).items():
        with tx(qq) as con:
            for r in rs:
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
                n += 1
    return n


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
    con = connect(account_qq)
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def get_contact(account_qq: int, kind: str, peer_id: str) -> dict | None:
    con = connect(account_qq)
    try:
        r = con.execute("SELECT * FROM contacts WHERE account_qq=? AND kind=? AND peer_id=?",
                        (int(account_qq), kind, str(peer_id))).fetchone()
        return dict(r) if r else None
    finally:
        con.close()


def set_contact_meta(account_qq: int, kind: str, peer_id: str,
                     name: str | None = None, remark: str | None = None,
                     avatar: str | None = None) -> None:
    with tx(account_qq) as con:
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
    """去重导入（唯一索引 ux_msg）；按 account_qq 分库写入。返回本次新增条数。"""
    rows = list(rows)
    if not rows:
        return 0
    added = 0
    for qq, rs in _group_by_account(rows).items():
        con = connect(qq)
        try:
            con.executescript(SCHEMA)
            _migrate_columns(con)
            before = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            con.executemany(
                f"INSERT OR IGNORE INTO messages({','.join(MSG_FIELDS)}) "
                f"VALUES({','.join('?'*len(MSG_FIELDS))})",
                [tuple(r.get(f) for f in MSG_FIELDS) for r in rs])
            con.commit()
            after = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            added += after - before
        finally:
            con.close()
    return added


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
    con = connect(account_qq)
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def count_messages(account_qq: int | None = None, kind: str | None = None,
                   peer_id: str | None = None) -> int:
    if not account_qq:
        raise ValueError("count_messages 必须指定 account_qq（多账号隔离要求）")
    sql = "SELECT COUNT(*) FROM messages WHERE account_qq=?"
    args: list[Any] = [int(account_qq)]
    if kind:
        sql += " AND kind=?"; args.append(kind)
    if peer_id:
        sql += " AND peer_id=?"; args.append(str(peer_id))
    con = connect(account_qq)
    try:
        return con.execute(sql, args).fetchone()[0]
    finally:
        con.close()


def search_messages(query: str, limit: int = 200,
                    account_qq: int | None = None) -> list[dict]:
    """全文搜索。必须显式指定 account_qq，绝不跨账号返回（多账号隔离硬约束）。"""
    if not account_qq:
        raise ValueError("search_messages 必须指定 account_qq（多账号隔离要求）")
    con = connect(account_qq)
    try:
        return [dict(r) for r in con.execute(
            "SELECT * FROM messages WHERE account_qq=? AND text LIKE ? "
            "ORDER BY ts DESC LIMIT ?",
            (int(account_qq), f"%{query}%", int(limit))).fetchall()]
    finally:
        con.close()


def refresh_contact_stats(account_qq: int) -> int:
    """根据 messages 重算 contacts 的统计字段（导入后调用）。"""
    with tx(account_qq) as con:
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
    con = connect(account_qq)
    try:
        w = "WHERE account_qq=?"
        a: list[Any] = [int(account_qq)]
        g = lambda s: con.execute(s, a).fetchone()[0]
        total = g(f"SELECT COUNT(*) FROM messages {w}")
        if not total:
            return {"total": 0, "self": 0, "c2c": 0, "group": 0,
                    "contacts": 0, "first_ts": None, "last_ts": None, "kinds": []}
        return {
            "total": total,
            "self": g(f"SELECT COUNT(*) FROM messages {w} AND direction=1"),
            "c2c": g(f"SELECT COUNT(*) FROM messages {w} AND kind='c2c'"),
            "group": g(f"SELECT COUNT(*) FROM messages {w} AND kind='group'"),
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
    con = connect(account_qq)
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
    con = connect(account_qq)
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
    con = connect(account_qq)
    try:
        rows = con.execute(
            "SELECT kind, peer_id, peer_qq, name, remark, msg_count, self_count, last_ts "
            "FROM contacts WHERE account_qq=? ORDER BY msg_count DESC LIMIT ?",
            (int(account_qq), int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()