"""QQScope · QZone 可行性探测脚本（真实发请求，证据落盘）。

用法（用项目自带 .venv Python，里面有 httpx）：
    tools\\nt_msg_db_util\\.venv\\Scripts\\python.exe scripts\\qzone_probe.py
    ... scripts\\qzone_probe.py --num 5
    ... scripts\\qzone_probe.py --cookie "uin=o123; p_skey=xxx"   # 只用于本地验证，不会落盘

探测结果（脱敏 URL / HTTP 状态 / 响应前 2KB / 结论）写入 data/qzone_probe/。
cookie / p_skey / skey 绝不写进证据文件或标准输出。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.sources import qzone


def _build_opts(args) -> dict:
    opts = {
        "uin": args.uin, "cookie": args.cookie, "p_skey": args.p_skey,
        "skey": args.skey, "base": args.base, "token": args.token, "num": args.num,
    }
    return {k: v for k, v in opts.items() if v not in ("", 0, None)}


def _write_evidence(res: dict) -> tuple[pathlib.Path, pathlib.Path]:
    out_dir = qzone.PROBE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    evidence = {
        "probed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "verdict": res.get("verdict"),
        "ok": res.get("ok"),
        "need_login": res.get("need_login"),
        "message": res.get("message"),
        "credential": res.get("credential"),
        "endpoints": res.get("endpoints"),
        "note": "cookie / p_skey / skey 已脱敏；g_tk 已脱敏。",
    }
    jp = out_dir / f"probe_{stamp}.json"
    jp.write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "latest.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = [
        f"QZone 可行性探测  {evidence['probed_at']}",
        "=" * 68,
        f"结论 verdict : {evidence['verdict']}",
        f"可用 ok      : {evidence['ok']}",
        f"需要登录     : {evidence['need_login']}",
        f"说明 message : {evidence['message']}",
        "",
        f"登录态来源   : {res.get('credential', {}).get('source')}",
        f"uin          : {res.get('credential', {}).get('uin')}",
        f"拿到 p_skey  : {res.get('credential', {}).get('has_p_skey')}",
        f"OneBot 尝试  : {res.get('credential', {}).get('onebot', {}).get('message') or '未尝试'}",
        "",
    ]
    for r in res.get("endpoints") or []:
        lines += [
            f"--- {r.get('host')} ---",
            f"URL(脱敏)    : {r.get('url_redacted')}",
            f"HTTP 状态    : {r.get('http_status')}",
            f"Content-Type : {r.get('content_type')}",
            f"耗时         : {r.get('elapsed')}s",
            f"接口 code    : {r.get('code')}",
            f"判定         : {r.get('verdict')}",
            f"说明         : {r.get('message')}",
            "响应前 2KB   :",
            str(r.get("body_preview") or ""),
            "",
        ]
    tp = out_dir / f"probe_{stamp}.txt"
    tp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jp, tp

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="QZone H5 动态接口可行性探测（真实请求）")
    ap.add_argument("--uin", type=int, default=0, help="账号 QQ；缺省用本地主库账号")
    ap.add_argument("--cookie", default="", help="完整 cookie（可选；仅本地验证，不会落盘）")
    ap.add_argument("--p_skey", default="", help="p_skey（可选）")
    ap.add_argument("--skey", default="", help="skey（可选）")
    ap.add_argument("--base", default="", help="OneBot 地址，如 http://127.0.0.1:3000")
    ap.add_argument("--token", default="", help="OneBot token")
    ap.add_argument("--num", type=int, default=5, help="每页条数（<=50）")
    ap.add_argument("--json", action="store_true", help="额外输出完整 JSON")
    args = ap.parse_args(argv)

    res = qzone.probe(_build_opts(args))
    jp, tp = _write_evidence(res)

    print("=" * 68)
    print("QZone 可行性探测（真实请求，证据已落盘）")
    print("=" * 68)
    print(f"结论 verdict : {res.get('verdict')}")
    print(f"可用 ok      : {res.get('ok')}")
    print(f"说明         : {res.get('message')}")
    cred = res.get("credential") or {}
    print(f"登录态来源   : {cred.get('source')}   uin={cred.get('uin')}   "
          f"p_skey={'有' if cred.get('has_p_skey') else '无'}")
    ob = cred.get("onebot") or {}
    if ob.get("tried"):
        state = "OK" if ob.get("ok") else "失败"
        print(f"OneBot 尝试  : {state} - {str(ob.get('message') or '')[:140]}")
    for r in res.get("endpoints") or []:
        print(f"  - {str(r.get('host')):<18} HTTP {r.get('http_status')}  "
              f"code={r.get('code')}  => {r.get('verdict')}")
        print(f"    {r.get('message')}")
    print(f"证据 JSON    : {jp}")
    print(f"证据 TXT     : {tp}")
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
