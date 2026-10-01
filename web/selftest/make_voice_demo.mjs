#!/usr/bin/env node
/* 真机语音会话导航夹具：只注入「打开指定会话」的脚本，不伪造任何数据。
 * 产物：web/selftest/out/demo-v5-voice-group.html / demo-v5-voice-c2c.html
 * 通过 proxy_server.mjs 托管时，/api/* 仍打到真实后端 http://127.0.0.1:15555。
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..", "..");
const dist = path.join(root, "app", "dist", "QQScope.html");
const outDir = path.join(__dirname, "out");
if (!fs.existsSync(dist)) { console.error("找不到构建产物，请先跑 scripts/build_web.py"); process.exit(1); }
fs.mkdirSync(outDir, { recursive: true });
const html = fs.readFileSync(dist, "utf8");

function make(name, targets) {
  const js = `
<script>
/* 真机夹具：只做导航（点开指定会话），不含任何假数据 */
(function () {
  var TARGETS = ${JSON.stringify(targets)};
  function pick() {
    var items = document.querySelectorAll(".conv-item");
    for (var n = 0; n < TARGETS.length; n++) {
      for (var i = 0; i < items.length; i++) {
        if ((items[i].textContent || "").indexOf(TARGETS[n]) >= 0) { items[i].click(); return true; }
      }
    }
    return false;
  }
  setTimeout(function () { location.hash = "#/sessions"; }, 2200);
  var tries = 0;
  var pickTimer = setInterval(function () {
    if (pick() || ++tries > 25) clearInterval(pickTimer);
  }, 600);
  /* 把语音气泡滚到视口中央，确保截图能拍到 */
  var focused = false;
  setInterval(function () {
    if (focused) return;
    var vt = document.querySelector(".voice-main .voice-text");
    var v = vt ? vt.closest(".voice-main") : document.querySelector(".voice-main");
    if (v && v.scrollIntoView) {
      v.scrollIntoView({ block: "center" });
      focused = true;
      /* 自检：把语音气泡的视口矩形写进 <html data-voice-rect>，供验收脚本确认截图能拍到 */
      setTimeout(function () { try { document.documentElement.setAttribute("data-voice-rect", JSON.stringify(v.getBoundingClientRect())); } catch (e) {} }, 60);
    }
  }, 900);
  /* 若首屏 200 条里没有语音，最多补点 3 次「加载更早」 */
  var tries = 0;
  setInterval(function () {
    if (document.querySelector(".voice-main") || document.querySelector(".voice-badge")) return;
    if (tries++ >= 3) return;
    var b = document.getElementById("btnLoadMore");
    if (b) b.click();
  }, 4200);
})();
</script>
`;
  const out = path.join(outDir, "demo-v5-voice-" + name + ".html");
  fs.writeFileSync(out, html.replace("</body>", js + "\n</body>"), "utf8");
  console.log("[真机语音夹具] " + out + "  targets=" + targets.join("/"));
}
make("text", ["苏然的小窝"]);
make("group", ["忧郁mbti群", "蔚蓝档案交流群", "MBTI聚贤茶舍"]);
make("c2c", ["韩凇凌"]);