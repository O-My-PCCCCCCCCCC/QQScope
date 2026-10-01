"""QQScope · 导出引擎自测。

流程：构造隔离测试库（>=300 条、跨 >=3 天、含自己/对方/空 text）
  -> 调 export 生成 3 会话 x 3 格式 = 9 文件 + 1 zip
  -> 断言 HTML 无外链 / TXT 带 BOM / MD 有统计表 / zip 文件数正确
  -> 用无头 Edge 对 HTML 截图，确认可正常渲染。

运行：
    python core/_export_selftest.py
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core import export as exporter
from core import paths, store

EDGE = pathlib.Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
TEST_DIR = _ROOT / "data" / "export" / "_test"
ACCOUNT = 100001
TARGETS = [
    {"kind": "c2c", "peer_id": "u_alice", "peer_qq": 22222, "name": "小爱", "remark": "爱丽丝"},
    {"kind": "c2c", "peer_id": "u_bob", "peer_qq": 33333, "name": "老王", "remark": None},
    {"kind": "group", "peer_id": "888888", "peer_qq": 888888, "name": "测试群", "remark": None},
]
TEXTS = [
    "你好，今天过得怎么样？",
    "第一行\n第二行\n第三行",
    "特殊字符测试：*加粗* _下划线_ `代码` | 竖线",
    "QQScope 测试内容 #tag 和中文标点，结束。",
    "这是一条比较长的消息，用来验证长文本换行与气泡宽度。",
    "外链防误报：https://example.com/a?b=1&c=2 以及 src= 字样。",
]
RESULTS = []

def check(cond, label, detail=""):
    ok = bool(cond)
    RESULTS.append((ok, label, detail))
    tag = "[ok]  " if ok else "[FAIL]"
    print("  " + tag + " " + label + (("  " + str(detail)) if detail != "" else ""))
    return ok


def _prepare_db():
    """把 store 指向 data/export/_test/ 下的隔离库，绝不污染主库。"""
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    db = TEST_DIR / "selftest.db"
    for suf in ("", "-wal", "-shm"):
        f = pathlib.Path(str(db) + suf)
        if f.exists():
            f.unlink()
    paths.STORE_DB = db
    store._MIGRATED = False
    store.init()
    return db


def _seed():
    store.upsert_account(ACCOUNT, label="自测账号", source="pack")
    contacts = []
    for t in TARGETS:
        contacts.append({
            "account_qq": ACCOUNT, "kind": t["kind"], "peer_id": t["peer_id"],
            "peer_qq": t["peer_qq"], "name": t["name"], "remark": t["remark"],
            "source": "pack",
        })
    store.upsert_contacts(contacts)

    base = int(datetime(2026, 8, 13, 9, 0).timestamp())
    step = 40 * 60        # 40 分钟一条：每个会话都能跨 >=3 天
    per = 120             # 3 会话 x 120 = 360 条
    rows = []
    for ti, t in enumerate(TARGETS):
        for i in range(per):
            ts = base + i * step + ti * 60
            direction = 1 if i % 2 == 0 else 0
            if i % 25 == 0:
                text = None            # 图片/文件等非文本
            elif i % 37 == 0:
                text = "   "           # 空白也算非文本
            else:
                text = f"[{t['name']}] 第 {i} 条消息 " + TEXTS[i % len(TEXTS)]
            sender_name = None
            sender_qq = ACCOUNT if direction == 1 else None
            if t["kind"] == "group" and direction == 0:
                sender_name = ["小张", "小李", "小王"][i % 3]
                sender_qq = 44440 + (i % 3)
            rows.append({
                "account_qq": ACCOUNT, "kind": t["kind"], "peer_id": t["peer_id"],
                "peer_qq": t["peer_qq"], "ts": ts, "direction": direction,
                "sender_qq": sender_qq, "sender_name": sender_name,
                "msg_type": 2, "text": text, "source": "pack",
            })
    added = store.insert_messages(rows)
    store.refresh_contact_stats(ACCOUNT)
    return rows, added

def _validate(res, expected_files):
    check(res.get("ok") is True, "export 返回 ok=True")
    names = [f["name"] for f in res["files"]]
    check(len(names) == expected_files, f"文件数 = {expected_files}", len(names))
    d = pathlib.Path(res["dir"])
    check(d.is_dir(), "输出目录存在", str(d))
    for f in res["files"]:
        p = d / f["name"]
        check(p.exists() and p.stat().st_size > 0 and p.stat().st_size == f["size"],
              f"文件落盘且 size 正确：{f['name']}", f["size"])
    zp = pathlib.Path(res["zip"])
    check(zp.exists() and zp.stat().st_size > 0, "zip 落盘", str(zp))
    with zipfile.ZipFile(zp) as zf:
        znames = zf.namelist()
    docs = [n for n in znames if not n.startswith("media/")]
    check(len(docs) == expected_files, f"zip 内文档数 = {expected_files}", len(docs))
    check(set(docs) == set(names), "zip 内文档与返回 files 一致")
    return names


def _html_ref_issues(s):
    """自包含校验：禁止 http(s)://；src/href 只能是 media/ 相对路径；img 不许没 src。"""
    issues = []
    if "http://" in s or "https://" in s:
        issues.append("含 http(s)://")
    for m in re.finditer(r'(?i)\b(src|href)\s*=\s*"([^"]*)"', s):
        v = m.group(2).strip()
        if not v.startswith("media/"):
            issues.append("非相对媒体引用: " + m.group(0)[:70])
    for tag in re.findall(r"(?i)<img\b[^>]*>", s):
        mm = re.search(r'(?i)src\s*=\s*"([^"]*)"', tag)
        if mm and not mm.group(1).startswith("media/"):
            issues.append("img 指向外部/绝对路径: " + tag[:80])
        elif not mm and "qs-lightbox-img" not in tag:
            issues.append("img 没有 src 会破图: " + tag[:80])
    low = s.lower()
    for bad in ("<link", "@import", "url("):
        if bad in low:
            issues.append("含 " + bad)
    return issues


