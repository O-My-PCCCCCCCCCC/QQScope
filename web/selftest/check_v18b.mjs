#!/usr/bin/env node
/* QQScope v18b 断言：退出登录必须真的退出 + 新增「全部退出」（web+框架一起）
 * 用法：node check_v18b.mjs [web/index.html]
 *  - 源码断言（按钮/动作序列/失败红字/二次确认文案）
 *  - 假后端 + 无头 Edge 抓包：live/stop -> logout{force:true} 顺序、回连接门、DOM 清空、jsErr=0
 *  - 失败路径：假后端返回 external/can_force:false -> 断言红色错误常驻且含原因
 * 绝不触碰真实 15555 / NapCat。
 */
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { spawn, execSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..", "..");
const SRC = process.argv.slice(2).filter((a) => a.indexOf("--") !== 0)[0] || path.join(root, "web", "index.html");
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
let passes = 0, fails = 0, skips = 0;
const ok = (n, c, d) => { if (c) { passes++; console.log(`  PASS  ${n}`); } else { fails++; console.log(`  FAIL  ${n}${d ? "  -> " + d : ""}`); } };
const skip = (n, why) => { skips++; console.log(`  SKIP  ${n}  -> ${why}`); };
const section = (t) => console.log(`\n[${t}]`);
const src = fs.readFileSync(SRC, "utf8");

function fnBody(text, name) {
  const start = text.indexOf("function " + name + "(");
  if (start < 0) return "";
  const open = text.indexOf("{", start);
  if (open < 0) return "";
  let depth = 0, j = open;
  for (; j < text.length; j++) {
    const c = text[j];
    if (c === "{") depth++;
    else if (c === "}") { depth--; if (depth === 0) { j++; break; } }
    else if (c === '"' || c === "'" || c === "`") { const q = c; j++; while (j < text.length && text[j] !== q) { if (text[j] === "\\") j++; j++; } }
  }
  return text.slice(start, j);
}

const fullBody = fnBody(src, "fullExitConfirm");
const doBody = fnBody(src, "doLogout");
const failBody = fnBody(src, "showLogoutFailure") + fnBody(src, "renderLogoutFailure");
const logoutBody = fnBody(src, "logoutUser");
const stopBody = fnBody(src, "stopFramework");
const prepBody = fnBody(src, "prepareLogout");
const resetBody = fnBody(src, "resetAppData");
const enterBody = fnBody(src, "enterApp");
const pollBody = fnBody(src, "poll");

section("v18b 源码：显眼的「全部退出」入口");
ok("#btnFullExit + #btnGateFullExit 两个入口", /id="btnFullExit"/.test(src) && /id="btnGateFullExit"/.test(src));
const feTag = (src.match(/<button[^>]*id="btnFullExit"[^>]*>[\s\S]{0,40}?<\/button>/) || [""])[0];
ok("按钮文案「全部退出」且 danger 样式（显眼）", /danger/.test(feTag) && /全部退出/.test(feTag), feTag.slice(0, 90));
ok("主界面进入后显示：enterApp 显示 btnFullExit", /btnFullExit[\s\S]{0,80}removeAttribute\("hidden"\)/.test(enterBody));
ok("resetAppData 隐藏 btnFullExit", /btnFullExit[\s\S]{0,80}setAttribute\("hidden"/.test(resetBody));
ok("连接门也绑定「全部退出」", /btnGateFullExit[\s\S]{0,80}fullExitConfirm/.test(src));

section("v18b 源码：全部退出 = 停采集 + logout{force:true} + 清展示层 + 回门");
ok("fullExitConfirm 存在且走 openConfirm（二次确认）", /function fullExitConfirm/.test(src) && /openConfirm\(/.test(fullBody));
ok("全部退出 onOk: resetAppData + doLogout(true)", /resetAppData\(\)/.test(fullBody) && /doLogout\(true\)/.test(fullBody));
ok("doLogout 先 POST /api/live/stop 再发 logout", doBody.indexOf("/api/live/stop") >= 0 && doBody.indexOf("requestFrameworkLogout") > doBody.indexOf("/api/live/stop"));
ok("框架退出走 /api/framework/logout{force}", /apiPost\(\s*["']\/api\/framework\/logout["']/.test(src) && /force:\s*!!force/.test(src));
ok("回连接门：prepareLogout 锁门 showGate", /entered\s*=\s*false/.test(prepBody) && /manualLock\s*=\s*true/.test(prepBody) && /showGate\(/.test(prepBody));
ok("清展示层复用 resetAppData", /resetAppData\(/.test(src));

section("v18b 源码：退出登录一次到位（external+can_force -> 自动 force）");
ok("doLogout 处理 external 且按 can_force 自动升级", /"external"/.test(doBody) && /can_force/.test(doBody) && /requestFrameworkLogout\(true\)/.test(doBody));
ok("升级前有明确提示（不静默）", /正在强制结束框架/.test(doBody));
ok("仍保留二次确认（logoutUser -> openConfirm -> doLogout(false)）", /openConfirm\(/.test(logoutBody) && /doLogout\(false\)/.test(logoutBody));
ok("保留原「退出登录」入口", /id="btnLogout"/.test(src) && /logoutUser/.test(src));

section("v18b 源码：失败必须红字 + 原因 + 手动办法");
ok("失败渲染 red 横幅（conn-banner err）", /conn-banner err/.test(failBody));
ok("失败文案含「失败 / 原因 / 手动处理」", /退出登录失败/.test(failBody) && /reason/.test(failBody) && /manual/.test(failBody));
ok("失败态常驻：poll 里 logoutFail -> renderLogoutFailure", /logoutFail/.test(pollBody) && /renderLogoutFailure\(\)/.test(pollBody));
ok("失败详情落到 loginHint（不只一条横幅）", /setHint\(/.test(failBody) && /true\)/.test(failBody));

section("v18b 源码：文案含「框架」「数据保留」");
const mFull = src.match(/var FULL_EXIT_COPY\s*=\s*([\s\S]*?);\s*\n/);
const fullCopy = mFull ? mFull[1] : "";
const mExit = src.match(/var EXIT_COPY\s*=\s*([\s\S]*?);\s*\n/);
const exitCopy = mExit ? mExit[1] : "";
ok("「全部退出」文案含「框架」", /框架/.test(fullCopy));
ok("「全部退出」文案含「数据保留」（保留+不会被清除）", /数据/.test(fullCopy) && /保留/.test(fullCopy) && /不会被清除/.test(fullCopy));
ok("「退出登录」文案同样含框架+数据保留", /框架/.test(exitCopy) && /保留/.test(exitCopy));
ok("「退出并停止框架」也走 FULL 文案", /body:\s*FULL_EXIT_COPY/.test(stopBody));
const stopCount = (src.match(/apiPost\("\/api\/live\/stop"/g) || []).length;
ok("live/stop 仍是 2 处（v17 约束不回退）", stopCount === 2, "count=" + stopCount);

/* ────────────────────────── 假后端 + 无头 Edge ────────────────────────── */
const METRICS = `(function(){var g=function(id){return document.getElementById(id)||{};};
var L=window.__QQSCOPE_LOGIN__?window.__QQSCOPE_LOGIN__.state():{};
var fe=document.getElementById('btnFullExit');
return JSON.stringify({entered:!!L.entered,manualLock:!!L.manualLock,
gateHidden:(g('loginGate').classList||{contains:function(){return true;}}).contains('hidden'),
convItemCount:document.querySelectorAll('.conv-item').length,
bubbleLen:(g('bubbleList').innerHTML||'').length,
authChipHidden:((g('authChip').className||'').indexOf('hidden')>=0),
fullExitHidden:!!(fe&&fe.hasAttribute('hidden')),
gateFullExit:!!document.getElementById('btnGateFullExit'),
bannerClass:(g('connBanner').className||''),
bannerHidden:((g('connBanner').className||'').indexOf('hidden')>=0),
bannerText:(g('connBanner').textContent||'').replace(/\\s+/g,' ').slice(0,340),
hintText:(g('loginHint').textContent||'').replace(/\\s+/g,' ').slice(0,340),
errors:document.documentElement.getAttribute('data-js-errors')});})()`;

const CONTACTS = [
  { account_qq: 1605289411, kind: "c2c", peer_id: "u_1001", peer_qq: 1001, name: "同桌", msg_count: 120, last_ts: 1786000000, last_text: "今天有点累" },
  { account_qq: 1605289411, kind: "c2c", peer_id: "u_1002", peer_qq: 1002, name: "李四", msg_count: 88, last_ts: 1785999000, last_text: "收到" },
  { account_qq: 1605289411, kind: "group", peer_id: "30001", peer_qq: 30001, name: "摸鱼群", msg_count: 161, last_ts: 1785998000, last_text: "哈哈哈哈" }
];
const MESSAGES = [0, 1, 2].map((i) => ({ account_qq: 1605289411, ts: 1786000000 - i * 60, direction: i ? 1 : 0, kind: "c2c", peer_id: "u_1001", peer_qq: 1001, text: ["今天有点累", "那就早点睡", "晚安"][i], sender_name: "同桌" }));

function startFake(mode) {
  const state = { loggedIn: true, reqs: [] };
  const server = http.createServer(async (req, res) => {
    const u = new URL(req.url, "http://localhost");
    const p = u.pathname;
    const send = (code, obj) => { res.writeHead(code, { "Content-Type": "application/json; charset=utf-8" }); res.end(JSON.stringify(obj)); };
    const readBody = () => new Promise((r) => { let b = ""; req.on("data", (c) => (b += c)); req.on("end", () => { let j = {}; try { j = JSON.parse(b || "{}"); } catch (e) {} r(j); }); });
    try {
      if (p === "/" || p === "/index.html") { res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" }); return res.end(fs.readFileSync(SRC)); }
      if (p === "/api/health") return send(200, { ok: true, version: "fake-v18b" });
      if (p === "/api/login/status") return send(200, { framework_running: true, logged_in: state.loggedIn, account: state.loggedIn ? 1605289411 : 0, nickname: state.loggedIn ? "霖ケ" : "", pid: 39760, can_stop: false, can_force: true, spawned_pid: 0, sync_allowed: state.loggedIn, account_in_store: true, qr: { available: !state.loggedIn, mtime: 1, url: "/api/framework/qrcode" }, message: state.loggedIn ? "已登录：霖ケ" : "等待扫码登录…" });
      if (p === "/api/accounts") return send(200, { accounts: [{ account_qq: 1605289411, label: "霖ケ", message_count: 369, contacts_count: 3, self_messages: 228, first_ts: 1785800000, last_ts: 1786000000, source: "snapshot" }] });
      if (p === "/api/overview") return send(200, { total: 369, self: 228, c2c: 208, group: 161, contacts: 3, first_ts: 1785800000, last_ts: 1786000000, daily: [], hour_hist: [], top_contacts: [] });
      if (p === "/api/report") return send(200, { overview: { total_messages: 369, self_messages: 228, active_days: 3, avg_per_day: 123, avg_len: 12, night_ratio: 4 }, sentiment: { avg_senti: 0.1, pos_hits: 2, neg_hits: 1 }, daily: [], hour_hist: [], peers: [], top_pos_words: [], top_neg_words: [] });
      if (p === "/api/contacts" && req.method === "GET") return send(200, { contacts: CONTACTS });
      if (p === "/api/messages") { const kind = u.searchParams.get("kind") || "", peer = u.searchParams.get("peer_id") || ""; const list = MESSAGES.filter((m) => (!kind || m.kind === kind) && (!peer || m.peer_id === peer)); return send(200, { messages: list, total: list.length, contact: CONTACTS.find((c) => c.kind === kind && c.peer_id === peer) || null }); }
      if (p === "/api/settings") return send(200, { data_root: "C:\\Users\\x", port: 15555, ai: {} });
      if (p === "/api/sources") return send(200, { sources: [{ id: "pack", name: "数据包读取", kind: "pack", ready: true, message: "就绪" }, { id: "bot", name: "机器人框架读取", kind: "bot", ready: true, message: "已连接" }] });
      if (p === "/api/live/stop") { state.reqs.push({ url: p, body: await readBody() }); return send(200, { ok: true }); }
      if (p === "/api/framework/logout") {
        const b = await readBody();
        state.reqs.push({ url: p, body: b });
        if (mode === "ok") { state.loggedIn = false; return send(200, { ok: true, mode: "force_stop", pid: 39760, message: "已退出 QQ 登录（框架已结束）" }); }
        if (mode === "escalate") {
          if (b.force) { state.loggedIn = false; return send(200, { ok: true, mode: "force_stop", pid: 39760, message: "已退出 QQ 登录（框架已结束）" }); }
          return send(200, { ok: false, mode: "external", pid: 39760, can_force: true, reason: "bot_exit 未生效（stub）", manual: "可点强制结束。", message: "框架由外部启动" });
        }
        state.loggedIn = true;
        return send(200, { ok: false, mode: "external", pid: 39760, can_force: false, reason: "模拟：身份校验未通过（不是 napcat）", manual: "手动处理：到项目目录双击「关闭机器人框架.bat」。", message: "退出登录失败：模拟：身份校验未通过（不是 napcat）" });
      }
      if (p === "/api/__reqs") return send(200, { reqs: state.reqs, loggedIn: state.loggedIn });
      if (p.indexOf("/api/") === 0) return send(200, {});
      return send(404, { error: "fake 未实现：" + p });
    } catch (e) { return send(500, { error: String(e && e.message || e) }); }
  });
  return new Promise((resolve) => { server.listen(0, "127.0.0.1", () => resolve({ server, port: server.address().port, state })); });
}

async function drive(port) {
  const url = "http://127.0.0.1:" + port + "/#sessions";
  const dbg = 19200 + Math.floor(Math.random() * 700);
  const profile = path.join(os.tmpdir(), "qqscope-v18b-" + Date.now() + "-" + Math.floor(Math.random() * 1e6));
  const edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--mute-audio", "--user-data-dir=" + profile, "--window-size=1440,2000", "--remote-debugging-port=" + dbg, url], { stdio: "ignore" });
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  let ws = null;
  const cleanup = () => { try { if (ws) ws.close(); } catch (e) {} try { execSync("taskkill /PID " + edge.pid + " /T /F", { stdio: "ignore" }); } catch (e) {} try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) {} };
  try {
    let target = null;
    for (let i = 0; i < 80 && !target; i++) { await sleep(300); try { const list = await (await fetch("http://127.0.0.1:" + dbg + "/json/list")).json(); target = list.filter((t) => t.type === "page")[0]; } catch (e) {} }
    if (!target) throw new Error("找不到 CDP target");
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let seq = 0; const pending = new Map();
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
    const send = (method, params) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params: params || {} })); });
    const ev = async (expr) => { const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true }); return r.result && r.result.result ? r.result.result.value : null; };
    await send("Runtime.enable");
    const waitFor = async (expr, ms) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await ev(expr)) return true; await sleep(250); } return false; };
    return { ev, waitFor, sleep, cleanup };
  } catch (e) { cleanup(); throw e; }
}

