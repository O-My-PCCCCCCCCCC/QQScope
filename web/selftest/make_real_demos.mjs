#!/usr/bin/env node
/* 用真实构建产物生成自动点击 demo 页（仅自测用）。
 * 用法：node make_real_demos.mjs <realbuild.html> <outDir>
 */
import fs from "node:fs";
import path from "node:path";

const src = process.argv[2];
const outDir = process.argv[3] || path.dirname(src);
const real = fs.readFileSync(src, "utf8");

const SESSIONS_JS = `
fetch('/api/contacts?account=' + encodeURIComponent(document.getElementById('accountSelect').value) + '&limit=1000')
  .then(function (r) { return r.json(); })
  .then(function (j) {
    var cs = (j.contacts || []).filter(function (c) { return c.msg_count > 0; });
    cs.sort(function (a, b) { return b.msg_count - a.msg_count; });
    if (!cs.length) return;
    var c = cs[0];
    var el = document.querySelector('.conv-item[data-key="' + c.kind + '|' + c.peer_id + '"]');
    if (el) el.click();
  });
`;

const demos = {
  sessions: { hash: "sessions", js: SESSIONS_JS },
  export: { hash: "export", js: "var a=document.getElementById('expAll');if(a)a.click();" },
  overview: { hash: "overview", js: "window.scrollTo(0,0);" },
  sources: { hash: "sources", js: "window.scrollTo(0,0);" },
  ai: { hash: "ai", js: "var a=document.getElementById('btnAiSend');if(a)a.click();" }
};

for (const [name, d] of Object.entries(demos)) {
  const body = "location.hash='#" + d.hash + "';setTimeout(function(){" + d.js.replace(/\s+/g, " ") + "},3000);";
  const html = real.replace("</body>", "<script>\n" + body + "\n</script>\n</body>");
  fs.writeFileSync(path.join(outDir, "demo-" + name + ".html"), html, "utf8");
  console.log("demo-" + name + ".html");
}