def _check_html(path):
    # 向后兼容旧断言：纯文本导出的 HTML 里不应出现任何 src= / <img>（灯箱仅在含媒体时注入）
    s = path.read_text(encoding="utf-8")
    low = s.lower()
    check("<style>" in low and "<script>" in low, f"HTML 内联 CSS/JS：{path.name}")
    check("http://" not in s and "https://" not in s, f"HTML 无 http(s)://：{path.name}")
    check("src=" not in s, f"HTML 无 src=：{path.name}")
    for bad in ("<link", "<img", "@import", "url("):
        check(bad not in low, f"HTML 无 {bad}：{path.name}")
    check("仅看自己发的" in s, f"HTML 有「仅看自己发的」按钮：{path.name}")
    check('class="msg self"' in s, f"HTML 有自己右侧气泡：{path.name}")
    check('class="msg peer"' in s, f"HTML 有对方左侧气泡：{path.name}")
    check("非文本消息被略过" in s, f"HTML 有非文本统计：{path.name}")
    check("QQScope" in s, f"HTML 有来源署名：{path.name}")
    return s


def _check_html_media(path):
    """带媒体的 HTML：允许 <img>/<audio>/<a>，但引用必须全是 media/ 相对路径。"""
    s = path.read_text(encoding="utf-8")
    low = s.lower()
    check("<style>" in low and "<script>" in low, f"HTML 内联 CSS/JS：{path.name}")
    check("http://" not in s and "https://" not in s, f"HTML 无 http(s)://：{path.name}")
    issues = _html_ref_issues(s)
    check(not issues, f"HTML 只有内联资源 + media/ 相对引用（无破图）：{path.name}", issues[:2])
    check("仅看自己发的" in s, f"HTML 有「仅看自己发的」按钮：{path.name}")
    check('class="msg self"' in s, f"HTML 有自己右侧气泡：{path.name}")
    check('class="msg peer"' in s, f"HTML 有对方左侧气泡：{path.name}")
    check("非文本消息被略过" in s, f"HTML 有非文本统计：{path.name}")
    check("QQScope" in s, f"HTML 有来源署名：{path.name}")
    return s


def _check_txt(path):
    raw = path.read_bytes()
    check(raw[:3] == b"\xef\xbb\xbf", f"TXT 前三字节是 UTF-8 BOM：{path.name}")
    s = raw.decode("utf-8-sig")
    check(re.search(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}  ", s, re.M) is not None,
          f"TXT 行格式「时间  名字：内容」：{path.name}")
    check("非文本消息被略过" in s, f"TXT 统计块含略过条数：{path.name}")
    check("时间范围" in s and "导出时间" in s, f"TXT 统计块完整：{path.name}")
    return s


def _check_md(path):
    s = path.read_text(encoding="utf-8")
    check("| 项目 | 值 |" in s and "| --- | --- |" in s, f"MD 有统计表：{path.name}")
    days = re.findall(r"^## (\d{4}-\d{2}-\d{2})$", s, re.M)
    check(len(days) >= 3, f"MD 按天分节（>=3 天）：{path.name}", f"days={len(days)}")
    check("> **" in s, f"MD 有引用块：{path.name}")
    check("\\|" in s, f"MD 转义了竖线：{path.name}")
    check(s.startswith("# 与 "), f"MD 标题以「# 与」开头：{path.name}")
    return s

def _screenshot(html_path, name="shot_export.png"):
    out = TEST_DIR / name
    if out.exists():
        out.unlink()
    if not EDGE.exists():
        check(False, "找不到无头 Edge", str(EDGE))
        return None
    uri = html_path.resolve().as_uri()
    profile = TEST_DIR / "edge_profile"
    base = [str(EDGE), "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", "--hide-scrollbars",
            f"--user-data-dir={profile}", "--virtual-time-budget=3000",
            "--window-size=1200,1600", f"--screenshot={out}", uri]
    fallback = [a.replace("--headless=new", "--headless") for a in base]
    for args in (base, fallback):
        try:
            subprocess.run(args, capture_output=True, timeout=180)
        except Exception as e:
            print("    edge 调用异常：", e)
        if out.exists() and out.stat().st_size > 5000:
            break
    if not out.exists():
        return None
    data = out.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        print("    PNG 文件头不正确")
        return None
    w = int.from_bytes(data[16:20], "big")
    h = int.from_bytes(data[20:24], "big")
    print(f"    截图：{out}")
    print(f"    大小：{len(data)} bytes  尺寸：{w}x{h}")
    if w <= 0 or h <= 0 or len(data) <= 5000:
        return None
    return out


