# -*- coding: utf-8 -*-
"""QQScope · 昵称 / 群名 / 备注补全（窗口 A 的增强步骤）

数据来源（QQ 9.9.36，与 nt_msg.db 同一把密钥、同一套解密参数）：
  · ``profile_info.db``  → ``profile_info_v6``（1000=uid, 1002=QQ, 20002=昵称,
    20009=备注, 20011=个性签名）、``buddy_list``（1000=uid, 1002=QQ）
  · ``group_info.db``    → ``group_list``（60001=群号, 60007=群名）、
    ``group_detail_info_ver1``（同名列，群名更全）

设计要点：
  · 本文件**只依赖标准库**。sqlcipher3 只装在 ``tools/nt_msg_db_util/.venv``，
    所以：本进程能 import sqlcipher3 就地解密；否则用 venv 里的 python 以
    ``--json`` 模式重新调用本文件，通过 stdout 的 ``@@NAMES@@ {json}`` 取结果。
  · 解密参数顺序严格固定（顺序错了会直接报 "file is not a database"）：
      cipher_page_size=4096 → key → kdf_iter=4000
      → cipher_hmac_algorithm=HMAC_SHA1 → cipher_kdf_algorithm=PBKDF2_HMAC_SHA512
  · 复制 → 跳过前 1024 字节 NTQQ 自定义头 → sqlcipher3；带 WAL 失败则退回不带。
  · 密钥按候选顺序逐个尝试（data/keys 里可能是旧密钥，助手会自动回退到
    qq-export/db_key.txt）——与 core/sources/_pack_extract.py 的策略一致。
  · 中间产物只写 ``data/decrypt/<qq>/``，文件名与 nt_msg 系列完全错开。
  · 所有失败都转成 ``{"ok": False, "message": "…"}``，绝不抛给调用方。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from core import paths

HEADER_SIZE = 1024                     # NTQQ 自定义头长度（字节）
NAME_DBS = ("profile_info.db", "group_info.db")
JSON_PREFIX = "@@NAMES@@ "             # 子进程协议前缀
SUB_TIMEOUT = 180.0                    # 子进程硬超时（秒）


class NamesError(Exception):
    """带人话中文说明的昵称补全错误。"""


# ── 基础工具 ────────────────────────────────────────────────────────────────
def _decode_bytes(b: bytes) -> str:
    """TEXT 列的兜底解码：优先 UTF-8；少数记录是 GBK；都失败就替换。"""
    if isinstance(b, (bytes, bytearray, memoryview)):
        b = bytes(b)
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return b.decode("gbk")
            except UnicodeDecodeError:
                return b.decode("utf-8", "replace")
    return str(b)


def _clean(v) -> str | None:
    """规范化字符串字段：去空白；空串 → None。"""
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray, memoryview)):
        v = _decode_bytes(v)
    s = str(v).strip()
    if not s:
        return None
    return s.strip("\x00").strip() or None


def _to_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _unlink(p: Path) -> None:
    try:
        if p.exists():
            p.unlink()
    except OSError:
        pass


def _have_sqlcipher() -> bool:
    try:
        import sqlcipher3.dbapi2  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _venv_python() -> Path | None:
    p = paths.python_executable()
    return p if p.exists() else None


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


# ── 路径 / 密钥解析 ─────────────────────────────────────────────────────────
def _nt_db_dir(account_qq: int, data_root) -> Path:
    """定位 ``<data_root>/<qq>/nt_qq/nt_db``；也兼容 data_root 本身就是账号目录。"""
    root = Path(data_root or paths.DEFAULT_DATA_ROOT)
    cands = [root / str(int(account_qq)) / "nt_qq" / "nt_db",
             root / "nt_qq" / "nt_db"]
    for c in cands:
        if (c / "profile_info.db").exists() or (c / "group_info.db").exists() \
                or (c / "nt_msg.db").exists():
            return c
    return cands[0]


def _candidate_keys(account_qq: int, key: str | None) -> list[str]:
    """候选密钥（去重保序）：显式 key → data/keys/<qq>.key → qq-export/db_key.txt。

    data/keys 里存的可能是旧密钥，所以不能只用第一个，必须逐个试。
    """
    out: list[str] = []
    seen: set[str] = set()

    def add(v) -> None:
        s = (str(v).strip() if v is not None else "")
        if s and s not in seen:
            seen.add(s)
            out.append(s)

    add(key)
    f = Path(paths.KEYS_DIR) / f"{int(account_qq)}.key"
    if f.exists():
        try:
            add(f.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            pass
    legacy = Path(paths.LEGACY_KEY_FILE)
    if legacy.exists():
        try:
            add(legacy.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            pass
    return out


def _strip_header(src: Path, dst: Path) -> None:
    """跳过前 1024 字节 NTQQ 自定义头，其余原样写出。"""
    size = src.stat().st_size
    if size <= HEADER_SIZE:
        raise NamesError(f"{src.name} 文件过小（{size} 字节），不是有效的 NTQQ 数据库")
    with open(src, "rb") as f:
        f.seek(HEADER_SIZE)
        with open(dst, "wb") as o:
            shutil.copyfileobj(f, o, 8 << 20)


def _open_encrypted(src_dir: Path, name: str, work: Path, key: str):
    """复制 → 剥头 → sqlcipher3（PRAGMA 顺序固定）。返回已连接的 con。"""
    import sqlcipher3.dbapi2 as sc

    src = Path(src_dir) / name
    if not src.exists():
        raise NamesError(f"找不到数据库：{src}")
    work.mkdir(parents=True, exist_ok=True)

    # 1) 快照（含 -wal / -shm），文件名与 nt_msg 系列错开
    dst = work / name
    shutil.copy2(src, dst)
    for suf in ("-wal", "-shm"):
        s = Path(str(src) + suf)
        d = Path(str(dst) + suf)
        _unlink(d)
        if s.exists():
            shutil.copy2(s, d)

    # 2) 剥头
    clear = work / (name[:-3] + "_clear.db")
    _strip_header(dst, clear)

    # 3) 解密：先带 WAL，失败退回不带 WAL
    last: Exception | None = None
    for use_wal in (True, False):
        for suf in ("-wal", "-shm"):
            w = work / (name + suf)
            c = Path(str(clear) + suf)
            _unlink(c)
            if use_wal and w.exists():
                shutil.copy2(w, c)
        con = None
        try:
            con = sc.connect(str(clear), isolation_level=None)
            con.execute("PRAGMA cipher_page_size = 4096;")
            con.execute("PRAGMA key = '%s';" % str(key).replace("'", "''"))
            con.execute("PRAGMA kdf_iter = 4000;")
            con.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA1;")
            con.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512;")
            con.execute("SELECT COUNT(*) FROM sqlite_master;").fetchone()
            con.text_factory = _decode_bytes
            return con
        except Exception as exc:  # noqa: BLE001
            last = exc
            if con is not None:
                try:
                    con.close()
                except Exception:  # noqa: BLE001
                    pass
    raise NamesError(f"{name} 解密失败（密钥不匹配或文件损坏）：{last}")


# ── 解析 ────────────────────────────────────────────────────────────────────
def _read_profile(con, friends: dict, people: dict) -> int:
    """返回 buddy_list 行数。friends 按 QQ 索引，people 按 uid 索引。"""
    by_uid: dict[str, dict] = {}
    by_qq: dict[int, dict] = {}
    try:
        rows = con.execute(
            'SELECT "1000","1002","20002","20009" FROM profile_info_v6').fetchall()
    except Exception as exc:  # noqa: BLE001
        raise NamesError(f"profile_info_v6 读取失败：{exc}") from exc

    for uid, qq, nick, remark in rows:
        name = _clean(nick)
        if not name:
            continue
        u = (str(uid).strip() if uid else "") or None
        q = _to_int(qq)
        rec = {"name": name, "remark": _clean(remark), "qq": q, "uid": u}
        if u:
            by_uid[u] = rec
            people[u] = {"name": name, "remark": rec["remark"], "qq": q}
        if q > 0:
            by_qq.setdefault(q, rec)

    n_buddy = 0
    try:
        for uid, qq in con.execute('SELECT "1000","1002" FROM buddy_list'):
            n_buddy += 1
            u = (str(uid).strip() if uid else "") or None
            q = _to_int(qq)
            rec = (by_uid.get(u) if u else None) or by_qq.get(q)
            if not rec:
                continue
            key_qq = rec["qq"] if rec["qq"] > 0 else q
            if key_qq <= 0:
                continue
            friends[key_qq] = {"name": rec["name"], "remark": rec["remark"],
                               "uid": rec["uid"] or u}
    except Exception:  # noqa: BLE001
        pass
    return n_buddy


def _read_groups(con, groups: dict) -> None:
    """group_list 优先，group_detail_info_ver1 补全缺失的群名。"""
    for tbl in ("group_list", "group_detail_info_ver1"):
        try:
            rows = con.execute(f'SELECT "60001","60007" FROM "{tbl}"').fetchall()
        except Exception:  # noqa: BLE001
            continue
        for gq, nm in rows:
            g = _to_int(gq)
            name = _clean(nm)
            if g > 0 and name and g not in groups:
                groups[g] = {"name": name}


# ── 就地执行（需要 sqlcipher3） ──────────────────────────────────────────────
def _empty(message: str, t0: float, notes=None) -> dict:
    stats = {"friends": 0, "groups": 0, "people": 0, "buddy_list": 0,
             "elapsed": round(time.monotonic() - t0, 2)}
    if notes:
        stats["notes"] = notes
    return {"ok": False, "message": message, "friends": {}, "groups": {},
            "people": {}, "stats": stats}


def _fetch_local(account_qq: int, data_root=None, key: str | None = None,
                 progress=None) -> dict:
    t0 = time.monotonic()
    qq = int(account_qq)
    try:
        raw_dir = _nt_db_dir(qq, data_root)
    except Exception as exc:  # noqa: BLE001
        return _empty(f"定位数据目录失败：{exc}", t0)

    keys = _candidate_keys(qq, key)
    if not keys:
        return _empty("没有可用密钥（data/keys/<qq>.key 或 qq-export/db_key.txt）", t0)

    work = paths.account_decrypt(qq)
    present = [n for n in NAME_DBS if (raw_dir / n).exists()]
    if not present:
        return _empty(f"在 {raw_dir} 找不到 profile_info.db / group_info.db", t0)

    last_notes: list[str] = []
    for i, k in enumerate(keys):
        if progress:
            try:
                progress("昵称", 5, f"尝试第 {i + 1}/{len(keys)} 个密钥…")
            except Exception:  # noqa: BLE001
                pass
        friends: dict = {}
        groups: dict = {}
        people: dict = {}
        notes: list[str] = []
        n_buddy = 0
        got_any = False
        for name in NAME_DBS:
            src = raw_dir / name
            if not src.exists():
                notes.append(f"缺 {name}")
                continue
            con = None
            try:
                con = _open_encrypted(raw_dir, name, work, k)
                if name == "profile_info.db":
                    n_buddy = _read_profile(con, friends, people)
                else:
                    _read_groups(con, groups)
                got_any = True
            except Exception as exc:  # noqa: BLE001
                notes.append(f"{name}：{exc}")
            finally:
                if con is not None:
                    try:
                        con.close()
                    except Exception:  # noqa: BLE001
                        pass
        if got_any:
            stats = {
                "friends": len(friends),
                "groups": len(groups),
                "people": len(people),
                "buddy_list": n_buddy,
                "key_source": ("显式传入" if (key and k == str(key).strip()) else
                               f"候选第 {i + 1} 个"),
                "elapsed": round(time.monotonic() - t0, 2),
            }
            if notes:
                stats["notes"] = notes
            return {"ok": True,
                    "message": f"好友昵称 {len(friends)} 条、群名 {len(groups)} 条",
                    "friends": friends, "groups": groups, "people": people,
                    "stats": stats}
        last_notes = notes

    return _empty(f"{len(keys)} 个候选密钥都打不开昵称库（{'；'.join(last_notes) or '未知原因'}）",
                  t0, notes=last_notes)


def _run_subprocess(account_qq: int, data_root=None, key: str | None = None) -> dict:
    """没有 sqlcipher3 时，用 venv 里的 python 以 --json 模式重新调用本文件。"""
    py = _venv_python()
    if py is None:
        return _empty(f"缺少解密环境：{py}（需要 sqlcipher3）", time.monotonic())
    root = _project_root()
    cmd = [str(py), str(Path(__file__).resolve()), "--qq", str(int(account_qq)), "--json"]
    if data_root:
        cmd += ["--data-root", str(data_root)]
    if key:
        cmd += ["--key", str(key)]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8", errors="replace",
                              env=env, cwd=str(root), timeout=SUB_TIMEOUT)
    except subprocess.TimeoutExpired:
        return _empty(f"昵称库解密超时（>{SUB_TIMEOUT:.0f}s）", time.monotonic())
    except OSError as exc:
        return _empty(f"无法启动昵称解密子进程：{exc}", time.monotonic())
    for line in (proc.stdout or "").splitlines():
        if line.startswith(JSON_PREFIX):
            try:
                return json.loads(line[len(JSON_PREFIX):])
            except json.JSONDecodeError as exc:
                return _empty(f"昵称子进程输出无法解析：{exc}", time.monotonic())
    tail = ((proc.stderr or "") or (proc.stdout or "")).strip()[-400:]
    return _empty(f"昵称子进程异常退出（代码 {proc.returncode}）：{tail or '没有输出'}",
                  time.monotonic())


def fetch_names(account_qq: int, data_root: str | None = None,
                key: str | None = None) -> dict:
    """抓取好友昵称 / 群名 / 备注。

    返回 ``{"ok", "message", "friends", "groups", "people", "stats"}``：
      friends : {<qq:int>: {"name","remark","uid"}}     好友列表解析到的昵称
      groups  : {<group_qq:int>: {"name"}}              群号 → 群名
      people  : {<uid:str>: {"name","remark","qq"}}     所有有昵称的人，供反查补全
      stats   : {"friends","groups","people","buddy_list","elapsed", …}
    """
    if _have_sqlcipher():
        return _fetch_local(account_qq, data_root, key)
    return _run_subprocess(account_qq, data_root, key)


# ── 写回主库 ────────────────────────────────────────────────────────────────
def _avatar_url(qq) -> str | None:
    q = _to_int(qq)
    if q <= 0:
        return None
    return f"https://q1.qlogo.cn/g?b=qq&nk={q}&s=640"


def apply_to_store(account_qq: int, names: dict) -> int:
    """把昵称/群名写回 ``store.contacts``，返回写回的会话数。

    匹配顺序（私聊）：peer_id(uid) → peer_qq → peer_id(QQ 号字符串)。
    """
    from core import store

    if not isinstance(names, dict) or not names.get("ok", True):
        return 0
    friends = names.get("friends") or {}
    groups = names.get("groups") or {}
    people = names.get("people") or {}

    # 注意：走子进程时整包 JSON 往返会把 dict 的 int 键变成字符串，
    # 所以这里统一重新归一化成 int / str，不能直接拿原键去 get()。
    groups_idx: dict[int, dict] = {}
    for g, rec in groups.items():
        rec = rec or {}
        if not rec.get("name"):
            continue
        gi = _to_int(g)
        if gi > 0:
            groups_idx.setdefault(gi, rec)

    # uid / qq 两套反查索引
    uid_idx: dict[str, dict] = {}
    qq_idx: dict[int, dict] = {}
    for uid, rec in people.items():
        rec = rec or {}
        if not rec.get("name"):
            continue
        uid_idx[str(uid)] = rec
        q = _to_int(rec.get("qq"))
        if q > 0:
            qq_idx.setdefault(q, rec)
    for q, rec in friends.items():
        rec = rec or {}
        if not rec.get("name"):
            continue
        q = _to_int(q)
        if q > 0:
            qq_idx.setdefault(q, rec)
        u = rec.get("uid")
        if u:
            uid_idx.setdefault(str(u), rec)

    def _lookup_c2c(pid: str, pq) -> dict | None:
        rec = uid_idx.get(pid)
        if rec:
            return rec
        if pq:
            rec = qq_idx.get(_to_int(pq))
            if rec:
                return rec
        if pid.lstrip("-").isdigit():
            return qq_idx.get(_to_int(pid))
        return None

    try:
        contacts = store.list_contacts(int(account_qq), limit=200000)
    except Exception:  # noqa: BLE001
        return 0

    applied = 0
    for c in contacts:
        kind = c.get("kind")
        pid = str(c.get("peer_id") or "")
        if not pid:
            continue
        rec = None
        avatar = None
        if kind == "c2c":
            rec = _lookup_c2c(pid, c.get("peer_qq"))
            avatar = _avatar_url((rec or {}).get("qq") or c.get("peer_qq"))
        elif kind == "group":
            g = _to_int(pid) if pid.lstrip("-").isdigit() else 0
            pq = _to_int(c.get("peer_qq"))
            rec = groups_idx.get(g) or (groups_idx.get(pq) if pq else None)
        else:
            continue
        if not rec or not rec.get("name"):
            continue

        # 只补空、不覆盖已有值：与 store.upsert_contacts 的 COALESCE 策略一致，
        # 也避免冲掉用户在 Web 上手工改过的名字。
        nm = None if (c.get("name") or "").strip() else rec.get("name")
        rm = None if (c.get("remark") or "").strip() else (rec.get("remark") or None)
        av = None if (c.get("avatar") or "").strip() else avatar
        if not (nm or rm or av):
            continue
        try:
            store.set_contact_meta(int(account_qq), kind, c.get("peer_id"),
                                   name=nm, remark=rm, avatar=av)
            applied += 1
        except Exception:  # noqa: BLE001
            continue
    return applied


# ── CLI（供 venv python 以 --json 模式调用） ────────────────────────────────
def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="QQScope 昵称/群名提取")
    ap.add_argument("--qq", type=int, required=True)
    ap.add_argument("--data-root", default="")
    ap.add_argument("--key", default="")
    ap.add_argument("--json", action="store_true", help="输出 @@NAMES@@ {json} 协议行")
    args = ap.parse_args(argv)

    if not _have_sqlcipher():
        res = _empty("当前 python 没有 sqlcipher3，请在 nt_msg_db_util/.venv 下运行",
                     time.monotonic())
    else:
        res = _fetch_local(args.qq, args.data_root or None, args.key or None)

    if args.json:
        sys.stdout.write(JSON_PREFIX + json.dumps(res, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(json.dumps(res, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.flush()
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    try:
        sys.exit(_main())
    except KeyboardInterrupt:
        sys.exit(130)


# ── 自身资料 / 完整资料包（SPEC 8.3 · task-7 追加） ──────────────────────────
# 说明：本段是**追加**能力，不改动上面任何已有函数的行为与签名。
# profile_info_v6 列义：1000=uid, 1002=QQ, 20002=昵称, 20009=备注,
# 20011=个性签名, 20004=头像 URL（带 ek 签名参数 → 只内部使用，不对外返回）。

_SELF_SELECT = ('SELECT "1000","1002","20002","20009","20011","20004" '
                'FROM profile_info_v6')


def _read_self_rows(con, account_qq: int) -> list[dict]:
    """读出该 QQ 在 ``profile_info_v6`` 中的所有候选本人行（可能不止一行）。"""
    qq = int(account_qq)
    rows = None
    last: Exception | None = None
    for sql, arg in ((_SELF_SELECT + ' WHERE "1002"=?', qq),
                     (_SELF_SELECT + ' WHERE CAST("1002" AS TEXT)=?', str(qq))):
        try:
            got = con.execute(sql, (arg,)).fetchall()
        except Exception as exc:  # noqa: BLE001
            last = exc
            continue
        if got:
            rows = got
            break
    if rows is None:
        if last is not None:
            raise NamesError(f"profile_info_v6 读取本人资料失败：{last}")
        return []

    out: list[dict] = []
    for uid, q2, nick, remark, sign, avatar in rows:
        if _to_int(q2) != qq:
            continue
        out.append({
            "account_qq": qq,
            "uid": _clean(uid),
            "nickname": _clean(nick),
            "remark": _clean(remark),
            "signature": _clean(sign),
            "avatar_raw": _clean(avatar),
        })
    return out


def _pick_self(rows: list[dict]) -> dict | None:
    """多行候选里挑最完整的一行：有昵称 > 有签名 > 有头像。"""
    if not rows:
        return None

    def score(r: dict) -> int:
        return ((2 if r.get("nickname") else 0)
                + (1 if r.get("signature") else 0)
                + (1 if r.get("avatar_raw") else 0))

    return max(rows, key=score)


def _read_self_with_keys(account_qq: int, data_root=None,
                         key: str | None = None) -> tuple[dict | None, str]:
    """用候选密钥尝试解密 ``profile_info.db`` 并取出本人资料行。

    返回 ``(self_dict | None, 错误说明)``；成功时错误说明为空串。
    """
    qq = int(account_qq)
    raw_dir = _nt_db_dir(qq, data_root)
    if not (Path(raw_dir) / "profile_info.db").exists():
        return None, f"找不到 profile_info.db（{raw_dir}）"
    keys = _candidate_keys(qq, key)
    if not keys:
        return None, "没有可用密钥"
    work = paths.account_decrypt(qq)
    last: Exception | None = None
    for k in keys:
        con = None
        try:
            con = _open_encrypted(raw_dir, "profile_info.db", work, k)
            rows = _read_self_rows(con, qq)
            if rows:
                return _pick_self(rows), ""
            return None, f"profile_info_v6 中没有 QQ={qq} 的本人行"
        except Exception as exc:  # noqa: BLE001
            last = exc
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:  # noqa: BLE001
                    pass
    return None, f"profile_info.db 解密失败：{last}"


def fetch_profile_bundle(account_qq: int, data_root=None, key: str | None = None) -> dict:
    """一次取齐「昵称库 + 本人资料」：好友/群/people 明细 + 本人 profile + 计数。

    返回 ``{"ok","message","self","friends","groups","people",
             "friend_count","group_count","stats"}``；任何失败都不抛出。
    """
    qq = _to_int(account_qq)
    try:
        base = fetch_names(qq, data_root, key)
    except Exception as exc:  # noqa: BLE001
        base = {"ok": False, "message": f"昵称库读取异常：{exc}", "friends": {},
                "groups": {}, "people": {}, "stats": {}}
    if not isinstance(base, dict):
        base = {"ok": False, "message": "昵称库返回格式异常", "friends": {},
                "groups": {}, "people": {}, "stats": {}}

    msg = str(base.get("message") or "")
    selfp: dict | None = None
    if _have_sqlcipher():
        try:
            selfp, err = _read_self_with_keys(qq, data_root, key)
            if err:
                msg = f"{msg}；{err}" if msg else err
        except Exception as exc:  # noqa: BLE001
            msg = (f"{msg}；本人资料读取异常：{exc}" if msg
                   else f"本人资料读取异常：{exc}")
    else:
        tip = "当前 python 无 sqlcipher3，本人资料降级"
        msg = f"{msg}；{tip}" if msg else tip

    friends = base.get("friends") or {}
    groups = base.get("groups") or {}
    stats = dict(base.get("stats") or {})
    buddy = _to_int(stats.get("buddy_list"))
    friend_count = len(friends) or buddy
    if not buddy:
        stats["buddy_list"] = friend_count
    return {
        "ok": bool(base.get("ok")) or selfp is not None,
        "message": msg,
        "self": selfp,
        "friends": friends,
        "groups": groups,
        "people": base.get("people") or {},
        "friend_count": friend_count,
        "group_count": len(groups),
        "stats": stats,
    }


def fetch_self_profile(account_qq: int, data_root=None, key: str | None = None) -> dict:
    """只取本人资料（``GET /api/profile`` 用）。

    返回 ``{"ok","message","profile","friend_count","group_count","stats"}``。
    ``profile`` 里的 ``has_avatar`` 只表示有没有头像；原始 20004 URL 带签名参数，
    **不在此返回**，接口层统一用 QQ 号重建 qlogo 固定地址。
    """
    b = fetch_profile_bundle(account_qq, data_root, key)
    p = b.get("self")
    profile = None
    if p:
        profile = {
            "account_qq": p.get("account_qq"),
            "uid": p.get("uid"),
            "nickname": p.get("nickname"),
            "remark": p.get("remark"),
            "signature": p.get("signature"),
            "has_avatar": bool(p.get("avatar_raw")),
        }
    return {"ok": bool(b.get("ok")), "message": b.get("message") or "",
            "profile": profile, "friend_count": b.get("friend_count", 0),
            "group_count": b.get("group_count", 0),
            "stats": b.get("stats") or {}}