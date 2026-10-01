#!/usr/bin/env node
/* v8/v9 额外夹具：
 *  - demo-v9-sendconfirm.html：打开发送确认弹窗（不点确认，不发任何东西）
 *  - demo-v8-pill.html：用【合成消息】驱动轮询，演示「N 条新消息 ↓」胶囊（自检用，产物会明确标注）
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..", "..");
const dist = path.join(root, "app", "dist", "QQScope.html");
const outDir = path.join(__dirname, "out");
if (!fs.existsSync(dist)) { console.error("先构建"); process.exit(1); }
fs.mkdirSync(outDir, { recursive: true });
const html = fs.readFileSync(dist, "utf8");
const CLICK = 'function clickWhen(sel, cb) { var n = 0; var t = setInterval(function () { var el = document.querySelector(sel); if (el) { el.click(); clearInterval(t); if (cb) cb(el); } if (++n > 30) clearInterval(t); }, 600); }';
function make(name, js) {
  const out = path.join(outDir, "demo-" + name + ".html");
  fs.writeFileSync(out, html.replace("</body>", "<script>\n" + CLICK + "\n" + js + "\n</script>\n</body>"), "utf8");
  console.log("[extra夹具] " + out);
}

make("v9-sendconfirm", `
window.__QQSCOPE_SEND_DRY_RUN = true;   /* 若有任何误触也只会 dry_run */
setTimeout(function () { location.hash = "#/sessions"; }, 2000);
clickWhen('.conv-item[data-key="c2c|3060648699"]', function () {
  var iv = setInterval(function () {
    var ta = document.getElementById("sendText");
    if (!ta || ta.disabled) return;
    clearInterval(iv);
    ta.value = "【QQScope v9 自测·dry_run】确认弹窗演示（不会真实发送）";
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    document.getElementById("btnSend").click();
    setTimeout(function () {
      var d = document.documentElement, m = document.getElementById("sendConfirm");
      d.setAttribute("data-confirm-open", (m && m.classList.contains("on")) ? "1" : "0");
    }, 600);
  }, 500);
});
`);

make("v8-pill", `
/* 自检合成数据：仅用于演示新消息胶囊，不是真实消息 */
(function () {
  var real = window.fetch.bind(window);
  var now = Math.floor(Date.now() / 1000);
  function J(o) { return Promise.resolve(new Response(JSON.stringify(o), { status: 200, headers: { "Content-Type": "application/json" } })); }
  window.fetch = function (url, opts) {
    var u = String(url);
    if (u.indexOf("/api/contacts") === 0) return J({ contacts: [{ account_qq: 0, kind: "c2c", peer_id: "u_selftest", peer_qq: 10001, name: "【自检合成数据】", msg_count: 4, last_ts: now, self_count: 0, first_ts: now - 100, last_text: "", source: "selftest" }] });
    if (u.indexOf("/api/live/") === 0) return Promise.resolve(new Response(JSON.stringify({ error: "live 接口自检未启用" }), { status: 404, headers: { "Content-Type": "application/json" } }));
    if (u.indexOf("/api/contact?") === 0) return J({ name: "【自检合成数据】", kind: "c2c", peer_qq: 10001, msg_count: 4, self_count: 0, first_ts: now - 100, last_ts: now, last_text: "", hourly: new Array(24).fill(1), top_words: [] });
    if (u.indexOf("/api/messages") === 0) {
      if (u.indexOf("since=") >= 0) {
        return J({ messages: [{ id: "s999", ts: now + 1, direction: 0, kind: "c2c", peer_id: "u_selftest", text: "（自检合成）这是一条新消息", source: "selftest" }], total: 4 });
      }
      var hist = [];
      for (var i = 30; i >= 1; i--) hist.push({ id: "s" + i, ts: now - 30 * i - 40, direction: i % 2, kind: "c2c", peer_id: "u_selftest", text: "（自检合成）历史消息 " + i, source: "selftest" });
      return J({ messages: hist, total: 31 });
    }
    return real(url, opts);
  };
})();
try { localStorage.setItem("qqscope_refresh_sec", "5"); localStorage.setItem("qqscope_auto_refresh", "1"); } catch (e) {}
setTimeout(function () { location.hash = "#/sessions"; }, 2200);
clickWhen('.conv-item[data-key="c2c|u_selftest"]', function () {
  setTimeout(function () { var b = document.getElementById("bubbleList"); if (b) b.scrollTop = 0; }, 2200);
  setTimeout(function () {
    var d = document.documentElement, p = document.getElementById("newMsgPill");
    d.setAttribute("data-pill-on", (p && p.classList.contains("on")) ? "1" : "0");
    d.setAttribute("data-pill-text", p ? (p.textContent || "") : "");
    d.setAttribute("data-synthetic", "1");
  }, 12000);
});
`);