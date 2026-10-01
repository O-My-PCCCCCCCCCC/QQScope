# -*- coding: utf-8 -*-
"""QQScope · c2c peer_id 归一化 + 数据健康度（task-19）

问题：两个数据源的会话主键格式不一致 ——
    pack（窗口A）：peer_id = NT UID（u_xxx）
    bot （窗口B）：peer_id = str(qq)
OneBot 拿不到 NT UID，所以统一到 **QQ 号**；完全没有 QQ 号可查的才保留 uid。

对外：
    backup_store()                                  # 迁移前备份（sqlite backup API，WAL 安全）
    migrate_c2c_peer_id(account_qq=None, backup=True)  # 幂等迁移 + 合并去重
    duplicate_stats(account_qq=None)                # 数据健康度指标

注意：不改 core/store.py 的 schema，只改数据。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from core import paths, store

BACKUP_DIR = paths.DATA / "backup"


# ── 备份 ────────────────────────────────────────────────────────────────────
def backup_store() -> Path:
    """用 sqlite3 的 backup API 做一致性快照（WAL 模式下直接拷文件可能不一致）。"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dst = BACKUP_DIR / f"qqscope_before_merge_{time.strftime('%Y%m%d_%H%M%S')}.db"
    src = sqlite3.connect(f"file:{paths.STORE_DB}?mode=ro", uri=True)
    out = sqlite3.connect(str(dst))
    try:
        src.backup(out)
        out.commit()
    finally:
        out.close()
        src.close()
    return dst


# ── 映射与指标 ──────────────────────────────────────────────────────────────
def _uid_to_qq(con, account_qq=None) -> dict:
    """(account, uid) -> qq，只取 peer_qq>0 的行。"""
    where = "kind='c2c' AND peer_id LIKE 'u_%' AND peer_qq IS NOT NULL AND peer_qq>0"
    args: list = []
    if account_qq:
        where += " AND account_qq=?"
        args.append(int(account_qq))
    out: dict = {}
    for r in con.execute(
            f"SELECT account_qq, peer_id, MAX(peer_qq) AS qq FROM messages "
            f"WHERE {where} GROUP BY account_qq, peer_id", args):
        out[(int(r["account_qq"]), str(r["peer_id"]))] = int(r["qq"])
    return out


def _scalar(con, sql: str, args=()) -> int:
    row = con.execute(sql, args).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def duplicate_stats(account_qq=None) -> dict:
    """数据健康度：c2c 会话数 vs 唯一 QQ 数，uid 形态残留量。"""
    con = store.connect()
    try:
        args = [int(account_qq)] if account_qq else []
        w = " AND account_qq=?" if account_qq else ""
        contacts = _scalar(con, f"SELECT COUNT(*) FROM contacts WHERE kind='c2c'{w}", args)
        distinct_qq = _scalar(
            con, f"SELECT COUNT(DISTINCT peer_qq) FROM contacts WHERE kind='c2c' AND peer_qq>0{w}", args)
        uid_contacts = _scalar(
            con, f"SELECT COUNT(*) FROM contacts WHERE kind='c2c' AND peer_id LIKE 'u_%'{w}", args)
        uid_msgs = _scalar(
            con, f"SELECT COUNT(*) FROM messages WHERE kind='c2c' AND peer_id LIKE 'u_%'{w}", args)
        uid_msgs_no_qq = _scalar(
            con, "SELECT COUNT(*) FROM messages WHERE kind='c2c' AND peer_id LIKE 'u_%' "
                 f"AND (peer_qq IS NULL OR peer_qq=0){w}", args)
        return {
            "account_qq": int(account_qq) if account_qq else None,
            "c2c_contacts": contacts,
            "distinct_peer_qq": distinct_qq,
            "duplicate_contacts": max(0, contacts - distinct_qq),
            "uid_contacts": uid_contacts,
            "uid_messages": uid_msgs,
            "uid_messages_without_qq": uid_msgs_no_qq,
            "healthy": (contacts == distinct_qq) and uid_contacts == 0,
        }
    finally:
        con.close()


