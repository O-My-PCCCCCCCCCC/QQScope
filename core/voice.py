# -*- coding: utf-8 -*-
"""QQScope · 本地语音转文字（ASR）管线

链路（工具链在 tools/asr/.venv，绕开 PyAV）：
    messages.media(kind='voice', file) -> 绝对路径
    -> tools/asr/worker.py（pilk 解 SILK -> 16k WAV -> numpy -> faster-whisper）
    -> 结果写回 media JSON：voice_text / voice_lang / voice_engine / voice_status ...

设计
----
* 本模块只用标准库，可被 server（nt_msg_db_util venv）直接 import；
  真正的 ASR 在 tools/asr/.venv 的 worker 子进程里跑。
* 多进程并行：按音频时长贪心分片，每个 worker 一个子进程；父进程边跑边读
  JSONL 结果并写回 store，天然支持断点续跑（voice_status='ok' 的跳过）。
* 失败如实记录（missing/decode/asr/empty），绝不当作成功。
* `media` 是 JSON 字符串列，读出 -> 改 -> 写回，不动 core/store.py。

CLI：
    python -m core.voice --account 1605289411 --workers 4 [--limit 20] [--model small]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from core import media, paths, store

SOURCE_ROOT = paths.ROOT
ASR_DIR = SOURCE_ROOT / "tools" / "asr"
ASR_PY = ASR_DIR / ".venv" / "Scripts" / "python.exe"
ASR_WORKER = ASR_DIR / "worker.py"
RUN_DIR = ASR_DIR / "_run"

DEFAULT_WORKERS = 16
DEFAULT_THREADS = 2
DEFAULT_VAD_SKIP_RATIO = 0.12
DEFAULT_MODEL = "small"
JOB_TIMEOUT = 7200.0

_TERMINAL_OK = ("ok",)


class VoiceError(Exception):
    """带人话中文说明的 ASR 错误。"""


ALLOW_ENV = "QQSCOPE_ASR_ALLOW"
LOCK_FILE = RUN_DIR / "asr.lock"


def _require_arm(explicit: bool) -> None:
    """本地 ASR 默认关闭：避免被 API/脚本意外触发把机器打满。"""
    if explicit or os.environ.get(ALLOW_ENV) == "1":
        return
    raise VoiceError(
        "本地 ASR 当前默认关闭（用户反馈机器卡，已暂停）。"
        "确认要跑请显式传 confirm=True，或先设环境变量 QQSCOPE_ASR_ALLOW=1；"
        "并且同一时间只允许一个 run。")


def _acquire_lock() -> None:
    """互斥锁：防止两套跑法叠加互抢 CPU。"""
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            pid = 0
        alive = False
        if pid > 0:
            try:
                os.kill(pid, 0)
                alive = True
            except OSError:
                alive = False
        if alive:
            raise VoiceError(f"已经有一个 ASR run 在跑（PID {pid}），拒绝重复启动。")
    LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")


def _release_lock() -> None:
    try:
        LOCK_FILE.unlink()
    except OSError:
        pass


def _venv_python(required: bool = True) -> Path:
    if ASR_PY.exists():
        return ASR_PY
    if required:
        raise VoiceError(
            f"缺少 ASR 环境：找不到 {ASR_PY}。"
            "请确认 tools/asr/.venv 存在（faster-whisper + pilk + numpy）。")
    return ASR_PY


def _worker_env() -> dict:
    env = dict(os.environ)
    env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    env.setdefault("HF_HUB_DISABLE_XET", "1")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


# ── 待处理任务 ──────────────────────────────────────────────────────────────
def pending_jobs(account_qq, limit: int | None = None, retry_failed: bool = False,
                 only_ids: list[int] | None = None, newest: bool = False) -> list[dict]:
    """列出需要转写的语音：kind=voice 且本地文件存在；已成功的跳过。"""
    qq = int(account_qq)
    # 注意：不要把 file IS NULL 的行排除掉，否则「本地文件缺失」的语音永远进不了
    # pending_jobs -> 永远不被标记为 missing -> /api/voice/stats 的 pending 永远清零不了。
    # 这类行会在 _run_locked 里走 missing 分支，如实记 voice_status='missing'。
    where = "account_qq=? AND json_extract(media,'$.kind')='voice'"
    args: list = [qq]
    if not retry_failed:
        # ok=已成功；empty=确认无语音，都不再重复跑
        where += (" AND (json_extract(media,'$.voice_status') IS NULL "
                  "OR json_extract(media,'$.voice_status') NOT IN ('ok','empty'))")
    if only_ids:
        ids = [int(x) for x in only_ids]
        where += " AND id IN (%s)" % ",".join("?" * len(ids))
        args += ids
    sql = f"SELECT id, ts, media FROM messages WHERE {where} ORDER BY ts {'DESC' if newest else 'ASC'}"
    con = store.connect(qq)
    try:
        rows = con.execute(sql, args).fetchall()
    finally:
        con.close()

    out: list[dict] = []
    for r in rows:
        try:
            m = json.loads(r["media"] or "{}")
        except (TypeError, ValueError):
            continue
        path = media.resolve(qq, m)
        out.append({
            "id": int(r["id"]), "ts": int(r["ts"]), "media": m,
            "path": path, "duration": float(m.get("duration") or 0),
            "name": m.get("name") or "",
        })
    if limit:
        out = out[: int(limit)]
    return out


def _shard(jobs: list[dict], workers: int) -> list[list[dict]]:
    """按时长贪心分片，尽量让每个 worker 的音频总时长接近。"""
    shards: list[list[dict]] = [[] for _ in range(max(1, workers))]
    loads = [0.0] * len(shards)
    for j in sorted(jobs, key=lambda x: x["duration"], reverse=True):
        i = loads.index(min(loads))
        shards[i].append(j)
        # media.duration 对非 SILK 文件（mp3/mp4…）会被按 AMR 估大，封顶 300s 避免分片失衡
        loads[i] += min(max(j["duration"], 1.0), 300.0)
    return [s for s in shards if s]


# ── 写回 store ──────────────────────────────────────────────────────────────
def apply_result(account_qq, msg_id: int, result: dict) -> bool:
    """把单条 ASR 结果合并进 messages.media（JSON 字符串列）。"""
    mid = int(msg_id)
    con = store.connect(account_qq)
    try:
        row = con.execute("SELECT media FROM messages WHERE id=? AND account_qq=?",
                          (mid, int(account_qq))).fetchone()
        if row is None or not row["media"]:
            return False
        try:
            m = json.loads(row["media"])
        except (TypeError, ValueError):
            m = {}
        ok = bool(result.get("ok")) and bool((result.get("text") or "").strip())
        if ok:
            m["voice_text"] = str(result.get("text")).strip()
            m["voice_lang"] = result.get("lang") or ""
            m["voice_engine"] = result.get("engine") or ""
            m["voice_seconds"] = result.get("seconds")
            m["voice_elapsed"] = result.get("elapsed")
            m["voice_status"] = "ok"
            m["voice_error"] = None
        else:
            m["voice_status"] = str(result.get("stage") or "failed")
            m["voice_error"] = str(result.get("error") or "")[:300]
            m["voice_lang"] = result.get("lang")
            m["voice_engine"] = result.get("engine")
            m["voice_seconds"] = result.get("seconds")
        m["voice_at"] = int(time.time())
        con.execute("UPDATE messages SET media=? WHERE id=?",
                    (json.dumps(m, ensure_ascii=False), mid))
        con.commit()
        return True
    finally:
        con.close()


def _read_new(path: Path, offset: int) -> tuple[list[dict], int]:
    """从 offset 开始读 JSONL 的新行。"""
    out: list[dict] = []
    if not path.exists():
        return out, offset
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            data = fh.read()
            offset = fh.tell()
    except OSError:
        return out, offset
    for line in data.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out, offset


# ── 单条 / 批量 ─────────────────────────────────────────────────────────────
def transcribe_one(silk_path, model=None, threads: int = DEFAULT_THREADS,
                   model_size: str | None = None, confirm: bool = False) -> dict:
    """转写单个 SILK 文件，返回 {text, lang, seconds, engine} 或 {ok:False, error}。"""
    _require_arm(confirm)
    py = _venv_python()
    src = Path(silk_path)
    if not src.is_file():
        return {"ok": False, "error": "文件不存在", "stage": "missing"}
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    jobs_file = RUN_DIR / "one_jobs.json"
    out_file = RUN_DIR / "one_out.jsonl"
    try:
        out_file.unlink()
    except OSError:
        pass
    jobs_file.write_text(json.dumps([{"id": 1, "path": str(src)}]), encoding="utf-8")
    cmd = [str(py), str(ASR_WORKER), "--jobs", str(jobs_file), "--out", str(out_file),
           "--model", str(model_size or model or DEFAULT_MODEL), "--threads", str(int(threads))]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=_worker_env(), cwd=str(ASR_DIR),
                              timeout=JOB_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"识别超时（>{int(JOB_TIMEOUT)}s）", "stage": "timeout"}
    results, _ = _read_new(out_file, 0)
    for r in results:
        if int(r.get("id") or 0) == 1:
            return r
    tail = ((proc.stdout or "") + (proc.stderr or ""))[-300:]
    return {"ok": False, "error": f"worker 没有返回结果：{tail}", "stage": "worker"}


def run(account_qq, limit: int | None = None, workers: int = DEFAULT_WORKERS,
        model_size: str = DEFAULT_MODEL, threads: int = DEFAULT_THREADS,
        progress=None, retry_failed: bool = False,
        vad_skip_ratio: float | None = DEFAULT_VAD_SKIP_RATIO,
        with_timestamps: bool = False,
        only_ids: list[int] | None = None, newest: bool = False,
        confirm: bool = False) -> dict:
    """并发转写一个账号的语音。返回统计字典。"""
    _require_arm(confirm)
    _acquire_lock()
    try:
        return _run_locked(account_qq, limit, workers, model_size, threads, progress,
                           retry_failed, vad_skip_ratio, with_timestamps, only_ids, newest)
    finally:
        _release_lock()


def _run_locked(account_qq, limit, workers, model_size, threads, progress,
                retry_failed, vad_skip_ratio, with_timestamps, only_ids, newest) -> dict:
    t0 = time.monotonic()
    qq = int(account_qq)
    py = _venv_python()

    def emit(stage: str, done: int, total: int, current: str = "") -> None:
        if progress:
            try:
                progress(stage, int(done), int(total), str(current))
            except Exception:  # noqa: BLE001
                pass

    jobs = pending_jobs(qq, limit=limit, retry_failed=retry_failed, only_ids=only_ids,
                        newest=newest)
    total = len(jobs)
    if not total:
        return {"ok": True, "message": "没有需要转写的语音（全部已完成）",
                "account": qq, "total": 0, "transcribed": 0, "failed": 0,
                "lang_mismatch": 0, "missing_files": 0, "elapsed": 0.0,
                "model": model_size, "engine": "", "workers": workers}

    emit("准备", 0, total, "检查本地文件")
    missing = [j for j in jobs if not (j["path"] and Path(j["path"]).is_file())]
    runnable = [j for j in jobs if j["path"] and Path(j["path"]).is_file()]
    for j in missing:
        apply_result(qq, j["id"], {"ok": False, "error": "本地文件不存在", "stage": "missing"})

    workers = max(1, min(int(workers), len(runnable))) if runnable else 1
    shards = _shard(runnable, workers)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = f"{qq}_{int(time.time())}"
    seq = [0]

    def _job_timeout(dur) -> float:
        """单文件看门狗上限：1 秒的音频也别卡住超过 90s。"""
        return max(180.0, min(10.0 * float(dur or 0), 600.0))

    def _spawn(shard: list[dict], tag: str) -> dict:
        seq[0] += 1
        jobs_file = RUN_DIR / f"jobs_{stamp}_{tag}_{seq[0]}.json"
        out_file = RUN_DIR / f"out_{stamp}_{tag}_{seq[0]}.jsonl"
        jobs_file.write_text(json.dumps(
            [{"id": j["id"], "path": j["path"]} for j in shard], ensure_ascii=False),
            encoding="utf-8")
        cmd = [str(py), str(ASR_WORKER), "--jobs", str(jobs_file), "--out", str(out_file),
               "--model", model_size, "--threads", str(int(threads))]
        if vad_skip_ratio is not None:
            cmd += ["--vad-skip-ratio", str(float(vad_skip_ratio))]
        if with_timestamps:
            cmd.append("--with-timestamps")
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace",
                                env=_worker_env(), cwd=str(ASR_DIR))
        return {"proc": proc, "out": out_file, "offset": 0, "jobs": list(shard),
                "order": [j["id"] for j in shard], "returned": set(), "ready": False,
                "names": {j["id"]: j["name"] for j in shard}, "last": time.monotonic()}

    procs: list[dict] = [_spawn(shard, "s%d" % idx) for idx, shard in enumerate(shards)]

    done = 0
    ok_n = 0
    fail_n = 0
    lang_bad = 0
    timeouts_n = 0
    last = 0.0
    current = ""

    def _absorb(p: dict, rows: list[dict]) -> None:
        nonlocal done, ok_n, fail_n, lang_bad, current
        for r in rows:
            if r.get("event"):
                p["ready"] = True
                continue
            mid = int(r.get("id") or 0)
            if mid in p["returned"]:
                continue
            p["returned"].add(mid)
            apply_result(qq, mid, r)
            done += 1
            if r.get("ok") and (r.get("text") or "").strip():
                ok_n += 1
                if (r.get("lang") or "zh") not in ("zh", ""):
                    lang_bad += 1
            else:
                fail_n += 1
            current = p["names"].get(mid, str(mid))

    while True:
        for i, p in enumerate(procs):
            rows, p["offset"] = _read_new(p["out"], p["offset"])
            if rows:
                p["last"] = time.monotonic()
                _absorb(p, rows)
            if p["proc"].poll() is not None:
                continue
            stuck = next((mid for mid in p["order"] if mid not in p["returned"]), None)
            if stuck is None:
                continue
            dur = next((j["duration"] for j in p["jobs"] if j["id"] == stuck), 0)
            limit_s = _job_timeout(dur)
            grace = 0.0 if p.get("ready") else 240.0
            if time.monotonic() - p["last"] <= limit_s + grace:
                continue
            # 看门狗：单文件卡死 -> 杀 worker、记 timeout、用剩余任务重启
            try:
                p["proc"].kill()
            except Exception:  # noqa: BLE001
                pass
            apply_result(qq, stuck, {"ok": False,
                                     "error": "单文件超时（>%ds），已跳过" % int(limit_s),
                                     "stage": "timeout"})
            p["returned"].add(stuck)
            done += 1
            fail_n += 1
            timeouts_n += 1
            current = "%s 单文件超时" % stuck
            rest = [j for j in p["jobs"] if j["id"] not in p["returned"]]
            if rest:
                procs[i] = _spawn(rest, "s%dr" % i)
        if not any(p["proc"].poll() is None for p in procs):
            break
        now = time.monotonic()
        if now - last >= 1.0:
            emit("转写中", done + len(missing), total, current)
            last = now
        time.sleep(0.5)

    # 收尾：读干净 + 统计没返回的任务
    for p in procs:
        rows, p["offset"] = _read_new(p["out"], p["offset"])
        _absorb(p, rows)
        for mid in (set(p["order"]) - p["returned"]):
            apply_result(qq, mid, {"ok": False, "error": "worker 未返回结果（可能崩溃）",
                                   "stage": "worker"})
            fail_n += 1
        try:
            p["proc"].wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass
    elapsed = round(time.monotonic() - t0, 1)
    emit("完成", total, total, "")
    audio_sec = sum(float(j.get("duration") or 0) for j in runnable)
    result = {
        "ok": True,
        "message": (f"语音转写完成：成功 {ok_n} / 失败 {fail_n}"
                    f"（本地缺失 {len(missing)}），语言跑偏 {lang_bad}，耗时 {elapsed:.0f}s"),
        "account": qq, "total": total, "transcribed": ok_n, "failed": fail_n,
        "lang_mismatch": lang_bad, "missing_files": len(missing), "timeouts": timeouts_n,
        "elapsed": elapsed, "model": model_size, "workers": len(shards),
        "threads": int(threads), "vad_skip_ratio": vad_skip_ratio,
        "engine": f"faster-whisper-{model_size}-int8",
        "audio_seconds": round(audio_sec, 1),
        "speed": round(audio_sec / elapsed, 2) if elapsed > 0 else None,
    }
    return result


# ── 统计 ────────────────────────────────────────────────────────────────────
def stats(account_qq) -> dict:
    """语音转写统计（真实数字，失败单独计）。"""
    qq = int(account_qq)
    con = store.connect(qq)
    try:
        row = con.execute(
            "SELECT "
            "COUNT(*) AS total, "
            "SUM(CASE WHEN json_extract(media,'$.file') IS NOT NULL THEN 1 ELSE 0 END) AS local_files, "
            "SUM(CASE WHEN json_extract(media,'$.voice_status')='ok' THEN 1 ELSE 0 END) AS transcribed, "
            "SUM(CASE WHEN json_extract(media,'$.voice_status') IS NOT NULL "
            "         AND json_extract(media,'$.voice_status')<>'ok' THEN 1 ELSE 0 END) AS failed, "
            "SUM(CASE WHEN json_extract(media,'$.voice_status')='ok' "
            "         AND json_extract(media,'$.voice_lang') IS NOT NULL "
            "         AND json_extract(media,'$.voice_lang')<>'' "
            "         AND json_extract(media,'$.voice_lang')<>'zh' THEN 1 ELSE 0 END) AS lang_mismatch, "
            "AVG(CASE WHEN json_extract(media,'$.voice_status')='ok' "
            "         THEN json_extract(media,'$.voice_seconds') END) AS avg_seconds, "
            "SUM(CASE WHEN json_extract(media,'$.voice_status')='ok' "
            "         THEN json_extract(media,'$.voice_seconds') ELSE 0 END) AS ok_seconds, "
            "SUM(CASE WHEN json_extract(media,'$.voice_status')='ok' "
            "         THEN json_extract(media,'$.voice_elapsed') ELSE 0 END) AS ok_elapsed "
            "FROM messages WHERE account_qq=? AND json_extract(media,'$.kind')='voice'",
            (qq,)).fetchone()
        status_rows = con.execute(
            "SELECT json_extract(media,'$.voice_status') AS st, COUNT(*) AS n "
            "FROM messages WHERE account_qq=? AND json_extract(media,'$.kind')='voice' "
            "GROUP BY st", (qq,)).fetchall()
        engine = con.execute(
            "SELECT json_extract(media,'$.voice_engine') AS e, COUNT(*) AS n "
            "FROM messages WHERE account_qq=? AND json_extract(media,'$.voice_engine') IS NOT NULL "
            "GROUP BY e ORDER BY n DESC LIMIT 1", (qq,)).fetchone()
    finally:
        con.close()
    avg = row["avg_seconds"]
    total = int(row["total"] or 0)
    transcribed = int(row["transcribed"] or 0)
    return {
        "account": qq,
        "voice_total": total,
        "local_files": int(row["local_files"] or 0),
        "transcribed": transcribed,
        "failed": int(row["failed"] or 0),
        "pending": total - transcribed - int(row["failed"] or 0),
        "lang_mismatch": int(row["lang_mismatch"] or 0),
        "avg_seconds": round(float(avg), 2) if avg is not None else None,
        "transcribed_seconds": round(float(row["ok_seconds"] or 0), 1),
        "asr_seconds": round(float(row["ok_elapsed"] or 0), 1),
        "engine": (engine["e"] if engine else None),
        "model": DEFAULT_MODEL,
        "status_counts": {(r["st"] or "pending"): int(r["n"]) for r in status_rows},
    }


# ── CLI ─────────────────────────────────────────────────────────────────────
def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(description="QQScope 语音转文字（本地 ASR）")
    ap.add_argument("--account", type=int, required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--recent", type=int, default=0,
                    help="只转最近 N 条（轻量用法；优先于 --limit）")
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    ap.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--vad-skip-ratio", type=float, default=DEFAULT_VAD_SKIP_RATIO)
    ap.add_argument("--with-timestamps", action="store_true")
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--yes", action="store_true",
                    help="确认执行（本地 ASR 默认关闭，需显式 --yes 或 QQSCOPE_ASR_ALLOW=1）")
    ap.add_argument("--stats-only", action="store_true")
    args = ap.parse_args(argv)

    if args.stats_only:
        print(json.dumps(stats(args.account), ensure_ascii=False, indent=1))
        return 0

    def prog(stage, done, total, cur):
        sys.stdout.write(f"\r[{stage}] {done}/{total}  {cur[:40]:<40}")
        sys.stdout.flush()

    limit = args.recent or args.limit or None
    res = run(args.account, limit=limit, newest=bool(args.recent), workers=args.workers,
              model_size=args.model, threads=args.threads, progress=prog,
              retry_failed=args.retry_failed, vad_skip_ratio=args.vad_skip_ratio,
              with_timestamps=args.with_timestamps, confirm=args.yes)
    print()
    print(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps(stats(args.account), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())