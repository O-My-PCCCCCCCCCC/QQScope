#!/usr/bin/env node
/* 群聊发言人头像验证夹具：打开真实群聊，收集不同发言人的头像 URL */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..", "..");
const dist = path.join(root, "app", "dist", "QQScope.html");
const outDir = path.join(__dirname, "out");
if (!fs.existsSync(dist)) process.exit(1);
fs.mkdirSync(outDir, { recursive: true });
const html = fs.readFileSync(dist, "utf8");
const CLICK = 'function clickWhen(sel, cb) { var n = 0; var t = setInterval(function () { var el = document.querySelector(sel); if (el) { el.click(); clearInterval(t); if (cb) cb(el); } if (++n > 30) clearInterval(t); }, 600); }';
const js = `
setTimeout(function () { location.hash = "#/sessions"; }, 2000);
clickWhen('.conv-item[data-key="group|1082659781"]', function () {
  setTimeout(function () {
    var imgs = document.querySelectorAll(".bubble.other .av img");
    var qqs = [], fallbacks = 0, groupAv = 0;
    Array.prototype.forEach.call(imgs, function (im) {
      var s = im.getAttribute("src") || "";
      if (s.indexOf("/api/avatar/group") >= 0) groupAv++;
      var m = s.match(/[?&]qq=(\d+)/);
      if (m && qqs.indexOf(m[1]) < 0) qqs.push(m[1]);
      if (im.classList.contains("hide")) fallbacks++;
    });
    var d = document.documentElement;
    d.setAttribute("data-group-avatars", String(qqs.length));
    d.setAttribute("data-group-avatar-qqs", qqs.slice(0, 12).join(","));
    d.setAttribute("data-group-avatar-fallbacks", String(fallbacks));
    d.setAttribute("data-group-avatar-groupreq", String(groupAv));
    d.setAttribute("data-other-bubbles", String(document.querySelectorAll(".bubble.other").length));
  }, 9000);
});
`;
fs.writeFileSync(path.join(outDir, "demo-avatar-group.html"), html.replace("</body>", "<script>\n" + CLICK + "\n" + js + "\n</script>\n</body>"), "utf8");
console.log("[头像夹具] out/demo-avatar-group.html");