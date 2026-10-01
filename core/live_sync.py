# -*- coding: utf-8 -*-
"""QQScope · 实时采集器（轮询版）

问题：NapCat 实时收到新消息，但从没有通道进我们的 store → 网页（读 store）看不到
「别人发来的新消息」。手动 /api/sources/bot/sync 不会自动跑。

本模块起一个后台线程，持续轮询 NapCat 的历史接口，把新消息增量写进统一主库：
    focus 会话（前端正在看的）   每 5 秒
    最近活跃 top20 会话          每 30 秒（错开）
    其余会话                    每 10 分钟
解析/方向/media 全部复用 core.sources.bot_source.ob_msg_to_row（内含 parse_onebot_message），
写库用 store.insert_messages（ux_msg 唯一索引自动去重）。

安全/负载：
* 请求之间 sleep >= 0.16s；单轮 <= 60 个会话请求；连续失败指数退避。
* 只读 OneBot 历史接口，不发送任何消息。
* 绝不重启 NapCat；stop() 通过 Event 干净退出线程。
"""
from __future__ import annotations

import json
import os
import random
import threading
import time

from core import framework_log, paths, store
from core.sources import bot_source

# ── 轮询参数 ────────────────────────────────────────────────────────────────
FOCUS_INTERVAL = 5.0        # focus 会话轮询间隔（秒）
TOP_INTERVAL = 30.0         # 最近活跃 top20 会话
OTHER_INTERVAL = 600.0      # 其余会话（10 分钟）
TOP_N = 20
COUNT = 40                  # 每次 get_*_msg_history 的 count
REQUEST_GAP = 0.16          # OneBot 请求之间的最小间隔（>=0.15s）
MAX_REQUESTS_PER_ROUND = 60  # 单轮最多轮询多少个会话
ROUND_TIME_BUDGET = 4.0     # 单轮最多花多少秒，到点就跳出，保证 focus 及时
TICK = 0.5                  # 主循环节拍
LOGIN_REFRESH = 120.0       # 重新 get_login_info 的间隔
LIST_REFRESH = 60.0         # 重新读 store contacts 的间隔
CONTACT_REFRESH = 300.0     # 重新拉好友/群列表（发现新会话）的间隔
BACKOFF_CAP = 300.0         # 单会话退避上限
GLOBAL_BACKOFF_CAP = 60.0   # 全局异常退避上限
WAIT_LOGIN_INTERVAL = 10.0  # 未登录时的重查间隔（秒）：期间不发任何请求、不写库


_LIVE_SETTINGS: dict = {"at": 0.0, "data": {}}


def _live_settings() -> dict:
    """读 settings.json 的 live 段（5 秒缓存）。"""
    now = time.time()
    if _LIVE_SETTINGS["data"] and now - _LIVE_SETTINGS["at"] < 5.0:
        return _LIVE_SETTINGS["data"]
    data: dict = {}
    try:
        p = paths.ROOT / "data" / "server" / "settings.json"
        s = json.loads(p.read_text(encoding="utf-8"))
        live = s.get("live")
        data = live if isinstance(live, dict) else {}
    except Exception:  # noqa: BLE001
        data = {}
    _LIVE_SETTINGS["at"] = now
    _LIVE_SETTINGS["data"] = data
    return data


def require_login() -> bool:
    """「登录之后才采集」开关，默认 **true**（设置里没写也算 true）。"""
    v = _live_settings().get("require_login")
    return True if v is None else bool(v)


def _to_int(v, default: int = 0) -> int:
    return bot_source._to_int(v, default)


def _endpoint() -> tuple[str, str]:
    """OneBot base/token：优先 data/framework/onebot.json，其次设置。"""
    for p, wrapped in ((paths.ROOT / "data" / "framework" / "onebot.json", False),
                       (paths.ROOT / "data" / "server" / "settings.json", True)):
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        ob = j.get("onebot") if wrapped else j
        if isinstance(ob, dict) and str(ob.get("base") or "").strip():
            return str(ob["base"]).strip(), str(ob.get("token") or "").strip()
    return "", ""


