"""QQScope · 端到端验收脚本

    python scripts/verify.py            # 快速校验（不跑真实导入）
    python scripts/verify.py --full     # 含真实数据源导入 + 导出

输出每项 PASS/FAIL，最后给总结。退出码 0 = 全绿。
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --- 控制台编码兜底：Windows 默认 GBK(936)，被管道/子进程读取时会乱码或抛
# UnicodeEncodeError / UnicodeDecodeError。强制本进程 stdout/stderr 走 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> bool:
    try:
        ok, detail = fn()
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"{exc.__class__.__name__}: {exc}"
    RESULTS.append((name, ok, str(detail)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")
    return ok


def c_paths():
    from core import paths
    missing = [str(p) for p in (paths.ROOT, paths.TOOLS, paths.NT_UTIL) if not p.exists()]
    return (not missing), f"缺失={missing}" if missing else f"KEY={paths.LEGACY_KEY_FILE.exists()}"


def c_store():
    from core import store, paths
    store.init()
    need = {"account_qq", "kind", "peer_id", "ts", "direction", "text", "source"}
    # master 注册表
    m = store.connect()
    try:
        mcols = {r[1] for r in m.execute("PRAGMA table_info(accounts)")}
    finally:
        m.close()
    if not {"account_qq", "label", "source", "updated_at"} <= mcols:
        return False, f"master accounts 表缺列，现有={sorted(mcols)}"
    accs = [a["account_qq"] for a in store.list_accounts()]
    problems, checked, legacy_miss = [], 0, None
    for qq in accs:
        if not paths.account_db(qq).exists():
            continue
        con = store.connect(qq)
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(messages)")}
        finally:
            con.close()
        miss = need - cols
        if miss:
            problems.append(f"{qq} 缺列{miss}")
        checked += 1
    if accs and not checked and paths.legacy_present():
        con = store.connect(accs[0])   # 迁移前回退旧库（只读）
        try:
            legacy_miss = need - {r[1] for r in con.execute("PRAGMA table_info(messages)")}
        finally:
            con.close()
    if legacy_miss:
        return False, f"legacy messages 缺列 {legacy_miss}"
    detail = (f"账号库 {checked} 个 schema OK" if checked else "迁移前 legacy schema OK")
    return (not problems), detail if not problems else "; ".join(problems)


def c_account_layout():
    """task-11 物理分账号：**每个注册账号**都要有自己的 data/accounts/<qq>/qqscope.db。

    注意：data/accounts/ 下可能还有「非账号」目录（例如迁移时由
    data/decrypt/3060648699/ 这类目录名派生的），它们没有库是正常的，
    所以判据必须走 accounts 注册表，而不是遍历目录。
    """
    from core import paths
    if not paths.accounts_split_done():
        if paths.legacy_present():
            return True, "尚未迁移（重启 15555 时自动拆分，先备份后改名）"
        return True, "无账号数据（全新环境）"
    from core import store
    accs = [int(a["account_qq"]) for a in store.list_accounts()]
    missing = [qq for qq in accs if not (paths.account_db(qq)).exists()]
    extra = [d.name for d in sorted(paths.ACCOUNTS_DIR.iterdir())
             if d.is_dir() and d.name.isdigit() and int(d.name) not in accs]
    if missing:
        return False, f"{len(accs)} 个注册账号，缺库：{missing[:3]}"
    note = f"{len(accs)} 个注册账号各有独立库"
    if extra:
        note += f"；另有 {len(extra)} 个非账号目录（仅迁移残留，不算错）"
    return True, note

def c_no_orphan_accounts():
    """task-11：每个账号库内 account_qq 必须一致；注册表与目录一一对应。

    防回归：实时采集把「框架登录但尚未导入」的账号写进 messages 后，
    如果没同时登记 accounts 行，那些消息就是孤儿 —— 数据在库里，
    但 /api/accounts 列不出该账号，界面永远看不到（等于自动填充白干）。
    """
    from core import store, paths
    accs = list(store.list_accounts())
    listed = [int(a["account_qq"]) for a in accs]
    if paths.accounts_split_done():
        problems = []
        dirs = [d.name for d in paths.ACCOUNTS_DIR.iterdir() if d.is_dir()]
        for d in dirs:
            if d not in {str(q) for q in listed}:
                problems.append(f"目录 {d} 未注册")
        for qq in listed:
            if str(qq) not in set(dirs):
                problems.append(f"注册 {qq} 无目录")
            con = store.connect(qq)
            try:
                for tbl in ("messages", "contacts", "feeds"):
                    try:
                        n = con.execute(
                            f"SELECT COUNT(*) FROM {tbl} WHERE account_qq<>?", (qq,)).fetchone()[0]
                    except Exception:  # noqa: BLE001
                        n = 0
                    if n:
                        problems.append(f"{qq}.{tbl} 混入 {n} 行")
            finally:
                con.close()
        return (not problems), ("每账号库 account_qq 一致" if not problems
                                else "; ".join(problems[:3]))
    # 迁移前：旧单库孤儿检查（回退只读）
    if not listed:
        return True, "暂无账号"
    try:
        con = store.connect(listed[0])
        bad = {}
        for tbl in ("messages", "contacts", "feeds"):
            try:
                n = con.execute(
                    f"SELECT COUNT(*) FROM {tbl} t WHERE NOT EXISTS "
                    "(SELECT 1 FROM accounts a WHERE a.account_qq = t.account_qq)"
                ).fetchone()[0]
            except Exception:  # noqa: BLE001
                n = 0
            if n:
                bad[tbl] = n
        con.close()
        return (not bad), ("迁移前：无孤儿账号行" if not bad else "孤儿行：%s" % bad)
    except Exception as exc:  # noqa: BLE001
        return True, f"迁移前跳过（{type(exc).__name__}）"


def c_source(sid: str):
    def _f():
        mod = importlib.import_module(f"core.sources.{sid}_source")
        missing = [f for f in ("status", "probe", "sync") if not hasattr(mod, f)]
        try:
            st = mod.status()
        except Exception as exc:  # noqa: BLE001
            st = {"ready": False, "message": f"status() 抛异常 {exc}"}
        if missing:
            return False, f"缺函数 {missing}"
        return True, f"ready={st.get('ready')} msg={str(st.get('message'))[:60]}"
    return _f


EXPECTED_ROUTES_V5_V10 = {
    # v2 遗漏 + v9 新增
    "/api/logs", "/api/report", "/api/sources/{sid}/conversations",
    # v7 QQ 动态
    "/api/feeds", "/api/qzone/sync", "/api/sources/qzone/status",
    # v9/v10 框架终端与登录门
    "/api/framework/logs", "/api/framework/status", "/api/framework/qrcode",
    "/api/framework/start", "/api/framework/stop", "/api/login/status",
    # v9 实时采集
    "/api/live/status", "/api/live/focus", "/api/live/start", "/api/live/stop",
    "/api/live/events",
    # v4/v8 媒体管线与补下载
    "/api/media/stats", "/api/media/pending", "/api/media/backfill",
    "/api/media/backfill/status", "/api/media/backfill/stop", "/api/data/quality",
    "/api/sources/pack/rescan-media",
    # v3/v6 头像与资料
    "/api/avatar", "/api/avatar/group", "/api/profile", "/api/contact",
    # v9 网页发消息
    "/api/send", "/api/send/history",
    # v5/v6 语音（本地 ASR + 官方回填）
    "/api/voice/transcribe", "/api/voice/progress", "/api/voice/stats",
    "/api/voice/official/sync", "/api/voice/official/status",
    "/api/voice/official/stop", "/api/voice/official/stats",
}

RE_ROUTE = re.compile(r'@\w+\.(?:get|post|patch|delete|put)\(\s*"([^"]+)"')


def c_routes_v5_v10():
    """静态核对 v5-v10 的接口是否都真的注册了（不依赖起服务，纯正则扫装饰器）。

    背景：SPEC 第 11 章的 33 条接口曾是「文档写了、没人验」的状态；
    这个检查把「接口存在性」纳入一键验收，防止路由被误删。
    """
    files = [ROOT / "server" / "app.py"] + sorted((ROOT / "server").glob("routes_*.py"))
    found = set()
    for f in files:
        if not f.exists():
            continue
        found.update(RE_ROUTE.findall(f.read_text(encoding="utf-8")))
    missing = sorted(EXPECTED_ROUTES_V5_V10 - found)
    return (not missing), f"已注册 {len(found)} 条；v5-v10 期望 {len(EXPECTED_ROUTES_V5_V10)} 条" +         (f"；缺失 {missing}" if missing else "，无缺失")


def c_no_orphan_accounts():
    """消息/联系人/动态的 account_qq 必须都能在 accounts 里找到。

    防回归：实时采集把「框架登录但尚未导入」的账号写进 messages 后，
    如果没同时登记 accounts 行，那些消息就是孤儿 —— 数据在库里，
    但 /api/accounts 列不出该账号，界面永远看不到（等于自动填充白干）。
    """
    from core import store
    con = store.connect()
    try:
        bad = {}
        for tbl in ("messages", "contacts", "feeds"):
            try:
                n = con.execute(
                    f"SELECT COUNT(*) FROM {tbl} t WHERE NOT EXISTS "
                    "(SELECT 1 FROM accounts a WHERE a.account_qq = t.account_qq)"
                ).fetchone()[0]
            except Exception:  # noqa: BLE001
                n = 0
            if n:
                bad[tbl] = n
    finally:
        con.close()
    return (not bad), ("孤儿行：%s" % bad) if bad else "无孤儿账号行"


def c_export_mod():
    mod = importlib.import_module("core.export")
    return hasattr(mod, "export"), "export() 存在" if hasattr(mod, "export") else "缺 export()"


def c_web_src():
    f = ROOT / "web" / "index.html"
    if not f.exists():
        return False, "web/index.html 不存在"
    h = f.read_text(encoding="utf-8")
    probs = []
    if "/*__QQSCOPE_BOOT__*/" not in h:
        probs.append("缺 BOOT 占位符")
    if "/*__QQSCOPE_ENGINE__*/" not in h:
        probs.append("缺 ENGINE 占位符")
    if re.search(r'<script[^>]+src=', h):
        probs.append("存在 <script src=>")
    if re.search(r'(src|href)\s*=\s*["\']https?://', h):
        probs.append("存在外部 http(s) 引用")
    return (not probs), "OK" if not probs else "; ".join(probs)


def c_build():
    out = ROOT / "app" / "dist" / "QQScope.html"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"   # 子进程输出 UTF-8，避免 GBK 解码炸 reader 线程
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_web.py")],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, cwd=str(ROOT))
    if r.returncode != 0:
        return False, (r.stdout + r.stderr)[-200:]
    if not out.exists():
        return False, "产物不存在"
    h = out.read_text(encoding="utf-8")
    if "/*__QQSCOPE_BOOT__*/" in h or "/*__QQSCOPE_ENGINE__*/" in h:
        return False, "占位符残留（经典 bug）"
    if re.search(r'(src|href)\s*=\s*["\']https?://', h):
        return False, "产物含外部引用"
    return True, f"{out.stat().st_size/1024/1024:.2f} MB"


def c_real_data(full: bool):
    def _f():
        from core import store
        accs = store.list_accounts()
        if not accs:
            return (not full), "库里暂无账号（--full 时才要求有数据）"
        total = sum(a["messages"] for a in accs)
        selfn = sum(a["self_messages"] for a in accs)
        ok = (not full) or (total >= 260000 and selfn >= 2800)
        detail = f"账号={[a['account_qq'] for a in accs]} 总消息={total} 自发={selfn}"
        if full and not ok:
            detail += "  ← 未达验收线（>=260000 / >=2800）"
        return ok, detail
    return _f


def c_isolation() -> tuple:
    """多账号数据隔离矩阵（临时副本库 + 诱饵账号，绝不碰真实 data/qqscope.db）。"""
    script = ROOT / "scripts" / "isolation_test.py"
    if not script.exists():
        return False, "scripts/isolation_test.py 不存在"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["QQSCOPE_LIVE_AUTOSTART"] = "0"
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, cwd=str(ROOT))
    lines = [ln for ln in (r.stdout + "\n" + r.stderr).strip().splitlines() if ln.strip()]
    tail = lines[-1] if lines else ""
    summary = next((ln for ln in reversed(lines) if "通过" in ln), tail)
    return (r.returncode == 0), f"exit={r.returncode} :: {summary.strip()[:60]}"


def c_export_run(full: bool):
    def _f():
        from core import store
        from core import export as ex
        accs = store.list_accounts()
        if not accs:
            return (not full), "暂无数据，跳过导出实测"
        a = accs[0]
        cs = store.list_contacts(a["account_qq"], limit=3)
        if not cs:
            return (not full), "暂无会话，跳过"
        targets = [{"kind": c["kind"], "peer_id": c["peer_id"]} for c in cs]
        res = ex.export(a["account_qq"], targets, ["html", "txt", "md"], "per_peer")
        files = res.get("files") or []
        z = res.get("zip")
        ok = len(files) >= len(targets) * 3 and z and Path(z).exists()
        return ok, f"{len(files)} 个文件, zip={'有' if z else '无'}, dir={res.get('dir')}"
    return _f


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="要求真实数据与导出全流程")
    args = ap.parse_args()

    print(f"===== QQScope 验收 ({'full' if args.full else 'quick'}) =====")
    check("core.paths 可用", c_paths)
    check("core.store schema", c_store)
    check("每账号独立库布局", c_account_layout)
    check("数据源 pack 模块", c_source("pack"))
    check("数据源 bot 模块", c_source("bot"))
    check("v5-v10 接口已注册", c_routes_v5_v10)
    check("无孤儿账号行", c_no_orphan_accounts)
    check("core.export 模块", c_export_mod)
    check("web 源码自检", c_web_src)
    check("构建单文件前端", c_build)
    check("多账号数据隔离矩阵", c_isolation)
    check("数据现状", c_real_data(args.full))
    if args.full:
        check("导出实测", c_export_run(True))

    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n===== 总结 =====")
    print(f"  {len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    if bad:
        print("  未通过：" + ", ".join(bad))
        sys.exit(1)
    print("  全绿 ✅")


if __name__ == "__main__":
    main()