async function runScenario(mode, btnId) {
  const fake = await startFake(mode);
  let drv = null;
  try {
    drv = await drive(fake.port);
    const { ev, waitFor, sleep } = drv;
    const out = {};
    out.entered = await waitFor("!!(window.__QQSCOPE_LOGIN__ && window.__QQSCOPE_LOGIN__.state().entered)", 15000);
    out.hadConv = await waitFor("document.querySelectorAll('.conv-item').length>0", 9000);
    if (out.hadConv) { await ev("document.querySelector('.conv-item').click()"); await sleep(1200); }
    out.before = JSON.parse((await ev(METRICS)) || "{}");
    await ev("document.getElementById('" + btnId + "').click()");
    await sleep(700);
    out.confirmShown = await ev("!document.getElementById('confirmScrim').classList.contains('hidden')");
    out.confirmBody = await ev("(document.getElementById('confirmBody')||{}).textContent||''");
    await ev("document.getElementById('confirmOk').click()");
    await sleep(2200);
    out.after = JSON.parse((await ev(METRICS)) || "{}");
    out.calls1 = await (await fetch("http://127.0.0.1:" + fake.port + "/api/__reqs")).json();
    await sleep(3400);   /* 跨过一个 poll 周期，验证失败红字/成功态都常驻 */
    out.after2 = JSON.parse((await ev(METRICS)) || "{}");
    return out;
  } finally {
    if (drv) drv.cleanup();
    try { fake.server.close(); } catch (e) {}
  }
}