class _Peer:
    """一个会话的轮询状态。"""

    __slots__ = ("key", "kind", "peer_id", "peer_qq", "name", "last_ts",
                 "last_poll", "inserted", "errors", "next_due", "top")

    def __init__(self, key: str, kind: str, peer_id: str, peer_qq: int = 0, name: str = ""):
        self.key = key
        self.kind = kind
        self.peer_id = str(peer_id)
        self.peer_qq = int(peer_qq or 0)
        self.name = name or ""
        self.last_ts = 0
        self.last_poll = 0.0
        self.inserted = 0
        self.errors = 0
        self.next_due = 0.0
        self.top = False


class LiveSync:
    """后台轮询采集器（进程内单例由 get_live() 提供）。"""

    def __init__(self, base: str = "", token: str = ""):
        self.base = base or ""
        self.token = token or ""
        self.uin = 0
        self.nick = ""

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._peers: dict[str, _Peer] = {}
        self._focus: str | None = None

        self._poll_count = 0
        self._requests_total = 0
        self._inserted_total = 0
        self._rounds = 0
        self._last_poll_ts = 0
        self._last_error: str | None = None
        self._started_at = 0
        self._login_at = 0.0
        self._peers_refreshed = 0.0
        self._contacts_fetched = 0.0
        self._last_round: dict = {}
        self._global_backoff = 0.0
        # 登录门（task-34）：未登录时线程只等待，不发请求、不写库
        self._gate: dict = {}
        self._waiting_login = False
        self._waiting_since = 0.0

    def _ensure_account_row(self) -> None:
        """把「框架已登录但尚未导入」的账号登记进 accounts。

        否则实时采集到的消息是孤儿：消息按 self.uin 入库了，但 /api/accounts
        列不出这个号 → 界面选择器里没有它 → 用户永远看不到自动采到的内容。
        只对不存在的账号建行，绝不改已有账号的 label/source。
        """
        if not self.uin:
            return
        try:
            known = {int(a["account_qq"]) for a in store.list_accounts()}
            if int(self.uin) not in known:
                store.upsert_account(int(self.uin), label=self.nick or None, source="live")
        except Exception:  # noqa: BLE001
            pass

    # ── 生命周期 ────────────────────────────────────────────────────────────
    def start(self) -> dict:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return {"ok": True, "running": True, "message": "实时采集已在运行"}
            if not self.base:
                b, t = _endpoint()
                self.base = b or self.base
                self.token = t or self.token
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, name="qqscope-live", daemon=True)
            self._started_at = int(time.time())
            self._thread.start()
            gate = {}
            if require_login():
                try:
                    gate = framework_log.login_gate()
                except Exception:  # noqa: BLE001
                    gate = {}
            hint = ""
            if require_login() and not gate.get("sync_allowed", False):
                hint = "；未登录，将等待扫码登录后自动开始"
            return {"ok": True, "running": True,
                    "waiting_login": bool(require_login() and not gate.get("sync_allowed", False)),
                    "message": f"实时采集已启动（focus {FOCUS_INTERVAL:.0f}s / top{TOP_N} "
                               f"{TOP_INTERVAL:.0f}s / 其余 {OTHER_INTERVAL/60:.0f}min）{hint}"}

    def stop(self) -> dict:
        with self._lock:
            th = self._thread
            if not th or not th.is_alive():
                self._thread = None
                return {"ok": True, "running": False, "message": "实时采集未在运行"}
            self._stop_event.set()
        th.join(timeout=10.0)
        alive = th.is_alive()
        with self._lock:
            if not alive:
                self._thread = None
        return {"ok": True, "running": alive,
                "message": "已停止" if not alive else "停止超时（线程仍在收尾）"}

    def is_running(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive())

    # ── 线程主体 ────────────────────────────────────────────────────────────
    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
                self._global_backoff = 0.0
            except Exception as exc:  # noqa: BLE001
                self._last_error = f"{exc.__class__.__name__}: {exc}"
                self._global_backoff = min(max(self._global_backoff * 2, 2.0),
                                           GLOBAL_BACKOFF_CAP)
                self._stop_event.wait(self._global_backoff)
                continue
            if not self._stop_event.is_set():
                self._stop_event.wait(TICK)
        # 线程结束
        with self._lock:
            self._thread = None
        return

    def _tick(self) -> dict:
        now = time.monotonic()

        # ── 登录门：没登录 / 框架没跑时，绝不发 OneBot 请求、绝不写库 ────────
        if require_login():
            try:
                gate = framework_log.login_gate()
            except Exception as exc:  # noqa: BLE001
                gate = {"sync_allowed": False, "waiting_login": True,
                        "message": f"登录门检测失败：{exc}"}
            self._gate = gate
            if not gate.get("sync_allowed"):
                if not self._waiting_login:
                    self._waiting_login = True
                    self._waiting_since = time.time()
                self._last_error = str(gate.get("message") or "等待扫码登录")
                self._stop_event.wait(WAIT_LOGIN_INTERVAL)
                return {"at": int(time.time()), "requests": 0, "inserted": 0, "queued": 0,
                        "seconds": 0.0, "budget_hit": False, "waiting_login": True}
            if self._waiting_login:
                self._waiting_login = False
                self._last_error = None
        else:
            self._gate = {"sync_allowed": True, "require_login": False,
                          "message": "未开启登录门（live.require_login=false）"}
            self._waiting_login = False

        if not self.base:
            b, t = _endpoint()
            self.base, self.token = b or self.base, t or self.token
            if not self.base:
                self._last_error = "未配置 OneBot 地址（data/framework/onebot.json）"
                return {"requests": 0, "inserted": 0}

        # 登录身份（顺带判断框架在线 / 已登录）
        if not self.uin or now - self._login_at > LOGIN_REFRESH:
            uin, nick = bot_source._login(self.base, self.token)
            self._requests_total += 1
            self.uin, self.nick = int(uin), str(nick)
            self._login_at = now
            self._last_poll_ts = int(time.time())
            self._ensure_account_row()
            self._gap()

        # 偶尔重新拉好友/群列表，发现新会话
        if now - self._contacts_fetched > CONTACT_REFRESH:
            self._refresh_contacts_from_onebot()
            self._contacts_fetched = now

        # 重新读 store 的会话列表 / top20 标记
        if now - self._peers_refreshed > LIST_REFRESH:
            self._refresh_peers()
            self._peers_refreshed = now

        queue = self._due_queue()
        round_inserted = 0
        round_start = time.monotonic()
        polled = 0
        for st in queue:
            if self._stop_event.is_set():
                break
            if polled > 0 and time.monotonic() - round_start >= ROUND_TIME_BUDGET:
                break   # 本轮预算用完：先回去跑 focus，剩下的下一轮再轮
            if st.peer_qq <= 0:
                st.errors += 1
                st.next_due = time.monotonic() + OTHER_INTERVAL
                self._last_error = f"{st.kind}:{st.peer_id} 缺少 QQ 号，无法轮询"
                continue
            self._gap()
            try:
                inserted = self._poll_peer(st)
            except bot_source.BotError as exc:
                st.errors += 1
                st.last_poll = time.time()
                self._last_error = f"{st.kind}:{st.peer_id}: {exc}"
                back = min(self._interval_for(st) * (2 ** min(st.errors, 5)), BACKOFF_CAP)
                st.next_due = time.monotonic() + back + random.uniform(0, 1.0)
                self._poll_count += 1
                continue
            except Exception as exc:  # noqa: BLE001
                st.errors += 1
                st.last_poll = time.time()
                self._last_error = f"{st.kind}:{st.peer_id}: {exc.__class__.__name__}: {exc}"
                back = min(self._interval_for(st) * (2 ** min(st.errors, 5)), BACKOFF_CAP)
                st.next_due = time.monotonic() + back + random.uniform(0, 1.0)
                self._poll_count += 1
                continue

            st.errors = 0
            st.last_poll = time.time()
            st.inserted += inserted
            self._inserted_total += inserted
            round_inserted += inserted
            st.next_due = time.monotonic() + self._interval_for(st) + random.uniform(0, 1.5)
            if inserted:
                self._last_poll_ts = int(time.time())
                self._touch_contact(st)
            self._poll_count += 1
            polled += 1

        self._rounds += 1
        self._last_round = {
            "at": int(time.time()), "requests": polled,
            "queued": len(queue), "inserted": round_inserted,
            "seconds": round(time.monotonic() - now, 2),
            "budget_hit": bool(polled > 0 and time.monotonic() - round_start >= ROUND_TIME_BUDGET),
        }
        return self._last_round

    def poll_once(self) -> dict:
        """同步跑一轮（测试/调试用）。"""
        return self._tick()

    def _gap(self) -> None:
        if REQUEST_GAP > 0:
            self._stop_event.wait(REQUEST_GAP)

    def _interval_for(self, st: _Peer) -> float:
        if self._focus == st.key:
            return FOCUS_INTERVAL
        if st.top:
            return TOP_INTERVAL
        return OTHER_INTERVAL

    def _due_queue(self) -> list[_Peer]:
        now = time.monotonic()
        with self._lock:
            focus = self._focus
            peers = list(self._peers.values())
        due = [p for p in peers if p.next_due <= now]
        due.sort(key=lambda p: (0 if p.key == focus else 1,
                                0 if p.top else 1,
                                -p.last_ts))
        return due[:MAX_REQUESTS_PER_ROUND]

    # ── 数据刷新 ────────────────────────────────────────────────────────────
    def _refresh_contacts_from_onebot(self) -> None:
        if not self.uin:
            return
        friends = bot_source._fetch_list(self.base, self.token, "get_friend_list")
        self._requests_total += 1
        self._gap()
        groups = bot_source._fetch_list(self.base, self.token, "get_group_list")
        self._requests_total += 1
        self._gap()
        store.upsert_contacts(bot_source._to_conversations(self.uin, friends, groups))

    def _refresh_peers(self) -> None:
        if not self.uin:
            return
        try:
            con = store.connect(self.uin)
            try:
                rows = con.execute(
                    "SELECT kind, peer_id, peer_qq, name, COALESCE(last_ts,0) AS last_ts "
                    "FROM contacts WHERE account_qq=? ORDER BY COALESCE(last_ts,0) DESC",
                    (self.uin,)).fetchall()
            finally:
                con.close()
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"读取会话列表失败：{exc}"
            return

        top_keys = {(r["kind"], str(r["peer_id"])) for r in rows[:TOP_N]}
        with self._lock:
            seen: set[str] = set()
            for r in rows:
                kind = str(r["kind"] or "")
                pid = str(r["peer_id"] or "")
                if kind not in ("c2c", "group") or not pid:
                    continue
                key = f"{kind}:{pid}"
                qq = _to_int(r["peer_qq"]) or (_to_int(pid) if pid.isdigit() else 0)
                st = self._peers.get(key)
                if st is None:
                    st = _Peer(key, kind, pid, qq, r["name"] or "")
                    # 首次纳入时按优先级错开，避免启动瞬间把所有会话打一遍
                    st.next_due = time.monotonic() + (
                        random.uniform(0, TOP_INTERVAL)
                        if (kind, pid) in top_keys else random.uniform(0, OTHER_INTERVAL))
                    self._peers[key] = st
                if qq:
                    st.peer_qq = qq
                if r["name"]:
                    st.name = str(r["name"])
                st.last_ts = _to_int(r["last_ts"])
                st.top = (kind, pid) in top_keys
                seen.add(key)
            for key, st in self._peers.items():
                if key not in seen:
                    st.top = False   # 不在联系人里了：降级，不删（focus 可能还指着它）

    def _poll_peer(self, st: _Peer) -> int:
        self._requests_total += 1
        msgs = bot_source._history(self.base, self.token, st.kind, st.peer_qq, COUNT)
        rows: list[dict] = []
        for m in msgs:
            if not isinstance(m, dict):
                continue
            row = bot_source.ob_msg_to_row(m, self.uin, kind=st.kind, peer_id=st.peer_id,
                                           peer_qq=st.peer_qq, account_qq=self.uin)
            if row and row["ts"]:
                rows.append(row)
        return store.insert_messages(rows) if rows else 0

    def _touch_contact(self, st: _Peer) -> None:
        """单会话重算 contacts 统计（比重算全部联系人便宜）。"""
        if not self.uin:
            return
        try:
            with store.tx(self.uin) as con:
                con.execute("""
                    UPDATE contacts SET
                      msg_count=(SELECT COUNT(*) FROM messages m
                        WHERE m.account_qq=? AND m.kind=? AND m.peer_id=?),
                      self_count=(SELECT COUNT(*) FROM messages m
                        WHERE m.account_qq=? AND m.kind=? AND m.peer_id=? AND m.direction=1),
                      first_ts=(SELECT MIN(ts) FROM messages m
                        WHERE m.account_qq=? AND m.kind=? AND m.peer_id=?),
                      last_ts=(SELECT MAX(ts) FROM messages m
                        WHERE m.account_qq=? AND m.kind=? AND m.peer_id=?),
                      last_text=(SELECT text FROM messages m
                        WHERE m.account_qq=? AND m.kind=? AND m.peer_id=?
                        ORDER BY ts DESC, id DESC LIMIT 1)
                    WHERE account_qq=? AND kind=? AND peer_id=?
                """, (self.uin, st.kind, st.peer_id) * 5 + (self.uin, st.kind, st.peer_id))
        except Exception:
            pass

    # ── 对外接口 ────────────────────────────────────────────────────────────
    def set_focus(self, kind, peer_id, peer_qq=0) -> dict:
        kind = str(kind or "").strip().lower()
        if kind not in ("c2c", "group") or peer_id in (None, ""):
            with self._lock:
                self._focus = None
            return {"ok": True, "focus": None, "message": "已取消 focus"}
        pid = str(peer_id)
        key = f"{kind}:{pid}"
        qq = _to_int(peer_qq) or (_to_int(pid) if pid.isdigit() else 0)
        with self._lock:
            self._focus = key
            st = self._peers.get(key)
            if st is None:
                st = _Peer(key, kind, pid, qq)
                self._peers[key] = st
            elif qq:
                st.peer_qq = qq
            st.next_due = 0.0
            st.errors = 0
            top = st.top
        return {"ok": True, "focus": {"kind": kind, "peer_id": pid},
                "peer_qq": qq, "top": top,
                "message": f"focus 已设为 {kind}:{pid}，将每 {FOCUS_INTERVAL:.0f}s 轮询"}

    def status(self) -> dict:
        now = time.monotonic()
        with self._lock:
            peers = sorted(self._peers.values(), key=lambda p: p.last_poll, reverse=True)
            running = bool(self._thread and self._thread.is_alive())
            focus_key = self._focus
            per = [{
                "kind": p.kind, "peer_id": p.peer_id, "peer_qq": p.peer_qq,
                "name": p.name, "top": p.top, "last_poll": p.last_poll,
                "inserted": p.inserted, "errors": p.errors,
                "next_due_in": max(0.0, round(p.next_due - now, 1)),
            } for p in peers[:100]]
            focus = None
            if focus_key and focus_key in self._peers:
                fp = self._peers[focus_key]
                focus = {"kind": fp.kind, "peer_id": fp.peer_id, "peer_qq": fp.peer_qq}
            return {
                "running": running,
                "focus": focus,
                "waiting_login": bool(self._waiting_login),
                "sync_allowed": bool((self._gate or {}).get("sync_allowed", True)),
                "require_login": require_login(),
                "gate": self._gate or None,
                "waiting_since": int(self._waiting_since) if self._waiting_login else 0,
                "account_qq": self.uin,
                "nickname": self.nick,
                "poll_count": self._poll_count,
                "requests_total": self._requests_total,
                "inserted_total": self._inserted_total,
                "rounds": self._rounds,
                "last_poll_ts": self._last_poll_ts,
                "last_error": self._last_error,
                "started_at": self._started_at,
                "peers": len(self._peers),
                "top_n": TOP_N,
                "last_round": self._last_round,
                "intervals": {"focus": FOCUS_INTERVAL, "top": TOP_INTERVAL,
                              "other": OTHER_INTERVAL, "count": COUNT,
                              "request_gap": REQUEST_GAP,
                              "max_requests_per_round": MAX_REQUESTS_PER_ROUND},
                "thread": self._thread.name if self._thread else None,
                "per_peer": per,
            }

    def events(self, since: int = 0, account=None, since_id: int = 0,
               limit: int = 300) -> dict:
        """只返回增量：since 之后的新消息 + last_ts 变化的联系人。

        since     : Unix 秒；messages 取 ts > since
        since_id  : 若 >0 则按 id 增量（更精确，推荐前端用）
        """
        try:
            limit = max(1, min(int(limit or 300), 1000))
        except (TypeError, ValueError):
            limit = 300
        since = _to_int(since)
        since_id = _to_int(since_id)
        aqq = _to_int(account) if account else 0
        if not aqq:
            # 多账号隔离：没有 account 绝不返回全局增量（路由层已 400，这里再兜底）
            return {"ts": int(time.time()), "since": since, "since_id": since_id,
                    "next_since": since, "next_id": since_id, "count": 0,
                    "messages": [], "messages_by_peer": {}, "contacts": [],
                    "error": "缺少 account（多账号隔离：增量必须指定账号）"}
        con = store.connect(aqq)
        try:
            where = "account_qq=?" if aqq else "1=1"
            args: list = [aqq] if aqq else []
            if since_id > 0:
                msql = (f"SELECT * FROM messages WHERE {where} AND id > ? "
                        f"ORDER BY id ASC LIMIT ?")
                margs = args + [since_id, limit]
            else:
                msql = (f"SELECT * FROM messages WHERE {where} AND ts > ? "
                        f"ORDER BY ts ASC, id ASC LIMIT ?")
                margs = args + [since, limit]
            messages = [dict(r) for r in con.execute(msql, margs).fetchall()]

            csql = (f"SELECT * FROM contacts WHERE {where} "
                    f"AND COALESCE(last_ts,0) > ? ORDER BY last_ts DESC LIMIT 200")
            cargs = args + [since]
            contacts = [dict(r) for r in con.execute(csql, cargs).fetchall()]
        finally:
            con.close()

        by_peer: dict[str, list] = {}
        for m in messages:
            by_peer.setdefault(f"{m.get('kind')}:{m.get('peer_id')}", []).append(m)
        next_id = max([_to_int(m.get("id")) for m in messages] + [since_id]) if messages else since_id
        next_since = max([_to_int(m.get("ts")) for m in messages] + [since]) if messages else since
        return {
            "ts": int(time.time()),
            "since": since, "since_id": since_id,
            "next_since": next_since, "next_id": next_id,
            "count": len(messages),
            "messages": messages,
            "messages_by_peer": by_peer,
            "contacts": contacts,
        }


# ── 进程内单例 ──────────────────────────────────────────────────────────────
_LIVE: LiveSync | None = None
_LIVE_LOCK = threading.Lock()


def get_live() -> LiveSync:
    global _LIVE
    with _LIVE_LOCK:
        if _LIVE is None:
            _LIVE = LiveSync()
        return _LIVE