def main():
    print("=" * 72)
    print("QQScope 导出引擎自测")
    print("=" * 72)

    print("[1] 构造隔离测试数据 ...")
    db = _prepare_db()
    rows, added = _seed()
    print(f"    隔离库：{db}")
    print(f"    插入 {len(rows)} 条（新增 {added}），库里共 {store.count_messages(ACCOUNT)} 条")
    check(len(rows) >= 300, "测试数据 >= 300 条", len(rows))
    check(added == len(rows), "全部新增（无重复被去重）", added)
    check(store.count_messages(ACCOUNT) == len(rows), "库里条数与造数一致")
    day_set = {datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d") for r in rows}
    check(len(day_set) >= 3, "测试数据跨 >=3 天", f"{len(day_set)} 天")
    kinds = {(r["kind"], r["peer_id"]) for r in rows}
    check(len(kinds) == 3, "覆盖 3 个会话", len(kinds))
    dirs = {r["direction"] for r in rows}
    check(dirs == {0, 1}, "同时含自己(1)和对方(0)", sorted(dirs))
    empty = [r for r in rows if r["text"] is None or not str(r["text"]).strip()]
    check(len(empty) > 0, "含空 text（图片/文件）", len(empty))
    group_senders = {r["sender_name"] for r in rows
                     if r["kind"] == "group" and r["direction"] == 0}
    check(len(group_senders) > 0, "群聊含多名发送者", sorted(group_senders))

    targets = [{"kind": t["kind"], "peer_id": t["peer_id"]} for t in TARGETS]

    print("[2] per_peer 导出 3 会话 x 3 格式 ...")
    res = exporter.export(ACCOUNT, targets, ["html", "txt", "md"], mode="per_peer")
    _validate(res, 9)
    print(f"    job={res['job']}  count={res['count']}  total={res['total']}  skipped={res['skipped']}")
    check(res["total"] == 360, "导出覆盖 360 条消息", res["total"])
    check(res["skipped"] == 24, "非文本略过 24 条", res["skipped"])
    check(res["count"] == 336, "导出文本消息 336 条", res["count"])
    check(res["files_count"] == 9, "files_count=9", res["files_count"])

    print("[3] 逐格式断言 ...")
    d = pathlib.Path(res["dir"])
    html_files = [f for f in res["files"] if f["fmt"] == "html"]
    txt_files = [f for f in res["files"] if f["fmt"] == "txt"]
    md_files = [f for f in res["files"] if f["fmt"] == "md"]
    check(len(html_files) == 3 and len(txt_files) == 3 and len(md_files) == 3,
          "每种格式各 3 个文件")
    for f in html_files:
        _check_html(d / f["name"])
    for f in txt_files:
        _check_txt(d / f["name"])
    for f in md_files:
        _check_md(d / f["name"])
    print("[4] merged 模式（全部会话合成一个文件）...")
    res2 = exporter.export(ACCOUNT, targets, ["html", "txt", "md"], mode="merged",
                           opts={"merged_name": "三个会话合并"})
    _validate(res2, 3)
    check(res2["total"] == 360, "merged 覆盖 360 条消息", res2["total"])
    check(res2["count"] == 336, "merged 文本 336 条", res2["count"])

    print("[5] 文件名净化 / 超长 / 重名 ...")
    bad = 'a\\b/c:d*e?f"g<h>i|j' + "x" * 200
    safe = exporter._sanitize(bad)
    check(not any(ch in safe for ch in '\\/:*?"<>|'), "非法字符已净化", safe[:40])
    used = set()
    n1 = exporter._unique_filename("同名", "c2c", "u1", "txt", used)
    n2 = exporter._unique_filename("同名", "c2c", "u1", "txt", used)
    check(n1 != n2, "重名自动去重", f"{n1} / {n2}")
    long_name = exporter._unique_filename("长" * 300, "c2c", "u" * 300, "html", set())
    check(len(long_name) <= 120, "超长文件名 <=120 字符", len(long_name))
    check(not any(ch in long_name for ch in '\\/:*?"<>|'), "超长文件名已净化")
    reserved = exporter._sanitize("CON.txt")
    check(reserved.lower() != "con.txt", "Windows 保留名处理", reserved)

    print("[6] 无头 Edge 对 HTML 截图 ...")
    shot = _screenshot(d / html_files[0]["name"])
    check(shot is not None, "无头 Edge 截图成功", str(shot) if shot else "失败")

    print("[7] 文件清单 ...")
    print("    per_peer：")
    for f in res["files"]:
        print(f"      {f['fmt']:4}  {f['name']:<52}  {f['size']:>8} bytes")
    z1 = pathlib.Path(res["zip"])
    print(f"      zip   {z1.name:<52}  {z1.stat().st_size:>8} bytes")
    print("    merged：")
    for f in res2["files"]:
        print(f"      {f['fmt']:4}  {f['name']:<52}  {f['size']:>8} bytes")
    z2 = pathlib.Path(res2["zip"])
    print(f"      zip   {z2.name:<52}  {z2.stat().st_size:>8} bytes")

    print("[8] 媒体导出（图片/语音/文件/表情/视频 + 占位徽章）...")
    media_root, real, specs = _media_fixture()
    file_kinds = ("image", "voice", "video", "file", "sticker")
    exp_total = sum(1 for s in specs if s["kind"] in file_kinds)
    exp_copied = sum(1 for s in specs if s["kind"] in file_kinds and s.get("rel")
                     and (media_root / s["rel"]).is_file())
    exp_missing = exp_total - exp_copied
    check(len(specs) >= 6, "媒体用例 >=6 条不同类型", len(specs))
    check(exp_copied >= 4, "指向真实本地文件的媒体 >=4 条", f"{exp_copied}/{exp_total}")
    check(bool(real.get("voice")), "拿到真实 .amr 语音文件", str(real.get("voice")))

    store.upsert_account(MEDIA_ACCOUNT, label="媒体自测", source="pack")
    store.upsert_contacts([{"account_qq": MEDIA_ACCOUNT, "kind": "c2c",
                            "peer_id": "u_media", "peer_qq": 66666,
                            "name": "媒体好友", "remark": None, "source": "pack"}])
    mrows = _media_rows(MEDIA_ACCOUNT, "u_media", specs)
    store.insert_messages(mrows)
    store.refresh_contact_stats(MEDIA_ACCOUNT)
    check(store.count_messages(MEDIA_ACCOUNT) == len(mrows), "媒体用例已入库", len(mrows))

    targets_m = [{"kind": "c2c", "peer_id": "u_media"}]
    resolver = _media_resolver(media_root)
    res_m = exporter.export(MEDIA_ACCOUNT, targets_m, ["html", "txt", "md"],
                            opts={"media": True, "media_resolver": resolver})
    _validate(res_m, 3)
    ms = res_m["media"]
    check(ms["total"] == exp_total, "media.total 正确", f'{ms["total"]}/{exp_total}')
    check(ms["copied"] == exp_copied, "media.copied 正确", f'{ms["copied"]}/{exp_copied}')
    check(ms["missing"] == exp_missing, "media.missing 正确", f'{ms["missing"]}/{exp_missing}')
    check(ms["bytes"] > 0, "media.bytes > 0", ms["bytes"])
    check(ms["cards"] == 1, "media.cards = 1（名片）", ms["cards"])
    check(ms["voice_total"] == 3, "voice_total = 3", ms["voice_total"])
    check(ms["voice_transcribed"] == 2, "voice_transcribed = 2", ms["voice_transcribed"])
    check(ms["voice_no_text"] == 1, "voice_no_text = 1", ms["voice_no_text"])
    check(ms["truncated"] is False, "未超限 truncated=False")
    check(res_m["count"] == len(mrows), "媒体会话 count = 全部消息", res_m["count"])
    check(res_m["media_count"] == len(specs), "media_count = 媒体消息数", res_m["media_count"])

    md_dir = pathlib.Path(res_m["dir"]) / "media"
    media_files = sorted(p.name for p in md_dir.glob("*")) if md_dir.is_dir() else []
    check(len(media_files) == exp_copied, "media/ 目录文件数 = copied", len(media_files))
    with zipfile.ZipFile(res_m["zip"]) as zf:
        zmedia = [n for n in zf.namelist() if n.startswith("media/")]
    check(len(zmedia) == exp_copied, "zip 内 media/ 文件数 = copied", len(zmedia))

    d_m = pathlib.Path(res_m["dir"])
    f_by_fmt = {f["fmt"]: f["name"] for f in res_m["files"]}
    html_m = _check_html_media(d_m / f_by_fmt["html"])
    txt_m = _check_txt(d_m / f_by_fmt["txt"])
    md_m = (d_m / f_by_fmt["md"]).read_text(encoding="utf-8")
    check("| 项目 | 值 |" in md_m and "| --- | --- |" in md_m, "媒体 MD 有统计表")
    check(md_m.startswith("# 与 ") and "> **" in md_m, "媒体 MD 标题/引用块")
    check("\\|" in md_m, "媒体 MD 转义了竖线")

    check("<audio" in html_m and "controls" in html_m, "HTML 有带 controls 的语音播放器")
    check('src="media/' in html_m, "HTML 用 media/ 相对路径引用媒体")
    check('loading="lazy"' in html_m, "HTML 图片懒加载")
    check('href="media/' in html_m and "download" in html_m, "HTML 文件卡片带下载链接")
    check('id="qs-lightbox"' in html_m, "HTML 有灯箱容器")
    check("[图片·未缓存]" in html_m, "HTML 缺失图片显示占位徽章")
    check('class="voice-text"' in html_m and "今天晚上终于可以画画了" in html_m, "HTML 语音文字是主体")
    check("可能非中文" in html_m, "HTML 非中文语音有标记")
    check("未转文字" in html_m, "HTML 未转写语音显示占位")
    check(html_m.count("<audio") == 2, "HTML 只有 2 条本地语音渲染 <audio>", html_m.count("<audio"))
    check('class="voice-player"' in html_m and 'class="voice-dur"' in html_m, "HTML 播放器为辅助")
    check("This is a test voice message." in html_m, "HTML 无音频但识别文字仍在")
    check("[图片]" in txt_m and '[语音 3"]' in txt_m, 'TXT 有 [图片] / [语音 3"]')
    check('[语音 3"] 今天晚上终于可以画画了' in txt_m, "TXT 语音识别文字在同行")
    check('[语音 5"]' in txt_m, 'TXT 未转写语音退回 [语音 5"]')
    check('[语音 8"] This is a test voice message.' in txt_m, "TXT 非中文语音文字也在")
    check("[文件: 示例资料.7z (34.4 MB)]" in txt_m, "TXT 文件占位带名字+大小")
    check("[表情]" in txt_m and "[视频]" in txt_m, "TXT 有 [表情] / [视频]")
    check("[名片] 张三" in txt_m, "TXT 名片走 fallback")
    check("\U0001F5BC 图片" in md_m, "MD 有图片行")
    check("> \U0001F3A4 **媒体好友** · 10:02 · 语音 3\"" in md_m, "MD 语音行含说话人/时间/时长")
    check("> 今天晚上终于可以画画了" in md_m, "MD 语音识别文字另起引用块")
    check("> This is a test voice message." in md_m, "MD 非中文语音文字也在")
    check(md_m.count("\U0001F3A4") == 3, "MD 三条语音各一行")
    check("\U0001F4CE 文件: 示例资料.7z (34.4 MB)" in md_m, "MD 有文件行")
    check("[打开](media/" in md_m, "MD 本地媒体带相对链接")

    shot_m = _screenshot(d_m / f_by_fmt["html"], name="shot_media.png")
    check(shot_m is not None, "媒体 HTML 截图成功", str(shot_m) if shot_m else "失败")
    if shot_m:
        mag_px = _count_color(shot_m, MAGENTA, tol=40)
        if mag_px < 0:
            print("    (Pillow 不可用，跳过品红像素校验)")
        else:
            check(mag_px > 1000, "截图能看到品红图片（<img> 真渲染）", f"pixels={mag_px}")

    res_b = exporter.export(MEDIA_ACCOUNT, targets_m, ["html"],
                            opts={"media": True, "media_resolver": lambda aq, md: None})
    b = res_b["media"]
    check(b["copied"] == 0 and b["missing"] == exp_total,
          "全缺失：copied=0 / missing=total", f'{b["copied"]}/{b["missing"]}')
    html_b = (pathlib.Path(res_b["dir"]) / res_b["files"][0]["name"]).read_text(encoding="utf-8")
    check('src="media/' not in html_b, "全缺失 HTML 无 media/ 引用（不破图）")
    check("[图片·未缓存]" in html_b and "未转文字" in html_b, "全缺失显示占位徽章")
    check("今天晚上终于可以画画了" in html_b, "音频全缺失时语音文字仍在")
    check(not _html_ref_issues(html_b), "全缺失 HTML 仍无外部引用")

    res_c = exporter.export(MEDIA_ACCOUNT, targets_m, ["html"],
                            opts={"media": True, "media_max_mb": 0, "media_resolver": resolver})
    c = res_c["media"]
    check(c["truncated"] is True, "media_max_mb=0 -> truncated=True")
    check(c["copied"] == 0, "超限不复制任何媒体", c["copied"])
    check(c["missing"] == exp_total, "超限媒体按缺失处理", c["missing"])

    res_d = exporter.export(MEDIA_ACCOUNT, targets_m, ["html"], opts={"media": False})
    dd = res_d["media"]
    check(dd["total"] == 0 and dd["copied"] == 0, "media=False 不收集媒体", dd)
    check(res_d["count"] == 1, "media=False 只导出纯文本消息", res_d["count"])
    html_d = (pathlib.Path(res_d["dir"]) / res_d["files"][0]["name"]).read_text(encoding="utf-8")
    check("<audio" not in html_d and 'src="media/' not in html_d, "media=False HTML 不含媒体")

    saved_mod = exporter.media_mod
    try:
        exporter.media_mod = None
        res_e = exporter.export(MEDIA_ACCOUNT, targets_m, ["html"],
                                opts={"media": True, "media_root": str(media_root)})
    finally:
        exporter.media_mod = saved_mod
    e = res_e["media"]
    check(e["copied"] == exp_copied, "内置回退（无 core.media）也能复制媒体",
          f'{e["copied"]}/{exp_copied}')

    if real:
        store.upsert_account(REAL_ACCOUNT, label="真实媒体", source="pack")
        store.upsert_contacts([{"account_qq": REAL_ACCOUNT, "kind": "c2c",
                                "peer_id": "u_real_media", "peer_qq": 77777,
                                "name": "真实媒体好友", "remark": None, "source": "pack"}])
        rrows = _real_media_rows(REAL_ACCOUNT, "u_real_media", real)
        if rrows:
            store.insert_messages(rrows)
            store.refresh_contact_stats(REAL_ACCOUNT)
            res_f = exporter.export(REAL_ACCOUNT, [{"kind": "c2c", "peer_id": "u_real_media"}],
                                    ["html"], opts={"media": True})
            f = res_f["media"]
            check(f["copied"] >= 3, "core.media.resolve 命中真实缓存（copied>=3）",
                  f'{f["copied"]}/{f["total"]}')
            check(f["missing"] == 0, "真实缓存用例无缺失", f["missing"])

    store.upsert_contacts([{"account_qq": MEDIA_ACCOUNT, "kind": "c2c",
                            "peer_id": "u_media_mix", "peer_qq": 66666,
                            "name": "图文混合", "remark": None, "source": "pack"}])
    mix_md = {"kind": "image", "file": "Pic/2026-08/Ori/test_magenta.png",
              "name": "test_magenta.png", "size": 664}
    store.insert_messages([{
        "account_qq": MEDIA_ACCOUNT, "kind": "c2c", "peer_id": "u_media_mix",
        "peer_qq": 66666, "ts": int(datetime(2026, 8, 22, 9, 0).timestamp()),
        "direction": 1, "sender_qq": MEDIA_ACCOUNT, "sender_name": None,
        "msg_type": 2, "text": "带图文本一起发",
        "media": json.dumps(mix_md, ensure_ascii=False), "source": "pack",
    }])
    res_x = exporter.export(MEDIA_ACCOUNT, [{"kind": "c2c", "peer_id": "u_media_mix"}],
                            ["html", "txt", "md"],
                            opts={"media": True, "media_resolver": resolver})
    hx = (pathlib.Path(res_x["dir"]) / res_x["files"][0]["name"]).read_text(encoding="utf-8")
    tx = (pathlib.Path(res_x["dir"]) / [f["name"] for f in res_x["files"] if f["fmt"] == "txt"][0]).read_text(encoding="utf-8-sig")
    mx = (pathlib.Path(res_x["dir"]) / [f["name"] for f in res_x["files"] if f["fmt"] == "md"][0]).read_text(encoding="utf-8")
    check("带图文本一起发" in hx and 'src="media/' in hx, "HTML 文本+媒体同条都渲染")
    check("带图文本一起发" in tx and "[图片]" in tx, "TXT 文本+媒体同条都渲染")
    check("带图文本一起发" in mx and "\U0001F5BC 图片" in mx, "MD 文本+媒体同条都渲染")

    print("[9] 时间段 / 分类排序 / 头部信息 ...")
    base_ts = int(datetime(2026, 8, 13, 9, 0).timestamp())
    since_w = base_ts + 2 * 86400
    until_w = base_ts + 3 * 86400
    tgt_all = [{"kind": t["kind"], "peer_id": t["peer_id"]} for t in TARGETS]

    res_all = exporter.export(ACCOUNT, tgt_all, ["txt"], opts={"all_time": True})
    res_win = exporter.export(ACCOUNT, tgt_all, ["txt"],
                              opts={"since": since_w, "until": until_w})
    check(res_all["total"] == 360, "all_time 导出全量 360 条", res_all["total"])
    check(res_all["range"]["since"] is None and res_all["range"]["until"] is None,
          "all_time 忽略 since/until", res_all["range"])
    check(0 < res_win["total"] < 360, "指定时间段后条数变少", res_win["total"])
    check(res_win["range"]["since"] == since_w and res_win["range"]["until"] == until_w,
          "range 回显 since/until", f'{res_win["range"]["since"]}/{res_win["range"]["until"]}')
    check(res_win["range"]["count"] == res_win["total"], "range.count = total",
          f'{res_win["range"]["count"]}/{res_win["total"]}')
    check(since_w <= res_win["range"]["first_ts"] <= res_win["range"]["last_ts"] <= until_w,
          "导出消息都在时间段内",
          f'{res_win["range"]["first_ts"]}..{res_win["range"]["last_ts"]}')
    res_at = exporter.export(ACCOUNT, tgt_all, ["txt"],
                             opts={"all_time": True, "since": since_w, "until": until_w})
    check(res_at["total"] == 360 and res_at["range"]["since"] is None,
          "all_time=True 覆盖 since/until", res_at["total"])

    shuffled = [{"kind": "group", "peer_id": "888888"},
                {"kind": "c2c", "peer_id": "u_bob"},
                {"kind": "c2c", "peer_id": "u_alice"}]
    res_sorted = exporter.export(ACCOUNT, shuffled, ["html"], opts={"all_time": True})
    kinds = [f["kind"] for f in res_sorted["files"]]
    check(kinds == ["c2c", "c2c", "group"], "per_peer 文件顺序 c2c 在前 group 在后", kinds)
    tgt_order = [f'{t["kind"]}:{t["peer_id"]}' for t in res_sorted["targets"]]
    check(tgt_order == ["c2c:u_bob", "c2c:u_alice", "group:888888"],
          "同 kind 按消息数/最后时间稳定排序", tgt_order)

    res_merged = exporter.export(ACCOUNT, shuffled, ["html", "txt", "md"], mode="merged",
                                 opts={"all_time": True, "merged_name": "排序测试"})
    _validate(res_merged, 3)
    fmap = {f["fmt"]: f["name"] for f in res_merged["files"]}
    mdir = pathlib.Path(res_merged["dir"])
    hm = (mdir / fmap["html"]).read_text(encoding="utf-8")
    tm = (mdir / fmap["txt"]).read_text(encoding="utf-8-sig")
    mm = (mdir / fmap["md"]).read_text(encoding="utf-8")
    for label, txtv in (("HTML", hm), ("TXT", tm), ("MD", mm)):
        has_both = ("===== 私聊 =====" in txtv) and ("===== 群聊 =====" in txtv)
        check(has_both, f"merged {label} 有私聊/群聊分节")
        if has_both:
            check(txtv.index("===== 私聊 =====") < txtv.index("===== 群聊 ====="),
                  f"merged {label} 私聊排在群聊前")
    check(res_merged["count"] == 336 and res_merged["total"] == 360, "merged 分节后条数不变", "文本 %s / 全部 %s" % (res_merged["count"], res_merged["total"]))

    res_h = exporter.export(ACCOUNT, [{"kind": "c2c", "peer_id": "u_alice"}],
                            ["html", "txt", "md"], opts={"all_time": True})
    dh = pathlib.Path(res_h["dir"])
    fh = {f["fmt"]: f["name"] for f in res_h["files"]}
    hh = (dh / fh["html"]).read_text(encoding="utf-8")
    th = (dh / fh["txt"]).read_text(encoding="utf-8-sig")
    mh = (dh / fh["md"]).read_text(encoding="utf-8")
    check("类型：" in hh and "私聊" in hh, "HTML 头部有类型")
    check("QQ号/群号" in hh and "22222" in hh, "HTML 头部有 QQ号")
    check("备注：爱丽丝" in hh, "HTML 头部有备注")
    check("时间范围" in hh, "HTML 头部有时间范围")
    check("类型：私聊" in th, "TXT 头部有类型")
    check("QQ号/群号：22222" in th, "TXT 头部有 QQ号")
    check("对象：小爱（备注：爱丽丝）" in th, "TXT 头部昵称+备注")
    check("时间范围：" in th and "导出来源：" in th, "TXT 头部有时间范围/来源")
    check("| 类型 | 私聊 |" in mh, "MD 表格有类型")
    check("| QQ号/群号 | 22222 |" in mh, "MD 表格有 QQ号")
    check("| 备注 | 爱丽丝 |" in mh, "MD 表格有备注")
    check("| 时间范围 |" in mh, "MD 表格有时间范围")
    res_g = exporter.export(ACCOUNT, [{"kind": "group", "peer_id": "888888"}], ["txt"],
                            opts={"all_time": True})
    tg = (pathlib.Path(res_g["dir"]) / res_g["files"][0]["name"]).read_text(encoding="utf-8-sig")
    check("类型：群聊" in tg and "QQ号/群号：888888" in tg, "群聊头部类型/群号正确")

    passed = sum(1 for ok, _, _ in RESULTS if ok)
    total = len(RESULTS)
    print("-" * 72)
    print(f"断言结果：{passed}/{total} 通过")
    if passed != total:
        for ok, label, detail in RESULTS:
            if not ok:
                print("  FAIL:", label, detail)
        return 1
    print("全部通过。")
    print(f"验收产物目录：{d}")
    print(f"截图：{shot}")
    return 0


# ── 媒体导出自测 ───────────────────────────────────────────────────────────
MEDIA_ACCOUNT = 100002
REAL_ACCOUNT = 1605289411
MAGENTA = (255, 0, 200)


def _find_real_media():
    nd = paths.DEFAULT_DATA_ROOT / str(REAL_ACCOUNT) / "nt_qq" / "nt_data"
    out = {}
    if not nd.is_dir():
        return out, nd

    def first(*pats):
        for pat in pats:
            got = sorted(nd.glob(pat))
            if got:
                return got[0]
        return None

    cand = {
        "voice": first("Ptt/*/Ori/*.amr", "Ptt/**/*.amr"),
        "image": first("Pic/*/Ori/*.jpg", "Pic/*/Ori/*.png",
                       "Pic/**/*.jpg", "Pic/**/*.png"),
        "file": first("File/Thumb/*.jpg", "File/**/*.jpg", "File/**/*.png"),
        "sticker": first("Pic/**/*.png"),
        "video": first("Video/**/*_0.png", "Video/**/*.png"),
    }
    for k, v in cand.items():
        if v:
            out[k] = v
    return out, nd


def _write_png(path, w, h, rgb):
    import struct
    import zlib
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    row = bytes(rgb) * w
    raw = b"".join(b"\x00" + row for _ in range(h))
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 6))
                     + chunk(b"IEND", b""))


