#!/usr/bin/env node
/* QQScope v7 断言：QQ 表情渲染 + 运行日志控制台 + 数据源状态 + 同步可视化
 * 用法：node check_v7.mjs [web/index.html] [dom ...]
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..", "..");
const args = process.argv.slice(2);
const SRC = args[0] || path.join(root, "web", "index.html");
const DOMS = args.slice(1);
let fails = 0, passes = 0;
const ok = (n, c, d) => { if (c) { passes++; console.log(`  PASS  ${n}`); } else { fails++; console.log(`  FAIL  ${n}${d ? "  -> " + d : ""}`); } };
const section = (t) => console.log(`\n[${t}]`);

const src = fs.readFileSync(SRC, "utf8");
section("v7 源码：QQ 表情");
const mapMatch = src.match(/var QQ_EMOJI = \{([\s\S]*?)\n\};/);
const mapSize = mapMatch ? (mapMatch[1].match(/"[^"]+":/g) || []).length : 0;
ok(`QQ_EMOJI 映射表 ≥100 词（实际 ${mapSize}）`, mapSize >= 100, "size=" + mapSize);
ok("emojiToText / renderTextWithEmoji 存在", /function emojiToText/.test(src) && /function renderTextWithEmoji/.test(src));
ok("只替换命中（fallback 保留原样）", /QQ_EMOJI\[code\] \|\| m/.test(src) && /var e = QQ_EMOJI\[code\]/.test(src));
ok("消息气泡调用 renderTextWithEmoji", /renderTextWithEmoji\(m\.text\)/.test(src));
ok("列表摘要也走 emojiToText", /emojiToText\(sub\)/.test(src));

section("v7 源码：运行日志控制台");
ok("面板 DOM：logPanel / consoleFab", src.includes('id="logPanel"') && src.includes('id="consoleFab"'));
ok("数据来自 /api/logs", src.includes("/api/logs?limit=200"));
ok("折叠开关 openConsole/closeConsole/toggleConsole", /function openConsole/.test(src) && /function closeConsole/.test(src) && /function toggleConsole/.test(src));
ok("level 过滤 + 清屏 + 滚到底", src.includes("logFilter") && src.includes("logClear") && src.includes("scrollLogsBottom"));
ok("接口未就绪显示提示不白屏", src.includes("日志接口未就绪"));

section("v7 源码：数据源状态真实化");
ok("最后检测时间戳 fmtHMS", /function fmtHMS/.test(src) && src.includes("最后检测 "));
ok("检测中加载态 + 三态徽章", /function sourceStatus/.test(src) && src.includes("检测中…") && src.includes("未配置"));
ok("10 秒后自动重试一次", /setTimeout\(function \(\) \{ state\.sourcesRetry = null; loadSources\(\); \}, 10000\)/.test(src));
ok("窗口B 真实登录信息字段", src.includes("登录账号") && src.includes("好友数") && src.includes("群数") && src.includes("OneBot 地址") && src.includes("上次拉取"));
ok("按钮进行中文案", src.includes("扫描中…") && src.includes("拉取中…") && src.includes("探测中…") && src.includes("导入中…"));

section("v7 源码：同步可视化");
ok("syncBarMain 本机数据/最后同步", src.includes("syncBarMain") && src.includes("本机数据") && src.includes("最后同步"));
ok("每窗口上次同步/新增", src.includes("packLastSync") && src.includes("botLastSync") && src.includes("renderSyncLines"));
ok("pack/bot 来源徽章", src.includes("src-badge") && /a\.source/.test(src) && /m\.source/.test(src));

for (const f of DOMS) {
  if (!fs.existsSync(f)) { fails++; console.log(`  FAIL  找不到 ${f}`); continue; }
  const dom = fs.readFileSync(f, "utf8");
  const tag = path.basename(f);
  section(`v7 DOM：${tag}`);
  const err = dom.match(/data-js-errors="(\d+)"/);
  ok(`${tag}：data-js-errors=0`, !err || err[1] === "0", err ? "=" + err[1] : "无标记");
  ok(`${tag}：无 <script src=`, !/<script[^>]+src\s*=/i.test(dom));
  ok(`${tag}：无 http(s) 外链`, !/<img[^>]+src\s*=\s*["']?https?:/i.test(dom) && !/href\s*=\s*["']?https?:/i.test(dom));

  if (/emoji/.test(tag)) {
    const emo = (dom.match(/<span class="qq-emoji">/g) || []).length;
    ok(`${tag}：.qq-emoji 节点 ×${emo}`, emo >= 1, "count=" + emo);
    const bodies = [...dom.matchAll(/<div class="b-text">([\s\S]*?)<\/div>/g)].map((m) => m[1]);
    const raw = bodies.some((b) => /\[(大哭|doge|微笑|呲牙|发呆|强|玫瑰)\]/.test(b));
    ok(`${tag}：消息气泡无裸 [emoji码]`, !raw);
    const rect = dom.match(/data-emoji-rect="([^"]*)"/);
    if (rect) {
      const r = JSON.parse(rect[1].replace(/&quot;/g, '"'));
      ok(`${tag}：emoji 在截图视口内 (y=${Math.round(r.top)})`, r.top >= 0 && r.bottom <= 2000 && r.left >= 0 && r.right <= 1600);
    }
  }
  if (/console/.test(tag)) {
    const on = (dom.match(/data-console-on="(\d)"/) || [])[1];
    const n = Number((dom.match(/data-log-count="(\d+)"/) || [])[1] || 0);
    ok(`${tag}：控制台已打开`, on === "1", "on=" + on);
    ok(`${tag}：日志行数 ≥20（实际 ${n}）`, n >= 20);
    ok(`${tag}：日志内容含接口调用`, /GET \/api\//.test(dom));
    ok(`${tag}：面板可折叠（.logpanel transform 侧滑）`, dom.includes('class="logpanel') && /\.logpanel\{[^}]*transform:translateY\(102%\)/.test(src));
  }
  if (/sources/.test(tag)) {
    const checked = (dom.match(/data-checked="([^"]*)"/) || [])[1] || "";
    ok(`${tag}：最后检测含 HH:MM:SS（${checked}）`, /最后检测 \d{2}:\d{2}:\d{2}/.test(checked));
    const info = (dom.match(/data-bot-info="([^"]*)"/) || [])[1] || "";
    ok(`${tag}：窗口B 显示登录身份`, /登录账号/.test(info) && /昵称/.test(info) && /好友数/.test(info) && /群数/.test(info) && /OneBot 地址/.test(info));
    ok(`${tag}：徽章已就绪`, /已就绪/.test((dom.match(/data-badges="([^"]*)"/) || [])[1] || ""));
  }
  if (/sync/.test(tag)) {
    const main = (dom.match(/data-sync-main="([^"]*)"/) || [])[1] || "";
    ok(`${tag}：同步条格式 本机数据 N 条 · 最后同步 HH:MM:SS`, /本机数据 [\d,]+ 条 · 最后同步 \d{2}:\d{2}:\d{2}/.test(main), main);
    const cb = Number((dom.match(/data-conv-src-badges="(\d+)"/) || [])[1] || 0);
    ok(`${tag}：会话来源徽章 ×${cb}`, cb >= 1);
  }
}
console.log(`\n===== check_v7 结果：PASS ${passes} / FAIL ${fails} =====`);
process.exit(fails ? 1 : 0);