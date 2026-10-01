#!/usr/bin/env node
/* QQScope v16 · 多账号隔离「前端 DOM」复核
 *
 * 自己起一个真实双账号副本后端（scripts/isolation_serve.py, 默认 :15557，只停自起 PID），
 * 用无头 Edge + CDP 打开构建产物 http://127.0.0.1:<port>/?authed=1：
 *   - A 视角逐页 dump DOM -> 必须出现 A·/A#、零 B·/B#
 *   - 切到 B -> 必须重新拉数据、DOM 变化、出现 B·/B#、零 A·/A#
 *   - localStorage 无「不含账号维度」的业务键；data-js-errors=0
 *
 * 用法：node check_v16_dom.mjs [--port 15557] [--python <python.exe>]
 */
import { spawn, execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..", "..");
function arg(name, def) {
  const i = process.argv.indexOf("--" + name);
  return i >= 0 && process.argv[i + 1] != null ? process.argv[i + 1] : def;
}
const PORT = Number(arg("port", process.env.ISO_PORT || 15557));
const PY = arg("python", process.env.QQSCOPE_PY ||
  path.join(root, "tools", "nt_msg_db_util", ".venv", "Scripts", "python.exe"));
const EDGE_CANDS = [
  process.env.MSEDGE,
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].filter(Boolean);
const EDGE = EDGE_CANDS.find((p) => { try { return fs.existsSync(p); } catch (e) { return false; } });
const URL = `http://127.0.0.1:${PORT}/?authed=1`;
const A_QQ = "1605289411", B_QQ = "3060648699";

let passes = 0, fails = 0;
const ok = (n, c, d) => { if (c) { passes++; console.log(`  PASS  ${n}`); } else { fails++; console.log(`  FAIL  ${n}${d ? "  -> " + d : ""}`); } };
const section = (t) => console.log(`\n[${t}]`);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

if (!EDGE) { console.error("找不到 msedge.exe"); process.exit(2); }

// 端口占用保护：若已有后端在听，绝不乱杀，直接退出
try {
  const r = await fetch(`http://127.0.0.1:${PORT}/api/health`);
  if (r.ok) { console.error(`[v16dom] 端口 ${PORT} 已有服务在听，拒绝启动以免误杀别人的进程`); process.exit(2); }
} catch (e) { /* 端口空闲，正常 */ }

const srv = spawn(PY, [path.join(root, "scripts", "isolation_serve.py"), "--port", String(PORT)], { stdio: ["ignore", "pipe", "pipe"] });
let srvLog = "", srvReady = false;
srv.stdout.on("data", (d) => { srvLog += String(d); if (srvLog.includes("ISO_READY")) srvReady = true; });
srv.stderr.on("data", (d) => { srvLog += String(d); });
for (let i = 0; i < 240 && !srvReady; i++) await wait(250);
if (!srvReady) { console.error("[v16dom] 后端未就绪：" + srvLog.slice(-2000)); try { execSync(`taskkill /PID ${srv.pid} /T /F`, { stdio: "ignore" }); } catch (e) {} process.exit(1); }
const srvTmp = (srvLog.match(/ISO_READY\s+(\S+)/) || [])[1] || "";
console.log("[v16dom] 双账号后端已就绪 :" + PORT + (srvTmp ? "  tmp=" + srvTmp : ""));

const debugPort = 19000 + Math.floor(Math.random() * 900);
const profile = path.join(os.tmpdir(), "qqscope-dom16-" + Date.now());
const edge = spawn(EDGE, [
  "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
  "--no-default-browser-check", "--disable-extensions", "--mute-audio",
  "--user-data-dir=" + profile, "--window-size=1440,2200",
  "--remote-debugging-port=" + debugPort, URL,
], { stdio: "ignore" });

let ws = null;
try {
  let target = null;
  for (let i = 0; i < 100 && !target; i++) {
    await wait(300);
    try {
      const list = await (await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json();
      const pages = list.filter((t) => t.type === "page" && t.url.indexOf("http") === 0);
      target = pages.find((t) => t.url === URL) || pages[0];
    } catch (e) {}
  }
  if (!target) throw new Error("找不到 CDP page target");
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let seq = 0;
  const pending = new Map();
  ws.onmessage = (m) => { const j = JSON.parse(m.data); if (j.id && pending.has(j.id)) { pending.get(j.id)(j); pending.delete(j.id); } };
  const send = (method, params) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params: params || {} })); });
  const ev = async (expr, awaitPromise) => {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: !!awaitPromise });
    if (r.result && r.result.exceptionDetails) return undefined;
    return r.result && r.result.result ? r.result.result.value : undefined;
  };
  const waitFor = async (expr, ms) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) { if (await ev(expr)) return true; await wait(300); }
    return false;
  };
  await send("Runtime.enable");
  await send("Page.enable");

  await ev("try{window.__QQSCOPE_LOGIN__.showGate=function(){};}catch(e){}");
  const gotAcc = await waitFor("(function(){var s=document.getElementById('accountSelect');return !!(s&&s.options&&s.options.length>=2);})()", 30000);
  ok("前端加载出 A/B 两个账号", gotAcc);

  async function selectAccount(qq) {
    await ev(`(function(){var s=document.getElementById('accountSelect');if(s){s.value='${qq}';s.dispatchEvent(new Event('change'));}return true;})()`);
    await waitFor(`((document.getElementById('unitCode')||{}).textContent||'')==='QQS-${qq}'`, 15000);
    await wait(1800);
    await ev("try{document.body.classList.remove('gate-mode');}catch(e){}");
  }

  async function dumpPage(page) {
    await ev("try{document.body.classList.remove('gate-mode');window.__QQSCOPE_LOGIN__.showGate=function(){};}catch(e){}");
    await ev(`location.hash='#${page}'`);
    await wait(400);
    await waitFor(`!!document.querySelector('.view.on[data-page="${page}"]')`, 8000);
    if (page === "sessions") {
      await waitFor("document.querySelectorAll('#convItems .conv-item').length>0", 20000);
      await wait(600);
      const list = await ev("(document.getElementById('convItems')||{}).innerHTML||''");
      await ev("var it=document.querySelector('#convItems .conv-item');if(it)it.click();");
      await wait(2000);
      const bub = await ev("(document.getElementById('bubbleList')||{}).innerHTML||''");
      return list + "\n===BUBBLES===\n" + bub;
    }
    if (page === "overview") await wait(3000);
    else if (page === "feeds") await wait(2500);
    else if (page === "ai") await wait(3500);
    else if (page === "export") await wait(2000);
    else await wait(900);
    return (await ev("(document.querySelector('.view.on')||{}).innerHTML||''")) || "";
  }

  const PAGES = ["sessions", "overview", "feeds", "export", "ai", "settings"];
  const hasB = (s) => s.includes("B·") || s.includes("B#");
  const hasA = (s) => s.includes("A·") || s.includes("A#");

  section("v16 前端 DOM：A 视角（先选 A）");
  await selectAccount(A_QQ);
  const unitA = await ev("(document.getElementById('unitCode')||{}).textContent||''");
  let omniA = "";
  for (const p of PAGES) { const h = await dumpPage(p); omniA += "\n" + h; }
  ok("A 视角 unitCode=QQS-A", unitA === "QQS-" + A_QQ, unitA);
  ok("A 视角 DOM 出现 A·/A#（正对照）", hasA(omniA), "未见 A 标记");
  ok("A 视角 DOM 零 B·/B#（无 B 数据）", !hasB(omniA),
     "泄漏样本=" + ((omniA.match(/.{0,24}B[·#].{0,24}/) || [""])[0]));

  section("v16 前端 DOM：切到 B 必须重拉 + 清旧 DOM");
  const convA = await ev("(document.getElementById('convItems')||{}).innerHTML||''");
  await selectAccount(B_QQ);
  await ev("location.hash='#sessions'"); await wait(2000);
  await waitFor("document.querySelectorAll('#convItems .conv-item').length>0", 20000);
  const convB = await ev("(document.getElementById('convItems')||{}).innerHTML||''");
  const unitB = await ev("(document.getElementById('unitCode')||{}).textContent||''");
  ok("切账号后 unitCode=QQS-B", unitB === "QQS-" + B_QQ, unitB);
  ok("切账号后会话列表 DOM 变化（真的重拉）", !!convA && !!convB && convA !== convB,
     `lenA=${convA.length} lenB=${convB.length}`);
  ok("切账号后旧 DOM 被清（B 列表含 B·，不再等于 A 快照）", convB.includes("B·") && !convB.includes("A·"), "");

  section("v16 前端 DOM：B 视角逐页");
  let omniB = "";
  for (const p of PAGES) { const h = await dumpPage(p); omniB += "\n" + h; }
  ok("B 视角 DOM 出现 B·/B#（正对照）", hasB(omniB), "未见 B 标记");
  ok("B 视角 DOM 零 A·/A#（无 A 数据）", !hasA(omniB),
     "泄漏样本=" + ((omniB.match(/.{0,24}A[·#].{0,24}/) || [""])[0]));

  section("v16 前端 DOM：localStorage / JS 错误");
  const keys = await ev("JSON.stringify(Object.keys(localStorage))");
  let arr = [];
  try { arr = JSON.parse(keys || "[]"); } catch (e) {}
  const allow = new Set(["qqscope_theme", "qqscope_auto_refresh", "qqscope_refresh_sec",
    "qqscope_autosync", "qqscope_autosync_pack", "qqscope_autosync_bot",
    "qqscope_authed", "qqscope_authed_nick"]);
  const bad = arr.filter((k) => {
    if (allow.has(k)) return false;
    if (k.indexOf("qqscope_remark_") === 0) return !/^qqscope_remark_\d+_/.test(k);
    return true;
  });
  ok("localStorage 无「不含账号维度」的业务键", bad.length === 0, "可疑键=" + JSON.stringify(bad));
  const remarkKeys = arr.filter((k) => k.indexOf("qqscope_remark_") === 0);
  ok("remark 键均带账号维度", remarkKeys.every((k) => /^qqscope_remark_\d+_/.test(k)),
     JSON.stringify(remarkKeys));
  const jsErr = await ev("document.documentElement.getAttribute('data-js-errors')");
  ok("data-js-errors = 0", jsErr === "0" || jsErr === null || jsErr === undefined, String(jsErr));

} catch (e) {
  console.log("[v16dom] 异常：" + (e && e.message || e));
  fails++;
} finally {
  try { if (ws) ws.close(); } catch (e) {}
  try { execSync(`taskkill /PID ${edge.pid} /T /F`, { stdio: "ignore" }); } catch (e) {}
  try { execSync(`taskkill /PID ${srv.pid} /T /F`, { stdio: "ignore" }); } catch (e) {}
  try { if (srvTmp) fs.rmSync(srvTmp, { recursive: true, force: true }); } catch (e) {}
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) {}
}

console.log(`\n===== check_v16_dom 结果：PASS ${passes} / FAIL ${fails} =====`);
process.exit(fails ? 1 : 0);