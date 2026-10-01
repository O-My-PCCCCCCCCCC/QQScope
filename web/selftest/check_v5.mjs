#!/usr/bin/env node
/* QQScope v5 语音文字断言：文字为主 / 音频为辅 / 未转文字占位
 * 用法：node check_v5.mjs [web/index.html] [dom ...]
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

section("v5 源码：语音文字为主、音频为辅");
const src = fs.readFileSync(SRC, "utf8");
ok("读取 voice_text / voice_lang / voice_engine", /voice_text/.test(src) && /voice_lang/.test(src) && /voice_engine/.test(src));
ok("主显示 .voice-text（文字气泡样式）", src.includes(".voice-text") && src.includes('class="voice-text"'));
ok("文字可选中复制（user-select:text）", /\.voice-text\{[^}]*user-select:text/.test(src));
ok("副显示小播放按钮 .voice-play", src.includes(".voice-play") && src.includes("voice-play"));
ok("点击才展开 <audio>（QQVoiceToggle + preload=none + hidden）", /QQVoiceToggle/.test(src) && /preload="none"/.test(src) && /hidden><\/audio>/.test(src));
ok("未转文字占位标签 voiceLabel", /function voiceLabel/.test(src) && src.includes("·未转文字"));
ok("非中文提示（voice_lang 判定）", /function voiceLangNote/.test(src) && src.includes("可能是非中文"));
ok("404 时按钮禁用 + 未缓存标记", src.includes("voice-missing") && /data-state="missing"\] \.voice-play/.test(src));
ok("会话摘要支持 [语音] + 文字", /\[语音\]/.test(src) && /md\.voice_text/.test(src));
ok("语音转写统计 /api/voice/stats（没好则隐藏该行）", src.includes("/api/voice/stats"));
ok("列表最后一条懒探测（不为 120 会话各发一次）", /function probeLastMsg/.test(src) && /function observeLastMsgs/.test(src) && /lastProbeN >= 60/.test(src));
ok("接口仍走 /api/media/{id}", src.includes('"/api/media/"'));
ok("占位符两行逐字 / 无外链", /\n\s*window\.__QQSCOPE_BOOT__\s*=\s*\/\*__QQSCOPE_BOOT__\*\/\s*null\s*;\s*\n/.test(src) && !/<script[^>]+src\s*=/i.test(src));
ok("kei HUD 风格未回退", src.includes("--cyan:#5ad2ff") && /--r-sm:\s*3px/.test(src));

for (const f of DOMS) {
  if (!fs.existsSync(f)) { fails++; console.log(`  FAIL  找不到 ${f}`); continue; }
  const dom = fs.readFileSync(f, "utf8");
  const tag = path.basename(f);
  section(`v5 DOM：${tag}`);
  const err = dom.match(/data-js-errors="(\d+)"/);
  ok(`${tag}：data-js-errors=0`, !err || err[1] === "0", err ? "=" + err[1] : "无标记");
  ok(`${tag}：无 <script src=`, !/<script[^>]+src\s*=/i.test(dom));
  ok(`${tag}：无外链 http`, !/<img[^>]+src\s*=\s*["']?https?:/i.test(dom) && !/href\s*=\s*["']?https?:/i.test(dom));
  const hasVoice = dom.includes('class="voice-main"') || dom.includes("voice-main");
  ok(`${tag}：存在语音条目 .voice-main`, hasVoice);
  if (hasVoice) {
    ok(`${tag}：语音播放按钮 .voice-play`, dom.includes("voice-play"));
    ok(`${tag}：<audio src=/api/media/`, /<audio[^>]*src="\/api\/media\//.test(dom));
    const hasText = dom.includes('class="voice-text"');
    const hasBadge = dom.includes("·未转文字");
    ok(`${tag}：语音文字 .voice-text 或 [语音·未转文字] 占位`, hasText || hasBadge, `text=${hasText} badge=${hasBadge}`);
    if (/voice-text/.test(tag)) {
      var tms = [], tre = /<span class="voice-text"[^>]*>([^<]*)<\/span>/g, tm;
      while ((tm = tre.exec(dom)) !== null) { if (tm[1].indexOf("esc(vtext)") < 0 && tm[1].trim()) tms.push(tm[1]); }
      ok(`${tag}：真实识别文字 .voice-text ×${tms.length}`, tms.length >= 1, tms.join(" | ").slice(0, 80));
    }
    ok(`${tag}：绝不出现破图（无 onerror 裸露 img）`, !/<img[^>]*src="\/api\/media\/[^"]*"[^>]*onerror="QQMediaErr"[^>]*>\s*<\/span>\s*<span class="media-badge" hidden/.test(dom));
    var rectM = dom.match(/data-voice-rect="([^"]*)"/);
    if (rectM) {
      try {
        var rect = JSON.parse(rectM[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&"));
        ok(`${tag}：语音气泡在截图视口内 (y=${Math.round(rect.top)})`,
          rect.top >= 0 && rect.bottom <= 2000 && rect.left >= 0 && rect.right <= 1600, JSON.stringify(rect));
      } catch (e) { ok(`${tag}：voice-rect 可解析`, false, e.message); }
    }
  }
  if (/media-demo/.test(tag)) {
    ok(`${tag}：自检含 voice_text 文字节点`, dom.includes("今天晚上终于可以画画了"));
    ok(`${tag}：自检含非中文提示`, dom.includes("可能是非中文"));
    ok(`${tag}：自检含未转文字占位`, dom.includes("·未转文字"));
  }
}

console.log(`\n===== check_v5 结果：PASS ${passes} / FAIL ${fails} =====`);
process.exit(fails ? 1 : 0);