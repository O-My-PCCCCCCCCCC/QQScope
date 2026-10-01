# -*- coding: utf-8 -*-
"""QQScope · 窗口 A 数据源：数据包读取（离线）

流程（严格按 docs/SPEC-重构接口.md 第 2、3 节）：
    C:\\Users\\...\\Tencent Files\\<qq>\\nt_qq\\nt_db\\nt_msg.db (+ -wal / -shm)
      → 复制到 data/decrypt/<qq>/
      → 剥掉前 1024 字节 NTQQ 自定义头
      → tools/nt_msg_db_util/.venv 里用 sqlcipher3 解密（PRAGMA 顺序固定）
      → 复用 msgdb 的 c2c/parser.py、group/exporter.py 解析 text / direction / peer
      → core.store 统一入库（自动去重）

设计要点：
- 本模块不依赖任何第三方库：解密 + protobuf 解析全部交给 venv 子进程
  core/sources/_pack_extract.py，因此系统 Python 也能 import 本模块。
- 密钥候选顺序：opts["keys"][qq] → data/keys/<qq>.key → paths.LEGACY_KEY_FILE。
  data/keys 里可能是旧密钥，助手会逐个尝试，谁成功用谁（实测主号只有
  data/keys/<qq>.key（或包内 data/keys/legacy.key）有效）。
- 中间产物只写 data/decrypt/<qq>/：nt_msg.db(+-wal/shm)、nt_msg_clear.db、messages.jsonl。
- ts<=0 的行一律跳过；所有失败都转成「人话中文」，不把异常栈丢给用户。

对外契约：SOURCE_ID / SOURCE_NAME / SOURCE_KIND + status / probe / sync / conversations。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from core import media, paths, store

SOURCE_ID = "pack"
SOURCE_NAME = "数据包读取"
SOURCE_KIND = "pack"

HELPER = Path(__file__).resolve().parent / "_pack_extract.py"
VENV_PY = paths.python_executable()

BATCH_SIZE = 5000          # 每次写 store 的消息条数
SYNC_TIMEOUT = 600.0       # 单个账号解密+解析的硬超时（秒）
HEADER_SIZE = 1024         # NTQQ 自定义头长度

PROG_PREFIX = "@@PROG@@ "
KEYOK_PREFIX = "@@KEYOK@@ "
CONVS_PREFIX = "@@CONVS@@ "
DONE_PREFIX = "@@DONE@@ "
ERR_PREFIX = "@@ERROR@@ "


class PackError(Exception):
    """带人话中文说明的数据包读取错误。"""


# ── 基础工具 ────────────────────────────────────────────────────────────────
def _venv_python(required: bool = True) -> Path | None:
    if VENV_PY.exists():
        return VENV_PY
    if required:
        raise PackError(
            f"缺少解密环境：找不到 {VENV_PY}。"
            "请确认 tools/nt_msg_db_util/.venv 存在，并且已安装 sqlcipher3。")
    return None


def _discover_accounts(data_root) -> list[dict]:
    """扫描 data_root 下所有带 nt_qq/nt_db/nt_msg.db 的账号目录。"""
    root = Path(data_root)
    if not root.exists():
        return []
    dirs: list[Path] = []
    if (root / "nt_qq" / "nt_db" / "nt_msg.db").exists():
        dirs.append(root)  # data_root 本身就是一个账号目录
    try:
        for d in sorted(root.iterdir()):
            if d.is_dir() and d.name.isdigit() and (d / "nt_qq" / "nt_db" / "nt_msg.db").exists():
                dirs.append(d)
    except OSError:
        pass

    found: list[dict] = []
    seen: set[int] = set()
    for d in dirs:
        qq = int(d.name) if d.name.isdigit() else 0
        if qq in seen:
            continue
        seen.add(qq)
        found.append({"account_qq": qq, "db": d / "nt_qq" / "nt_db" / "nt_msg.db"})
    return sorted(found, key=lambda a: a["account_qq"])


def _resolve_accounts(opts: dict) -> list[dict]:
    """按 opts["data_root"] / opts["accounts"] 解析要处理的账号。"""
    root = opts.get("data_root") or paths.DEFAULT_DATA_ROOT
    all_acc = _discover_accounts(root)
    want = opts.get("accounts")
    if not want:
        return all_acc
    wanted = set()
    for x in want:
        s = str(x).strip()
        if s.lstrip("-").isdigit():
            wanted.add(int(s))
    picked = [a for a in all_acc if a["account_qq"] in wanted]
    if not picked:
        raise PackError(
            f"在 {root} 下找不到指定账号的数据包：{sorted(wanted) or want}。"
            "请确认路径与账号 QQ 是否正确。")
    return picked


def _key_sources(qq: int, opts: dict) -> list[tuple[str, str]]:
    """返回候选密钥 [(来源说明, 密钥), ...]，已去重并保持优先级。"""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(src: str, val) -> None:
        v = (str(val) if val is not None else "").strip()
        if not v or v in seen:
            return
        seen.add(v)
        out.append((src, v))

    keys = opts.get("keys") or {}
    if isinstance(keys, dict):
        add("opts.keys", keys.get(str(qq)) if keys.get(str(qq)) is not None else keys.get(qq))

    f = paths.KEYS_DIR / f"{qq}.key"
    if f.exists():
        try:
            add("data/keys", f.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            pass

    if paths.LEGACY_KEY_FILE.exists():
        try:
            add("qq-export/db_key.txt", paths.LEGACY_KEY_FILE.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            pass
    return out


def _work_dir(qq: int) -> Path:
    return paths.DECRYPT_DIR / str(qq)


def _copy_snapshot(acc: dict, work: Path) -> None:
    """复制 nt_msg.db + -wal + -shm 到工作目录（源库保持只读不动）。"""
    src = Path(acc["db"])
    if not src.exists():
        raise PackError(f"源数据库不存在：{src}")
    work.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, work / "nt_msg.db")
    for suf in ("-wal", "-shm"):
        s = Path(str(src) + suf)
        d = work / f"nt_msg.db{suf}"
        if s.exists():
            shutil.copy2(s, d)
        elif d.exists():
            try:
                d.unlink()
            except OSError:
                pass


# ── 子进程调用 ──────────────────────────────────────────────────────────────
def _run_helper(py: Path, work: Path, qq: int, keys: list[tuple[str, str]],
                out: Path | None, probe_only: bool, include_c2c: bool,
                include_group: bool, on_progress, timeout: float,
                limit: int = 0,
                reuse_clear: bool = False) -> tuple[dict, dict, dict, list[str]]:
    """跑 _pack_extract.py，逐行解析 stdout 协议。返回 (done, convs, keyok, log)。"""
    cmd = [str(py), str(HELPER), "--work", str(work), "--qq", str(qq)]
    for _src, key in keys:
        # 用 --key=xxx 形式：密钥可能以 "-" 开头，分开写会被 argparse 当成选项
        cmd += ["--key=" + str(key)]
    if probe_only:
        cmd.append("--probe-only")
    elif out is not None:
        cmd += ["--out", str(out)]
    if not include_c2c:
        cmd.append("--no-c2c")
    if not include_group:
        cmd.append("--no-group")
    if limit:
        cmd += ["--limit", str(int(limit))]
    if reuse_clear:
        cmd.append("--reuse-clear")

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            bufsize=1, env=env, cwd=str(work))
    except OSError as exc:
        raise PackError(f"无法启动解密子进程：{exc}") from exc

    # sqlcipher3 的原生日志直接写 fd2（Windows 下是 UTF-16 字节），
    # 绝不能与 stdout 合并，否则会污染 @@KEYOK@@ 等行协议；这里单独抽干 stderr。
    err_lines: list[str] = []

    def _drain_stderr() -> None:
        try:
            assert proc.stderr is not None
            for ln in proc.stderr:
                s = ln.replace("\x00", "").rstrip("\r\n")
                if s:
                    err_lines.append(s)
        except Exception:  # noqa: BLE001
            pass

    err_thread = threading.Thread(target=_drain_stderr, daemon=True)
    err_thread.start()

    done: dict = {}
    convs: dict = {}
    keyok: dict = {}
    err: dict = {}
    log: list[str] = []

    timer = threading.Timer(timeout, proc.kill)
    timer.daemon = True
    timer.start()
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if line.startswith(PROG_PREFIX):
                try:
                    p = json.loads(line[len(PROG_PREFIX):])
                    if on_progress:
                        on_progress(str(p.get("stage") or "解析"),
                                    int(p.get("pct") or 0), str(p.get("msg") or ""))
                except Exception:  # noqa: BLE001
                    pass
            elif line.startswith(KEYOK_PREFIX):
                try:
                    keyok = json.loads(line[len(KEYOK_PREFIX):])
                except Exception:  # noqa: BLE001
                    pass
            elif line.startswith(CONVS_PREFIX):
                try:
                    convs = json.loads(line[len(CONVS_PREFIX):])
                except Exception:  # noqa: BLE001
                    pass
            elif line.startswith(DONE_PREFIX):
                try:
                    done = json.loads(line[len(DONE_PREFIX):])
                except Exception:  # noqa: BLE001
                    pass
            elif line.startswith(ERR_PREFIX):
                try:
                    err = json.loads(line[len(ERR_PREFIX):])
                except Exception:  # noqa: BLE001
                    err = {"code": "unknown", "msg": line}
            elif line.strip():
                log.append(line)
    finally:
        timer.cancel()
        try:
            code = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = -9
        try:
            proc.stdout.close()
        except Exception:  # noqa: BLE001
            pass
        err_thread.join(timeout=5)

    if err_lines:
        log.extend(err_lines[-10:])
    if err:
        raise PackError(str(err.get("msg") or "解密/解析失败"))
    if code != 0 or not done:
        tail = "\n".join(log[-8:])
        raise PackError(f"解密/解析子进程异常退出（代码 {code}）：{tail or '没有任何输出'}")
    return done, convs, keyok, log


# ── 记录转换 ────────────────────────────────────────────────────────────────
_UPDATE_MEDIA_SQL = (
    "UPDATE messages SET content=:content, media=:media "
    "WHERE account_qq=:account_qq AND kind=:kind AND peer_id=:peer_id "
    "AND ts=:ts AND direction=:direction AND COALESCE(text,'')=COALESCE(:text,'')"
)


def update_media_rows(rows: list[dict]) -> int:
    """把 content / media 补写到已存在的行。

    store.insert_messages 用的是 INSERT OR IGNORE（ux_msg 去重），所以老数据
    重跑 sync 不会自动补 content/media；这里按同样的唯一键做 UPDATE。
    返回实际写入的行数。
    """
    data = [{
        "content": r.get("content"), "media": r.get("media"),
        "account_qq": r.get("account_qq"), "kind": r.get("kind"),
        "peer_id": r.get("peer_id"), "ts": r.get("ts"),
        "direction": r.get("direction"), "text": r.get("text"),
    } for r in rows]
    if not data:
        return 0
    con = store.connect()
    try:
        before = con.total_changes
        con.executemany(_UPDATE_MEDIA_SQL, data)
        con.commit()
        return con.total_changes - before
    finally:
        con.close()


def _to_store_row(rec: dict, account_qq: int) -> dict | None:
    """把助手输出的一行 JSONL 转成 store.insert_messages 需要的 dict。"""
    try:
        ts = int(rec.get("ts") or 0)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None

    try:
        sq = int(rec.get("sq") or 0)
    except (TypeError, ValueError):
        sq = 0
    try:
        mt = int(rec.get("mt") or 0)
    except (TypeError, ValueError):
        mt = 0

    # 本人发出的判定：发送者 QQ == 本账号（真实库里 40013 有 0/1/2/3/4/5，
    # 其中 1/2/4/5 的 sender_qq 均为本人，统一映射成 store 约定的 1/0）
    direction = 1 if (sq and sq == account_qq) else 0

    text = rec.get("tx")
    if text is not None and not isinstance(text, str):
        text = str(text)

    # content：原始结构化 JSON（原样存）；media：归一化 JSON + 本地 file 路径
    content = rec.get("content")
    if content is not None and not isinstance(content, str):
        try:
            content = json.dumps(content, ensure_ascii=False)
        except (TypeError, ValueError):
            content = None

    media_json = None
    raw_media = rec.get("media")
    if raw_media:
        try:
            m = json.loads(raw_media) if isinstance(raw_media, str) else raw_media
        except (TypeError, ValueError, json.JSONDecodeError):
            m = None
        if isinstance(m, dict) and m.get("kind"):
            try:
                m = media.fill_media(account_qq, m)
            except Exception:  # noqa: BLE001
                pass
            if m:
                media_json = json.dumps(m, ensure_ascii=False)

    if rec.get("k") == "c":
        kind = "c2c"
        try:
            pq = int(rec.get("pq") or 0)
        except (TypeError, ValueError):
            pq = 0
        pu = str(rec.get("pu") or "").strip()
        # 统一到 QQ 号（与窗口 B 一致，避免同一人两个会话）；拿不到 QQ 号才退回 uid
        peer_id = str(pq) if pq else (pu or "0")
        peer_qq = pq
    else:
        kind = "group"
        gi = str(rec.get("gi") or "").strip()
        try:
            gq = int(rec.get("gq") or 0)
        except (TypeError, ValueError):
            gq = 0
        # 真实库有 2.2 万行 group_qq=0，但 group_id(40021) 是完整的群号字符串
        if gq <= 0 and gi.isdigit():
            gq = int(gi)
        peer_id = gi or str(gq)
        peer_qq = gq

    return {
        "account_qq": account_qq,
        "kind": kind,
        "peer_id": peer_id,
        "peer_qq": peer_qq,
        "ts": ts,
        "direction": direction,
        "sender_qq": sq or None,
        "sender_name": None,
        "msg_type": mt,
        "text": text,
        "content": content,
        "media": media_json,
        "source": SOURCE_ID,
    }


# ── 契约：status / probe / sync / conversations ────────────────────────────
def status() -> dict:
    """只做本机环境探测，不解密、不写库。"""
    py = _venv_python(required=False)
    root = paths.DEFAULT_DATA_ROOT
    try:
        accounts = _discover_accounts(root)
    except Exception:  # noqa: BLE001
        accounts = []
    try:
        stored_keys = sorted(p.name for p in paths.KEYS_DIR.glob("*.key"))
    except Exception:  # noqa: BLE001
        stored_keys = []
    legacy_key = paths.LEGACY_KEY_FILE.exists()

    detail = {
        "helper": str(HELPER),
        "helper_exists": HELPER.exists(),
        "venv_python": str(VENV_PY),
        "venv_exists": py is not None,
        "data_root": str(root),
        "data_root_exists": root.exists(),
        "decrypt_dir": str(paths.DECRYPT_DIR),
        "legacy_key_file": str(paths.LEGACY_KEY_FILE),
        "legacy_key_exists": legacy_key,
        "data_keys_dir": str(paths.KEYS_DIR),
        "data_keys": stored_keys,
        "accounts": [
            {"account_qq": a["account_qq"], "db": str(a["db"]),
             "db_size": a["db"].stat().st_size if a["db"].exists() else 0}
            for a in accounts
        ],
    }

    if py is None:
        ready, msg = False, f"缺少解密 venv：{VENV_PY}（需要 sqlcipher3）"
    elif not HELPER.exists():
        ready, msg = False, f"缺少解析助手：{HELPER}"
    elif not accounts:
        ready, msg = False, f"未在 {root} 发现带 nt_msg.db 的 QQ 账号目录"
    elif not stored_keys and not legacy_key:
        ready, msg = False, "找到账号但没有任何密钥文件（data/keys/*.key 或 qq-export/db_key.txt）"
    else:
        qqs = "、".join(str(a["account_qq"]) for a in accounts)
        hint = "（data/keys/*.key 可能是旧密钥，助手会自动回退到 qq-export/db_key.txt）" if legacy_key else ""
        ready = True
        msg = f"就绪：发现 {len(accounts)} 个账号（{qqs}），密钥来源 {len(stored_keys)} 个 data/keys + {'有' if legacy_key else '无'} 历史密钥文件{hint}"
    return {"id": SOURCE_ID, "name": SOURCE_NAME, "kind": SOURCE_KIND,
            "ready": ready, "message": msg, "detail": detail}


def probe(opts: dict | None = None) -> dict:
    """只探测不写库：账号、密钥是否可用、库内消息/会话数。"""
    opts = dict(opts or {})
    result: dict = {"ok": False, "message": "", "accounts": [], "conversations": []}
    try:
        py = _venv_python()
        accounts = _resolve_accounts(opts)
    except PackError as exc:
        result["message"] = str(exc)
        return result
    if not accounts:
        result["message"] = (f"未在 {opts.get('data_root') or paths.DEFAULT_DATA_ROOT} "
                             "发现含 nt_msg.db 的 QQ 账号目录")
        return result

    verify = bool(opts.get("verify", True))
    include_c2c = bool(opts.get("include_c2c", True))
    include_group = bool(opts.get("include_group", True))
    timeout = float(opts.get("timeout") or SYNC_TIMEOUT)
    entries: list[dict] = []
    conversations: list[dict] = []
    problems: list[str] = []
    ok_any = False

    for acc in accounts:
        qq = int(acc["account_qq"])
        db = Path(acc["db"])
        entry = {
            "account_qq": qq, "db": str(db),
            "db_size": db.stat().st_size if db.exists() else 0,
            "has_key": False, "key_source": None, "decrypted": False,
            "c2c": 0, "group": 0, "conversations": 0, "error": None,
        }
        keys = _key_sources(qq, opts)
        if not keys:
            entry["error"] = "没有可用密钥"
            problems.append(f"{qq}：没有可用密钥")
            entries.append(entry)
            continue
        entry["has_key"] = True
        if not verify:
            entry["key_source"] = keys[0][0]
            entries.append(entry)
            ok_any = True
            continue
        work = _work_dir(qq)
        try:
            _copy_snapshot(acc, work)
            done, convs, keyok, _log = _run_helper(
                py, work, qq, keys, None, True, include_c2c, include_group,
                lambda *a: None, timeout)
        except PackError as exc:
            entry["error"] = str(exc)
            problems.append(f"{qq}：{exc}")
            entries.append(entry)
            continue
        idx = int(keyok.get("index") or 0)
        entry.update({
            "decrypted": True,
            "key_source": keys[idx][0] if 0 <= idx < len(keys) else "未知",
            "c2c": int(done.get("c2c_total") or 0),
            "group": int(done.get("group_total") or 0),
            "conversations": int(done.get("conversations") or 0),
        })
        conversations.extend(convs.get("conversations") or [])
        entries.append(entry)
        ok_any = True

    result["accounts"] = entries
    result["conversations"] = conversations
    if ok_any:
        total = sum(int(a["c2c"]) + int(a["group"]) for a in entries)
        result["ok"] = True
        result["message"] = (f"发现 {len(accounts)} 个账号，密钥可用；"
                             f"共 {total} 条消息、{len(conversations)} 个会话")
        if problems:
            result["message"] += "；" + "；".join(problems)
    else:
        result["message"] = "；".join(problems) or "探测失败"
    return result


def conversations(opts: dict | None = None) -> list[dict]:
    """列出可采集的会话。已入库的账号直接用主库；否则解密真库统计。"""
    opts = dict(opts or {})
    try:
        accounts = _resolve_accounts(opts)
    except PackError:
        return []
    out: list[dict] = []
    for acc in accounts:
        qq = int(acc["account_qq"])
        try:
            cs = store.list_contacts(qq, limit=5000)
        except Exception:  # noqa: BLE001
            cs = []
        for c in cs:
            out.append({
                "account_qq": qq, "kind": c["kind"], "peer_id": c["peer_id"],
                "peer_qq": c.get("peer_qq") or 0, "name": c.get("name"),
                "remark": c.get("remark"), "avatar": c.get("avatar"),
            })
    if out:
        return out

    pr = probe({**opts, "verify": True})
    fields = ("account_qq", "kind", "peer_id", "peer_qq", "name", "remark", "avatar")
    return [{k: c.get(k) for k in fields} for c in (pr.get("conversations") or [])]


def sync(opts: dict | None = None, progress=None) -> dict:
    """复制 → 解密 → 解析 → 写入 core.store。返回 SPEC 约定的结果字典。"""
    t_all = time.monotonic()
    opts = dict(opts or {})
    log_lines: list[str] = []

    def emit(stage: str, pct: int, msg: str) -> None:
        log_lines.append(f"[{stage}] {pct}% {msg}")
        if progress:
            try:
                progress(stage, int(pct), str(msg))
            except Exception:  # noqa: BLE001
                pass

    def fail(msg: str) -> dict:
        emit("失败", 0, msg)
        return {"ok": False, "message": msg, "imported": 0, "accounts": [],
                "elapsed": round(time.monotonic() - t_all, 2),
                "log": "\n".join(log_lines)}

    try:
        py = _venv_python()
        accounts = _resolve_accounts(opts)
    except PackError as exc:
        return fail(str(exc))
    if not accounts:
        return fail(f"未在 {opts.get('data_root') or paths.DEFAULT_DATA_ROOT} "
                    "发现含 nt_msg.db 的 QQ 账号目录")

    include_c2c = bool(opts.get("include_c2c", True))
    include_group = bool(opts.get("include_group", True))
    if not include_c2c and not include_group:
        return fail("include_c2c 与 include_group 不能同时为 False")
    try:
        media.set_data_root(opts.get("data_root"))
    except Exception:  # noqa: BLE001
        pass
    limit = int(opts.get("limit") or 0)
    timeout = float(opts.get("timeout") or SYNC_TIMEOUT)
    labels = opts.get("labels") if isinstance(opts.get("labels"), dict) else {}

    n = len(accounts)
    span = 100.0 / max(n, 1)

    def g(pct_in_account: float) -> int:
        return max(0, min(99, int((idx * span) + (pct_in_account * span / 100.0))))

    imported = 0
    scanned = 0
    skipped_ts = 0
    parse_errors = 0
    media_filled = 0
    done_accounts: list[int] = []
    failures: list[str] = []
    keys_used: dict[int, str] = {}
    conv_keys: set[tuple[str, str]] = set()
    stages = {"复制": 0.0, "解密": 0.0, "解析": 0.0, "入库": 0.0}

    for idx, acc in enumerate(accounts):
        qq = int(acc["account_qq"])
        keys = _key_sources(qq, opts)
        if not keys:
            failures.append(f"{qq}：没有可用密钥")
            emit("跳过", g(0), f"账号 {qq} 没有可用密钥，已跳过")
            continue

        work = _work_dir(qq)
        try:
            size_mb = Path(acc["db"]).stat().st_size / 1048576.0
        except OSError:
            size_mb = 0.0
        emit("复制", g(0), f"复制账号 {qq} 的 nt_msg.db（约 {size_mb:.0f} MB）+ wal/shm")
        t_copy = time.monotonic()
        try:
            _copy_snapshot(acc, work)
        except PackError as exc:
            failures.append(f"{qq}：{exc}")
            emit("失败", g(0), str(exc))
            continue
        except OSError as exc:
            failures.append(f"{qq}：复制数据库失败（{exc}）")
            emit("失败", g(0), f"复制账号 {qq} 数据库失败：{exc}")
            continue
        stages["复制"] += time.monotonic() - t_copy

        out = work / "messages.jsonl"
        emit("解密", g(1), "解密并校验密钥（PRAGMA 顺序固定）…")
        try:
            done, _convs, keyok, _hlog = _run_helper(
                py, work, qq, keys, out, False, include_c2c, include_group,
                lambda st, pc, ms: emit(st, g(pc), ms), timeout, limit)
        except PackError as exc:
            failures.append(f"{qq}：{exc}")
            emit("失败", g(1), f"账号 {qq} 同步失败：{exc}")
            continue

        kidx = int(keyok.get("index") or 0)
        key_src = keys[kidx][0] if 0 <= kidx < len(keys) else "未知"
        keys_used[qq] = key_src
        skipped_ts += int(done.get("skipped_ts") or 0)
        parse_errors += int(done.get("parse_errors") or 0)
        stages["解密"] += float(done.get("decrypt_sec") or 0.0)
        stages["解析"] += float(done.get("parse_sec") or 0.0)
        log_lines.append(
            f"[{qq}] 解析完成：私聊 {done.get('c2c_out')} / 群聊 {done.get('group_out')} 行，"
            f"跳过 ts<=0 {done.get('skipped_ts')} 行，解析失败 {done.get('parse_errors')} 行，"
            f"密钥来源 {key_src}，WAL={'是' if done.get('used_wal') else '否'}")

        label = labels.get(str(qq)) or labels.get(qq)
        try:
            store.upsert_account(qq, label=label, source=SOURCE_ID)
        except Exception as exc:  # noqa: BLE001
            log_lines.append(f"[{qq}] 写账号失败：{exc}")

        scanned_acc = 0
        batch: list[dict] = []
        contacts: dict[tuple[str, str], dict] = {}
        emit("导入", g(85), "解析完成，开始写入主库（自动去重）…")
        t_ins = time.monotonic()
        try:
            with open(out, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    scanned_acc += 1
                    row = _to_store_row(rec, qq)
                    if row is None:
                        continue
                    batch.append(row)
                    ck = (row["kind"], row["peer_id"])
                    if ck not in contacts:
                        contacts[ck] = {
                            "account_qq": qq, "kind": row["kind"], "peer_id": row["peer_id"],
                            "peer_qq": row["peer_qq"], "source": SOURCE_ID,
                        }
                    if len(batch) >= BATCH_SIZE:
                        new = store.insert_messages(batch)
                        imported += new
                        if new < len(batch):
                            media_filled += update_media_rows(batch)
                        batch.clear()
                        frac = 85 + 14 * min(1.0, scanned_acc / max(int(done.get("scanned") or 1), 1))
                        emit("导入", g(frac), f"入库 {imported} 条 / 已读 {scanned_acc}")
            if batch:
                new = store.insert_messages(batch)
                imported += new
                if new < len(batch):
                    media_filled += update_media_rows(batch)
        except OSError as exc:
            failures.append(f"{qq}：读取解析结果失败（{exc}）")
            emit("失败", g(85), f"读取解析结果失败：{exc}")
            stages["入库"] += time.monotonic() - t_ins
            scanned += scanned_acc
            continue
        stages["入库"] += time.monotonic() - t_ins

        try:
            if contacts:
                store.upsert_contacts(list(contacts.values()))
            store.refresh_contact_stats(qq)
        except Exception as exc:  # noqa: BLE001
            log_lines.append(f"[{qq}] 写联系人统计失败：{exc}")

        scanned += scanned_acc
        conv_keys.update(contacts.keys())
        done_accounts.append(qq)
        emit("完成", g(100), f"账号 {qq} 完成：本次新增 {imported} 条，会话 {len(contacts)} 个")

    # ── 末尾追加：昵称 / 群名 / 备注补全（可选增强） ──────────────────────
    # 昵称库（profile_info.db / group_info.db）与 nt_msg.db 同密钥、同解密参数。
    # 这里任何一步失败都静默降级：绝不因为昵称库解不开就让整个消息导入失败。
    names_summary: dict = {}
    _names_mod = None
    try:
        from core.sources import names as _names_mod  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        _names_mod = None
        log_lines.append(f"[昵称] 昵称模块不可用，跳过昵称补全（不影响消息导入）：{exc}")
    if _names_mod is not None and done_accounts:
        emit("昵称", 99, "开始补全联系人昵称 / 群名…")
        applied_total = 0
        for qq in done_accounts:
            cands = [k for _src_name, k in _key_sources(qq, opts)] or [None]
            got = None
            for k in cands:
                try:
                    got = _names_mod.fetch_names(
                        qq, data_root=(opts.get("data_root") or None), key=k)
                except Exception as exc:  # noqa: BLE001
                    got = None
                    log_lines.append(f"[{qq}] 昵称补全异常（已忽略）：{exc}")
                    continue
                if got and got.get("ok"):
                    break
                got = None
            if not got:
                log_lines.append(f"[{qq}] 昵称库不可用，跳过昵称补全（不影响消息导入）")
                names_summary[str(qq)] = {"ok": False}
                continue
            try:
                applied = int(_names_mod.apply_to_store(qq, got))
            except Exception as exc:  # noqa: BLE001
                log_lines.append(f"[{qq}] 写回昵称失败（已忽略）：{exc}")
                names_summary[str(qq)] = {"ok": False, "message": str(exc)}
                continue
            st = got.get("stats") or {}
            applied_total += applied
            names_summary[str(qq)] = {
                "ok": True,
                "friends": st.get("friends"),
                "groups": st.get("groups"),
                "applied": applied,
            }
            log_lines.append(
                f"[{qq}] 昵称补全：好友 {st.get('friends')} 条 / 群名 {st.get('groups')} 条，"
                f"写回 {applied} 个会话")
        emit("昵称", 100, f"昵称补全完成：共写回 {applied_total} 个会话")

    elapsed = time.monotonic() - t_all
    stages["总耗时"] = round(elapsed, 2)
    for k in stages:
        stages[k] = round(stages[k], 2)

    if not done_accounts:
        msg = "同步失败：" + ("；".join(failures) if failures else "没有可同步的账号")
        emit("失败", 0, msg)
        return {"ok": False, "message": msg, "imported": 0, "accounts": [],
                "elapsed": round(elapsed, 2), "log": "\n".join(log_lines),
                "failures": failures}

    msg = (f"同步完成：{len(done_accounts)} 个账号，新增入库 {imported} 条"
           f"（扫描 {scanned} 条，跳过 ts<=0 脏数据 {skipped_ts} 条，解析失败 {parse_errors} 条），"
           f"耗时 {elapsed:.1f}s")
    if failures:
        msg += f"；{len(failures)} 个账号失败"
    emit("完成", 100, msg)
    return {
        "ok": True, "message": msg, "imported": imported, "accounts": done_accounts,
        "elapsed": round(elapsed, 2), "log": "\n".join(log_lines),
        "scanned": scanned, "skipped": skipped_ts, "parse_errors": parse_errors,
        "media_filled": media_filled,
        "conversations": len(conv_keys), "keys_used": keys_used,
        "stages": stages, "failures": failures,
        "names": names_summary,
    }

def rescan_media(opts: dict | None = None, progress=None) -> dict:
    """只重扫 content / media 两列：复用 data/decrypt/<qq>/nt_msg_clear.db，
    不重新复制 / 剥头 / 解密整库，按唯一键 UPDATE 老数据。"""
    t0 = time.monotonic()
    opts = dict(opts or {})
    log_lines: list[str] = []

    def emit(stage: str, pct: int, msg: str) -> None:
        log_lines.append(f"[{stage}] {pct}% {msg}")
        if progress:
            try:
                progress(stage, int(pct), str(msg))
            except Exception:  # noqa: BLE001
                pass

    def fail(msg: str) -> dict:
        return {"ok": False, "message": msg, "updated": 0, "accounts": [],
                "elapsed": round(time.monotonic() - t0, 2), "log": "\n".join(log_lines)}

    if not opts.get("accounts") and opts.get("account_qq"):
        opts["accounts"] = [opts["account_qq"]]
    try:
        py = _venv_python()
        accounts = _resolve_accounts(opts)
    except PackError as exc:
        return fail(str(exc))
    if not accounts:
        return fail("未发现可重扫的账号数据包")
    try:
        media.set_data_root(opts.get("data_root"))
    except Exception:  # noqa: BLE001
        pass

    timeout = float(opts.get("timeout") or SYNC_TIMEOUT)
    limit = int(opts.get("limit") or 0)
    scanned = 0
    updated = 0
    media_rows = 0
    done_accounts: list[int] = []
    failures: list[str] = []

    for acc in accounts:
        qq = int(acc["account_qq"])
        work = _work_dir(qq)
        if not (work / "nt_msg_clear.db").exists():
            failures.append(f"{qq}：没有解密缓存（请先跑一次完整同步）")
            continue
        keys = _key_sources(qq, opts)
        if not keys:
            failures.append(f"{qq}：没有可用密钥")
            continue
        out = work / "rescan_media.jsonl"
        emit("重扫", 5, f"账号 {qq}：复用已有解密缓存重扫 content/media…")
        try:
            done, _convs, _keyok, _hlog = _run_helper(
                py, work, qq, keys, out, False, True, True,
                lambda st, pc, ms: emit(st, min(90, pc), ms), timeout, limit,
                reuse_clear=True)
        except PackError as exc:
            failures.append(f"{qq}：{exc}")
            emit("失败", 5, f"账号 {qq} 重扫失败：{exc}")
            continue

        total = max(int(done.get("scanned") or 0), 1)
        acc_scanned = 0
        batch: list[dict] = []
        try:
            with open(out, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    row = _to_store_row(rec, qq)
                    if row is None:
                        continue
                    acc_scanned += 1
                    if row.get("media"):
                        media_rows += 1
                    batch.append(row)
                    if len(batch) >= BATCH_SIZE:
                        updated += update_media_rows(batch)
                        batch.clear()
                        emit("写库", 10 + int(85 * acc_scanned / total),
                             f"账号 {qq}：已写 {acc_scanned}/{total}")
            if batch:
                updated += update_media_rows(batch)
        except OSError as exc:
            failures.append(f"{qq}：读取重扫结果失败（{exc}）")
            continue

        scanned += acc_scanned
        done_accounts.append(qq)
        log_lines.append(
            f"[{qq}] 重扫完成：扫描 {acc_scanned} 行，含媒体 {done.get('media_out')} 行，"
            f"kinds={done.get('media_kinds')}，密钥来源 {keys[int(_keyok.get('index') or 0)][0]}")

    elapsed = round(time.monotonic() - t0, 2)
    if not done_accounts:
        msg = "媒体重扫失败：" + ("；".join(failures) if failures else "没有可重扫的账号")
        return {"ok": False, "message": msg, "updated": 0, "accounts": [],
                "elapsed": elapsed, "log": "\n".join(log_lines), "failures": failures}
    msg = (f"媒体重扫完成：{len(done_accounts)} 个账号，扫描 {scanned} 行，"
           f"其中含媒体 {media_rows} 行，写入 {updated} 行，耗时 {elapsed:.1f}s")
    if failures:
        msg += f"；{len(failures)} 个账号失败"
    emit("完成", 100, msg)
    return {"ok": True, "message": msg, "updated": updated, "accounts": done_accounts,
            "scanned": scanned, "media_rows": media_rows, "elapsed": elapsed,
            "log": "\n".join(log_lines), "failures": failures}