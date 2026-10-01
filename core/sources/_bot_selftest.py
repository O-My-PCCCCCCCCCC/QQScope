r"""QQScope · 窗口B 离线自测（假 OneBot HTTP 服务 + 真实环境探测）

不依赖真实 NapCat：用标准库 http.server 起一个假的 OneBot v11 服务，
返回构造的 get_login_info / get_friend_list / get_group_list /
get_friend_msg_history / get_group_msg_history，
然后跑通 probe + sync，断言：
  1) 入库条数正确
  2) direction 判定正确（自己发的=1，别人=0）
  3) 连续 sync 两次不产生重复数据（依赖 store 的 ux_msg 唯一索引）
  4) 对不存在的地址 probe 返回人话中文错误（无异常栈）

运行（必须有 httpx，项目自带 .venv 里有）：
  <包根>\python\python.exe core\sources\_bot_selftest.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── 用临时库，绝不污染 data/qqscope.db ─────────────────────────────────────
_TMP = tempfile.mkdtemp(prefix="qqscope_bot_selftest_")
from core import paths                     # noqa: E402
paths.STORE_DB = Path(_TMP) / "qqscope_selftest.db"
from core import store                     # noqa: E402
store._MIGRATED = False
from core.sources import bot_source        # noqa: E402

try:
    import httpx  # noqa: F401
except Exception:
    print("FAIL 缺少 httpx，无法自测。请用 tools\\nt_msg_db_util\\.venv\\Scripts\\python.exe 运行")
    sys.exit(2)


# ── 构造的假数据 ───────────────────────────────────────────────────────────
SELF_UIN = 10001
SELF_NICK = "测试主号"
FRIENDS = [
    {"user_id": 20001, "nickname": "好友甲", "remark": "甲甲"},
    {"user_id": 20002, "nickname": "好友乙", "remark": ""},
]
GROUPS = [{"group_id": 30001, "group_name": "测试群"}]


def _msg(ts, user_id, text, sender_id, group_id=None):
    d = {
        "time": ts,
        "user_id": user_id,
        "sender": {"user_id": sender_id, "nickname": f"用户{sender_id}"},
        "raw_message": text,
        "message_type": "group" if group_id else "private",
    }
    if group_id:
        d["group_id"] = group_id
    return d


PRIVATE_HISTORY = {
    20001: [
        _msg(1700000000, 20001, "你好 [CQ:face,id=1]", 20001),        # 收到 + QQ 表情
        _msg(1700000060, 20001, "在的", SELF_UIN),                     # 自己发
        _msg(1700000120, 20001, "[CQ:image,file=a.jpg]", 20001),       # 纯图片（应入 media）
    ],
    20002: [
        _msg(1700000200, 20002, "[CQ:at,qq=20001] 你好", 20002),       # @某人 + 文本
    ],
}
GROUP_HISTORY = {
    30001: [
        _msg(1700000300, 20002, "群里的消息", 20002, group_id=30001),  # 收到
        _msg(1700000360, 20002, "[CQ:face,id=14]", SELF_UIN, group_id=30001),  # 自己发 + 表情
        _msg(1700000420, 20002, "[CQ:image,file=b.jpg]", 20002, group_id=30001),  # 收到图片
    ],
}
EXPECTED_TOTAL = 7
EXPECTED_SELF = 2          # 私聊「在的」 + 群聊「😲」
EXPECTED_GROUP_SELF = 1
EXPECTED_MEDIA = 2         # 两张图片
# ── 假 OneBot HTTP 服务 ────────────────────────────────────────────────────
class FakeOneBot(BaseHTTPRequestHandler):
    token_required = ""
    calls: list[str] = []

    def log_message(self, *args):   # 静音
        pass

    def _send(self, data, status=200):
        body = json.dumps({"status": "ok", "retcode": 0, "data": data},
                          ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_fail(self, retcode, msg, status=200):
        body = json.dumps({"status": "failed", "retcode": retcode, "msg": msg},
                          ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        parsed = urlparse(self.path)
        action = parsed.path.strip("/")
        qs = parse_qs(parsed.query)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            params = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            params = {}
        type(self).calls.append(action)

        if type(self).token_required:
            auth = self.headers.get("Authorization", "")
            ok_hdr = auth == f"Bearer {type(self).token_required}"
            ok_qs = qs.get("access_token", [None])[0] == type(self).token_required
            if not (ok_hdr or ok_qs):
                return self._send_fail(1401, "token 校验失败", status=401)

        if action == "get_login_info":
            return self._send({"user_id": SELF_UIN, "nickname": SELF_NICK})
        if action == "get_friend_list":
            return self._send(FRIENDS)
        if action == "get_group_list":
            return self._send(GROUPS)
        if action == "get_friend_msg_history":
            uid = int(params.get("user_id") or 0)
            # NapCat 风格的包装：{"messages": [...]}
            return self._send({"messages": PRIVATE_HISTORY.get(uid, [])})
        if action == "get_group_msg_history":
            gid = int(params.get("group_id") or 0)
            return self._send({"messages": GROUP_HISTORY.get(gid, [])})
        return self._send_fail(1404, f"不支持的 action: {action}")


def start_fake(token=""):
    FakeOneBot.token_required = token
    FakeOneBot.calls = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOneBot)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


# ── 断言工具 ───────────────────────────────────────────────────────────────
_RESULTS = []


def check(name, cond, extra=""):
    _RESULTS.append((name, bool(cond)))
    print(("  PASS " if cond else "  FAIL ") + name + (f"  | {extra}" if extra else ""))
    return bool(cond)


def _db_counts():
    con = store.connect()
    try:
        total = con.execute("SELECT COUNT(*) FROM messages WHERE account_qq=? AND source='bot'",
                            (SELF_UIN,)).fetchone()[0]
        selfn = con.execute("SELECT COUNT(*) FROM messages WHERE account_qq=? AND direction=1",
                            (SELF_UIN,)).fetchone()[0]
        grp_self = con.execute("SELECT COUNT(*) FROM messages "
                               "WHERE account_qq=? AND kind='group' AND direction=1",
                               (SELF_UIN,)).fetchone()[0]
        return total, selfn, grp_self
    finally:
        con.close()


def _media_count():
    con = store.connect()
    try:
        return con.execute(
            "SELECT COUNT(*) FROM messages WHERE account_qq=? AND source='bot' "
            "AND media IS NOT NULL", (SELF_UIN,)).fetchone()[0]
    finally:
        con.close()


def _direction_of(kind, peer_id, text):
    con = store.connect()
    try:
        r = con.execute("SELECT direction FROM messages WHERE account_qq=? AND kind=? "
                        "AND peer_id=? AND text=?", (SELF_UIN, kind, peer_id, text)).fetchone()
        return r[0] if r else None
    finally:
        con.close()


NO_STACK = ("Traceback", "ConnectError", "TimeoutException", "httpx.", "Exception(")


def main():
    print("=" * 78)
    print("QQScope 窗口B（bot_source）离线自测")
    print(f"临时主库：{paths.STORE_DB}")
    print("=" * 78)
    store.init()   # 自测用临时库，先建表

    # ── 0. 契约常量 ────────────────────────────────────────────────────────
    print("\n[0] 接口常量")
    check("SOURCE_ID == 'bot'", bot_source.SOURCE_ID == "bot", bot_source.SOURCE_ID)
    check("SOURCE_NAME == '机器人框架读取'", bot_source.SOURCE_NAME == "机器人框架读取",
          bot_source.SOURCE_NAME)
    check("SOURCE_KIND == 'bot'", bot_source.SOURCE_KIND == "bot", bot_source.SOURCE_KIND)

    # ── 1. status()（真实环境，预期未检测到服务）─────────────────────────
    print("\n[1] status() 真实环境探测（预期未检测到 OneBot）")
    st = bot_source.status()
    print(f"     ready={st['ready']}")
    print(f"     message={st['message']}")
    check("status() 返回契约字段", {"id", "name", "kind", "ready", "message", "detail"} <= set(st))
    check("status() message 为中文人话",
          bool(st["message"]) and any(k in st["message"] for k in ("检测到", "未检测到")))
    check("status() 只探测并提示 NapCat 启动器（不自动启动）",
          "napcat_launcher" in st["detail"]
          and isinstance(st["detail"]["napcat_launcher_exists"], bool))

    # ── 2. probe 假服务 ────────────────────────────────────────────────────
    print("\n[2] probe(fake) —— 只探测，不写库")
    srv, base = start_fake()
    pr = bot_source.probe({"base": base, "token": "", "top_friends": 30, "top_groups": 30})
    print(f"     message={pr['message']}")
    check("probe ok=True", pr["ok"] is True)
    check("probe 账号正确", pr["accounts"] and pr["accounts"][0]["account_qq"] == SELF_UIN,
          str(pr["accounts"]))
    check("probe 会话数=3（2 好友 + 1 群）", len(pr["conversations"]) == 3,
          f"实际 {len(pr['conversations'])}")
    check("probe 不做历史拉取（请求数≤3）", len(FakeOneBot.calls) <= 3,
          f"实际请求 {FakeOneBot.calls}")
    probe_calls = list(FakeOneBot.calls)
    check("probe 不写库（messages 仍为 0）", _db_counts()[0] == 0)

    convs = bot_source.conversations({"base": base, "token": ""})
    check("conversations() 返回 3 个会话", len(convs) == 3,
          str([(c["kind"], c["peer_id"]) for c in convs]))
    check("conversations() kind 覆盖 c2c + group",
          {c["kind"] for c in convs} == {"c2c", "group"})

    # ── 3. sync 假服务（第一次）───────────────────────────────────────────
    print("\n[3] sync(fake) 第一次 —— 采集入库")
    events = []
    r1 = bot_source.sync({"base": base, "top_friends": 30, "top_groups": 30, "per_peer": 200},
                         progress=lambda stage, pct, msg: events.append((stage, pct, msg)))
    print(f"     message={r1['message']}")
    print(f"     imported={r1['imported']}  elapsed={r1['elapsed']}s  log_lines={len(r1['log'].splitlines())}")
    check("sync ok=True", r1["ok"] is True)
    check(f"入库条数正确（imported=={EXPECTED_TOTAL}）", r1["imported"] == EXPECTED_TOTAL,
          f"实际 {r1['imported']}")
    check("accounts == [self_uin]", r1["accounts"] == [SELF_UIN], str(r1["accounts"]))
    check("progress 回调被调用且以 100 结束",
          bool(events) and events[-1][1] == 100 and events[0][0] == "准备",
          f"共 {len(events)} 次，最后 {events[-1] if events else None}")
    check("sync 请求数多于 probe（probe 更快）", len(FakeOneBot.calls) > len(probe_calls),
          f"probe {len(probe_calls)} vs sync {len(FakeOneBot.calls)}")

    # ── 4. CQ 解析单测（不依赖 DB）─────────────────────────────────────────
    print("\n[4] CQ 解析单测：face → emoji / image → media / at / 未知码保留")
    t_face, m_face = bot_source.parse_cq("看我雷霆操作[CQ:face,id=4]")
    check("face id=4 → Unicode emoji", t_face == "看我雷霆操作😎" and m_face is None, repr(t_face))
    t_face2, _ = bot_source.parse_cq("[CQ:face,id=14]")
    check("face id=14 → 😲", t_face2 == "😲", repr(t_face2))
    t_unk_face, _ = bot_source.parse_cq("[CQ:face,id=199]")
    check("拿不准的 face 退回 [表情N]", t_unk_face == "[表情199]", repr(t_unk_face))
    t_at, _ = bot_source.parse_cq("[CQ:at,qq=2537360768] 你好")
    check("at → @QQ（不再变空白）", t_at == "@2537360768 你好", repr(t_at))
    t_all, _ = bot_source.parse_cq("[CQ:at,qq=all] 通知")
    check("at all → @全体成员", t_all == "@全体成员 通知", repr(t_all))
    t_img, m_img = bot_source.parse_cq(
        "[CQ:image,file=18EF41914623A18AE981482DC0EE328B.jpg,file_size=125714]")
    check("image → text 空 + media(kind/md5/size)",
          t_img == "" and bool(m_img) and m_img["kind"] == "image"
          and m_img["md5"] == "18ef41914623a18ae981482dc0ee328b" and m_img["size"] == 125714,
          json.dumps(m_img, ensure_ascii=False))
    _, m_voice = bot_source.parse_cq("[CQ:record,file=8e97018c498fe2563e5cd07d5cf181c3.amr]")
    check("record → media kind=voice", bool(m_voice) and m_voice["kind"] == "voice", str(m_voice))
    _, m_multi = bot_source.parse_cq(
        "[CQ:image,file=a.jpg][CQ:image,file=b.jpg][CQ:image,file=c.jpg]")
    check("多段媒体只留第一个 + fallback 标 +N",
          bool(m_multi) and m_multi["fallback"] == "[图片+2]",
          bool(m_multi) and m_multi["fallback"])
    t_bad, _ = bot_source.parse_cq("[CQ:bface,id=123]x")
    check("未知 CQ 码原样保留（不凭空删）", t_bad == "[CQ:bface,id=123]x", repr(t_bad))

    # ── 4b. 入库内容 / direction 断言 ─────────────────────────────────────
    print("\n[4b] 入库内容与 direction 判定")
    total, selfn, grp_self = _db_counts()
    print(f"     messages={total}  self={selfn}  group_self={grp_self}  media={_media_count()}")
    check(f"主库总条数 == {EXPECTED_TOTAL}", total == EXPECTED_TOTAL, f"实际 {total}")
    check(f"自己发出条数 == {EXPECTED_SELF}", selfn == EXPECTED_SELF, f"实际 {selfn}")
    check(f"群里自己发出 == {EXPECTED_GROUP_SELF}", grp_self == EXPECTED_GROUP_SELF, f"实际 {grp_self}")
    check("私聊 20001 自己发的『在的』direction=1", _direction_of("c2c", "20001", "在的") == 1)
    check("私聊 20002 别人发的『@20001 你好』direction=0",
          _direction_of("c2c", "20002", "@20001 你好") == 0)
    check("群聊 30001 自己发的『😲』direction=1", _direction_of("group", "30001", "😲") == 1)
    check("群聊 30001 别人发的『群里的消息』direction=0",
          _direction_of("group", "30001", "群里的消息") == 0)
    c2c1 = store.list_messages(SELF_UIN, "c2c", "20001")
    texts1 = [str(r["text"]) for r in c2c1]
    check("QQ 表情已变成 Unicode emoji（『你好 😁』）",
          any(t.startswith("你好") and "😁" in t for t in texts1), str(texts1))
    check("图片消息不丢：text 占位 + media 非空",
          any(r["text"] and str(r["text"]).startswith("[图片") and r["media"] for r in c2c1),
          str([(r["text"], bool(r["media"])) for r in c2c1]))
    check(f"带 media 的入库条数 == {EXPECTED_MEDIA}", _media_count() == EXPECTED_MEDIA,
          f"实际 {_media_count()}")
    contacts = store.list_contacts(SELF_UIN)
    print(f"     contacts={[(c['kind'], c['peer_id'], c['name'], c['msg_count']) for c in contacts]}")
    check("contacts 写入 3 条（2 好友 + 1 群）", len(contacts) == 3, f"实际 {len(contacts)}")
    c20001 = next((c for c in contacts if c["peer_id"] == "20001"), None)
    check("refresh_contact_stats 生效（20001 msg_count=3）",
          c20001 and c20001["msg_count"] == 3 and c20001["self_count"] == 1,
          str(c20001))
    # ── 5. 连续第二次 sync：去重 ──────────────────────────────────────────
    print("\n[5] sync(fake) 第二次 —— 依赖 ux_msg 唯一索引去重")
    r2 = bot_source.sync({"base": base, "top_friends": 30, "top_groups": 30, "per_peer": 200})
    print(f"     message={r2['message']}")
    total2 = _db_counts()[0]
    check("第二次 imported == 0", r2["imported"] == 0, f"实际 {r2['imported']}")
    check(f"主库总条数仍为 {EXPECTED_TOTAL}（无重复）", total2 == EXPECTED_TOTAL, f"实际 {total2}")

    # ── 5b. 老库占位符行自愈（模拟旧版本 [@]/[图片] 行）───────────────────
    print("\n[5b] 老库占位符行自愈（旧版本行不再残留、不重复）")
    check("legacy 文本：未知 CQ → [type]",
          bot_source._legacy_raw_to_text("[CQ:flashtransfer,fileSetId=x]") == "[flashtransfer]")
    check("legacy 文本：图片+文本保留尾巴",
          bot_source._legacy_raw_to_text("[CQ:image,file=a.jpg]真好看") == "[图片]真好看")
    con = store.connect()
    con.execute(
        "INSERT OR IGNORE INTO messages(account_qq,kind,peer_id,peer_qq,ts,direction,"
        "sender_qq,sender_name,msg_type,text,source,content,media) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,NULL)",
        (SELF_UIN, "c2c", "20002", 20002, 1700000200, 0, 20002, "用户20002",
         0, "[@] 你好", "bot"))
    con.commit()
    con.close()
    r3 = bot_source.sync({"base": base, "top_friends": 30, "top_groups": 30, "per_peer": 200})
    total3 = _db_counts()[0]
    print(f"     message={r3['message']}")
    check("旧占位符行被识别/去重（deduped>=1）", (r3.get("deduped") or 0) >= 1,
          f"deduped={r3.get('deduped')}")
    check(f"总量仍为 {EXPECTED_TOTAL}（占位行被清理，不是重复插入）",
          total3 == EXPECTED_TOTAL, f"实际 {total3}")
    con = store.connect()
    left = con.execute("SELECT COUNT(*) FROM messages WHERE account_qq=? AND text='[@] 你好'",
                       (SELF_UIN,)).fetchone()[0]
    con.close()
    check("旧占位符文本不再残留", left == 0, f"残留 {left}")
    srv.shutdown()

    # ── 6. 不存在的地址 / token 错误 ──────────────────────────────────────
    print("\n[6] 错误路径 —— 人话中文、无异常栈")
    bad = bot_source.probe({"base": "http://127.0.0.1:1"})
    print(f"     不存在地址 message={bad['message']}")
    check("不存在地址 probe ok=False", bad["ok"] is False)
    check("不存在地址给出中文人话（拒绝/超时/连不上）",
          any(k in bad["message"] for k in ("连不上", "拒绝", "超时")), bad["message"][:60])
    check("不存在地址不含异常栈/英文异常名",
          not any(t in bad["message"] for t in NO_STACK), bad["message"][:60])

    srv2, base2 = start_fake(token="s3cret")
    wrong = bot_source.probe({"base": base2, "token": "bad-token"})
    print(f"     token 错误 message={wrong['message']}")
    check("token 错误 probe ok=False", wrong["ok"] is False)
    check("token 错误给出中文提示", "token" in wrong["message"] or "授权" in wrong["message"],
          wrong["message"][:60])
    right = bot_source.probe({"base": base2, "token": "s3cret"})
    check("token 正确可连接", right["ok"] is True, right["message"])
    srv2.shutdown()

    # ── 7. 真实环境探测（预期失败）──────────────────────────────────────────
    print("\n[7] 真实环境探测 http://127.0.0.1:3000（现场无 NapCat，预期失败）")
    t = time.time()
    real = bot_source.probe({"base": "http://127.0.0.1:3000"})
    dt = time.time() - t
    print(f"     ok={real['ok']}  elapsed={dt:.2f}s")
    print(f"     message={real['message']}")
    check("真实环境 probe 返回结构化结果", isinstance(real, dict) and "ok" in real and "message" in real)
    check("真实环境失败信息为人话（无异常栈）",
          not any(x in real["message"] for x in NO_STACK), real["message"][:60])
    check("probe 足够快（<5s，不挂死）", dt < 5.0, f"{dt:.2f}s")

    # ── 汇总 ───────────────────────────────────────────────────────────────
    passed = sum(1 for _, ok in _RESULTS if ok)
    failed = [n for n, ok in _RESULTS if not ok]
    print("\n" + "=" * 78)
    print(f"自测结果：{passed}/{len(_RESULTS)} 通过")
    if failed:
        print("失败项：")
        for n in failed:
            print("   - " + n)
        print("=" * 78)
        return 1
    print("全部断言通过 ✅")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)