#!/usr/bin/env node
/* 生成前端自测产物（不写 app/dist，只写 web/selftest/out）：
 *   QQScope.selftest.html  内嵌 boot 快照 + 引擎（离线 file:// 可跑）
 *   QQScope.api.html       只注入引擎、boot=null（给 mock API 服务用）
 * 用法：node build.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..", "..");
const outDir = path.join(__dirname, "out");
fs.mkdirSync(outDir, { recursive: true });

const src = fs.readFileSync(path.join(root, "web", "index.html"), "utf8");
const engine = fs.readFileSync(path.join(root, "app", "js", "analysis.js"), "utf8");
const BOOT_RE = /\/\*__QQSCOPE_BOOT__\*\/[ \t]*null/;
const ENGINE_RE = /\/\*__QQSCOPE_ENGINE__\*\//;

let seed = 20261001;
function rnd() { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; }
function pick(a) { return a[Math.floor(rnd() * a.length)]; }

const ACCOUNT = 1605289411;
const PEERS = [
  { kind: "c2c", peer_id: "u_1001", peer_qq: 3246197489, name: "张三", remark: "同桌" },
  { kind: "c2c", peer_id: "u_1002", peer_qq: 276543210, name: "李四", remark: "" },
  { kind: "group", peer_id: "1038804640", peer_qq: 1038804640, name: "摸鱼群", remark: "" }
];
const SELF_TEXT = ["今天有点累", "晚上一起吃饭吗", "哈哈哈笑死", "好的收到", "这个方案我再改改",
  "有点烦，先睡了", "早，今天状态不错", "谢谢啦", "明天开个会", "冲！", "emo了", "还行吧",
  "这周末去打球", "太棒了", "有点焦虑", "摸鱼中", "晚安", "在吗", "帮我看看这个", "辛苦啦"];
const OTHER_TEXT = ["在的", "可以啊", "我也觉得", "哈哈哈哈", "那你早点休息", "明天见", "这个不错",
  "收到", "别想太多", "一起加油", "我看看", "稍等", "好嘞"];
const BASE_DAY = Math.floor(new Date("2026-09-24T09:00:00+08:00").getTime() / 1000);

const messages = [];
const perPeer = {};
for (let d = 0; d < 9; d++) {
  for (const p of PEERS) {
    const n = 8 + Math.floor(rnd() * 16);
    for (let i = 0; i < n; i++) {
      const hour = 8 + Math.floor(rnd() * 16);
      const ts = BASE_DAY + d * 86400 + hour * 3600 + Math.floor(rnd() * 3600);
      const self = rnd() < 0.62 ? 1 : 0;
      const text = self ? pick(SELF_TEXT) : pick(OTHER_TEXT);
      const k = p.kind + "|" + p.peer_id;
      if (!perPeer[k]) perPeer[k] = { count: 0, self: 0, first: ts, last: 0, lastText: "" };
      const s = perPeer[k];
      s.count++; if (self) s.self++;
      if (ts < s.first) s.first = ts;
      if (ts >= s.last) { s.last = ts; s.lastText = text; }
      messages.push({ t: ts, d: self, k: p.kind, p: p.peer_id, x: text, account_qq: ACCOUNT, n: self ? "我" : p.name });
    }
  }
}
messages.sort((a, b) => a.t - b.t);
const selfTotal = messages.filter((m) => m.d === 1).length;
const firstTs = messages[0].t, lastTs = messages[messages.length - 1].t;

const contacts = PEERS.map((p) => {
  const s = perPeer[p.kind + "|" + p.peer_id];
  return {
    account_qq: ACCOUNT, kind: p.kind, peer_id: p.peer_id, peer_qq: p.peer_qq,
    name: p.name, remark: p.remark, avatar: null,
    msg_count: s.count, self_count: s.self, first_ts: s.first, last_ts: s.last,
    last_text: s.lastText, source: "pack"
  };
});
const boot = {
  generated: "2026-10-01 12:00:00",
  mode: "offline-snapshot",
  settings: { data_root: "C:\\Users\\Administrator\\Documents\\Tencent Files", port: 15555,
    ai: { provider: "deepseek", base: "api.deepseek.com/v1", key: "", model: "deepseek-chat" } },
  accounts: [{ account_qq: ACCOUNT, label: "主号（示例快照）", source: "pack",
    contacts: contacts.length, messages: messages.length, self_messages: selfTotal,
    first_ts: firstTs, last_ts: lastTs }],
  contacts,
  messages
};

if (!BOOT_RE.test(src) || !ENGINE_RE.test(src)) {
  console.error("占位符未找到：请检查 web/index.html 的 boot/engine 占位符格式");
  process.exit(1);
}
const bootHtml = src.replace(BOOT_RE, JSON.stringify(boot)).replace(ENGINE_RE, engine);
const apiHtml = src.replace(BOOT_RE, "null").replace(ENGINE_RE, engine);
fs.writeFileSync(path.join(outDir, "QQScope.selftest.html"), bootHtml, "utf8");
fs.writeFileSync(path.join(outDir, "QQScope.api.html"), apiHtml, "utf8");
fs.writeFileSync(path.join(outDir, "sample-summary.json"), JSON.stringify({
  account: ACCOUNT, contacts: contacts.length, messages: messages.length, self_messages: selfTotal,
  first_ts: firstTs, last_ts: lastTs
}, null, 2), "utf8");
console.log(`[selftest] selftest.html ${(bootHtml.length / 1024).toFixed(0)} KB · api.html ${(apiHtml.length / 1024).toFixed(0)} KB`);
console.log(`[selftest] 账号 ${ACCOUNT} · 会话 ${contacts.length} · 消息 ${messages.length}（自发 ${selfTotal}）`);