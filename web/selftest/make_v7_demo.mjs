#!/usr/bin/env node
/* v7 交互夹具（只做导航/点击，不伪造数据）
 * 产物：out/demo-v7-{emoji,console,sources,sync}.html
 * 探针写进 <html data-*>，供 --dump-dom 断言。
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..", "..");
const dist = path.join(root, "app", "dist", "QQScope.html");
const outDir = path.join(__dirname, "out");
if (!fs.existsSync(dist)) { console.error("先构建 app/dist/QQScope.html"); process.exit(1); }
fs.mkdirSync(outDir, { recursive: true });
const html = fs.readFileSync(dist, "utf8");
function make(name, js) {
  const out = path.join(outDir, "demo-v7-" + name + ".html");
  fs.writeFileSync(out, html.replace("</body>", "<script>\n" + "function clickWhen(sel, cb) { var n = 0; var t = setInterval(function () { var el = document.querySelector(sel); if (el) { el.click(); clearInterval(t); if (cb) cb(el); } if (++n > 25) clearInterval(t); }, 600); }" + "\n" + js + "\n</script>\n</body>"), "utf8");
  console.log("[v7夹具] " + out);
}

make("emoji", `
setTimeout(function () { location.hash = "#/sessions"; }, 2200);
clickWhen('.conv-item[data-key="c2c|2378896156"]');
setInterval(function () {
  var e = document.querySelector(".qq-emoji");
  if (!e || e.getAttribute("data-focused")) return;
  e.setAttribute("data-focused", "1");
  e.scrollIntoView({ block: "center" });
  setTimeout(function () {
    var d = document.documentElement;
    d.setAttribute("data-emoji-count", String(document.querySelectorAll(".qq-emoji").length));
    d.setAttribute("data-emoji-title", e.getAttribute("title") || "");
    d.setAttribute("data-emoji-text", e.textContent || "");
    d.setAttribute("data-emoji-rect", JSON.stringify(e.getBoundingClientRect()));
  }, 80);
}, 500);
`);

make("console", `
setTimeout(function () { location.hash = "#/sources"; }, 2200);
setTimeout(function () { var b = document.getElementById("consoleFab"); if (b) b.click(); }, 3600);
setTimeout(function () {
  var d = document.documentElement;
  var p = document.getElementById("logPanel");
  var lines = document.querySelectorAll(".log-line");
  d.setAttribute("data-console-on", (p && p.classList.contains("on")) ? "1" : "0");
  d.setAttribute("data-log-count", String(lines.length));
  d.setAttribute("data-log-total", (document.getElementById("logCount") || {}).textContent || "");
  var first = lines[0];
  d.setAttribute("data-log-first", first ? (first.textContent || "").slice(0, 120) : "");
}, 7000);
`);

make("sources", `
setTimeout(function () { location.hash = "#/sources"; }, 2200);
setTimeout(function () {
  var d = document.documentElement;
  d.setAttribute("data-checked", (document.getElementById("sourcesCheckedAt") || {}).textContent || "");
  d.setAttribute("data-bot-info", (document.getElementById("botInfo") || {}).textContent || "");
  d.setAttribute("data-badges", (document.getElementById("badge-pack") ? document.getElementById("badge-pack").textContent : "") + "/" + (document.getElementById("badge-bot") ? document.getElementById("badge-bot").textContent : ""));
}, 9000);
`);

make("sync", `
setTimeout(function () { location.hash = "#/sessions"; }, 2200);
setTimeout(function () {
  var d = document.documentElement;
  d.setAttribute("data-sync-main", (document.getElementById("syncBarMain") || {}).textContent || "");
  d.setAttribute("data-src-badges", (document.getElementById("syncBarSrc") || {}).textContent || "");
  d.setAttribute("data-conv-src-badges", String(document.querySelectorAll(".conv-item .src-badge").length));
}, 6000);
`);