# ── 迁移 ────────────────────────────────────────────────────────────────────
def migrate_c2c_peer_id(account_qq=None, backup: bool = True) -> dict:
    """把 c2c 的 peer_id 统一成 QQ 号字符串，并合并重复会话。幂等。

    返回统计：合并会话数 / 删除重复消息数 / 前后会话数 / 备份路径。
    """
    con = store.connect()
    try:
        con.executescript(store.SCHEMA)
        acc = int(account_qq) if account_qq else None
        acct = " AND account_qq=?" if acc else ""
        args = [acc] if acc else []

        before = duplicate_stats(acc)
        # 先看有没有活干：没事就不备份、零副作用（保证第二遍零变化）
        uid2qq = _uid_to_qq(con, acc)
        if not uid2qq:
            con.commit()
            return {"ok": True, "changed": False, "message": "没有需要归一化的 c2c 会话（幂等：零变化）",
                    "backup": None, "before": before, "after": before,
                    "contacts_merged": 0, "contacts_renamed": 0,
                    "messages_updated": 0, "messages_deduped": 0, "elapsed": 0.0}
        backup_path = str(backup_store()) if backup else None

        t0 = time.monotonic()
        con.execute("BEGIN")

        # ── 1) messages：先查重，删掉与目标键冲突的行，再改写 ──
        rows = con.execute(
            "SELECT id, account_qq, peer_id, peer_qq, ts, direction, COALESCE(text,'') AS t "
            f"FROM messages WHERE kind='c2c' AND peer_id LIKE 'u_%'{acct} ORDER BY id", args).fetchall()
        taken: set = set()
        for r in con.execute(
                "SELECT account_qq, peer_id, ts, direction, COALESCE(text,'') FROM messages "
                f"WHERE kind='c2c' AND peer_id NOT LIKE 'u_%'{acct}", args):
            taken.add((int(r[0]), str(r[1]), int(r[2]), int(r[3]), r[4]))
        dedupe: list[int] = []
        updates: list[tuple] = []
        for r in rows:
            qq = uid2qq.get((int(r["account_qq"]), str(r["peer_id"])))
            if not qq:
                continue
            key = (int(r["account_qq"]), str(qq), int(r["ts"]), int(r["direction"]), r["t"])
            if key in taken:
                dedupe.append(int(r["id"]))
            else:
                taken.add(key)
                updates.append((str(qq), qq, int(r["id"])))
        if dedupe:
            con.executemany("DELETE FROM messages WHERE id=?", [(i,) for i in dedupe])
        if updates:
            con.executemany("UPDATE messages SET peer_id=?, peer_qq=? WHERE id=?", updates)

        # ── 2) contacts：合并字段（优先 uid 行的非空值），再改键 ──
        crows = con.execute(
            "SELECT account_qq, peer_id, name, remark, avatar FROM contacts "
            f"WHERE kind='c2c' AND peer_id LIKE 'u_%'{acct}", args).fetchall()
        merged = renamed = 0
        for r in crows:
            qq = uid2qq.get((int(r["account_qq"]), str(r["peer_id"])))
            if not qq:
                continue
            target = str(qq)
            t = con.execute(
                "SELECT name, remark, avatar FROM contacts WHERE account_qq=? AND kind='c2c' AND peer_id=?",
                (int(r["account_qq"]), target)).fetchone()
            if t is None:
                con.execute(
                    "UPDATE contacts SET peer_id=?, peer_qq=? WHERE account_qq=? AND kind='c2c' AND peer_id=?",
                    (target, qq, int(r["account_qq"]), str(r["peer_id"])))
                renamed += 1
            else:
                name = r["name"] or t["name"]
                remark = r["remark"] or t["remark"]
                avatar = r["avatar"] or t["avatar"]
                con.execute(
                    "UPDATE contacts SET name=?, remark=?, avatar=?, peer_qq=? "
                    "WHERE account_qq=? AND kind='c2c' AND peer_id=?",
                    (name, remark, avatar, qq, int(r["account_qq"]), target))
                con.execute("DELETE FROM contacts WHERE account_qq=? AND kind='c2c' AND peer_id=?",
                            (int(r["account_qq"]), str(r["peer_id"])))
                merged += 1
        con.commit()

        # ── 3) 重算联系人统计 ──
        for a in (set(int(r["account_qq"]) for r in rows) or ({acc} if acc else set())):
            try:
                store.refresh_contact_stats(a)
            except Exception:  # noqa: BLE001
                pass

        after = duplicate_stats(acc)
        elapsed = round(time.monotonic() - t0, 2)
        return {
            "ok": True, "changed": bool(merged or renamed or updates or dedupe),
            "message": (f"c2c 会话归一化完成：contacts {before['c2c_contacts']} -> {after['c2c_contacts']}"
                        f"（合并 {merged}、重命名 {renamed}），消息改写 {len(updates)} 条、"
                        f"去重删除 {len(dedupe)} 条"),
            "backup": backup_path, "before": before, "after": after,
            "contacts_merged": merged, "contacts_renamed": renamed,
            "messages_updated": len(updates), "messages_deduped": len(dedupe),
            "elapsed": elapsed,
        }
    except Exception:
        try:
            con.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        con.close()