def _media_resolver(media_root):
    mr = pathlib.Path(media_root)

    def _r(account_qq, md):
        rel = md.get("file") or ""
        if rel:
            p = mr / rel
            if p.is_file():
                return str(p)
        return None

    return _r


def _media_fixture():
    root = TEST_DIR / "media_root"
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    real, nd = _find_real_media()
    specs = []

    def add(label, kind, rel, name, duration=0, size=None, fallback="",
            voice_text=None, voice_lang=None, voice_engine=None, missing=False):
        src = None if missing else real.get(kind)
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src is not None:
            shutil.copyfile(src, dst)
        actual = dst.stat().st_size if dst.exists() else 0
        specs.append({"label": label, "kind": kind, "rel": rel, "name": name,
                      "size": size if size is not None else actual,
                      "duration": duration, "fallback": fallback,
                      "voice_text": voice_text, "voice_lang": voice_lang,
                      "voice_engine": voice_engine})

    mag_rel = "Pic/2026-08/Ori/test_magenta.png"
    _write_png(root / mag_rel, 320, 200, MAGENTA)
    specs.append({"label": "magenta", "kind": "image", "rel": mag_rel,
                  "name": "test_magenta.png",
                  "size": (root / mag_rel).stat().st_size,
                  "duration": 0, "fallback": "", "voice_text": None,
                  "voice_lang": None, "voice_engine": None})
    add("voice-transcribed", "voice", "Ptt/2026-08/Ori/test_voice1.amr",
        "test_voice1.amr", duration=3, voice_text="今天晚上终于可以画画了",
        voice_lang="zh", voice_engine="whisper-small")
    add("voice-no-text", "voice", "Ptt/2026-08/Ori/test_voice2.amr",
        "test_voice2.amr", duration=5)
    add("voice-foreign-no-audio", "voice", "Ptt/2026-08/Ori/test_voice3.amr",
        "test_voice3.amr", duration=8, missing=True,
        voice_text="This is a test voice message.", voice_lang="en",
        voice_engine="whisper-small")
    add("image", "image", "Pic/2026-08/Ori/test_image.jpg", "test_image.jpg")
    add("file", "file", "File/Thumb/test_file_750.jpg", "示例资料.7z", size=36071014)
    add("sticker", "sticker", "Pic/2026-08/Ori/test_sticker.png", "test_sticker.png")
    add("video", "video", "Video/2026-08/Thumb/test_video_0.png", "视频封面.png")
    specs.append({"label": "missing-img", "kind": "image",
                  "rel": "Pic/2026-08/Ori/never_cached_zzz.png",
                  "name": "never_cached_zzz.png", "size": 0, "duration": 0,
                  "fallback": "", "voice_text": None, "voice_lang": None,
                  "voice_engine": None})
    specs.append({"label": "card", "kind": "card", "rel": None, "name": "",
                  "size": 0, "duration": 0, "fallback": "[名片] 张三",
                  "voice_text": None, "voice_lang": None, "voice_engine": None})
    return root, real, specs