const seqOf = (calls, url) => calls.reqs.findIndex((r) => r.url === url);
const allOf = (calls, url) => calls.reqs.filter((r) => r.url === url);

if (fs.existsSync(EDGE)) {
  let s1 = null, s2 = null, s3 = null;
  try { s1 = await runScenario("ok", "btnFullExit"); } catch (e) { section("v18b DOM：全部退出（成功）"); ok("场景可运行", false, String(e && e.message || e)); }
  if (s1) {
    section("v18b DOM：全部退出 -> 抓包 + 回门 + DOM 清空");
    const rq = s1.calls1.reqs;
    ok("点按钮弹出二次确认（含框架+数据保留）", s1.confirmShown === true && /框架/.test(s1.confirmBody) && /数据/.test(s1.confirmBody) && /保留/.test(s1.confirmBody), s1.confirmBody.slice(0, 60));
    ok("先 live/stop 再 framework/logout", seqOf(s1.calls1, "/api/live/stop") >= 0 && seqOf(s1.calls1, "/api/framework/logout") > seqOf(s1.calls1, "/api/live/stop"), JSON.stringify(rq.map((r) => r.url)));
    ok("logout 带 {force:true}", allOf(s1.calls1, "/api/framework/logout").every((r) => r.body && r.body.force === true), JSON.stringify(allOf(s1.calls1, "/api/framework/logout").map((r) => r.body)));
    ok("最终回到连接门", s1.after.gateHidden === false, "gateHidden=" + s1.after.gateHidden);
    ok("连接门上有「全部退出」入口", s1.after.gateFullExit === true, s1.after.gateFullExit);
    ok("DOM 清空（会话 0 / 消息 0）", s1.after.convItemCount === 0 && s1.after.bubbleLen === 0, "conv=" + s1.after.convItemCount + " bubble=" + s1.after.bubbleLen);
    ok("authChip 隐藏（web 登录标记已清）", s1.after.authChipHidden === true, s1.after.authChipHidden);
    ok("明确成功结果文案", /已退出登录/.test(s1.after2.bannerText) && /框架/.test(s1.after2.bannerText), s1.after2.bannerText.slice(0, 80));
    ok("无 JS 报错（jsErr=0）", s1.after2.errors === "0", "errors=" + s1.after2.errors);
  }
  try { s3 = await runScenario("escalate", "btnLogout"); } catch (e) { section("v18b DOM：退出登录自动升级"); ok("场景可运行", false, String(e && e.message || e)); }
  if (s3) {
    section("v18b DOM：退出登录 external+can_force -> 自动 force_stop");
    const lg = allOf(s3.calls1, "/api/framework/logout");
    ok("依次发两次 logout：force=false -> force=true", lg.length === 2 && lg[0].body.force === false && lg[1].body.force === true, JSON.stringify(lg.map((r) => r.body)));
    ok("live/stop 在 logout 之前", seqOf(s3.calls1, "/api/live/stop") >= 0 && seqOf(s3.calls1, "/api/live/stop") < seqOf(s3.calls1, "/api/framework/logout"), JSON.stringify(s3.calls1.reqs.map((r) => r.url)));
    ok("升级后真的退出（loggedIn=false + 回门）", s3.calls1.loggedIn === false && s3.after.gateHidden === false, "loggedIn=" + s3.calls1.loggedIn + " gateHidden=" + s3.after.gateHidden);
    ok("无 JS 报错（jsErr=0）", s3.after2.errors === "0", "errors=" + s3.after2.errors);
  }
  try { s2 = await runScenario("fail", "btnFullExit"); } catch (e) { section("v18b DOM：失败红字"); ok("场景可运行", false, String(e && e.message || e)); }
  if (s2) {
    section("v18b DOM：失败 -> 红色错误 + 原因 + 手动办法（不静默）");
    ok("红色错误横幅可见", s2.after.bannerHidden === false && /err/.test(s2.after.bannerClass), s2.after.bannerClass);
    ok("含「失败」与具体原因", /失败/.test(s2.after.bannerText) && /身份校验未通过/.test(s2.after.bannerText), s2.after.bannerText.slice(0, 120));
    ok("含手动处理办法", /手动处理/.test(s2.after.bannerText) && /关闭机器人框架/.test(s2.after.bannerText), s2.after.bannerText.slice(0, 160));
    ok("失败态常驻（3.4s 后仍在，不被 poll 冲掉）", s2.after2.bannerHidden === false && /err/.test(s2.after2.bannerClass) && /失败/.test(s2.after2.bannerText), s2.after2.bannerClass);
    ok("门提示也显示失败（不只一条横幅）", /退出登录失败/.test(s2.after2.hintText), s2.after2.hintText.slice(0, 80));
    ok("最终仍在连接门", s2.after2.gateHidden === false, "gateHidden=" + s2.after2.gateHidden);
    ok("无 JS 报错（jsErr=0）", s2.after2.errors === "0", "errors=" + s2.after2.errors);
  }
  if (process.argv.indexOf("--evidence") >= 0) console.log("\n[EVIDENCE]\n" + JSON.stringify({ s1, s2, s3 }, null, 1));
} else {
  skip("假后端 + 无头 Edge 抓包", "找不到 Edge：" + EDGE);
}

console.log(`\n===== check_v18b 结果：PASS ${passes} / FAIL ${fails}${skips ? " / SKIP " + skips : ""} =====`);
process.exit(fails ? 1 : 0);