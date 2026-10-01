#!/usr/bin/env node
/* QQScope 前端自测用的极简 mock 后端（零依赖，只实现 SPEC 第 5 节 + Lead 新增的 /api/report、/api/progress）。
 * 不写任何文件、不联网，只用于无头浏览器验收前端 fetch 路径与降级逻辑。
 * 用法：node mock_server.mjs [port]
 */
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..", "..");
const PORT = Number(process.argv[2] || 15556);
const ACCOUNT = 1605289411;
const PAGE = path.join(__dirname, "out", "QQScope.api.html");
const PAGE_FALLBACK = path.join(root, "web", "index.html");

/* ---------- 造数据 ---------- */
let seed = 20261001;
const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
const pick = (a) => a[Math.floor(rnd() * a.length)];
const PEERS = [
  { kind: "c2c", peer_id: "u_1001", peer_qq: 3246197489, name: "张三", remark: "同桌" },
  { kind: "c2c", peer_id: "u_1002", peer_qq: 276543210, name: "李四", remark: "" },
  { kind: "group", peer_id: "1038804640", peer_qq: 1038804640, name: "摸鱼群", remark: "" }
];
const SELF_TEXT = ["今天有点累", "晚上一起吃饭吗", "哈哈哈笑死", "好的收到", "这个方案我再改改", "有点烦，先睡了",
  "早，今天状态不错", "谢谢啦", "明天开个会", "冲！", "emo了", "还行吧", "这周末去打球", "太棒了", "有点焦虑", "摸鱼中", "晚安"];
const OTHER_TEXT = ["在的", "可以啊", "我也觉得", "哈哈哈哈", "那你早点休息", "明天见", "这个不错", "收到", "别想太多", "一起加油"];
const BASE_DAY = Math.floor(new Date("2026-09-24T09:00:00+08:00").getTime() / 1000);
const messages = [];
const perPeer = {};
for (let d = 0; d < 9; d++) {
  for (const p of PEERS) {
    const n = 6 + Math.floor(rnd() * 9);
    for (let i = 0; i < n; i++) {
      const ts = BASE_DAY + d * 86400 + (8 + Math.floor(rnd() * 16)) * 3600 + Math.floor(rnd() * 3600);
      const self = rnd() < 0.62 ? 1 : 0;
      const text = self ? pick(SELF_TEXT) : pick(OTHER_TEXT);
      const key = p.kind + "|" + p.peer_id;
      const s = perPeer[key] || (perPeer[key] = { count: 0, self: 0, first: ts, last: 0, lastText: "" });
      s.count++; if (self) s.self++;
      if (ts < s.first) s.first = ts;
      if (ts >= s.last) { s.last = ts; s.lastText = text; }
      messages.push({ account_qq: ACCOUNT, kind: p.kind, peer_id: p.peer_id, peer_qq: p.peer_qq,
        ts, direction: self, sender_qq: self ? ACCOUNT : p.peer_qq, sender_name: self ? "我" : p.name,
        msg_type: 1, text, source: "pack" });
    }
  }
}
messages.sort((a, b) => a.ts - b.ts);
const selfMsgs = messages.filter((m) => m.direction === 1);
const counts = messages.reduce((a, m) => { a.total++; if (m.direction === 1) a.self++; if (m.kind === "c2c") a.c2c++; else a.group++; return a; }, { total: 0, self: 0, c2c: 0, group: 0 });
const contactRows = PEERS.map((p) => {
  const s = perPeer[p.kind + "|" + p.peer_id];
  return { account_qq: ACCOUNT, kind: p.kind, peer_id: p.peer_id, peer_qq: p.peer_qq, name: p.name, remark: p.remark,
    avatar: null, msg_count: s.count, self_count: s.self, first_ts: s.first, last_ts: s.last, last_text: s.lastText, source: "pack" };
});
const accountRow = { account_qq: ACCOUNT, label: "主号（Mock）", source: "pack", contacts: contactRows.length,
  messages: counts.total, self_messages: counts.self, first_ts: messages[0].ts, last_ts: messages[messages.length - 1].ts };