def _media_rows(account_qq, peer_id, specs):
    ts0 = int(datetime(2026, 8, 20, 10, 0).timestamp())
    rows = [{
        "account_qq": account_qq, "kind": "c2c", "peer_id": peer_id,
        "peer_qq": 66666, "ts": ts0, "direction": 1, "sender_qq": account_qq,
        "sender_name": None, "msg_type": 2,
        "text": "媒体导出自测 | 下面依次是图片 / 语音 / 文件 / 表情 / 视频。",
        "source": "pack",
    }]
    for i, sp in enumerate(specs):
        md = {"kind": sp["kind"], "name": sp["name"], "size": sp["size"],
              "duration": sp["duration"]}
        if sp.get("rel"):
            md["file"] = sp["rel"]
        if sp.get("fallback"):
            md["fallback"] = sp["fallback"]
        for k in ("voice_text", "voice_lang", "voice_engine"):
            if sp.get(k):
                md[k] = sp[k]
        rows.append({
            "account_qq": account_qq, "kind": "c2c", "peer_id": peer_id,
            "peer_qq": 66666, "ts": ts0 + (i + 1) * 60,
            "direction": 1 if i % 2 == 0 else 0, "sender_qq": account_qq,
            "sender_name": None, "msg_type": 2, "text": "",
            "media": json.dumps(md, ensure_ascii=False), "source": "pack",
        })
    return rows


