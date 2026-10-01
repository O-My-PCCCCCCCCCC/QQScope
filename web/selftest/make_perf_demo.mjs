#!/usr/bin/env node
/* 性能对比夹具：只做导航 + 计时探针，不伪造数据。
 * 产物：out/demo-v6-perf.html
 * 通过在 <html> 上写 data-perf-* 属性暴露：首屏渲染耗时 / 气泡数 / 已发请求的媒体 <img> 数 / 媒体节点总数。
 * 用法：node make_perf_demo.mjs "<目标会话名>"
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..", "..");
const dist = path.join(root, "app", "dist", "QQScope.html");
const outDir = path.join(__dirname, "out");
const target = process.argv[2] || "蔚蓝档案交流群";
if (!fs.existsSync(dist)) { console.error("先构建 app/dist/QQScope.html"); process.exit(1); }
const html = fs.readFileSync(dist, "utf8");

const js = `
<script>
(function () {
  var TARGET = ${JSON.stringify(target)};
  var t0 = performance.now();
  var dcl = 0;
  document.addEventListener("DOMContentLoaded", function () { dcl = Math.round(performance.now() - t0); });
  function pick() {
    var items = document.querySelectorAll(".conv-item");
    for (var i = 0; i < items.length; i++) {
      if ((items[i].textContent || "").indexOf(TARGET) >= 0) { window.__tClick = performance.now(); items[i].click(); return true; }
    }
    return false;
  }
  setTimeout(function () { location.hash = "#/sessions"; setTimeout(pick, 900); }, 2200);
  var last = -1, stable = 0;
  var iv = setInterval(function () {
    var n = document.querySelectorAll(".bubble").length;
    if (n > 0 && window.__tClick && !document.documentElement.getAttribute("data-perf-render-ms")) {
      document.documentElement.setAttribute("data-perf-render-ms", String(Math.round(performance.now() - window.__tClick)));
    }
    if (n === last && n > 0) stable++; else { stable = 0; last = n; }
    if (stable >= 3) {
      clearInterval(iv);
      setTimeout(record, 2500);
      return;
    }
  }, 300);
  function record() {
      var n = last;
      var d = document.documentElement;
      d.setAttribute("data-perf-dcl-ms", dcl);
      d.setAttribute("data-perf-first-render-ms", Math.round(performance.now() - t0));
      d.setAttribute("data-perf-bubbles", n);
      d.setAttribute("data-perf-msg-loaded", (window.__QQSCOPE_PERF && window.__QQSCOPE_PERF.messages) || "");
      d.setAttribute("data-perf-imgs-src", document.querySelectorAll('img[src*="/api/media/"]').length);
      d.setAttribute("data-perf-imgs-all", document.querySelectorAll("img[data-media-src], img[src*='/api/media/']").length);
      d.setAttribute("data-perf-dom-nodes", document.querySelectorAll("*").length);
      /* 自动点两次「加载更早」，量分页：真实渲染耗时 + DOM 新增节点数 */
      var box2 = document.getElementById("bubbleList");
      var added = 0;
      if (box2 && window.MutationObserver) {
        new MutationObserver(function (ms) { ms.forEach(function (m) { added += m.addedNodes.length; }); })
          .observe(box2, { childList: true });
      }
      var times = [];
      function oneLoadMore(cb) {
        var before = document.querySelectorAll(".bubble").length;
        var b = document.getElementById("btnLoadMore");
        if (!b) { cb(); return; }
        var t = performance.now();
        b.click();
        var iv = setInterval(function () {
          if (document.querySelectorAll(".bubble").length > before) { clearInterval(iv); times.push(Math.round(performance.now() - t)); cb(); }
        }, 25);
      }
      oneLoadMore(function () { oneLoadMore(function () {
        setTimeout(function () {
          d.setAttribute("data-perf-loadmore-ms", times.join(","));
          d.setAttribute("data-perf-loadmore-added", String(added));
          d.setAttribute("data-perf-final-bubbles", String(document.querySelectorAll(".bubble").length));
          d.setAttribute("data-perf-final-nodes", String(document.querySelectorAll("*").length));
        }, 500);
      }); });
      var box = document.getElementById("bubbleList");
      if (box) d.setAttribute("data-perf-bubbles-scroll", box.scrollHeight + "x" + box.clientHeight + "@" + Math.round(box.scrollTop));
      var vh = window.innerHeight || 0, near = 0, all = 0;
      document.querySelectorAll('img[data-media-src], img[src*="/api/media/"]').forEach(function (el) {
        all++;
        var r = el.getBoundingClientRect();
        if (!(r.bottom < -240 || r.top > vh + 240)) near++;
      });
      d.setAttribute("data-perf-rect-near", near + "/" + all + " vh=" + vh);
  }
})();
</script>
`;
const out = path.join(outDir, "demo-v6-perf.html");
fs.writeFileSync(out, html.replace("</body>", js + "\n</body>"), "utf8");
console.log("[性能夹具] " + out + "  target=" + target);