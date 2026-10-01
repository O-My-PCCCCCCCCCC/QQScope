"""QQScope · PC 后端服务
自动读取 QQ 数据 + 提供 REST API + 托管网页界面 + 局域网同步
运行：uv run --project ../tools/nt_msg_db_util python server.py   （或 python server.py）
访问：http://127.0.0.1:15555  （手机连局域网 IP 同端口）
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "app" / "dist" / "QQScope.html"
SETTINGS_FILE = ROOT / "data" / "server" / "settings.json"

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

import reader

app = FastAPI(title="QQScope", version="1.0.0")

DEFAULT_SETTINGS = {
    "data_root": "C:\\Users\\Administrator\\Documents\\Tencent Files",
    "port": 15555,
    "ai": {"provider": "deepseek", "base": "https://api.deepseek.com/v1", "key": "", "model": "deepseek-chat"},
}


def load_settings():
    if SETTINGS_FILE.exists():
        try:
            s = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            for k, v in DEFAULT_SETTINGS.items():
                s.setdefault(k, v)
            return s
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


def save_settings(s):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
def index():
    html = DIST.read_text(encoding="utf-8") if DIST.exists() else "<h1>请先构建前端 (build_app.py)</h1>"
    return HTMLResponse(html)


@app.get("/api/status")
def status():
    packs = reader.list_packs()
    return {
        "ok": True,
        "version": "1.0.0",
        "accounts": [{"qq": p.get("qq"), "label": p.get("label")} for p in packs],
        "data_root": load_settings().get("data_root"),
        "time": int(time.time()),
    }


@app.get("/api/accounts")
def accounts():
    packs = reader.list_packs()
    return [{
        "qq": p.get("qq"),
        "label": p.get("label"),
        "total_messages": p.get("total_messages"),
        "self_messages": p.get("self_messages"),
        "time_start": p.get("time_start"),
        "time_end": p.get("time_end"),
        "url": f"/api/accounts/{p.get('qq')}/data",
    } for p in packs]


@app.get("/api/accounts/{qq}/data")
def account_data(qq: int):
    import gzip
    d = reader.PACK_DIR / str(qq)
    if not (d / "meta.json").exists():
        return JSONResponse({"error": "账号数据不存在"}, status_code=404)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    gz = d / "messages.json.gz"
    jf = d / "messages.json"
    if gz.exists():
        with gzip.open(gz, "rt", encoding="utf-8") as f:
            messages = json.load(f)
    elif jf.exists():
        messages = json.loads(jf.read_text(encoding="utf-8"))
    else:
        messages = []
    return {"qq": qq, "messages": messages, "meta": meta}


@app.post("/api/refresh")
async def refresh():
    s = load_settings()
    result = reader.refresh(data_root=s.get("data_root"))
    return result


@app.post("/api/onebot/sync")
async def onebot_sync(request: Request):
    """从 Linux 框架（NapCat OneBot）拉取数据并生成数据包"""
    body = await request.json()
    base = (body.get("base") or "").strip().rstrip("/")
    token = (body.get("token") or "").strip()
    if not base:
        return JSONResponse({"error": "缺少 OneBot 地址"}, status_code=400)
    try:
        import onebot
        result = onebot.sync(base, token)
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/settings")
def get_settings():
    s = load_settings()
    s["ai"]["key"] = "******" if s["ai"]["key"] else ""
    return s


@app.post("/api/settings")
async def post_settings(request: Request):
    body = await request.json()
    s = load_settings()
    if "data_root" in body and isinstance(body["data_root"], str):
        s["data_root"] = body["data_root"]
    if "port" in body:
        s["port"] = int(body["port"])
    if "ai" in body and isinstance(body["ai"], dict):
        ai = s["ai"]
        for k in ("provider", "base", "model"):
            if k in body["ai"] and isinstance(body["ai"][k], str):
                ai[k] = body["ai"][k]
        if "key" in body["ai"] and isinstance(body["ai"]["key"], str):
            if body["ai"]["key"] and body["ai"]["key"] != "******":
                ai["key"] = body["ai"]["key"]
    save_settings(s)
    return {"ok": True}


@app.post("/api/ai/chat")
async def ai_chat(request: Request):
    """代理 AI 请求：API Key 只存在 PC 端，手机不需要持有"""
    body = await request.json()
    s = load_settings()
    ai = s.get("ai", {})
    if not ai.get("key"):
        return JSONResponse({"error": "服务端未配置 API Key（在设置中填写）"}, status_code=400)
    import httpx
    messages = body.get("messages") or []
    model = body.get("model") or ai.get("model", "deepseek-chat")
    url = ai.get("base", "https://api.deepseek.com/v1").rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": messages, "temperature": 0.7, "max_tokens": 1200}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(url, json=payload, headers={"Authorization": f"Bearer {ai['key']}"})
            r.raise_for_status()
            j = r.json()
        return {"content": j["choices"][0]["message"]["content"]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


# 静态：dist 目录下的其他资源（如有）
if DIST.parent.exists():
    app.mount("/dist", StaticFiles(directory=str(DIST.parent)), name="dist")


def main():
    s = load_settings()
    port = int(s.get("port", 15555))
    print(f"QQScope 后端启动: http://127.0.0.1:{port}  （局域网: http://<本机IP>:{port}）")
    print(f"数据目录: {s.get('data_root')}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
