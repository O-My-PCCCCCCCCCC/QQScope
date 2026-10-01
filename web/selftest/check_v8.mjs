#!/usr/bin/env node
/* QQScope v8 断言：聊天自动更新 + 数据源折叠 + 图片补下载 */
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
section("v8 源码：聊天自动更新");
ok("自动刷新状态 + 间隔可配", /autoRefresh: true, refreshSec: 15/.test(src) && /qqscope_refresh_sec/.test(src));
ok("start/stop/autoRefreshTick", /function startAutoRefresh/.test(src) && /function stopAutoRefresh/.test(src) && /function autoRefreshTick/.test(src));
ok("增量 poll（since=max_ts + ASC）", /since=" \+ since/.test(src) && /order=ASC/.test(src) && /state\._maxTs/.test(src));
ok("增量追加不重建（afterbegin/append）", /insertAdjacentHTML\("beforeend"/.test(src) && /function appendNewMessages/.test(src));
ok("新消息胶囊 + 点击滚到底", /id="newMsgPill"/.test(src) && /function showNewMsgPill/.test(src) && /hideNewMsgPill/.test(src));
ok("页面隐藏暂停", /visibilitychange/.test(src) && /stopAutoRefresh\(\)/.test(src));
ok("最后更新 + 手动刷新", /sessionsUpdatedAt/.test(src) && /最后更新/.test(src) && /manualRefresh/.test(src));
section("v8 源码：数据源折叠 + 无意义轮询");
ok("折叠摘要 srcSummary + srcDetail hidden", /id="srcSummary"/.test(src) && /id="srcDetail" hidden/.test(src));
ok("默认折叠（_srcDetailOpen=false）", /_srcDetailOpen: false/.test(src) && /function toggleSrcDetail/.test(src));
ok("30 秒缓存 + 10 秒超时", /_sourcesAt/.test(src) && /< 30/.test(src) && /apiGetTimeout/.test(src) && /10000\)/.test(src));
ok("超时文案 + 点击重试（禁止无限 spinner）", /检测超时，点击重试/.test(src) && /btnSrcRetry/.test(src));
ok("自动同步总开关 + 一键刷新全部", /id="autoSyncOn"/.test(src) && /function syncAll/.test(src) && /id="btnRefreshAll"/.test(src));
section("v8 源码：图片补下载");
ok("backfill 启动/停止/状态", /function startBackfill/.test(src) && /function stopBackfill/.test(src) && /\/api\/media\/backfill/.test(src));
ok("进度渲染（已补/成功/失败）", /function renderBackfill/.test(src) && /已补/.test(src) && /成功/.test(src) && /失败/.test(src));
const doms = {};
for (const f of DOMS) { if (fs.existsSync(f)) doms[path.basename(f)] = fs.readFileSync(f, "utf8"); else { fails++; console.log(`  FAIL  找不到 ${f}`); } }
for (const [tag, dom] of Object.entries(doms)) {
  section(`v8 DOM：${tag}`);
  const err = dom.match(/data-js-errors="(\d+)"/);
  ok(`${tag}：data-js-errors=0`, !err || err[1] === "0", err ? "=" + err[1] : "无标记");
  if (/sessions|autorefresh/.test(tag)) {
    ok(`${tag}：自动刷新控件`, dom.includes('id="autoRefreshToggle"') && dom.includes('id="refreshInterval"'));
    ok(`${tag}：最后更新 HH:MM:SS`, /最后更新 \d{2}:\d{2}:\d{2}/.test(dom));
    ok(`${tag}：新消息胶囊节点`, dom.includes('id="newMsgPill"'));
  }
  if (/sources/.test(tag)) {
    ok(`${tag}：折叠摘要 ≥2 行`, (dom.match(/class="src-summary"/g) || []).length >= 2);
    ok(`${tag}：srcDetail 默认隐藏`, /id="srcDetail"\s+hidden/.test(dom));
    ok(`${tag}：最后检测时间戳`, /最后检测(\s+\d{2}:\d{2}:\d{2})?/.test(dom));
  }
  if (/home|overview/.test(tag)) {
    ok(`${tag}：补下载按钮`, dom.includes('id="btnBackfill"') && dom.includes('id="backfillLine"'));
  }
}
console.log(`\n===== check_v8 结果：PASS ${passes} / FAIL ${fails} =====`);
process.exit(fails ? 1 : 0);