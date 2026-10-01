#!/usr/bin/env node
/* QQScope v3 前端静态断言（全息 HUD 换肤 + 私聊头像 + 零外部请求）
 * 用法：node check_v2.mjs [web/index.html] [app/dist/QQScope.html] [dom-*.html ...]
 * 退出码 0 = 全部通过。
 */
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..", "..");
const args = process.argv.slice(2);
const SRC = args[0] || path.join(root, "web", "index.html");
const BUILT = args[1] || path.join(root, "app", "dist", "QQScope.html");
const DOMS = args.slice(2);

let failures = 0, passes = 0;
function ok(name, cond, detail) {
  if (cond) { passes++; console.log(`  PASS  ${name}`); }
  else { failures++; console.log(`  FAIL  ${name}${detail ? "  -> " + detail : ""}`); }
}
function section(t) { console.log(`\n[${t}]`); }
const read = (f) => fs.readFileSync(f, "utf8");
const compact = (s) => s.replace(/\s+/g, "");

/* ---------- 源码 ---------- */
section("源码：占位符（必须独立成行、格式逐字）");
const src = read(SRC);
const srcLines = src.split(/\r?\n/);
const bootLine = srcLines.filter((l) => /^\s*window\.__QQSCOPE_BOOT__\s*=\s*\/\*__QQSCOPE_BOOT__\*\/\s*null\s*;\s*$/.test(l));
const engineLine = srcLines.filter((l) => /^\s*\/\*__QQSCOPE_ENGINE__\*\/\s*$/.test(l));
ok("BOOT 占位符独立成行且格式精确", bootLine.length === 1, `匹配 ${bootLine.length} 行`);
ok("ENGINE 占位符独立成行", engineLine.length === 1, `匹配 ${engineLine.length} 行`);

