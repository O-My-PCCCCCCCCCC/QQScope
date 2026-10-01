#!/usr/bin/env node
/* 验收用零依赖反向代理：静态托管一份前端产物，/api/* 透传到真实后端。
 * 用法：node proxy_server.mjs <listenPort> <targetBase> <indexHtmlPath>
 *        /demo/<name>.html -> <indexHtmlDir>/demo-<name>.html
 */
import http from "node:http";
import fs from "node:fs";
import path from "node:path";

const PORT = Number(process.argv[2] || 15558);
const TARGET = (process.argv[3] || "http://127.0.0.1:15555").replace(/\/+$/, "");
const INDEX = process.argv[4];
const DIR = INDEX ? path.dirname(path.resolve(INDEX)) : process.cwd();
const INDEX_BUF = INDEX && fs.existsSync(INDEX) ? fs.readFileSync(INDEX) : null;

const HOP = new Set(["content-encoding", "transfer-encoding", "connection", "content-length", "keep-alive"]);
const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, "http://localhost");
  if (!u.pathname.startsWith("/api/")) {
    let file = null;
    if (u.pathname === "/" || u.pathname === "/index.html") file = INDEX_BUF;
    else if (u.pathname.startsWith("/demo/")) {
      const f = path.join(DIR, "demo-" + path.basename(u.pathname));
      if (fs.existsSync(f)) file = fs.readFileSync(f);
    }
    if (file) { res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" }); return res.end(file); }
    res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" }); return res.end("not found");
  }
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = Buffer.concat(chunks);
  try {
    const headers = {};
    if (req.headers["content-type"]) headers["content-type"] = req.headers["content-type"];
    if (req.headers["authorization"]) headers["authorization"] = req.headers["authorization"];
    const r = await fetch(TARGET + req.url, {
      method: req.method,
      headers,
      body: (req.method === "GET" || req.method === "HEAD") ? undefined : body
    });
    const ab = Buffer.from(await r.arrayBuffer());
    const h = {};
    r.headers.forEach((v, k) => { if (!HOP.has(k.toLowerCase())) h[k] = v; });
    res.writeHead(r.status, h);
    res.end(ab);
  } catch (e) {
    res.writeHead(502, { "Content-Type": "application/json; charset=utf-8" });
    res.end(JSON.stringify({ error: "proxy: " + (e && e.message || e) }));
  }
});
server.listen(PORT, "127.0.0.1", () => console.log(`[proxy] :${PORT} -> ${TARGET}  static=${INDEX || "(none)"}`));