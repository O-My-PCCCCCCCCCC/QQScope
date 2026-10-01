#!/usr/bin/env node
/* v10 验收用 CDP 驱动（真实时间，不受 --virtual-time-budget 影响）：
 * 启动无头 Edge → 等页面条件成立 → 可选执行一段注入脚本 → 截图 + 收集 JS 报错。
 * 用法：
 *   node v10_cdp.mjs --url <url> --out <png> [--wait "<js布尔表达式>"] [--timeout 60000]
 *                    [--eval "<js>"] [--size 1440x2000] [--settle 1200]
 * 输出：JSON 摘要（title / errors / jsLog / waitResult / png）
 */
import { spawn, execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
function arg(name, def) {
  const i = process.argv.indexOf("--" + name);
  return i >= 0 && process.argv[i + 1] != null ? process.argv[i + 1] : def;
}
const URL = arg("url");
const OUT = arg("out");
const WAIT = arg("wait", "true");
const TIMEOUT = Number(arg("timeout", "60000"));
const EVAL_JS = arg("eval", "");
const SIZE = arg("size", "1440x2000");
const SETTLE = Number(arg("settle", "1000"));
if (!URL || !OUT) { console.error("缺少 --url / --out"); process.exit(2); }

const port = 19000 + Math.floor(Math.random() * 900);
const profile = path.join(os.tmpdir(), "qqscope-cdp-" + Date.now());
const edge = spawn(EDGE, [
  "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
  "--no-default-browser-check", "--disable-extensions", "--mute-audio",
  "--user-data-dir=" + profile, "--window-size=" + SIZE.replace("x", ","),
  "--remote-debugging-port=" + port, URL
], { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let ws = null;
try {
  let target = null;
  for (let i = 0; i < 80 && !target; i++) {
    await sleep(300);
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      const want = URL.split("#")[0];
      const pages = list.filter((t) => t.type === "page" && (t.url.indexOf("http") === 0 || t.url.indexOf("file:") === 0));
      target = pages.find((t) => t.url === URL) || pages.find((t) => t.url.split("#")[0] === want) || pages[0];
    } catch (e) {}
  }
  if (!target) throw new Error("找不到 CDP page target");
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let seq = 0;
  const pending = new Map();
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  };
  const send = (method, params) => new Promise((res) => {
    const id = ++seq;
    pending.set(id, res);
    ws.send(JSON.stringify({ id, method, params: params || {} }));
  });
  const evalJs = async (expr, awaitPromise) => {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: !!awaitPromise });
    if (r.result && r.result.exceptionDetails) return null;
    return r.result && r.result.result ? r.result.result.value : null;
  };
  await send("Runtime.enable");
  await send("Page.enable");

  const t0 = Date.now();
  let waitResult = null, done = false;
  while (Date.now() - t0 < TIMEOUT) {
    waitResult = await evalJs(WAIT);
    if (waitResult) { done = true; break; }
    await sleep(400);
  }
  if (EVAL_JS) await evalJs(EVAL_JS);
  await sleep(SETTLE);

  const title = await evalJs("document.title");
  const errors = await evalJs("document.documentElement.getAttribute('data-js-errors')");
  const jsLog = await evalJs("(document.getElementById('jsErrorLog')||{}).textContent || ''");
  const extra = await evalJs("JSON.stringify({gate: (document.getElementById('loginGate')||{}).className||'', gateInner:(document.getElementById('loginGate')||{}).innerHTML.length||0, confirm:(document.getElementById('confirmScrim')||{}).className||'', canvas: (document.getElementById('holoCanvas')||{}).className||'', holo: window.__QQSCOPE_HOLO__ ? window.__QQSCOPE_HOLO__.state() : null, login: window.__QQSCOPE_LOGIN__ ? window.__QQSCOPE_LOGIN__.state() : null, authed: localStorage.getItem('qqscope_authed'), theme: localStorage.getItem('qqscope_theme'), remarkKeys: Object.keys(localStorage).filter(function(k){return k.indexOf('qqscope_remark_')===0;}).length, authKeys: Object.keys(localStorage).filter(function(k){return k.indexOf('qqscope_authed')===0;}), enterMs: (window.__QQSCOPE_ENTER_MS__||null), overviewMs: (window.__QQSCOPE_OVERVIEW_MS__||null), holoReadyMs: (window.__QQSCOPE_HOLO_READY_MS__||null), navLoadMs: (function(){try{var n=performance.getEntriesByType('navigation')[0]||{};return Math.round(n.loadEventEnd||n.domContentLoadedEventEnd||0);}catch(e){return null;}})(), navDclMs: (function(){try{var n=performance.getEntriesByType('navigation')[0]||{};return Math.round(n.domContentLoadedEventEnd||0);}catch(e){return null;}})()})");
  const live = await evalJs("fetch('/api/live/status').then(function(r){return r.json();}).then(function(j){return 'running='+j.running;}).catch(function(e){return 'ERR '+e;})", true);
  const shot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
  if (shot.result && shot.result.data) fs.writeFileSync(OUT, Buffer.from(shot.result.data, "base64"));
  console.log(JSON.stringify({ ok: done, waitedMs: Date.now() - t0, waitResult, title, errors, jsLog, extra, live, png: fs.existsSync(OUT) ? fs.statSync(OUT).size : 0 }, null, 1));
} catch (e) {
  console.log(JSON.stringify({ ok: false, error: String(e && e.message || e) }));
} finally {
  try { if (ws) ws.close(); } catch (e) {}
  try { execSync("taskkill /PID " + edge.pid + " /T /F", { stdio: "ignore" }); } catch (e) {}
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) {}
}