function overview() {
  const dayMap = {};
  const hour = new Array(24).fill(0);
  const peerMap = {};
  selfMsgs.forEach((m) => {
    const d = new Date(m.ts * 1000);
    const k = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
    const dd = dayMap[k] || (dayMap[k] = { date: k, count: 0, self: 0, night: 0 });
    dd.count++; dd.self++;
    const h = d.getHours(); hour[h]++;
    if (h >= 23 || h < 5) dd.night++;
    const pk = m.kind + "|" + m.peer_id;
    const pp = peerMap[pk] || (peerMap[pk] = { kind: m.kind, peer_id: m.peer_id, name: (contactRows.find((c) => c.peer_id === m.peer_id) || {}).name || m.peer_id, msg_count: 0, self_count: 0 });
    pp.msg_count++; pp.self_count++;
  });
  return { total: counts.total, self: counts.self, c2c: counts.c2c, group: counts.group,
    contacts: contactRows.length, first_ts: messages[0].ts, last_ts: messages[messages.length - 1].ts,
    kinds: [{ kind: "c2c", n: counts.c2c }, { kind: "group", n: counts.group }],
    daily: Object.keys(dayMap).sort().map((k) => dayMap[k]),
    hour_hist: hour.map((n, h) => [h, n]),
    top_contacts: Object.values(peerMap).sort((a, b) => b.self_count - a.self_count) };
}

const jobs = [];
const progress = {};
let settings = { data_root: "C:\\Users\\Administrator\\Documents\\Tencent Files", port: 15555,
  ai: { provider: "deepseek", base: "api.deepseek.com/v1", key: "", model: "deepseek-chat" } };

