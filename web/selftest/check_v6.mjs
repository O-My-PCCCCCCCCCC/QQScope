#!/usr/bin/env node
/* QQScope v6 断言：名片/小程序卡片 + dossier 不自动打开 + 媒体性能
 * 用法：node check_v6.mjs [web/index.html] [dom ...]
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
const stripScripts = (s) => s.replace(/<script[\s\S]*?<\/script>/gi, "");

section("v6 源码：名片 / 小程序卡片");
const src = fs.readFileSync(SRC, "utf8");
ok("cardParts + prettyCardTitle + isPackageName", /function cardParts/.test(src) && /function prettyCardTitle/.test(src) && /function isPackageName/.test(src));
ok("名片/小程序渲染成 .media-card", src.includes("media-card media-card-") && src.includes("mc-eyebrow") && src.includes("mc-title"));
ok("包名 com.tencent. 会被替换掉", /com\\.tencent\\./.test(src) && /prettyCardTitle/.test(src));
ok("会话摘要也走美化（mediaSummary 处理 card）", /md\.kind === "card"/.test(src) && /cardParts\(md, 0\)/.test(src));
ok("系统消息 m-sys 居中", src.includes("m-sys") && src.includes("system-note"));

section("v6 源码：dossier 绝不自动打开");
const scBody = (src.match(/function selectConversation\(c\) \{[\s\S]*?\n\}/) || [""])[0];
ok("selectConversation 内不调用 openDrawer", scBody.length > 20 && !scBody.includes("openDrawer"));
ok("selectConversation 主动 closeDrawer", /function selectConversation\(c\) \{[\s\S]*?closeDrawer\(\);/.test(src));
const noDef = src.split("function openDrawer(c)").join("function _DEF_");
ok("只有 btnShowCard 显式打开资料卡", (noDef.match(/openDrawer\(c\)/g) || []).length === 1 && src.includes('id="btnShowCard"'));
ok("无 localStorage 记录资料卡开关状态", !/localStorage[^;]*drawer/i.test(src));

section("v6 源码：媒体性能");
ok("图片/视频用 data-media-src + loading=lazy", src.includes('data-media-src="') && src.includes('loading="lazy"'));
ok("IntersectionObserver 双保险（rootMargin ≤ 300px）", /new IntersectionObserver/.test(src) && /rootMargin: MEDIA_ROOT_MARGIN/.test(src) && /MEDIA_ROOT_MARGIN = "\d+px"/.test(src));
const rm = src.match(/MEDIA_ROOT_MARGIN = "(\d+)px"/);
ok("rootMargin ≤ 300px", rm ? Number(rm[1]) <= 300 : false, rm ? rm[1] : "n/a");
ok("音频 preload=none 且渲染时不预加载", src.includes('preload="none"') && !/audio[^>]*preload="auto"/.test(src));
ok("分页：首屏 200 / 后续 100", /pageSize = reset \? 200 : 100/.test(src));
ok("分页：增量 insertAdjacentHTML(afterbegin)", src.includes('insertAdjacentHTML("afterbegin"'));
ok("上滑自动加载 + 分页条", src.includes("loadMoreBar") && src.includes("scrollTop < 120"));
ok("轮询定时器可清理", /function clearProgressTimers/.test(src) && /visibilitychange/.test(src) && /page !== "sources"/.test(src) && /clearProgressTimers\(\)/.test(src));

section("v6 源码：语音统计 / 进度");
ok("/api/voice/stats + /api/voice/progress", src.includes("/api/voice/stats") && src.includes("/api/voice/progress"));
ok("语音已转文字 + 进度行 DOM", src.includes("voiceStatsLine") && src.includes("voiceProgressLine"));
ok("全部完成文案", src.includes("全部完成"));

const doms = {};
for (const f of DOMS) { if (fs.existsSync(f)) doms[path.basename(f)] = fs.readFileSync(f, "utf8"); else { fails++; console.log(`  FAIL  找不到 ${f}`); } }
for (const [tag, dom] of Object.entries(doms)) {
  section(`v6 DOM：${tag}`);
  const err = dom.match(/data-js-errors="(\d+)"/);
  ok(`${tag}：data-js-errors=0`, !err || err[1] === "0", err ? "=" + err[1] : "无标记");
  if (/card(?!mini)/.test(tag)) {
    const cards = dom.match(/<span class="media-el media-card media-card-/g) || [];
    const named = dom.match(/media-card-namecard/g) || [];
    ok(`${tag}：media-card 节点 ×${cards.length}`, cards.length >= 3, `找到 ${cards.length}`);
    ok(`${tag}：包含名片卡 ×${named.length}`, named.length >= 1);
    const vis = stripScripts(dom);
    ok(`${tag}：可见文本无 com.tencent. 包名`, !/com\.tencent\./.test(vis));
  }
  if (/cardmini/.test(tag)) {
    const mini = dom.match(/media-card-miniapp/g) || [];
    ok(`${tag}：小程序卡片 ×${mini.length}`, mini.length >= 1);
    const vis = stripScripts(dom);
    ok(`${tag}：可见文本无 com.tencent. 包名`, !/com\.tencent\./.test(vis));
  }
  if (/avatar|row|open|switch/.test(tag)) {
    const don = (dom.match(/data-drawer-on="(\d)"/) || [])[1];
    if (/avatar|row/.test(tag)) ok(`${tag}：点会话行/头像后 dossier 关闭`, don === "0", "drawer-on=" + don);
    if (/open/.test(tag)) ok(`${tag}：点「资料卡」按钮后 dossier 打开`, don === "1", "drawer-on=" + don);
    if (/switch/.test(tag)) {
      const ao = (dom.match(/data-drawer-after-open="(\d)"/) || [])[1];
      ok(`${tag}：打开后 drawer-on=1`, ao === "1", "after-open=" + ao);
      ok(`${tag}：切另一个会话后 dossier 关闭`, don === "0", "drawer-on=" + don);
    }
    /* dossier 必须仍是侧滑隐藏态（不是内联常显） */
    ok(`${tag}：dossier 使用 transform 侧滑隐藏`, dom.includes('class="drawer') && /\.drawer\s*\{[^}]*transform:translateX\(102%\)/.test(src));
  }
  if (/perf/.test(tag)) {
    const b = Number((dom.match(/data-perf-bubbles="(\d+)"/) || [])[1] || 0);
    const srcImgs = Number((dom.match(/data-perf-imgs-src="(\d+)"/) || [])[1] || 0);
    const allImgs = Number((dom.match(/data-perf-imgs-all="(\d+)"/) || [])[1] || 0);
    ok(`${tag}：气泡数 ≥ 200（分页首屏）`, b >= 200, `bubbles=${b}`);
    ok(`${tag}：懒加载生效（已设 src 的媒体 < 媒体总数）`, srcImgs < allImgs && allImgs > 0, `src=${srcImgs} all=${allImgs}`);
    ok(`${tag}：DOM 节点数已采集`, /data-perf-dom-nodes="\d+"/.test(dom));
  }
  ok(`${tag}：无 <script src=`, !/<script[^>]+src\s*=/i.test(dom));
  ok(`${tag}：无 http(s) 外链`, !/<img[^>]+src\s*=\s*["']?https?:/i.test(dom) && !/href\s*=\s*["']?https?:/i.test(dom));
}
console.log(`\n===== check_v6 结果：PASS ${passes} / FAIL ${fails} =====`);
process.exit(fails ? 1 : 0);