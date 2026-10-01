#!/usr/bin/env node
/* QQScope 前端自测：零外部请求 / 占位符独立成行 / 内联脚本语法。
 * 用法：node check_web.mjs [web/index.html] [built.html ...]
 * 退出码 0 = 全部通过，1 = 有失败项。
 *
 * 说明：构建产物会把后端联系人头像 URL、AI base URL 等作为「数据」写进 boot JSON。
 * 这些 URL 只是字符串数据，前端不会渲染成 <img>/<script>/<link> 资源，也不会请求它们；
 * 因此本脚本把 boot JSON 内的 URL 与「真正的外部资源引用」分开判定。
 */
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..", "..");
const args = process.argv.slice(2);
const files = args.length ? args : [path.join(root, "web", "index.html")];

let failures = 0;
function ok(name, cond, detail) {
  if (cond) console.log(`  PASS  ${name}`);
  else { failures++; console.log(`  FAIL  ${name}${detail ? "  -> " + detail : ""}`); }
}
function section(t) { console.log(`\n[${t}]`); }

const RESOURCE_RE = /<script[^>]+src\s*=|@import|<link\b|<img[^>]+src\s*=\s*["']?https?:|url\(\s*["']?https?:|href\s*=\s*["']?https?:/i;
const BOOT_JSON_RE = /window\.__QQSCOPE_BOOT__\s*=\s*(\{[\s\S]*?\});/;

for (const file of files) {
  if (!fs.existsSync(file)) { console.log(`跳过（不存在）：${file}`); continue; }
  const html = fs.readFileSync(file, "utf8");
  // 产物判据：只认「独立成行的 ENGINE 占位符」是否存在；web/index.html 的 escRe() 里也含该字面量，
  // 旧的「字符串包含」启发式会把产物误判为源码态（v12 定位的假阳性）。
  const isBuilt = !/^\s*\/\*__QQSCOPE_ENGINE__\*\/\s*$/m.test(html);
  console.log(`\n===== ${path.relative(root, file)}  (${(html.length / 1024).toFixed(0)} KB) =====`);

  section("外部请求");
  const resHit = html.match(RESOURCE_RE);
  ok("无外部资源引用（script src / link / @import / img / url() / href=http）", !resHit, resHit ? resHit[0] : "");
  const bootMatch = html.match(BOOT_JSON_RE);
  const bootJson = bootMatch ? bootMatch[1] : "";
  const rest = bootJson ? html.split(bootJson).join("") : html;
  const outUrls = (rest.match(/https?:\/\//g) || []).length;
  const inBoot = (bootJson.match(/https?:\/\//g) || []).length;
  ok("除注入数据外无 http(s)://", outUrls === 0, `发现 ${outUrls} 处`);
  if (inBoot) console.log(`  NOTE  注入数据（boot JSON）内含 ${inBoot} 个 URL（头像 / AI base 等），仅作字符串数据，前端不会渲染为资源、不会请求`);

  section("注入占位符（源码必须保留；产物必须已被替换）");
  const lines = html.split(/\r?\n/);
  const bootExact = lines.map((l, i) => [i + 1, l]).filter(([, l]) => /^\s*window\.__QQSCOPE_BOOT__\s*=\s*\/\*__QQSCOPE_BOOT__\*\/\s*null\s*;\s*$/.test(l));
  const engineExact = lines.map((l, i) => [i + 1, l]).filter(([, l]) => /^\s*\/\*__QQSCOPE_ENGINE__\*\/\s*$/.test(l));
  if (isBuilt) {
    ok("boot 占位符注释已替换", !/\/\*__QQSCOPE_BOOT__\*\//.test(html), "仍残留 /*__QQSCOPE_BOOT__*/");
    ok("boot 值为 null / 对象 / 数组", /window\.__QQSCOPE_BOOT__\s*=\s*(null|[\[{])/.test(html), "值异常");
    ok("engine 占位符已替换", engineExact.length === 0, "仍残留 /*__QQSCOPE_ENGINE__*/");
    ok("产物中无残留兜底 null（boot 行）", bootExact.length === 0, `第 ${bootExact.map((x) => x[0]).join(",")} 行仍残留`);
  } else {
    ok("boot 占位符独立成行且格式精确", bootExact.length === 1, `匹配 ${bootExact.length} 行`);
    ok("engine 占位符独立成行", engineExact.length === 1, `匹配 ${engineExact.length} 行`);
    if (bootExact.length === 1) ok("boot 行后无多余兜底值", /null\s*;\s*$/.test(lines[bootExact[0][0] - 1]));
  }

  section("内联脚本语法");
  const re = /<script\b[^>]*>([\s\S]*?)<\/script>/gi;
  let m, idx = 0;
  while ((m = re.exec(html)) !== null) {
    idx++;
    const body = m[1];
    const trimmed = body.replace(/\/\*__QQSCOPE_ENGINE__\*\//, "/* engine injected */");
    try {
      new vm.Script(trimmed, { filename: `${path.basename(file)}#script${idx}` });
      console.log(`  PASS  脚本块 ${idx} 语法 OK（${(body.length / 1024).toFixed(1)} KB）`);
    } catch (e) {
      failures++;
      console.log(`  FAIL  脚本块 ${idx} 语法错误：${e.message}`);
    }
  }
  ok("脚本块数量 >= 3", idx >= 3, `实际 ${idx}`);

  section("无头浏览器错误采集钩子");
  ok("包含 window error / unhandledrejection 采集（#jsErrorLog）", /jsErrorLog/.test(html) && /unhandledrejection/.test(html));
}

console.log(`\n===== 结果：${failures === 0 ? "全部通过 ✅" : failures + " 项失败 ❌"} =====`);
process.exit(failures === 0 ? 0 : 1);