r"""QQScope · 统一后端（FastAPI）

两个数据源（窗口A 数据包 / 窗口B 机器人框架）汇聚到 core.store，
对外提供 SPEC 第 5 节冻结的 HTTP API，并托管单文件前端。

启动：
  tools\nt_msg_db_util\.venv\Scripts\python.exe server\app.py
访问：http://127.0.0.1:15555
"""
from __future__ import annotations

import collections
import importlib
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from core import paths, store

DIST_HTML = ROOT / "app" / "dist" / "QQScope.html"
SETTINGS_FILE = ROOT / "data" / "server" / "settings.json"

VERSION = "2.0.0"

DEFAULT_SETTINGS = {
    "data_root": str(paths.DEFAULT_DATA_ROOT),
    "port": 15555,
    "onebot": {"base": "http://127.0.0.1:3000", "token": ""},
    "ai": {"provider": "deepseek", "base": "https://api.deepseek.com/v1",
           "key": "", "model": "deepseek-chat"},
    "ui": {"export_dir": str(paths.EXPORT_DIR)},
    # QZone 登录态：cookie 只存服务端，GET /api/settings 一律掩码
    "qzone": {"cookie": "", "uin": 0, "enabled": False},
    # 发送策略（安全阀）：默认只允许发给自己，群聊默认禁止
    "send": {"allow_others": False, "allow_groups": False, "dry_run": False},
}

app = FastAPI(title="QQScope", version=VERSION)

# ── 运行日志（给前端「控制台」面板用）────────────────────────────────────────
LOG_BUFFER: "collections.deque[dict]" = collections.deque(maxlen=800)


def log(level: str, source: str, msg: str) -> None:
    LOG_BUFFER.append({"ts": int(time.time()), "level": level,
                       "source": source, "msg": str(msg)[:500]})
    try:
        print(f"[{level}] [{source}] {msg}", flush=True)
    except Exception:
        pass


@app.middleware("http")
async def _log_middleware(request: Request, call_next):
    t0 = time.time()
    try:
        resp = await call_next(request)
    except Exception as exc:  # noqa: BLE001
        log("error", "http", f"{request.method} {request.url.path} 异常：{exc}")
        raise
    ms = int((time.time() - t0) * 1000)
    path = request.url.path
    if path.startswith("/api/") and path != "/api/logs":
        lvl = "warn" if resp.status_code >= 400 else "info"
        q = ("?" + request.url.query) if request.url.query else ""
        log(lvl, "http", f"{request.method} {path}{q} -> {resp.status_code} ({ms}ms)")
    return resp


@app.get("/api/logs")
def get_logs(limit: int = 200, level: str | None = None, since: int | None = None):
    items = list(LOG_BUFFER)
    if level:
        items = [x for x in items if x["level"] == level]
    if since:
        items = [x for x in items if x["ts"] > int(since)]
    return {"logs": items[-int(limit):], "total": len(LOG_BUFFER)}


# ── 数据源注册表 ────────────────────────────────────────────────────────────
SOURCE_MODULES = {"pack": "core.sources.pack_source", "bot": "core.sources.bot_source",
                  "qzone": "core.sources.qzone"}   # qzone 只走通用 probe/sync，不上「数据源」页
SOURCE_LABELS = {"pack": "数据包读取（离线）", "bot": "机器人框架读取（在线）"}

_PROGRESS: dict[str, dict] = {}
_PROGRESS_LOCK = threading.Lock()


def set_progress(sid: str, stage: str, pct: int, msg: str) -> None:
    with _PROGRESS_LOCK:
        p = _PROGRESS.setdefault(sid, {})
        p.update({"stage": stage, "pct": int(pct), "msg": msg, "at": int(time.time())})


def load_source(sid: str):
    """返回数据源模块；模块不存在或导入失败时返回 (None, 原因)。"""
    mod_name = SOURCE_MODULES.get(sid)
    if not mod_name:
        return None, f"未知数据源 {sid}"
    try:
        mod = importlib.import_module(mod_name)
    except Exception as exc:  # noqa: BLE001
        return None, f"数据源模块未就绪（{mod_name}）：{exc.__class__.__name__}: {exc}"
    for fn in ("status", "probe", "sync"):
        if not hasattr(mod, fn):
            return None, f"数据源 {sid} 缺少函数 {fn}()，不符合 SPEC 契约"
    return mod, ""


