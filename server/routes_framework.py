# -*- coding: utf-8 -*-
"""QQScope · 框架（NapCat）终端 + 登录门路由

  GET  /api/framework/logs     框架终端日志（增量 offset / 关键字 q / 脱敏）
  GET  /api/framework/status   框架进程 / 端口 / 登录态
  GET  /api/framework/qrcode   二维码 PNG（404=暂无；带 X-QR-Mtime；no-store）
  POST /api/framework/start    启动框架（幂等 + 5s 节流 + 记录 PID 到 spawned.json）
  POST /api/framework/stop     停止**仅由本服务启动的**框架（校验 PID 身份，绝不误杀）
  POST /api/framework/logout   退出 QQ 登录（NapCat 无登出动作 -> 结束框架进程；分级 + 身份校验）
  GET  /api/login/status       登录门状态机（sync_allowed / can_stop）

红线：
* 本模块**永远不会** kill 不是自己启动的框架进程；跨后端重启用
  data/framework/spawned.json 认领。停止前必须校验 PID 现在还在跑、
  是 node.exe、且命令行含 napcat，三者缺一不杀。
* 未登录时不发任何 OneBot 请求 —— 门控在 core/live_sync.py，本模块只读状态。
* 所有对外文本都过 core.framework_log.scrub()（token / qrcode 的 k= 参数）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from core import framework_log

router = APIRouter(tags=["framework"])

_START_LOCK = threading.Lock()
_START_THROTTLE = 5.0                 # /start 最短间隔（秒）
_START_GRACE = 120.0                  # 启动中的宽限期：这段时间内不重复拉起
_MEM: dict = {"pid": 0, "at": 0.0, "last_try": 0.0}


# ── 小工具 ──────────────────────────────────────────────────────────────────
def _log(level: str, msg: str) -> None:
    """写进后端 /api/logs（source='framework'）；拿不到就退回 stdout。"""
    msg = framework_log.scrub(msg)
    for name in ("__main__", "server.app", "app"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "log"):
            try:
                mod.log(level, "framework", msg)
                return
            except Exception:  # noqa: BLE001
                pass
    print(f"[{level}] [framework] {msg}", flush=True)


def _to_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return int(default)


def _node_path():
    return framework_log.NAPCAT_DIR / "node.exe"


def _entry_path():
    return framework_log.NAPCAT_DIR / "index.js"


def _spawned_read() -> dict:
    try:
        j = json.loads(framework_log.SPAWNED_FILE.read_text(encoding="utf-8"))
        return j if isinstance(j, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _spawned_write(rec: dict | None) -> None:
    """内存 + 文件双写；rec=None 表示清除记录。"""
    try:
        if rec is None:
            _MEM["pid"] = 0
            try:
                if framework_log.SPAWNED_FILE.exists():
                    framework_log.SPAWNED_FILE.unlink()
            except OSError:
                pass
            return
        _MEM["pid"] = _to_int(rec.get("pid"))
        framework_log.SPAWNED_FILE.parent.mkdir(parents=True, exist_ok=True)
        framework_log.SPAWNED_FILE.write_text(
            json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        _log("warn", f"写 spawned.json 失败（只记内存）：{exc}")


def _spawned_pid() -> int:
    """本服务启动的框架 PID：优先文件（跨重启），退回内存。"""
    pid = _to_int(_spawned_read().get("pid"))
    return pid or _to_int(_MEM.get("pid"))


def _can_stop(strict: bool = False) -> bool:
    """能代停吗？

    strict=False（/api/login/status 首屏用）：只看「有没有我们自己启动的记录」，
    不查进程（WMI 要几百毫秒，会拖慢首屏）。
    strict=True （/api/framework/stop 真正要杀之前）：再校验 PID 现在确实还是
    框架进程（node.exe + 命令行含 napcat），不合格一律不杀。
    """
    pid = _spawned_pid()
    if not pid:
        return False
    return framework_log.is_framework_proc(pid) if strict else True


def _who(request: Request | None, extra: str = "") -> str:
    try:
        ip = request.client.host if (request and request.client) else "?"
    except Exception:  # noqa: BLE001
        ip = "?"
    return f"{extra or 'web'}@{ip}"


async def _body(request: Request) -> dict:
    try:
        b = await request.json()
        return b if isinstance(b, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _wait_ports_free(pairs, timeout: float = 12.0) -> dict:
    """等端口释放，返回最终端口状态。"""
    end = time.time() + timeout
    while time.time() < end:
        if not any(framework_log._port_open(h, p, timeout=0.25) for h, p in pairs):
            break
        time.sleep(0.3)
    return {str(p): framework_log._port_open(h, p, timeout=0.25) for h, p in pairs}


def _kill_framework_proc(pid: int) -> tuple[bool, str]:
    """结束一个**已通过 is_framework_proc 身份校验**的框架 PID。

    Windows 用 taskkill /T /F（连子进程一起），其它平台 SIGTERM。只发信号；
    身份校验与 spawned 记录清理由调用方负责。framework_stop / framework_logout
    共用这一条 taskkill 路径，避免出现第二套杀进程逻辑。
    """
    if os.name == "nt":
        r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=20)
        out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace").strip()
        return True, out
    os.kill(pid, 15)
    return True, "SIGTERM"


def _fw_pid_verified(force: bool = False) -> int:
    """当前框架 PID（只认「node.exe 且命令行含 napcat」的进程）。

    优先本服务记录；否则在 napcat_procs()（已按 node.exe + napcat 过滤）里挑
    「OneBot/WebUI 端口真正在听」的那个 PID（实测外部框架 = napcat.mjs 子进程），
    都没有再退回列表第一个。找不到返回 0。
    """
    cand = _spawned_pid()
    if cand and framework_log.is_framework_proc(cand, force=force):
        return cand
    cfg = framework_log.load_framework_cfg()
    pids = [_to_int(p.get("pid")) for p in framework_log.napcat_procs(force=force)]
    listen = framework_log._netstat_listen(force=force)
    for key, dflt in (("webui", 6099), ("base", 3000)):
        owner = _to_int(listen.get(framework_log._host_port(cfg.get(key), dflt)[1]))
        if owner and owner in pids:
            return owner
    return pids[0] if pids else 0


def _logout_kill(pid: int, ours_ports: list, cfg: dict, mode: str,
                 who: str, ts: str, ok_msg: str) -> dict:
    """kill 一个已通过身份校验的框架 PID，等端口释放；返回 logout 响应。

    **任何 kill 之前都再过一次 is_framework_proc**（node.exe + 命令行含 napcat）。
    """
    if not framework_log.is_framework_proc(pid, force=True):
        msg = f"拒绝退出登录：PID {pid} 不是 node.exe/napcat 框架进程"
        _log("error", f"{msg}，操作者 {who}，时间 {ts}")
        return {"ok": False, "mode": "external", "pid": pid, "can_force": False,
                "message": msg, "ports": {}}
    _log("warn", f"退出登录：准备结束框架 PID {pid}（mode={mode}，危险操作），"
                 f"操作者 {who}，时间 {ts}（该进程持有端口 {ours_ports or '无'}）")
    try:
        _, tk = _kill_framework_proc(pid)
    except Exception as exc:  # noqa: BLE001
        msg = f"退出登录失败：{exc}"
        _log("error", f"退出登录结束框架 PID {pid} 失败：{exc}，操作者 {who}，时间 {ts}")
        return {"ok": False, "mode": mode, "pid": pid, "can_force": True, "message": msg}
    time.sleep(0.6)
    gone = not framework_log.is_framework_proc(pid, force=True)
    if gone and pid == _spawned_pid():
        _spawned_write(None)                  # 停干净了才抹掉记录
    host = framework_log._host_port(cfg.get("webui"), 6099)[0]
    port_state = _wait_ports_free([(host, p) for p in ours_ports], timeout=12.0) if ours_ports else {}
    released = all(not bool(v) for v in port_state.values())
    ok = bool(gone and released)
    msg = ok_msg if ok else f"已发送停止信号（PID {pid}），但仍有残留：进程还在={not gone}，端口={port_state}"
    _log("warn" if ok else "error",
         f"退出登录结束框架 PID {pid}（mode={mode}）：{'成功' if ok else '未完全退出'}，"
         f"操作者 {who}，时间 {ts}，端口={port_state}")
    return {"ok": ok, "mode": mode, "pid": pid, "can_force": True, "message": msg,
            "ports": port_state, "process_gone": gone, "taskkill": tk[:200]}


# ── 日志 / 状态 ─────────────────────────────────────────────────────────────
@router.get("/api/framework/logs")
def framework_logs(
    limit: int = Query(300, ge=1, le=2000, description="最多返回多少行"),
    offset: int = Query(0, ge=0, description="run.log 字节游标；0=初始加载只读尾部"),
    offset_err: int | None = Query(None, ge=0, description="run.err 字节游标（可选）"),
    q: str | None = Query(None, description="关键字过滤，大小写不敏感"),
    grep: str | None = Query(None, description="q 的别名"),
):
    return framework_log.read_log(limit=limit, since_offset=offset,
                                  since_offset_err=offset_err, grep=q or grep)


@router.get("/api/framework/status")
def framework_status():
    return framework_log.framework_status()


# ── 二维码 ──────────────────────────────────────────────────────────────────
@router.get("/api/framework/qrcode")
def framework_qrcode():
    """返回 NapCat 未登录时写出的二维码 PNG。文件不存在 -> 404 + JSON。"""
    path = framework_log.qrcode_path()
    info = framework_log.qrcode_info()
    if not info.get("available") or not path.is_file():
        return JSONResponse(
            status_code=404,
            content={"error": "暂无二维码，请点「启动框架」",
                     "message": "暂无二维码：框架没在等待扫码（可能已登录，或还没启动）",
                     "qr": {"available": False, "mtime": 0,
                            "url": "/api/framework/qrcode"}})
    return FileResponse(
        str(path), media_type="image/png",
        headers={"X-QR-Mtime": str(info["mtime"]),
                 "X-QR-Size": str(info["size"]),
                 "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                 "Pragma": "no-cache", "Expires": "0"})


# ── 启动 / 停止 ─────────────────────────────────────────────────────────────
@router.post("/api/framework/start")
async def framework_start(request: Request):
    """启动框架。幂等：已在跑直接返回 already_running，绝不产生第二个进程。"""
    who = _who(request, (await _body(request)).get("by") or "web")
    gate = framework_log.login_gate(deep=True)
    if gate.get("framework_running"):
        ports_up = any(bool(v) for v in (gate.get("ports") or {}).values())
        if ports_up or gate.get("logged_in"):
            return {"ok": True, "already_running": True, "pid": int(gate.get("pid") or 0),
                    "message": "框架已在运行，无需重复启动", "status": gate}
        return {"ok": True, "already_running": False, "starting": True,
                "pid": int(gate.get("pid") or 0),
                "message": "框架进程已在（端口还没起），正在启动中，请稍候…",
                "status": gate}

    with _START_LOCK:
        now = time.time()
        pid0 = _spawned_pid()
        if pid0 and framework_log.is_framework_proc(pid0) and now - float(_MEM.get("at") or 0) < _START_GRACE:
            return {"ok": True, "already_running": False, "starting": True, "pid": pid0,
                    "message": "框架正在启动中（还是同一个进程），请稍候…"}
        if pid0 and not framework_log.is_framework_proc(pid0):
            _spawned_write(None)                 # 记录已失效，清掉
        last = float(_MEM.get("last_try") or 0)
        if now - last < _START_THROTTLE:
            wait = max(1, int(_START_THROTTLE - (now - last)) + 1)
            return JSONResponse(status_code=429, content={
                "ok": False, "throttled": True, "retry_after": wait,
                "message": f"操作太频繁，请 {wait} 秒后再点"})
        _MEM["last_try"] = now

        node, entry = _node_path(), _entry_path()
        if not node.is_file():
            msg = f"找不到框架主程序：{node}（请确认 tools/napcat 完整解压）"
            _log("error", msg)
            return JSONResponse(status_code=500, content={"ok": False, "message": msg, "error": msg})
        if not entry.is_file():
            msg = f"找不到框架入口：{entry}（请确认 tools/napcat 完整解压）"
            _log("error", msg)
            return JSONResponse(status_code=500, content={"ok": False, "message": msg, "error": msg})

        try:
            framework_log.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            fo = open(framework_log.LOG_FILE, "wb")      # 与 启动框架.bat 一致：新会话重写日志
            fe = open(framework_log.ERR_FILE, "wb")
        except OSError as exc:
            msg = f"无法写入框架日志文件：{exc}"
            _log("error", msg)
            return JSONResponse(status_code=500, content={"ok": False, "message": msg, "error": msg})

        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            proc = subprocess.Popen([str(node), str(entry)], cwd=str(framework_log.NAPCAT_DIR),
                                    stdin=subprocess.DEVNULL, stdout=fo, stderr=fe,
                                    creationflags=flags)
        except OSError as exc:
            msg = f"启动框架失败：{exc}（检查 node.exe 是否被杀软拦截）"
            _log("error", msg)
            return JSONResponse(status_code=500, content={"ok": False, "message": msg, "error": msg})
        finally:
            for fh in (fo, fe):
                try:
                    fh.close()
                except Exception:  # noqa: BLE001
                    pass

        rec = {"pid": int(proc.pid), "started_at": int(time.time()), "by": who,
               "node": str(node), "entry": str(entry), "dir": str(framework_log.NAPCAT_DIR)}
        _spawned_write(rec)
        _MEM["at"] = time.time()
        _log("warn", f"启动框架：PID {proc.pid}，操作者 {who}")
        return {"ok": True, "already_running": False, "pid": int(proc.pid),
                "message": f"框架已启动（PID {proc.pid}），请稍候查看二维码"}


@router.post("/api/framework/stop")
async def framework_stop(request: Request):
    """只停「本服务通过 /start 启动的」那个 PID；别人的框架一律不碰。"""
    body = await _body(request)
    who = _who(request, body.get("by") or "web")
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    pid = _spawned_pid()

    if not pid or not framework_log.is_framework_proc(pid):
        err = "这个框架不是 QQScope 启动的，为安全起见不代你停止。请手动关闭它的窗口。"
        _log("warn", f"拒绝停止框架（非本服务启动）：记录 PID={pid or '无'}，操作者 {who}，时间 {ts}")
        return JSONResponse(status_code=409, content={
            "ok": False, "error": err, "message": err, "stopped_pid": 0,
            "can_stop": False, "pid": 0})

    listen_before = framework_log._netstat_listen(force=True)
    ours_ports = sorted(int(p) for p, owner in listen_before.items()
                        if int(owner) == pid)
    _log("warn", f"准备停止框架：PID {pid}，操作者 {who}，时间 {ts}"
                 f"（危险操作；该进程持有端口 {ours_ports or '无'}）")
    killed = False
    out = ""
    try:
        killed, out = _kill_framework_proc(pid)
    except Exception as exc:  # noqa: BLE001
        msg = f"停止失败：{exc}"
        _log("error", f"停止框架 PID {pid} 失败：{exc}")
        return JSONResponse(status_code=500, content={"ok": False, "error": msg, "message": msg,
                                                     "stopped_pid": 0, "can_stop": _can_stop()})

    time.sleep(0.6)
    gone = not framework_log.is_framework_proc(pid, force=True)
    if gone:
        _spawned_write(None)                      # 停干净了才抹掉记录
    # 只校验「这个 PID 本来持有的端口」有没有释放（避免把别人的端口算进来）
    cfg = framework_log.load_framework_cfg()
    host = framework_log._host_port(cfg.get("webui"), 6099)[0]
    port_state = _wait_ports_free([(host, p) for p in ours_ports], timeout=12.0) if ours_ports else {}
    released = all(not bool(v) for v in port_state.values())
    ok = bool(gone and released)
    msg = (f"已停止 QQScope 启动的框架（PID {pid}）" if ok
           else f"已发送停止信号（PID {pid}），但仍有残留：进程还在={not gone}，端口={port_state}")
    _log("warn" if ok else "error",
         f"停止框架 PID {pid}：{'成功' if ok else '未完全停止'}，操作者 {who}，时间 {ts}，端口={port_state}")
    return {"ok": ok, "stopped_pid": pid, "pid": pid, "message": msg,
            "ports": port_state, "process_gone": gone,
            "taskkill": out[:200] if killed else "", "can_stop": _can_stop()}


@router.post("/api/framework/logout")
async def framework_logout(request: Request):
    """退出 QQ 登录（前端已二次确认；默认「一次到位」）。

    NapCat 没有「登出 QQ」的 OneBot 动作（只有 bot_exit / set_restart，见
    tools/napcat），所以「退出 QQ 登录」= 结束框架进程。分级：
      Tier 1 优雅     ：force=False 时先发扩展动作 bot_exit，最多等 ~2.4 秒
      Tier 2/3 结束   ：2 秒内没退出（或 force=True 直接）→ taskkill 结束进程
                        （自有框架 mode="stop"，外部框架 mode="force_stop"）
      真失败          ：找不到 / 身份校验不过 / kill 异常 → ok=False + mode="external"
                        + can_force=False + reason/manual，绝不假装成功
    任何 kill 之前都必须过 is_framework_proc（node.exe + 命令行含 napcat）。
    """
    body = await _body(request)
    who = _who(request, body.get("by") or "web")
    force = bool(body.get("force"))
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    cfg = framework_log.load_framework_cfg()
    pid = _fw_pid_verified(force=force) or _fw_pid_verified(force=True)

    ob_host, ob_port = framework_log._host_port(cfg.get("base"), 3000)
    wu_host, wu_port = framework_log._host_port(cfg.get("webui"), 6099)
    pairs = [(ob_host, ob_port), (wu_host, wu_port)]
    port_state = {str(p): framework_log._port_open(h, p) for h, p in pairs}
    verified = bool(pid and framework_log.is_framework_proc(pid, force=True))

    # ── Tier 1：优雅 bot_exit（仅默认退出 + PID 身份校验通过；2 秒不退就升级强杀）──
    if (not force) and verified and port_state.get(str(ob_port)):
        try:
            framework_log._onebot(cfg, "/bot_exit", timeout=3)
        except Exception as exc:  # noqa: BLE001
            _log("info", f"退出登录：bot_exit 未生效（{exc}），按用户确认自动升级为强制结束")
        else:
            freed = _wait_ports_free(pairs, timeout=2.4)
            alive = framework_log.is_framework_proc(pid, force=True)
            if not alive:
                if pid == _spawned_pid():
                    _spawned_write(None)
                _log("warn", f"退出登录：bot_exit 成功，框架已结束（PID {pid}），"
                             f"操作者 {who}，时间 {ts}")
                return {"ok": True, "mode": "bot_exit", "pid": pid, "can_force": False,
                        "message": "已退出 QQ 登录（框架已结束）", "ports": freed}
            _log("warn", f"退出登录：bot_exit 2 秒内未退出（端口={freed}），"
                         f"按用户确认自动升级为强制结束")

    # ── Tier 2/3：结束进程（kill 前必过身份校验）──────────────────────────
    target = pid if verified else 0
    if not target:
        own = _spawned_pid()
        if own and framework_log.is_framework_proc(own, force=True):
            target = own
    if target:
        listen = framework_log._netstat_listen(force=True)
        ours = sorted(int(p) for p, owner in listen.items() if int(owner) == target)
        own_stop = bool(target == _spawned_pid() and _can_stop(strict=True))
        mode = "stop" if own_stop else "force_stop"
        return _logout_kill(target, ours, cfg, mode, who, ts, "已退出 QQ 登录（框架已结束）")

    # ── 真失败：身份校验不过 / 找不到框架进程，绝不假装成功 ─────────────────
    if pid:
        reason = f"PID {pid} 身份校验未通过（不是 node.exe + 命令行含 napcat 的框架进程），为安全拒绝结束它"
    else:
        reason = "找不到 NapCat 框架进程（node.exe + 命令行含 napcat）"
    manual = ("手动处理：到项目目录双击「关闭机器人框架.bat」，或在任务管理器结束 NapCat/node 进程；"
              "随后重新双击 ①启动机器人框架.bat 并用手机 QQ 扫码。")
    _log("error", f"退出登录失败：{reason}；操作者 {who}，时间 {ts}")
    return {"ok": False, "mode": "external", "pid": pid, "can_force": False,
            "reason": reason, "manual": manual,
            "message": f"退出登录失败：{reason}。{manual}", "ports": port_state}


# ── 登录门 ──────────────────────────────────────────────────────────────────
@router.get("/api/login/status")
def login_status(account: int | None = None):
    """登录门（前端首屏唯一判据）：logged_in=true 直接进主界面，false 才显示二维码。

    task-10 身份绑定：可传 ?account=<被查看的账号>。传入且 != 框架登录号时，sync_allowed=false，
    并返回 account_in_store / view_account_mismatch / sync_block_reason，避免 UI 把登录号与查看号脱钩。

    快路径：端口探测 + get_login_info 实查（0.4s 超时 / 2s 缓存）——不查 WMI、
    不 spawn 子进程，热路径 <5ms，超时立刻用端口兜底，绝不卡首屏。
    """
    gate = framework_log.login_gate(probe_onebot=True)
    gate["can_stop"] = _can_stop()
    gate["spawned_pid"] = _spawned_pid()
    pid = int(gate.get("pid") or 0) or _spawned_pid() or 0
    if not pid and gate.get("framework_running"):
        # 外部启动的框架：deep=False 的首屏路径故意不查 WMI，这里补一次实扫
        # （napcat_procs / netstat 都有秒级缓存，不会每次轮询都真扫），
        # 保证前端首屏就能拿到 PID 与 can_force，而不是等别的接口把缓存捂热。
        pid = _fw_pid_verified()
    gate["pid"] = pid                                   # 当前框架 PID（0=没找到）
    gate["can_force"] = bool(pid and framework_log.is_framework_proc(pid))  # 身份校验通过才可强制停
    gate["qrcode_url"] = "/api/framework/qrcode"

    # ── task-10 身份绑定：框架登录号 vs 被查看账号 ───────────────────────────
    login_qq = int(gate.get("account") or 0)
    view_qq = int(account) if account else 0
    in_store = False
    if login_qq:
        try:
            from core import store
            con = store.connect()
            try:
                in_store = bool(con.execute(
                    "SELECT 1 FROM accounts WHERE account_qq=?", (login_qq,)).fetchone())
            finally:
                con.close()
        except Exception:  # noqa: BLE001
            in_store = False
    gate["account"] = login_qq
    gate["account_in_store"] = bool(in_store)
    gate["view_account"] = view_qq or None
    gate["view_account_mismatch"] = bool(view_qq and login_qq and view_qq != login_qq)
    if gate["view_account_mismatch"]:
        gate["sync_allowed"] = False
        gate["sync_block_reason"] = "account_mismatch"
        gate["message"] = (
            f"当前框架登录 {login_qq}，与正在查看的账号 {view_qq} 不一致；"
            f"为避免串号，已停用实时同步 / 发送 / 动态同步。")
    else:
        gate["sync_block_reason"] = ""
    return gate