def _real_media_rows(account_qq, peer_id, real):
    from core import media as _m
    ts0 = int(datetime(2026, 8, 21, 10, 0).timestamp())
    rows = []
    for i, kind in enumerate([k for k in ("image", "voice", "file") if real.get(k)]):
        p = real[kind]
        rel = _m.rel_path(account_qq, p)
        if not rel:
            continue
        md = {"kind": kind, "file": rel, "name": p.name,
              "size": p.stat().st_size, "duration": 3 if kind == "voice" else 0}
        rows.append({
            "account_qq": account_qq, "kind": "c2c", "peer_id": peer_id,
            "peer_qq": 77777, "ts": ts0 + i * 60, "direction": 1,
            "sender_qq": account_qq, "sender_name": None, "msg_type": 2,
            "text": "", "media": json.dumps(md, ensure_ascii=False), "source": "pack",
        })
    return rows


def _count_color(png_path, rgb, tol=30):
    """统计截图里接近指定颜色的像素数；Pillow 不可用返回 -1。"""
    try:
        from PIL import Image
    except Exception:
        return -1
    try:
        im = Image.open(png_path).convert("RGB")
    except Exception:
        return -1
    n = 0
    px = im.load()
    w, h = im.size
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            c = px[x, y]
            if (abs(c[0] - rgb[0]) <= tol and abs(c[1] - rgb[1]) <= tol
                    and abs(c[2] - rgb[2]) <= tol):
                n += 1
    return n


if __name__ == "__main__":
    raise SystemExit(main())