# ── 配置 ────────────────────────────────────────────────────────────────────
_FRAMEWORK_ONEBOT = ROOT / "data" / "framework" / "onebot.json"


def _framework_endpoint() -> dict:
    """框架安装时写下的 OneBot 地址/token，让窗口 B 开箱即用。"""
    try:
        if _FRAMEWORK_ONEBOT.exists():
            j = json.loads(_FRAMEWORK_ONEBOT.read_text(encoding="utf-8"))
            return {"base": j.get("base") or "", "token": j.get("token") or ""}
    except Exception:
        pass
    return {}


def load_settings() -> dict:
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    if SETTINGS_FILE.exists():
        try:
            got = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            for k, v in got.items():
                if isinstance(v, dict) and isinstance(s.get(k), dict):
                    s[k].update(v)
                else:
                    s[k] = v
        except Exception:
            pass
    # 框架的 OneBot 地址/token 作为兜底（用户没手工填时生效）
    fw = _framework_endpoint()
    _ob = s.setdefault("onebot", {})
    if fw.get("base") and not _ob.get("base"):
        _ob["base"] = fw["base"]
    if fw.get("token") and not _ob.get("token"):
        _ob["token"] = fw["token"]
    return s


def save_settings(s: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


def fail(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=code)


@app.on_event("startup")
def _startup() -> None:
    store.init()
    # task-11：一次性把旧单库 data/qqscope.db 拆成每账号独立库（先备份、旧库改名保留）
    if str(os.environ.get("QQSCOPE_AUTO_SPLIT", "1")).lower() not in ("0", "false", "no", "off"):
        try:
            res = store.migrate_legacy_split()
            if res.get("ok"):
                print(f"[迁移] 数据拆分完成：{res.get('message')}")
            elif res.get("reason") not in (None, "no_legacy", "already_split"):
                print(f"[迁移] 跳过：{res.get('message') or res.get('reason')}")
        except Exception as exc:  # noqa: BLE001
            print(f"[迁移] 失败（旧库保持不动）：{exc.__class__.__name__}: {exc}")
    print(f"QQScope {VERSION} 已启动")


# ── 基础 ────────────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"ok": True, "version": VERSION, "time": int(time.time())}


@app.get("/", response_class=HTMLResponse)
def index():
    if DIST_HTML.exists():
        return HTMLResponse(DIST_HTML.read_text(encoding="utf-8"))
    return HTMLResponse(
        "<h1>QQScope</h1><p>前端还没构建。先运行：<code>python scripts/build_web.py</code></p>",
        status_code=200)


# ── 数据源 ──────────────────────────────────────────────────────────────────
_SRC_CACHE: dict = {"at": 0.0, "data": None}
SRC_CACHE_TTL = 8          # status() 最坏 ~0.9s（探测 3 个端口），加短缓存避免拖慢首页


@app.get("/api/sources")
def sources(force: int = 0):
    now = time.time()
    if not force and _SRC_CACHE["data"] and (now - _SRC_CACHE["at"]) < SRC_CACHE_TTL:
        return _SRC_CACHE["data"]
    out = []
    for sid, label in SOURCE_LABELS.items():
        mod, why = load_source(sid)
        if mod is None:
            out.append({"id": sid, "name": label, "kind": sid, "ready": False,
                        "message": why, "detail": {}})
            continue
        try:
            st = mod.status() or {}
        except Exception as exc:  # noqa: BLE001
            st = {"ready": False, "message": f"status() 异常：{exc}"}
        st.setdefault("id", sid)
        st.setdefault("name", label)
        st.setdefault("kind", sid)
        st.setdefault("ready", False)
        st.setdefault("detail", {})
        out.append(st)
    payload = {"sources": out}
    _SRC_CACHE["at"] = now
    _SRC_CACHE["data"] = payload
    return payload


@app.post("/api/sources/{sid}/conversations")
async def source_conversations(sid: str, request: Request):
    mod, why = load_source(sid)
    if mod is None:
        return fail(why, 503)
    if not hasattr(mod, "conversations"):
        return fail(f"数据源 {sid} 不支持列出会话", 400)
    try:
        opts = await request.json()
    except Exception:
        opts = {}
    try:
        return {"conversations": mod.conversations(opts or [])}
    except Exception as exc:  # noqa: BLE001  BotError 等一律转成人话
        return fail(str(exc) or exc.__class__.__name__, 502)


def _fill_bot_defaults(sid: str, opts: dict) -> dict:
    """窗口B：前端没填 base/token 时，自动用设置里的（设置会兜底到 data/framework/onebot.json）。"""
    if sid != "bot":
        return opts
    ob = load_settings().get("onebot", {}) or {}
    if not (opts.get("base") or "").strip():
        opts["base"] = ob.get("base") or "http://127.0.0.1:3000"
    if not (opts.get("token") or "").strip():
        opts["token"] = ob.get("token") or ""
    return opts


@app.post("/api/sources/{sid}/probe")
async def source_probe(sid: str, request: Request):
    mod, why = load_source(sid)
    if mod is None:
        return fail(why, 503)
    try:
        opts = await request.json()
    except Exception:
        opts = {}
    opts = _fill_bot_defaults(sid, opts or {})
    try:
        return mod.probe(opts)
    except Exception as exc:  # noqa: BLE001
        log("error", sid, f"同步失败：{exc}")
        if getattr(exc, "need_login", False):
            return JSONResponse({"ok": False, "error": str(exc), "need_login": True}, status_code=502)
        return fail(f"探测失败：{exc}", 500)


@app.post("/api/sources/{sid}/sync")
async def source_sync(sid: str, request: Request):
    mod, why = load_source(sid)
    if mod is None:
        return fail(why, 503)
    try:
        opts = await request.json()
    except Exception:
        opts = {}
    opts = _fill_bot_defaults(sid, opts or {})
    log("info", sid, f"开始同步：{json.dumps(opts, ensure_ascii=False)[:200]}")
    set_progress(sid, "准备", 0, "开始")
    t0 = time.time()
    try:
        res = mod.sync(opts or {}, progress=lambda st, pc, ms: set_progress(sid, st, pc, ms))
    except Exception as exc:  # noqa: BLE001
        set_progress(sid, "失败", 0, str(exc))
        if "QZONE_ACCOUNT_MISMATCH" in str(exc):
            return fail(str(exc).replace("QZONE_ACCOUNT_MISMATCH：", ""), 400)
        if getattr(exc, "need_login", False):
            return JSONResponse({"error": str(exc), "need_login": True}, status_code=502)
        return fail(f"同步失败：{exc}", 500)
    set_progress(sid, "完成", 100, "已完成")
    res = res or {}
    log("info" if res.get("ok", True) else "warn", sid,
        f"同步完成：{res.get('message') or ''} 新增 {res.get('imported', 0)} 条（{res.get('elapsed')}s）")
    res.setdefault("elapsed", round(time.time() - t0, 1))
    return res


@app.get("/api/progress/{sid}")
def source_progress(sid: str):
    with _PROGRESS_LOCK:
        return dict(_PROGRESS.get(sid) or {})


# ── 账号 ────────────────────────────────────────────────────────────────────
@app.get("/api/accounts")
def accounts():
    return {"accounts": store.list_accounts()}


@app.delete("/api/accounts/{qq}")
def account_delete(qq: int):
    store.delete_account(qq)
    return {"ok": True}


@app.get("/api/overview")
def overview(account: int | None = None):
    if not account:
        return fail("缺少 account（多账号隔离：总览必须指定账号，禁止全局兜底）")
    account = int(account)
    o = store.overview(account)
    o["daily"] = store.daily_stats(account)
    o["hour_hist"] = store.hour_hist(account, 1)
    o["top_contacts"] = store.top_contacts(account, 10)
    return o


@app.get("/api/report")
def report(account: int, limit: int = 30000):
    """给前端分析引擎用：只返回"自己发的 + 有文本"的消息（体积小）。
    前端直接 QQScopeEngine.computeReport(messages, meta) 即可出完整报告，
    情绪词典的唯一真源保持在 app/js/analysis.js，后端不重复实现。"""
    con = store.connect(account)
    try:
        rows = con.execute(
            "SELECT ts, direction, kind, peer_id, text FROM messages "
            "WHERE account_qq=? AND direction=1 AND text IS NOT NULL AND text!='' "
            "ORDER BY ts LIMIT ?", (int(account), int(limit))).fetchall()
    finally:
        con.close()
    meta = next((a for a in store.list_accounts() if a["account_qq"] == account), {})
    if meta:
        meta = dict(meta)
        meta["self_qq"] = account
    return {
        "meta": meta,
        "count": len(rows),
        "messages": [{"t": r["ts"], "d": r["direction"], "k": r["kind"],
                      "p": r["peer_id"], "x": r["text"]} for r in rows],
    }


# ── 会话 ────────────────────────────────────────────────────────────────────
@app.get("/api/contacts")
def contacts(account: int | None = None, kind: str | None = None,
             q: str | None = None, limit: int = 2000):
    if not account:
        return fail("缺少 account（多账号隔离：联系人必须指定账号，禁止全局兜底）")
    return {"contacts": store.list_contacts(int(account), kind, q, limit=limit)}


@app.patch("/api/contacts")
async def contact_patch(request: Request):
    b = await request.json()
    for f in ("account_qq", "kind", "peer_id"):
        if b.get(f) in (None, ""):
            return fail(f"缺少字段 {f}")
    store.set_contact_meta(int(b["account_qq"]), b["kind"], str(b["peer_id"]),
                           b.get("name"), b.get("remark"), b.get("avatar"))
    return {"ok": True}


@app.get("/api/messages")
def messages(account: int, kind: str, peer_id: str, limit: int = 200,
             offset: int = 0, since: int | None = None, until: int | None = None,
             order: str = "ASC"):
    msgs = store.list_messages(account, kind, peer_id, limit, offset, order, since, until)
    return {"messages": msgs,
            "total": store.count_messages(account, kind, peer_id),
            "contact": store.get_contact(account, kind, peer_id)}


# ── 导出 ────────────────────────────────────────────────────────────────────
def load_exporter():
    try:
        return importlib.import_module("core.export"), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"导出引擎未就绪（core/export.py）：{exc.__class__.__name__}: {exc}"


@app.post("/api/export")
async def do_export(request: Request):
    mod, why = load_exporter()
    if mod is None:
        return fail(why, 503)
    b = await request.json()
    account = b.get("account_qq")
    targets = b.get("targets") or []
    formats = b.get("formats") or ["html"]
    mode = b.get("mode") or "per_peer"
    if not account:
        return fail("缺少 account_qq")
    if not targets:
        return fail("请至少选择一个会话")
    try:
        return mod.export(int(account), targets, formats, mode)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return fail(f"导出失败：{exc}", 500)


@app.get("/api/export/list")
def export_list(account: int | None = None):
    if not account:
        return fail("缺少 account（多账号隔离：导出历史必须按账号过滤）")
    account = int(account)
    mod, _why = load_exporter()
    jobs = []
    if paths.EXPORT_DIR.exists():
        for d in sorted(paths.EXPORT_DIR.iterdir(), reverse=True):
            if not d.is_dir() or d.name.startswith("_"):
                continue
            meta = mod.job_meta(d.name) if mod is not None else None
            if not meta or int(meta.get("account_qq") or 0) != account:
                continue   # 无元数据（旧任务）或属于其它账号：一律不展示
            files = [{"name": f.name, "size": f.stat().st_size} for f in sorted(d.iterdir()) if f.is_file()]
            z = paths.EXPORT_DIR / f"{d.name}.zip"
            jobs.append({"job": d.name, "created": int(d.stat().st_mtime),
                         "files": files, "count": len(files),
                         "zip": z.name if z.exists() else None})
    return {"jobs": jobs[:100]}


@app.get("/api/export/download")
def export_download(job: str, name: str | None = None, account: int | None = None):
    if not account:
        return fail("缺少 account（多账号隔离：导出下载必须指定账号）")
    if "/" in job or "\\" in job or ".." in job:
        return fail("非法 job")
    _mod, _why = load_exporter()
    _meta = _mod.job_meta(job) if _mod is not None else None
    if not _meta or int(_meta.get("account_qq") or 0) != int(account):
        return fail("任务不存在或不属于该账号", 404)
    if name:
        if "/" in name or "\\" in name or ".." in name:
            return fail("非法文件名")
        f = paths.EXPORT_DIR / job / name
    else:
        f = paths.EXPORT_DIR / f"{job}.zip"
    if not f.exists() or not f.is_file():
        return fail("文件不存在", 404)
    return FileResponse(str(f), filename=f.name)


# ── 设置 / AI ───────────────────────────────────────────────────────────────
@app.get("/api/settings")
def get_settings():
    s = load_settings()
    s["ai"]["key"] = "******" if s["ai"].get("key") else ""
    ob = s.setdefault("onebot", {})
    ob["has_token"] = bool(ob.get("token"))
    ob["token"] = "******" if ob.get("token") else ""
    qz = s.setdefault("qzone", {})
    qz["cookie"] = "******" if qz.get("cookie") else ""
    qz["has_cookie"] = bool(qz.get("cookie"))
    return s


@app.post("/api/settings")
async def post_settings(request: Request):
    b = await request.json()
    s = load_settings()
    for k in ("data_root", "port"):
        if k in b:
            s[k] = int(b[k]) if k == "port" else b[k]
    for k in ("ai", "onebot", "ui", "qzone", "send"):
        if isinstance(b.get(k), dict):
            for kk, vv in b[k].items():
                if kk in ("key", "cookie") and vv == "******":
                    continue          # 掩码值不回写，避免把真值冲掉
                s[k][kk] = vv
    save_settings(s)
    return {"ok": True}


@app.post("/api/ai/chat")
async def ai_chat(request: Request):
    b = await request.json()
    s = load_settings()
    ai = s.get("ai", {})
    if not ai.get("key"):
        return fail("服务端还没配置 AI Key，请到设置页填写", 400)
    import httpx
    payload = {"model": b.get("model") or ai.get("model", "deepseek-chat"),
               "messages": b.get("messages") or [],
               "temperature": 0.7, "max_tokens": 1500}
    url = ai.get("base", "").rstrip("/") + "/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=90) as cli:
            r = await cli.post(url, json=payload,
                               headers={"Authorization": f"Bearer {ai['key']}"})
            r.raise_for_status()
            j = r.json()
        return {"content": j["choices"][0]["message"]["content"]}
    except Exception as exc:  # noqa: BLE001
        return fail(f"AI 请求失败：{exc}", 502)


# ── 增量路由（由队友并行开发，模块不存在时自动跳过）─────────────────────────
def _mount_optional_routers() -> None:
    """自动发现并挂载 server/routes_*.py 里的 APIRouter（按文件名排序，幂等）。

    这样新增路由模块不需要再改本文件。
    """
    here = Path(__file__).resolve().parent
    for mod_name in sorted(p.stem for p in here.glob("routes_*.py")):
        try:
            mod = importlib.import_module(mod_name)
        except Exception as exc:  # noqa: BLE001
            print(f"[路由] {mod_name} 导入失败：{exc.__class__.__name__}: {exc}")
            continue
        router = getattr(mod, "router", None)
        if router is None:
            print(f"[路由] {mod_name} 模块缺少 router，跳过")
            continue
        app.include_router(router)
        print(f"[路由] {mod_name} 已挂载")


def _mount_assets() -> None:
    """把 web/assets 挂到 /assets。

    v10 的 3D 环绕背景素材（three.min.js / GLTFLoader.js / kei.vrm，约 44MB）走这里；
    素材缺失时**不挂载**，前端会自动降级成 CSS .bg-glow，不影响其它功能。
    """
    assets = paths.WEB_DIR / "assets"
    try:
        if not assets.is_dir():
            print("[静态] web/assets 不存在，跳过 /assets（3D 背景降级为 CSS）")
            return
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")
        print(f"[静态] /assets -> {assets}")
    except Exception as exc:  # noqa: BLE001
        print(f"[静态] /assets 挂载失败：{exc.__class__.__name__}: {exc}")


_mount_optional_routers()
_mount_assets()


def main() -> None:
    store.init()
    s = load_settings()
    port = int(s.get("port", 15555))
    print(f"QQScope {VERSION}  backend  ->  http://127.0.0.1:{port}")
    print(f"  数据根目录: {s.get('data_root')}")
    print(f"  统一主库  : {paths.STORE_DB}")
    print(f"  前端产物  : {DIST_HTML} ({'存在' if DIST_HTML.exists() else '未构建'})")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
