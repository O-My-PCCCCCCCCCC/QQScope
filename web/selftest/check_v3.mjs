#!/usr/bin/env node
/* QQScope v3 验收红线（全息 HUD 换肤 + 私聊真人头像）
 * 用法：node check_v3.mjs [web/index.html] [dom-*.html ...]
 * 退出码 0 = 全部通过。
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
const compact = (s) => s.replace(/\s+/g, "");

section("v3 红线：参考项目 kei-showcase 风格");
const src = fs.readFileSync(SRC, "utf8");
const cs = compact(src);
const kei = ["--bg-0:#05080f", "--bg-1:#0a1120", "--bg-2:#101a2d",
  "--cyan:#5ad2ff", "--cyan-dim:#2f9fd0", "--ice:#cdefff",
  "--violet:#8b7dff", "--magenta:#ff6bd6",
  "--text:#dceaf8", "--text-dim:#8ba0ba", "--text-faint:#61748c",
  "--line:rgba(122,186,240,.16)", "--line-strong:rgba(140,210,255,.42)", "--line-soft:rgba(122,186,240,.08)"];
ok("kei 冻结令牌逐字照搬", kei.every((t) => cs.includes(compact(t))), kei.filter((t) => !cs.includes(compact(t))).join(", "));
ok("直角 3px（不再圆角 SaaS）", /--r-sm:\s*3px/.test(src) && /--r:\s*3px/.test(src) && !/--r(-sm|-md|-lg|-xl)?:\s*(8|10|12|14|16)px/.test(src));
ok("主色 cyan #5ad2ff（无靛蓝 #5b7cfa）", cs.includes("#5ad2ff") && !cs.includes("#5b7cfa"));
ok("violet/magenta 仅作点缀", cs.includes("--violet:#8b7dff") && cs.includes("--magenta:#ff6bd6"));
ok("发丝描边 1px solid var(--line)", /1px solid var\(--line\)/.test(src));
ok("激活扫描条渐变 + 左缘 cyan", /linear-gradient\(90deg,\s*rgba\(90,210,255,\.16\),\s*transparent/.test(src) && /border-left-color:var\(--cyan\)/.test(src));
ok("辉光 0 0 16px rgba(90,210,255,.18)", /0 0 16px rgba\(90,210,255,\.18\)/.test(src));
ok("氛围层 bg-glow + scanlines", /\.bg-glow\s*\{/.test(src) && /\.scanlines\s*\{/.test(src));
ok("结构 masthead / rail / console", /class="masthead"/.test(src) && /class="rail"/.test(src) && /class="console"/.test(src));
ok("英文眉标 + 大写字距", /letter-spacing:\s*\.(1[0-9]|2[0-9]|3[0-9])em/.test(src));
ok("等宽数字 tabular-nums", /tabular-nums/.test(src) && /var\(--mono\)/.test(src));
ok("浅色 paper/ink/sage 直角风格", cs.includes('[data-theme="light"]') && cs.includes("--paper:#f2f3f0") &&
  cs.includes("--paper-2:#e8eae6") && cs.includes("--ink:#202726") && cs.includes("--sage:#5f7974"));

section("v3 红线：私聊头像修复");
ok("pidOf(c) 存在且按 kind 取号", /function pidOf\(c\)\s*\{/.test(src));
ok("头像调用点不再直接传 c.peer_id", (src.match(/avatarHTML\([^)]*c\.kind,\s*c\.peer_id/g) || []).length === 0);
ok("头像仍走 /api/avatar?qq=", src.includes("/api/avatar?qq="));
ok("头像仍走 /api/avatar/group?group=", src.includes("/api/avatar/group?group="));
ok("onerror 首字圆形兜底", /QQAvatarErr/.test(src));

section("v3 红线：占位符 / 零外部请求");
const lines = src.split(/\r?\n/);
ok("BOOT 占位符独立成行", lines.filter((l) => /^\s*window\.__QQSCOPE_BOOT__\s*=\s*\/\*__QQSCOPE_BOOT__\*\/\s*null\s*;\s*$/.test(l)).length === 1);
ok("ENGINE 占位符独立成行", lines.filter((l) => /^\s*\/\*__QQSCOPE_ENGINE__\*\/\s*$/.test(l)).length === 1);
ok("无 <script src=", !/<script[^>]+src\s*=/i.test(src));
ok('无 <img src="http / href="http', !/<img[^>]+src\s*=\s*["']?https?:/i.test(src) && !/href\s*=\s*["']?https?:/i.test(src));
ok("源码无字面 http(s)://", (src.match(/https?:\/\//g) || []).length === 0);

if (DOMS.length) {
  section("v3 红线：渲染后 DOM");
  for (const f of DOMS) {
    if (!fs.existsSync(f)) { fails++; console.log(`  FAIL  找不到 ${f}`); continue; }
    const dom = fs.readFileSync(f, "utf8");
    const tag = path.basename(f);
    const err = dom.match(/data-js-errors="(\d+)"/);
    ok(`${tag}：data-js-errors=0`, !err || err[1] === "0", err ? "=" + err[1] : "无标记");
    ok(`${tag}：两套主题令牌都在`, dom.includes("#05080f") && dom.includes("#f2f3f0"));
    if (/sessions/.test(tag)) {
      const av = dom.match(/<img src="\/api\/avatar\?qq=/g) || [];
      ok(`${tag}：私聊真人头像 <img src="/api/avatar?qq=" ×${av.length}`, av.length >= 10, `找到 ${av.length}`);
      const g = dom.match(/<div class="conv-group" data-group=/g) || [];
      ok(`${tag}：私聊/群聊/其他 三组`, g.length >= 3, `找到 ${g.length}`);
      ok(`${tag}：三组标题文字`, dom.includes(">私聊<") && dom.includes(">群聊<") && dom.includes(">其他<"));
    }
  }
}

console.log(`\n===== check_v3 结果：PASS ${passes} / FAIL ${fails} =====`);
process.exit(fails ? 1 : 0);