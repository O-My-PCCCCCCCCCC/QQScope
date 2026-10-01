#!/usr/bin/env node
/* QQScope v4 媒体渲染断言
 * 用法：node check_v4.mjs [web/index.html] [dom-v4-media-demo.html] [其它 dom ...]
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

section("v4 源码：媒体渲染器");
const src = fs.readFileSync(SRC, "utf8");
ok("parseMedia 容错解析 media JSON", /function parseMedia\(v\)/.test(src));
ok("mediaHTML 按 kind 渲染", /function mediaHTML\(m\)/.test(src));
["image", "sticker", "voice", "video", "file"].forEach((k) => ok(`覆盖 kind=${k}`, src.includes('"' + k + '"')));
ok("图片/视频走 <img src=\"/api/media/", /<img class="[^"]*m-img[^"]*" src="' \+ url/.test(src) || src.includes('src="\' + url + \'"'));
ok("<audio controls> 语音播放器", /<audio[^>]*controls[^>]*preload="none"/.test(src));
ok("文件卡片 name/size/下载", src.includes("m-file") && src.includes('download>下载'));
ok("占位徽章 media-badge", src.includes("media-badge") && src.includes("·未缓存"));
ok("data-state=missing 隐藏媒体", src.includes('.media-box[data-state="missing"]'));
ok("QQMediaErr 替换破图", /window\.QQMediaErr\s*=/.test(src));
ok("图片灯箱 QQLightboxOpen/Close", /window\.QQLightboxOpen/.test(src) && /window\.QQLightboxClose/.test(src));
ok("IntersectionObserver 懒探测", /IntersectionObserver/.test(src) && /data-media-probe/.test(src));
ok("会话列表媒体摘要", /function mediaSummary/.test(src) && /last_media/.test(src));
ok("总览媒体命中率卡（接口没好则隐藏）", src.includes("mediaStatsCard") && src.includes("/api/media/stats"));
ok("接口路径 /api/media/{id}", src.includes('"/api/media/"'));
ok("不直连外部 CDN", !/qlogo\.cn|qpic\.cn|https?:\/\//.test(src.replace(/https?:\/\/[^"']*api\.deepseek[^"']*/g, "")));

section("v4 源码：v3 HUD 风格与硬约束未回退");
ok("kei 令牌仍在", src.includes("--cyan:#5ad2ff") && src.includes("--bg-0:#05080f"));
ok("直角 3px 仍在", /--r-sm:\s*3px/.test(src));
ok("占位符两行逐字", /\n\s*window\.__QQSCOPE_BOOT__\s*=\s*\/\*__QQSCOPE_BOOT__\*\/\s*null\s*;\s*\n/.test(src) && /\n\s*\/\*__QQSCOPE_ENGINE__\*\/\s*\n/.test(src));
ok("无 <script src=", !/<script[^>]+src\s*=/i.test(src));

for (const f of DOMS) {
  if (!fs.existsSync(f)) { fails++; console.log(`  FAIL  找不到 ${f}`); continue; }
  const dom = fs.readFileSync(f, "utf8");
  const tag = path.basename(f);
  section(`v4 DOM：${tag}`);
  const err = dom.match(/data-js-errors="(\d+)"/);
  ok(`${tag}：data-js-errors=0`, !err || err[1] === "0", err ? "=" + err[1] : "无标记");
  ok(`${tag}：无 <script src=`, !/<script[^>]+src\s*=/i.test(dom));
  ok(`${tag}：无 <img src="http`, !/<img[^>]+src\s*=\s*["']?https?:/i.test(dom));
  ok(`${tag}：无 href="http`, !/href\s*=\s*["']?https?:/i.test(dom));
  if (/media-demo/.test(tag)) {
    const imgs = dom.match(/<img class="media-el[^"]*"[^>]*(?:data-media-src|src)="\/api\/media\//g) || [];
    const audios = dom.match(/<audio[^>]*src="\/api\/media\//g) || [];
    const badges = dom.match(/class="media-badge"/g) || [];
    const missing = dom.match(/data-state="missing"/g) || [];
    ok(`${tag}：<img src=/api/media/ ×${imgs.length}`, imgs.length >= 3, `找到 ${imgs.length}`);
    ok(`${tag}：<audio src=/api/media/ ×${audios.length}`, audios.length >= 2, `找到 ${audios.length}`);
    ok(`${tag}：占位徽章 ×${badges.length}`, badges.length >= 6, `找到 ${badges.length}`);
    ok(`${tag}：缺失态 data-state=missing ×${missing.length}`, missing.length >= 6, `找到 ${missing.length}`);
    ok(`${tag}：图片占位文案 [图片·未缓存]`, dom.includes("[图片·未缓存]"));
    ok(`${tag}：语音占位文案 [语音…·未缓存]`, /\[语音 ?\d*&quot;?·未缓存\]/.test(dom) || (dom.includes("语音") && dom.includes("未缓存")));
    ok(`${tag}：文件卡片含文件名`, dom.includes("期末复习资料.pdf"));
    ok(`${tag}：card kind 显示 fallback`, dom.includes("[名片] 某同学"));
  }
}

console.log(`\n===== check_v4 结果：PASS ${passes} / FAIL ${fails} =====`);
process.exit(fails ? 1 : 0);