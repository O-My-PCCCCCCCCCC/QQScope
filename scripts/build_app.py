"""QQScope · 应用构建器（多账号）
读取 data/pack/<qq>/ 下各账号的数据包 → 注入 app/index.html → app/dist/QQScope.html
用法：uv run --project tools/nt_msg_db_util python scripts/build_app.py
"""
import json
import gzip
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_HTML = ROOT / "app" / "index.html"
ENGINE_JS = ROOT / "app" / "js" / "analysis.js"
DIST_DIR = ROOT / "app" / "dist"
PACK_DIR = ROOT / "data" / "pack"


def load_accounts():
    accounts = []
    for d in sorted(PACK_DIR.iterdir()):
        if not d.is_dir():
            continue
        gz = d / "messages.json.gz"
        meta_f = d / "meta.json"
        if not gz.exists() or not meta_f.exists():
            continue
        with gzip.open(gz, "rt", encoding="utf-8") as f:
            messages = json.load(f)
        meta = json.loads(meta_f.read_text(encoding="utf-8"))
        accounts.append({"qq": int(d.name), "messages": messages, "meta": meta})
    return accounts


def main():
    accounts = load_accounts()
    if not accounts:
        raise SystemExit("没有找到数据包，先运行 import_and_pack.py")
    engine_js = ENGINE_JS.read_text(encoding="utf-8")

    html = SRC_HTML.read_text(encoding="utf-8")
    accounts_js = json.dumps(accounts, ensure_ascii=False, separators=(",", ":"))
    html = html.replace("/*__QQSCOPE_ACCOUNTS__*/", accounts_js)
    html = html.replace("/*__QQSCOPE_ENGINE__*/", engine_js)

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    out = DIST_DIR / "QQScope.html"
    out.write_text(html, encoding="utf-8")
    print(f"[构建完成] {out}  ({out.stat().st_size / 1024 / 1024:.1f} MB)")
    for a in accounts:
        print(f"  账号 {a['qq']}（{a['meta'].get('label', '')}）：{len(a['messages'])} 条文本消息")


if __name__ == "__main__":
    main()