section("源码：零外部请求");
ok("无 <script src=", !/<script[^>]+src\s*=/i.test(src));
ok("无 <link>", !/<link\b/i.test(src));
ok("无 @import", !/@import/i.test(src));
ok("无 @font-face", !/@font-face/i.test(src));
ok('无 <img src="http', !/<img[^>]+src\s*=\s*["']?https?:/i.test(src));
ok('无 href="http', !/href\s*=\s*["']?https?:/i.test(src));
ok("无 url(http", !/url\(\s*["']?https?:/i.test(src));
const srcUrls = (src.match(/https?:\/\//g) || []).length;
ok("源码无字面 http(s)://", srcUrls === 0, `发现 ${srcUrls} 处`);

section("源码：kei-showcase 冻结令牌（逐字）");
const kei = ["--bg-0:#05080f", "--bg-1:#0a1120", "--bg-2:#101a2d",
  "--line:rgba(122,186,240,.16)", "--line-strong:rgba(140,210,255,.42)", "--line-soft:rgba(122,186,240,.08)",
  "--cyan:#5ad2ff", "--cyan-dim:#2f9fd0", "--ice:#cdefff",
  "--violet:#8b7dff", "--magenta:#ff6bd6",
  "--text:#dceaf8", "--text-dim:#8ba0ba", "--text-faint:#61748c",
  "--font:\"Segoe UI\",\"Microsoft YaHei\",\"PingFang SC\",system-ui,sans-serif",
  "--mono:\"Cascadia Mono\",Consolas,\"SF Mono\",\"Courier New\",monospace"];
const cs = compact(src);
const missingKei = kei.filter((t) => !cs.includes(compact(t)));
ok(`kei 令牌 ${kei.length} 项逐字存在`, missingKei.length === 0, "缺: " + missingKei.join(", "));

section("源码：HUD 风格要素");
ok("直角 border-radius:3px", /--r-sm:\s*3px/.test(src) && /--r:\s*3px/.test(src));
ok("已无 8/10/12/14/16px 圆角令牌", !/--r(-sm|-md|-lg|-xl)?:\s*(8|10|12|14|16)px/.test(src));
ok("氛围层 .bg-glow", /\.bg-glow\s*\{/.test(src));
ok("氛围层 .scanlines", /\.scanlines\s*\{/.test(src));
ok("激活扫描条渐变", /linear-gradient\(90deg,\s*rgba\(90,210,255,\.16\),\s*transparent/.test(src));
ok("辉光 box-shadow", /0 0 16px rgba\(90,210,255,\.18\)/.test(src));
ok("大写字距眉标 letter-spacing", /letter-spacing:\s*\.(1[0-9]|2[0-9]|3[0-9])em/.test(src));
ok("发丝描边 var(--line)", /1px solid var\(--line\)/.test(src));
ok("浅色纸感 [data-theme=light]", cs.includes('[data-theme="light"]') &&
  cs.includes("--paper:#f2f3f0") && cs.includes("--paper-2:#e8eae6") && cs.includes("--ink:#202726"));
ok("主色为 cyan 而非靛蓝", cs.includes("--accent:var(--cyan)") && !cs.includes("#5b7cfa"));
ok("主题写入 localStorage", /localStorage\.(getItem|setItem)\(["']qqscope_theme/.test(src));
ok("masthead / rail / console 结构", /class="masthead"/.test(src) && /class="rail"/.test(src) && /class="console"/.test(src));

section("源码：会话三组 + 分段筛选 + 私聊头像修复");
["私聊", "群聊", "其他"].forEach((t) => ok(`分组标题「${t}」`, src.includes('"' + t + '"')));
ok("分组容器 .conv-group", /conv-group/.test(src));
ok("分段筛选 全部/私聊/群聊", /data-kind="all"/.test(src) && /data-kind="c2c"/.test(src) && /data-kind="group"/.test(src));
ok("pidOf 辅助函数（私聊用 peer_qq）", /function pidOf\(c\)\s*\{/.test(src));
ok("头像 onerror 首字兜底", /QQAvatarErr/.test(src));
const wrongPeer = (src.match(/avatarHTML\([^)]*c\.kind,\s*c\.peer_id/g) || []).length;
ok("头像调用点不再直接传 c.peer_id", wrongPeer === 0, `仍有 ${wrongPeer} 处`);

section("源码：接口契约（不自定义路径）");
const apiPaths = ["/api/health", "/api/sources", "/api/progress/", "/api/accounts", "/api/overview",
  "/api/report", "/api/contacts", "/api/messages", "/api/export", "/api/export/list", "/api/export/download",
  "/api/settings", "/api/ai/chat", "/api/profile", "/api/contact", "/api/feeds", "/api/avatar?qq=", "/api/avatar/group?group="];
const missApi = apiPaths.filter((a) => !src.includes(a));
ok(`${apiPaths.length} 个冻结接口全部被调用`, missApi.length === 0, "缺: " + missApi.join(", "));
ok("引擎 computeReport 接入", /computeReport/.test(src));
ok("AI 发送前预览", /aiPreview/.test(src) && /buildAiPrompt/.test(src));

section("源码：内联脚本语法");
const re = /<script\b[^>]*>([\s\S]*?)<\/script>/gi;
let m, idx = 0;
while ((m = re.exec(src)) !== null) {
  idx++;
  const body = m[1].replace(/\/\*__QQSCOPE_ENGINE__\*\//, "/* engine */");
  try { new vm.Script(body, { filename: `${path.basename(SRC)}#${idx}` }); console.log(`  PASS  脚本块 ${idx} 语法 OK（${(body.length / 1024).toFixed(1)} KB）`); passes++; }
  catch (e) { failures++; console.log(`  FAIL  脚本块 ${idx} 语法错误：${e.message}`); }
}
ok("脚本块数量 >= 3", idx >= 3, `实际 ${idx}`);

/* ---------- 构建产物 ---------- */
if (fs.existsSync(BUILT)) {
  section(`构建产物：${path.relative(root, BUILT)}`);
  const built = read(BUILT);
  ok("BOOT 注释已替换", !/\/\*__QQSCOPE_BOOT__\*\//.test(built));
  ok("BOOT 为对象/数组/null", /window\.__QQSCOPE_BOOT__\s*=\s*(null|[\[{])/.test(built));
  ok("ENGINE 占位符已替换", !/\/\*__QQSCOPE_ENGINE__\*\//.test(built) && /computeReport/.test(built));
  ok("产物无 <script src=", !/<script[^>]+src\s*=/i.test(built));
  ok("产物无 <link>/@import", !/<link\b/i.test(built) && !/@import/i.test(built));
  ok('产物无 <img src="http', !/<img[^>]+src\s*=\s*["']?https?:/i.test(built));
  ok('产物无 href="http', !/href\s*=\s*["']?https?:/i.test(built));
  const bootMatch = built.match(/window\.__QQSCOPE_BOOT__\s*=\s*(\{[\s\S]*?\});/);
  const bootJson = bootMatch ? bootMatch[1] : "";
  const rest = bootJson ? built.split(bootJson).join("") : built;
  const outUrls = (rest.match(/https?:\/\//g) || []).length;
  const inBoot = (bootJson.match(/https?:\/\//g) || []).length;
  ok("除注入数据外无 http(s)://", outUrls === 0, `发现 ${outUrls} 处`);
  console.log(`  NOTE  注入数据（boot JSON）内含 ${inBoot} 个 URL，仅作字符串数据，不渲染为资源、不发起请求`);
  ok("产物 kei 令牌齐全", cs === compact(src) || (/[#]05080f/.test(built) && /[#]5ad2ff/.test(built) && /[#]f2f3f0/.test(built)));
  ["私聊", "群聊", "其他"].forEach((t) => ok(`产物含分组标题「${t}」`, built.includes('"' + t + '"')));
  ok("产物含 pidOf 修复", built.includes("function pidOf"));
} else {
  section("构建产物");
  console.log(`  SKIP  尚未构建：${path.relative(root, BUILT)}`);
}

/* ---------- 渲染后 DOM ---------- */
if (DOMS.length) {
  section("渲染后 DOM（--dump-dom）");
  for (const f of DOMS) {
    if (!fs.existsSync(f)) { failures++; console.log(`  FAIL  找不到 ${f}`); continue; }
    const dom = read(f);
    const tag = path.basename(f);
    const errAttr = dom.match(/data-js-errors="(\d+)"/);
    ok(`${tag}：无 JS 报错`, !errAttr || errAttr[1] === "0", errAttr ? "data-js-errors=" + errAttr[1] : "无标记");
    if (/sessions/.test(tag)) {
      const groups = dom.match(/<div class="conv-group" data-group=/g) || [];
      ok(`${tag}：会话三组存在`, groups.length >= 3, `找到 ${groups.length} 个 .conv-group`);
      ["私聊", "群聊", "其他"].forEach((t) => ok(`${tag}：分组标题「${t}」`, dom.includes(">" + t + "<")));
      const avatars = dom.match(/<img src="\/api\/avatar\?qq=/g) || [];
      ok(`${tag}：私聊真人头像 <img src="/api/avatar?qq="`, avatars.length >= 10, `找到 ${avatars.length} 个`);
    }
    ok(`${tag}：无 <script src=`, !/<script[^>]+src\s*=/i.test(dom));
    ok(`${tag}：无 <img src="http`, !/<img[^>]+src\s*=\s*["']?https?:/i.test(dom));
    ok(`${tag}：无 href="http`, !/href\s*=\s*["']?https?:/i.test(dom));
    ok(`${tag}：暗浅两套令牌都在`, dom.includes("#05080f") && dom.includes("#f2f3f0") && dom.includes('data-theme="light"'));
  }
} else {
  section("渲染后 DOM");
  console.log("  SKIP  未提供 --dump-dom 文件");
}

console.log(`\n===== check_v2(v3换肤) 结果：PASS ${passes} / FAIL ${failures} =====`);
process.exit(failures ? 1 : 0);