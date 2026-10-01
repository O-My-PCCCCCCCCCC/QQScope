#!/usr/bin/env node
/* 媒体渲染自检夹具（不是真实数据！）
 * 目的：后端 /api/media 尚未就绪、库里 media 列也还没回填时，
 *       用一组合成的消息元数据驱动前端渲染器，验证：
 *       - 有 file 的图片/语音/文件/视频 → 生成 <img>/<audio> 节点
 *       - /api/media 404 → onerror/probe 落成 [xx·未缓存] 占位徽章，不出现破图
 *       - kind=card → fallback 文本气泡
 * 产物：web/selftest/out/demo-v4-media.html（仅供 run_v4 验收，不冒充真实媒体）
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..", "..");
const dist = path.join(root, "app", "dist", "QQScope.html");
const outDir = path.join(__dirname, "out");
const out = path.join(outDir, "demo-v4-media.html");
if (!fs.existsSync(dist)) { console.error("找不到构建产物，请先跑 scripts/build_web.py"); process.exit(1); }
fs.mkdirSync(outDir, { recursive: true });

let html = fs.readFileSync(dist, "utf8");

const inject = `
<script>
/* ===== 媒体渲染自检夹具（合成元数据；/api/media 一律 404 → 占位徽章路径） ===== */
(function () {
  var realFetch = window.fetch.bind(window);
  var now = Math.floor(Date.now() / 1000);
  function J(o, status) {
    return Promise.resolve(new Response(JSON.stringify(o), { status: status || 200, headers: { "Content-Type": "application/json" } }));
  }
  var MSGS = [
    { id: "900010", ts: now - 10, direction: 0, kind: "c2c", peer_id: "u_demo", peer_qq: 3647980032, text: "下面这些是【自检合成】的媒体元数据，本地均未缓存 → 全部走占位徽章。", media: null, sender_qq: 3647980032 },
    { id: "900013", ts: now - 15, direction: 0, kind: "c2c", peer_id: "u_demo", peer_qq: 3647980032, text: "", media: { kind: "voice", name: "v1.amr", size: 25190, duration: 3, file: "Ptt/2026-08/Ori/v1.amr", voice_text: "今天晚上终于可以画画了", voice_lang: "zh", voice_engine: "whisper-small", fallback: "[语音 3\\"]" }, sender_qq: 3647980032 },
    { id: "900012", ts: now - 18, direction: 1, kind: "c2c", peer_id: "u_demo", peer_qq: 3647980032, text: "", media: { kind: "voice", name: "v2.amr", size: 40000, duration: 12, file: "Ptt/2026-08/Ori/v2.amr", voice_text: "hello everyone nice to meet you", voice_lang: "en", voice_engine: "whisper-small", fallback: "[语音 12\\"]" }, sender_qq: 1605289411 },
    { id: "900009", ts: now - 20, direction: 0, kind: "c2c", peer_id: "u_demo", peer_qq: 3647980032, text: "", media: { kind: "image", name: "x.jpg", size: 123456, file: "Pic/2026-08/Ori/x.jpg", fallback: "[图片]" }, sender_qq: 3647980032 },
    { id: "900008", ts: now - 30, direction: 1, kind: "c2c", peer_id: "u_demo", peer_qq: 3647980032, text: "", media: { kind: "image", name: "y.png", size: 8888, file: null, fallback: "[图片]" }, sender_qq: 1605289411 },
    { id: "900007", ts: now - 40, direction: 0, kind: "c2c", peer_id: "u_demo", peer_qq: 3647980032, text: "", media: { kind: "voice", name: "z.amr", size: 25190, duration: 3, file: "Ptt/2026-08/Ori/z.amr", fallback: "[语音 3\\"]" }, sender_qq: 3647980032 },
    { id: "900006", ts: now - 50, direction: 1, kind: "c2c", peer_id: "u_demo", peer_qq: 3647980032, text: "", media: { kind: "voice", duration: 12, file: null, fallback: "[语音 12\\"]" }, sender_qq: 1605289411 },
    { id: "900005", ts: now - 60, direction: 0, kind: "c2c", peer_id: "u_demo", peer_qq: 3647980032, text: "", media: { kind: "file", name: "期末复习资料.pdf", size: 36000000, file: "File/2026-08/Ori/f.pdf", fallback: "[文件]" }, sender_qq: 3647980032 },
    { id: "900004", ts: now - 70, direction: 1, kind: "c2c", peer_id: "u_demo", peer_qq: 3647980032, text: "", media: { kind: "sticker", file: null, fallback: "[表情]" }, sender_qq: 1605289411 },
    { id: "900003", ts: now - 80, direction: 0, kind: "c2c", peer_id: "u_demo", peer_qq: 3647980032, text: "", media: { kind: "video", duration: 35, file: "Video/2026-08/Thumb/v_0.png", fallback: "[视频]" }, sender_qq: 3647980032 },
    { id: "900002", ts: now - 90, direction: 0, kind: "c2c", peer_id: "u_demo", peer_qq: 3647980032, text: "", media: { kind: "card", fallback: "[名片] 某同学" }, sender_qq: 3647980032 },
    { id: "900001", ts: now - 100, direction: 1, kind: "c2c", peer_id: "u_demo", peer_qq: 3647980032, text: "这条是普通文本消息", media: null, sender_qq: 1605289411 }
  ];
  var CONTACT = { account_qq: 0, kind: "c2c", peer_id: "u_demo", peer_qq: 3647980032, name: "媒体渲染自检", remark: "", msg_count: MSGS.length, self_count: 3, first_ts: now - 100, last_ts: now - 10, last_text: "" };
  window.fetch = function (url, opts) {
    var u = String(url);
    if (u.indexOf("/api/contacts") === 0) return J({ contacts: [CONTACT] });
    if (u.indexOf("/api/contact?") === 0) return J({ name: "媒体渲染自检", remark: null, peer_qq: 3647980032, kind: "c2c", msg_count: MSGS.length, self_count: 3, first_ts: now - 100, last_ts: now - 10, last_text: "", hourly: new Array(24).fill(2), top_words: [{ word: "自检", count: 9 }] });
    if (u.indexOf("/api/messages") === 0) return J({ messages: MSGS, total: MSGS.length, contact: CONTACT });
    if (u.indexOf("/api/media/") === 0) return J({ error: "本地未缓存（自检）" }, 404);
    return realFetch(url, opts);
  };
})();
</script>
`;

const click = `
<script>
setTimeout(function () {
  location.hash = "#/sessions";
  var it = document.querySelector(".conv-item");
  if (it) it.click();
}, 2800);
</script>
`;

html = html.replace("<body>", "<body>\n" + inject);
html = html.replace("</body>", click + "\n</body>");
fs.writeFileSync(out, html, "utf8");
fs.writeFileSync(path.join(outDir, "demo-v5-media.html"), html, "utf8");
console.log("[媒体自检夹具] 已写出 " + out + " （合成元数据，非真实媒体）");
