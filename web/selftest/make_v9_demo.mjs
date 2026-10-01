#!/usr/bin/env node
/* v9 交互夹具（只做导航/点击；send 演示只发给已授权的小号 3060648699）
 * 产物：out/demo-v9-{home,sources,consolefw,export,send,autorefresh}.html
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
const CLICK = 'function clickWhen(sel, cb) { var n = 0; var t = setInterval(function () { var el = document.querySelector(sel); if (el) { el.click(); clearInterval(t); if (cb) cb(el); } if (++n > 30) clearInterval(t); }, 600); }';
function make(name, js) {
  const out = path.join(outDir, "demo-v9-" + name + ".html");
  fs.writeFileSync(out, html.replace("</body>", "<script>\n" + CLICK + "\n" + js + "\n</script>\n</body>"), "utf8");
  console.log("[v9夹具] " + out);
}

make("home", `
setTimeout(function () { location.hash = "#/overview"; }, 2000);
setTimeout(function () {
  var d = document.documentElement, on = document.querySelector(".view.on");
  d.setAttribute("data-home-view", on ? on.getAttribute("id") : "");
  d.setAttribute("data-kpi", String(document.querySelectorAll(".kpi").length));
  var nav = document.querySelector("#nav .nav-item");
  d.setAttribute("data-first-nav", nav ? nav.getAttribute("data-page") : "");
}, 6000);
`);

make("sources", `
setTimeout(function () { location.hash = "#/sources"; }, 2000);
setTimeout(function () {
  var d = document.documentElement, det = document.getElementById("srcDetail");
  d.setAttribute("data-src-summary", String(document.querySelectorAll(".src-summary").length));
  d.setAttribute("data-src-detail-hidden", (det && det.hasAttribute("hidden")) ? "1" : "0");
  d.setAttribute("data-src-checked", (document.getElementById("sourcesCheckedAt") || {}).textContent || "");
}, 7000);
`);

make("consolefw", `
setTimeout(function () { location.hash = "#/sources"; }, 2000);
setTimeout(function () { var b = document.getElementById("consoleFab"); if (b) b.click(); }, 4000);
setTimeout(function () { var t = document.querySelector('#logTabs button[data-tab="framework"]'); if (t) t.click(); }, 4600);
setTimeout(function () {
  var d = document.documentElement;
  d.setAttribute("data-fw-tab", (document.querySelector('#logTabs button[data-tab="framework"]') || {}).className || "");
  d.setAttribute("data-fw-status", (document.getElementById("fwStatus") || {}).textContent || "");
  d.setAttribute("data-fw-lines", String(document.querySelectorAll("#logList .log-line").length));
  d.setAttribute("data-fw-empty", (document.querySelector("#logList .log-empty") || {}).textContent || "");
}, 9000);
`);

make("export", `
setTimeout(function () { location.hash = "#/export"; }, 2000);
setTimeout(function () { var c = document.querySelector('#expRange .chip[data-range="7"]'); if (c) c.click(); }, 3600);
setTimeout(function () {
  var d = document.documentElement;
  d.setAttribute("data-exp-range", (document.querySelector("#expRange .chip.on") || {}).textContent || "");
  d.setAttribute("data-exp-hint", (document.getElementById("expRangeHint") || {}).textContent || "");
  d.setAttribute("data-exp-groups", String(document.querySelectorAll(".exp-group-head").length));
  d.setAttribute("data-exp-quick", (document.getElementById("expAllC2C") ? "1" : "0") + (document.getElementById("expAllGroup") ? "1" : "0"));
}, 7000);
`);

make("send", `
window.__QQSCOPE_SEND_DRY_RUN = true;
setTimeout(function () { location.hash = "#/sessions"; }, 2000);
clickWhen('.conv-item[data-key="c2c|3060648699"]', function () {
  var tries = 0;
  var iv = setInterval(function () {
    var ta = document.getElementById("sendText");
    if (!ta || ta.disabled) { if (++tries > 20) clearInterval(iv); return; }
    clearInterval(iv);
    ta.value = "【QQScope v9 自测·dry_run】网页端发送验证 " + new Date().toLocaleTimeString();
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    document.getElementById("btnSend").click();
    setTimeout(function () {
      var m = document.getElementById("sendConfirm");
      document.documentElement.setAttribute("data-confirm-open", (m && m.classList.contains("on")) ? "1" : "0");
      document.documentElement.setAttribute("data-confirm-text", (document.getElementById("sendConfirmBody") || {}).textContent || "");
    }, 400);
    setTimeout(function () { var ok = document.getElementById("sendOk"); if (ok) ok.click(); }, 800);
    setTimeout(function () {
      var d = document.documentElement;
      d.setAttribute("data-send-err", (document.getElementById("sendErr") || {}).textContent || "");
      var bubbles = document.querySelectorAll(".bubble");
      var last = bubbles[bubbles.length - 1];
      d.setAttribute("data-last-bubble", last ? (last.textContent || "").slice(-80) : "");
      d.setAttribute("data-self-bubbles", String(document.querySelectorAll(".bubble.self").length));
      d.setAttribute("data-input-clear", (document.getElementById("sendText") || {}).value === "" ? "1" : "0");
    }, 6000);
  }, 500);
});
`);

make("autorefresh", `
try { localStorage.setItem("qqscope_refresh_sec", "5"); localStorage.setItem("qqscope_auto_refresh", "1"); } catch (e) {}
setTimeout(function () { location.hash = "#/sessions"; }, 2000);
clickWhen('.conv-item[data-key="c2c|3060648699"]', function () {
  setTimeout(function () {
    var box = document.getElementById("bubbleList");
    if (box) box.scrollTop = 0;
    /* 不再进行任何真实发送；仅观察轮询/最后更新时间 */
  }, 3000);
  setTimeout(function () {
    var d = document.documentElement, pill = document.getElementById("newMsgPill");
    d.setAttribute("data-pill-on", (pill && pill.classList.contains("on")) ? "1" : "0");
    d.setAttribute("data-pill-text", pill ? (pill.textContent || "") : "");
    d.setAttribute("data-updated-at", (document.getElementById("sessionsUpdatedAt") || {}).textContent || "");
    d.setAttribute("data-auto-refresh", (document.getElementById("autoRefreshToggle") || {}).checked ? "1" : "0");
  }, 26000);
});
`);