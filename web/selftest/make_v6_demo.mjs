#!/usr/bin/env node
/* v6 交互夹具（只做导航/点击，不伪造数据）
 * 产物：
 *   demo-v6-card.html   打开「"从"先」，把第一张名片/小程序卡滚到视口中央
 *   demo-v6-avatar.html 点会话行里的头像 → 记录 dossier 是否打开（期望 0）
 *   demo-v6-row.html    点会话行的名字区域 → 记录 dossier（期望 0）
 *   demo-v6-open.html   先点会话行，再点右上角「资料卡」按钮 → 记录 dossier（期望 1）
 *   demo-v6-switch.html 点行 → 开资料卡 → 再切另一个会话 → 记录 dossier（期望 0）
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
  const out = path.join(outDir, "demo-v6-" + name + ".html");
  fs.writeFileSync(out, html.replace("</body>", "<script>\n" + "function clickWhen(sel, cb) { var n = 0; var t = setInterval(function () { var el = document.querySelector(sel); if (el) { el.click(); clearInterval(t); if (cb) cb(el); } if (++n > 25) clearInterval(t); }, 600); }" + "\n" + js + "\n</script>\n</body>"), "utf8");
  console.log("[v6夹具] " + out);
}
const drawerOn = `document.documentElement.setAttribute("data-drawer-on", (function(){var dr=document.getElementById("contactDrawer");return (dr&&dr.classList.contains("on"))?"1":"0";})());
  document.documentElement.setAttribute("data-drawer-attr", (function(){var dr=document.getElementById("contactDrawer");return dr?(dr.getAttribute("class")||""):"";})());`;

make("card", `
setTimeout(function () { location.hash = "#/sessions"; }, 2200);
clickWhen('.conv-item[data-key="c2c|3797376097"]');
setInterval(function () {
  var cards = document.querySelectorAll(".media-card-namecard");
  var card = null;
  for (var i = 0; i < cards.length; i++) {
    var tt = (cards[i].querySelector(".mc-title") || {}).textContent || "";
    if (tt && tt !== "未命名联系人") { card = cards[i]; break; }
  }
  if (!card) card = document.querySelector(".media-card");
  if (!card || card.getAttribute("data-focused")) return;
  card.setAttribute("data-focused", "1");
  card.scrollIntoView({ block: "center" });
  setTimeout(function () {
    var d = document.documentElement;
    d.setAttribute("data-card-title", (card.querySelector(".mc-title") || {}).textContent || "");
    d.setAttribute("data-card-eyebrow", (card.querySelector(".mc-eyebrow") || {}).textContent || "");
    d.setAttribute("data-card-count", String(document.querySelectorAll(".media-card").length));
    d.setAttribute("data-card-rect", JSON.stringify(card.getBoundingClientRect()));
  }, 90);
}, 500);
`);

make("cardmini", `
setTimeout(function () { location.hash = "#/sessions"; }, 2200);
clickWhen('.conv-item[data-key="c2c|3797376097"]');
setInterval(function () {
  var card = document.querySelector(".media-card-miniapp");
  if (!card || card.getAttribute("data-focused")) return;
  card.setAttribute("data-focused", "1");
  card.scrollIntoView({ block: "center" });
  setTimeout(function () {
    var d = document.documentElement;
    d.setAttribute("data-card-title", (card.querySelector(".mc-title") || {}).textContent || "");
    d.setAttribute("data-card-eyebrow", (card.querySelector(".mc-eyebrow") || {}).textContent || "");
    d.setAttribute("data-card-rect", JSON.stringify(card.getBoundingClientRect()));
  }, 90);
}, 500);
`);

make("avatar", `
setTimeout(function () { location.hash = "#/sessions"; }, 2200);
clickWhen(".conv-item .av");
setTimeout(function () { document.documentElement.setAttribute("data-clicked", "avatar"); ${drawerOn} }, 5200);
`);

make("row", `
setTimeout(function () { location.hash = "#/sessions"; }, 2200);
clickWhen(".conv-item .ci-name");
setTimeout(function () { document.documentElement.setAttribute("data-clicked", "row"); ${drawerOn} }, 5200);
`);

make("open", `
setTimeout(function () { location.hash = "#/sessions"; }, 2200);
clickWhen(".conv-item");
setTimeout(function () {
  var b = document.getElementById("btnShowCard");
  document.documentElement.setAttribute("data-btn-found", b ? "1" : "0");
  if (b) b.click();
}, 5000);
setTimeout(function () { document.documentElement.setAttribute("data-clicked", "showcard"); ${drawerOn} }, 6400);
`);

make("switch", `
setTimeout(function () { location.hash = "#/sessions"; }, 2200);
clickWhen(".conv-item");
setTimeout(function () { var b = document.getElementById("btnShowCard"); if (b) b.click(); }, 5000);
setTimeout(function () {
  var dr = document.getElementById("contactDrawer");
  document.documentElement.setAttribute("data-drawer-after-open", (dr && dr.classList.contains("on")) ? "1" : "0");
  var items = document.querySelectorAll(".conv-item");
  if (items.length > 1) items[1].click(); else if (items[0]) items[0].click();
}, 6400);
setTimeout(function () { document.documentElement.setAttribute("data-clicked", "switch"); ${drawerOn} }, 8200);
`);