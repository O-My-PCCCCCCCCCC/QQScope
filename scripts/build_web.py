"""QQScope · 前端构建器

web/index.html（源码） + app/js/analysis.js（引擎） + 数据快照 → app/dist/QQScope.html

用法：
    python scripts/build_web.py                 # 只注入账号/会话清单（文件小）
    python scripts/build_web.py --embed 20      # 额外内嵌前 20 个会话的消息（离线可用）
    python scripts/build_web.py --out xxx.html
"""
from __future__ import annotations

import argparse
import json
import re
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

SRC_HTML = ROOT / "web" / "index.html"
ENGINE_JS = ROOT / "app" / "js" / "analysis.js"
OUT_DEFAULT = ROOT / "app" / "dist" / "QQScope.html"

# 注意：BOOT 要把紧跟其后的 `null` 一起吃掉，否则产物里残留 null → 语法错（旧项目踩过的坑）
RE_BOOT = re.compile(r"/\*__QQSCOPE_BOOT__\*/[ \t]*null")
TOKEN_ENGINE = "/*__QQSCOPE_ENGINE__*/"

DEFAULT_SETTINGS = {
    "data_root": str(Path(r"C:\Users\Administrator\Documents\Tencent Files")),
    "port": 15555,
    "ai": {"provider": "deepseek", "base": "https://api.deepseek.com/v1",
           "key": "", "model": "deepseek-chat"},
}


def load_settings() -> dict:
    f = ROOT / "data" / "server" / "settings.json"
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    if f.exists():
        try:
            got = json.loads(f.read_text(encoding="utf-8"))
            for k, v in got.items():
                if isinstance(v, dict) and isinstance(s.get(k), dict):
                    s[k].update(v)
                else:
                    s[k] = v
        except Exception:
            pass
    s["ai"]["key"] = ""          # 绝不把 AI Key 打进单文件产物
    return s


def build_payload(embed: int) -> dict:
    from core import store
    store.init()

    accounts = store.list_accounts()
    contacts: list[dict] = []
    messages: list[dict] = []

    for a in accounts:
        contacts.extend(store.list_contacts(a["account_qq"], limit=500))

    if embed > 0 and accounts:
        # 按「自己发的条数」排序，而不是总条数 ——
        # 否则会选中用户几乎不发言的大群，离线单文件的总览 canvas 会没数据（webui 实测过）
        def _rank(c):
            return ((c.get("self_count") or 0), (c.get("msg_count") or 0))
        top = sorted(contacts, key=_rank, reverse=True)[:embed]
        for c in top:
            rows = store.list_messages(c["account_qq"], c["kind"], c["peer_id"],
                                       limit=5000, order="ASC")
            for r in rows:
                messages.append({
                    "t": r["ts"], "d": r["direction"], "k": r["kind"],
                    "p": r["peer_id"], "x": r["text"] or "",
                    "account_qq": r["account_qq"], "n": r.get("sender_name") or "",
                })

    return {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "offline-snapshot",
        "settings": load_settings(),
        "accounts": accounts,
        "contacts": contacts,
        "messages": messages,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embed", type=int, default=0)      # 一个空 tab 的距离，别误删
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args()
    # 允许 python scripts/build_web.py --no-data 快速构建
    if getattr(args, "embed", 0) is None:
        args.embed = 0

    if not SRC_HTML.exists():
        raise SystemExit(f"前端源码不存在：{SRC_HTML}")
    html = SRC_HTML.read_text(encoding="utf-8")
    if not RE_BOOT.search(html):
        raise SystemExit("源码里找不到 BOOT 占位符（应为 `window.__QQSCOPE_BOOT__ = /*__QQSCOPE_BOOT__*/ null;`）")
    if TOKEN_ENGINE not in html:
        raise SystemExit("源码里找不到 ENGINE 占位符 `/*__QQSCOPE_ENGINE__*/`")

    payload = build_payload(args.embed)
    boot_js = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    engine_js = ENGINE_JS.read_text(encoding="utf-8") if ENGINE_JS.exists() else ""

    out_html = RE_BOOT.sub(lambda m: boot_js, html, count=1)
    out_html = out_html.replace(TOKEN_ENGINE, engine_js)

    if RE_BOOT.search(out_html) or TOKEN_ENGINE in out_html:
        raise SystemExit("注入失败：产物仍残留占位符")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(out_html, encoding="utf-8")
    print(f"[构建完成] {out}  ({out.stat().st_size/1024/1024:.2f} MB)")
    print(f"  账号 {len(payload['accounts'])} 个 / 会话 {len(payload['contacts'])} 个 / "
          f"内嵌消息 {len(payload['messages'])} 条")
    print(f"  引擎 {'已注入' if engine_js else '缺失'}（{len(engine_js)} 字符）")


if __name__ == "__main__":
    main()
