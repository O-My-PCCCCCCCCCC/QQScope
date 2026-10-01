#!/usr/bin/env node
/* QQScope v9 断言：总览首页 + 框架终端 + 发消息(dry_run) + 导出时间段 + live focus */
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
section("v9 源码：总览首页 + 导航顺序");
const navOrder = [...src.matchAll(/data-page="(overview|sessions|export|feeds|sources|ai|settings)"/g)].slice(0, 7).map((m) => m[1]);
ok("导航顺序 总览/会话/导出/动态/数据源/AI/设置", navOrder.join(",") === "overview,sessions,export,feeds,sources,ai,settings", navOrder.join(","));
ok("PAGES 默认第一个是 overview", /var PAGES = \["overview"/.test(src) && /parseHash\(\) \|\| "overview"/.test(src));
section("v9 源码：CONSOLE 框架标签");
ok("双标签 logTabs + framework", /id="logTabs"/.test(src) && /data-tab="framework"/.test(src) && /框架\(NapCat\)/.test(src));
ok("框架日志接口 + offset 增量", /\/api\/framework\/logs\?limit=300&offset=/.test(src) && /state\.fwOffset/.test(src));
ok("框架状态条 /api/framework/status", /\/api\/framework\/status/.test(src) && /function renderFwStatus/.test(src));
ok("接口未就绪文案", /框架日志接口未就绪/.test(src) && /框架未运行/.test(src));
section("v9 源码：发消息（含 dry_run，无自动发送）");
ok("输入区 + 字数计数 + 发送按钮", /id="sendText"/.test(src) && /id="sendCount"/.test(src) && /id="btnSend"/.test(src));
ok("二次确认弹窗", /id="sendConfirm"/.test(src) && /function openSendConfirm/.test(src) && /confirm: true/.test(src));
ok("群聊额外提示 N 人会看到", /这是群聊/.test(src) && /人会看到/.test(src));
ok("dry_run 测试通道（不真实发送）", /__QQSCOPE_SEND_DRY_RUN/.test(src) && /body\.dry_run = true/.test(src));
ok("428/403 人话处理", /428/.test(src) && /403/.test(src) && /policy_blocked/.test(src));
ok("禁止自动发送（无 setInterval 调 doSend）", !/setInterval\([^)]*doSend/.test(src));
section("v9 源码：导出时间段 + 分类");
ok("时间段 chips + 自定义日期", /id="expRange"/.test(src) && /data-range="7"/.test(src) && /id="expSince"/.test(src) && /id="expUntil"/.test(src));
ok("时间范围函数 + 起止校验", /function expRangeValue/.test(src) && /开始日期不能晚于结束日期/.test(src));
ok("导出列表私聊/群聊分组", /function expRowHTML/.test(src) && /exp-group-head/.test(src) && /私聊 \/ C2C/.test(src));
ok("全选私聊/全选群聊快捷", /id="expAllC2C"/.test(src) && /id="expAllGroup"/.test(src));
ok("导出携带 opts.since/until", /opts\.since = rng\.since/.test(src) && /opts\.until = rng\.until/.test(src));
section("v9 源码：live focus");
ok("/api/live/focus on 切会话", /function requestLiveFocus/.test(src) && /\/api\/live\/focus/.test(src) && /requestLiveFocus\(c\)/.test(src));
ok("focus 时轮询缩短到 ≤5s", /function effectiveRefreshSec/.test(src) && /Math\.min\(state\.refreshSec, 5\)/.test(src));
ok("live/events 增量（since_id + messages_by_peer）", /\/api\/live\/events\?account=/.test(src) && /since_id=/.test(src) && /messages_by_peer/.test(src));
ok("live 不可用回退 15s 轮询", /state\.live\.ok = false/.test(src) && /startAutoRefresh\(\)/.test(src) && /function startSessionPolling/.test(src));
section("v9 源码：群聊发言人头像修复");
ok("群聊发言人用 c2c+sender_qq（不再当群头像）", /\/\^\[0-9\]\+\$\/\.test\(sq\)\) return avatarHTML\(name, "c2c\|" \+ sq, 32, "c2c", sq\)/.test(src));
section("v9 源码：硬约束");
ok("占位符两行逐字", /\n\s*window\.__QQSCOPE_BOOT__\s*=\s*\/\*__QQSCOPE_BOOT__\*\/\s*null\s*;\s*\n/.test(src) && /\n\s*\/\*__QQSCOPE_ENGINE__\*\/\s*\n/.test(src));
ok("无 <script src=", !/<script[^>]+src\s*=/i.test(src));
const doms = {};
for (const f of DOMS) { if (fs.existsSync(f)) doms[path.basename(f)] = fs.readFileSync(f, "utf8"); else { fails++; console.log(`  FAIL  找不到 ${f}`); } }
for (const [tag, dom] of Object.entries(doms)) {
  section(`v9 DOM：${tag}`);
  const err = dom.match(/data-js-errors="(\d+)"/);
  ok(`${tag}：data-js-errors=0`, !err || err[1] === "0", err ? "=" + err[1] : "无标记");
  ok(`${tag}：无 <script src=`, !/<script[^>]+src\s*=/i.test(dom));
  if (/home/.test(tag)) {
    ok(`${tag}：默认落地总览`, (dom.match(/data-home-view="([^"]*)"/) || [])[1] === "view-overview");
    ok(`${tag}：KPI ≥5`, Number((dom.match(/data-kpi="(\d+)"/) || [])[1] || 0) >= 5);
    ok(`${tag}：首个导航=overview`, (dom.match(/data-first-nav="([^"]*)"/) || [])[1] === "overview");
    ok(`${tag}：媒体命中率/补下载卡`, dom.includes('id="mediaStatsCard"') && dom.includes('id="btnBackfill"'));
  }
  if (/consolefw/.test(tag)) {
    ok(`${tag}：框架标签激活`, /class="[^"]*on[^"]*"[^>]*data-tab="framework"|data-tab="framework"[^>]*class="[^"]*on/.test(dom) || (dom.match(/data-fw-tab="([^"]*)"/) || [])[1]?.includes("on") === true);
    const st = (dom.match(/data-fw-status="([^"]*)"/) || [])[1] || "";
    ok(`${tag}：状态条有内容`, st.length > 4, st);
    const lines = Number((dom.match(/data-fw-lines="(\d+)"/) || [])[1] || 0);
    const empty = (dom.match(/data-fw-empty="([^"]*)"/) || [])[1] || "";
    ok(`${tag}：有真实框架输出或未就绪提示`, lines > 0 || /未就绪/.test(empty), `lines=${lines} empty=${empty}`);
  }
  if (/export/.test(tag)) {
    ok(`${tag}：时间范围切换`, ((dom.match(/data-exp-range="([^"]*)"/) || [])[1] || "").includes("最近 7 天"));
    ok(`${tag}：时间范围提示`, ((dom.match(/data-exp-hint="([^"]*)"/) || [])[1] || "").includes("时间范围"));
    ok(`${tag}：私聊/群聊分组`, Number((dom.match(/data-exp-groups="(\d+)"/) || [])[1] || 0) >= 2);
    ok(`${tag}：全选快捷按钮`, (dom.match(/data-exp-quick="([^"]*)"/) || [])[1] === "11");
  }
  if (/avatar-group/.test(tag)) {
    const senders = [...dom.matchAll(/<div class="bubble other">[\s\S]{0,140}?<img src="\/api\/avatar\?qq=(\d+)"/g)].map((m) => m[1]);
    const uniq = [...new Set(senders)];
    ok(`${tag}：发言人用 qq 头像 ×${senders.length}（不同 ${uniq.length} 人）`, senders.length >= 5 && uniq.length >= 2);
    const groupReq = (dom.match(/<div class="bubble other">[\s\S]{0,140}?\/api\/avatar\/group/g) || []).length;
    ok(`${tag}：气泡里没有群头像请求`, groupReq === 0, "groupReq=" + groupReq);
  }
  if (/send/.test(tag)) {
    ok(`${tag}：确认弹窗已弹出`, (dom.match(/data-confirm-open="(\d)"/) || [])[1] === "1");
    ok(`${tag}：确认文案含「确定发送」`, /确定发送/.test((dom.match(/data-confirm-text="([^"]*)"/) || [])[1] || ""));
    ok(`${tag}：无发送错误`, ((dom.match(/data-send-err="([^"]*)"/) || [])[1] || "").length === 0);
    ok(`${tag}：dry_run 气泡（未真实发送）`, /dry_run/.test((dom.match(/data-last-bubble="([^"]*)"/) || [])[1] || ""));
  }
}
console.log(`\n===== check_v9 结果：PASS ${passes} / FAIL ${fails} =====`);
process.exit(fails ? 1 : 0);