/* ---------- HTTP ---------- */
function send(res, code, obj, type) {
  const body = type === "json" ? JSON.stringify(obj) : obj;
  res.writeHead(code, { "Content-Type": type === "json" ? "application/json; charset=utf-8" : "text/plain; charset=utf-8" });
  res.end(body);
}
function readBody(req) {
  return new Promise((resolve) => {
    let b = ""; req.on("data", (c) => (b += c)); req.on("end", () => { try { resolve(b ? JSON.parse(b) : {}); } catch (e) { resolve({}); } });
  });
}
function norm(q, key, def) { const v = q.get(key); return v == null ? def : v; }

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, "http://localhost");
  const p = u.pathname;
  console.log(`${req.method} ${req.url}`);
  try {
    if (p === "/" || p === "/index.html") {
      const file = fs.existsSync(PAGE) ? PAGE : PAGE_FALLBACK;
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      return res.end(fs.readFileSync(file));
    }
    if (p.startsWith("/demo/")) {
      const f = path.join(__dirname, "out", "demo-" + path.basename(p));
      if (fs.existsSync(f)) { res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" }); return res.end(fs.readFileSync(f)); }
      return send(res, 404, { error: "demo not found" }, "json");
    }    if (p === "/api/health") return send(res, 200, { ok: true, version: "mock-1.0.0", time: Math.floor(Date.now() / 1000) }, "json");
    if (p === "/api/sources") return send(res, 200, { sources: [
      { id: "pack", name: "数据包读取", kind: "pack", ready: true, message: "就绪：发现 1 个账号与可用密钥", detail: { root: settings.data_root } },
      { id: "bot", name: "机器人框架读取", kind: "bot", ready: false, message: "未配置：OneBot 地址为空", detail: { hint: "tools/napcat-win/napcat/launcher.bat" } }
    ] }, "json");
    if (p === "/api/sources/pack/probe") { await readBody(req); return send(res, 200, { ok: true,
      message: "发现 2 个账号，密钥可用；共 271200 条消息、42 个会话",
      accounts: [
        { account_qq: ACCOUNT, db: "nt_msg.db", db_size: 268435456, has_key: true, key_source: "data/keys/1605289411.key", decrypted: true, c2c: 80000, group: 191200, conversations: 42, error: null },
        { account_qq: 3101168700, db: "nt_msg.db", db_size: 12345678, has_key: false, key_source: null, decrypted: false, c2c: 0, group: 0, conversations: 0, error: "没有可用密钥" }
      ], conversations: [] }, "json"); }
    if (p === "/api/sources/pack/sync") { await readBody(req); progress.pack = { start: Date.now(), dur: 2500 };
      await new Promise((r) => setTimeout(r, 2600));
      return send(res, 200, { ok: true, message: "导入完成（mock）", imported: 369, accounts: [ACCOUNT], elapsed: 2.6 }, "json"); }
    if (p === "/api/sources/bot/probe") { const b = await readBody(req);
      if (b.base && String(b.base).indexOf(":15556") < 0) {
        return send(res, 502, { error: "连不上 " + b.base + "：目标计算机拒绝连接，请确认 NapCat 已启动" }, "json");
      }
      const friends = new Array(30).fill(0).map((_, i) => ({ user_id: 20000 + i, nickname: "好友" + i }));
      const groups = new Array(12).fill(0).map((_, i) => ({ group_id: 30000 + i, group_name: "群" + i }));
      return send(res, 200, { ok: true, message: "连接成功：MockBot（QQ 10001），好友 30 个，群 12 个",
        accounts: [{ account_qq: 10001, label: "MockBot", source: "bot" }],
        conversations: friends.map((f) => ({ account_qq: 10001, kind: "c2c", peer_id: "u_" + f.user_id, peer_qq: f.user_id, name: f.nickname }))
          .concat(groups.map((g) => ({ account_qq: 10001, kind: "group", peer_id: String(g.group_id), peer_qq: g.group_id, name: g.group_name }))),
        detail: { base: b.base, account_qq: 10001, nickname: "MockBot", friends: 30, groups: 12, warnings: [] } }, "json"); }
    if (p === "/api/sources/bot/sync") { await readBody(req); progress.bot = { start: Date.now(), dur: 2200 };
      await new Promise((r) => setTimeout(r, 2300));
      return send(res, 200, { ok: true, message: "拉取完成（mock）", imported: 128, accounts: [10001], elapsed: 2.3 }, "json"); }
    if (p.startsWith("/api/progress/")) {
      const id = p.split("/").pop();
      const st = progress[id];
      if (!st) return send(res, 200, { stage: "空闲", pct: 0, msg: "", at: Math.floor(Date.now() / 1000) }, "json");
      const pct = Math.min(100, Math.round((Date.now() - st.start) / st.dur * 100));
      return send(res, 200, { stage: pct >= 100 ? "完成" : "写入主库", pct, msg: pct >= 100 ? "" : "demo", at: Math.floor(Date.now() / 1000) }, "json");
    }
    if (p === "/api/accounts") return send(res, 200, { accounts: [accountRow] }, "json");
    if (p === "/api/overview") return send(res, 200, overview(), "json");
    if (p === "/api/report") return send(res, 200, { meta: Object.assign({ generated: new Date().toISOString() }, accountRow),
      messages: selfMsgs.map((m) => ({ t: m.ts, d: 1, k: m.kind, p: m.peer_id, x: m.text })) }, "json");
    if (p === "/api/contacts" && req.method === "GET") {
      let list = contactRows.slice();
      const kind = norm(u.searchParams, "kind", "");
      const q = norm(u.searchParams, "q", "");
      if (kind) list = list.filter((c) => c.kind === kind);
      if (q) list = list.filter((c) => (c.name + c.remark + c.peer_qq).indexOf(q) >= 0);
      return send(res, 200, { contacts: list }, "json");
    }
    if (p === "/api/messages") {
      const kind = norm(u.searchParams, "kind", ""), peer = norm(u.searchParams, "peer_id", "");
      const limit = Number(norm(u.searchParams, "limit", "200")), offset = Number(norm(u.searchParams, "offset", "0"));
      const order = String(norm(u.searchParams, "order", "ASC")).toUpperCase() === "DESC" ? "DESC" : "ASC";
      let list = messages.filter((m) => (!kind || m.kind === kind) && (!peer || m.peer_id === peer));
      list = list.slice().sort((a, b) => order === "DESC" ? b.ts - a.ts : a.ts - b.ts);
      const total = list.length;
      const page = list.slice(offset, offset + limit);
      const contact = contactRows.find((c) => c.kind === kind && c.peer_id === peer) || null;
      return send(res, 200, { messages: page, total, contact }, "json");
    }
    if (p === "/api/contacts" && req.method === "PATCH") { await readBody(req); return send(res, 200, { ok: true }, "json"); }
    if (p === "/api/export" && req.method === "POST") {
      const body = await readBody(req);
      const formats = body.formats && body.formats.length ? body.formats : ["html"];
      const targets = body.targets || [];
      const job = "mock-" + Date.now().toString(36);
      const files = [];
      const mk = (name, peer, fmt) => ({ name, peer, fmt, size: 1024 + Math.floor(rnd() * 200000) });
      if (body.mode === "merged") formats.forEach((f) => files.push(mk(`合并_全部.${f}`, "全部", f)));
      else targets.forEach((t) => formats.forEach((f) => {
        const c = contactRows.find((x) => x.kind === t.kind && x.peer_id === t.peer_id);
        files.push(mk(`${(c && c.name) || t.peer_id}_${t.kind}_${t.peer_id}.${f}`, (c && c.name) || t.peer_id, f));
      }));
      const rec = { job, created: Math.floor(Date.now() / 1000), files: files.map((f) => ({ name: f.name, size: f.size })), count: files.length, zip: `${job}.zip` };
      jobs.unshift(rec);
      return send(res, 200, { ok: true, job, dir: `data/export/${job}`, files, zip: `data/export/${job}.zip`, count: files.length }, "json");
    }
    if (p === "/api/export/list") return send(res, 200, { jobs: jobs.slice(0, 10) }, "json");
    if (p === "/api/export/download") {
      const job = norm(u.searchParams, "job", "job"), name = norm(u.searchParams, "name", "");
      res.writeHead(200, { "Content-Type": "text/plain; charset=utf-8",
        "Content-Disposition": `attachment; filename="${name || job + ".zip"}"` });
      return res.end(name ? `mock file ${name}\n` : `PK\u0003\u0004 mock zip for ${job}\n`);
    }
    if (p === "/api/ai/chat" && req.method === "POST") {
      const body = await readBody(req);
      const last = (body.messages || []).slice(-1)[0] || {};
      return send(res, 200, { content: "【Mock AI】已收到 " + (body.messages || []).length + " 条消息。你的预览内容长度 " +
        String(last.content || "").length + " 字。\n\n示例解读：整体作息尚可，深夜时段占比不高；建议保持规律作息，多与朋友互动。" }, "json");
    }
    if (p === "/api/settings") {
      if (req.method === "POST") { const b = await readBody(req); if (b.data_root) settings.data_root = b.data_root;
        if (b.ai) settings.ai = Object.assign(settings.ai, b.ai); return send(res, 200, { ok: true }, "json"); }
      return send(res, 200, { data_root: settings.data_root, port: settings.port,
        ai: Object.assign({}, settings.ai, { key: settings.ai.key ? "******" : "" }) }, "json");
    }
    return send(res, 404, { error: "mock 未实现：" + p }, "json");
  } catch (e) {
    console.error("mock error", e);
    return send(res, 500, { error: String(e && e.message || e) }, "json");
  }
});
server.listen(PORT, "127.0.0.1", () => {
  console.log(`[mock] QQScope mock API on http://127.0.0.1:${PORT}/  (page: ${fs.existsSync(PAGE) ? path.basename(PAGE) : "web/index.html"})`);
});