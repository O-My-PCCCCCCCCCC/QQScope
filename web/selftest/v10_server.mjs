#!/usr/bin/env node
/* v10 验收用零依赖静态服务：
 *   /                 -> 指定构建产物
 *   /assets/*         -> web/assets（three.min.js / GLTFLoader.js / kei.vrm）
 *   /demo/<name>.html -> out/demo-<name>.html（可注入自动点击/探针脚本）
 *   /api/*            -> 透传到真实后端（默认 http://127.0.0.1:15555）
 * 用法：node v10_server.mjs <port> <targetBase> <indexHtml> <outDir> <assetsDir>
 */
import http from "node:http";
import fs from "node:fs";
import path from "node:path";

const PORT = Number(process.argv[2] || 15610);
const TARGET = (process.argv[3] || "http://127.0.0.1:15555").replace(/\/+$/, "");
const INDEX = process.argv[4];
const OUTDIR = process.argv[5] || (INDEX ? path.dirname(INDEX) : process.cwd());
const ASSETS = process.argv[6];
if (!INDEX || !fs.existsSync(INDEX)) { console.error("缺少构建产物：" + INDEX); process.exit(1); }
const INDEX_BUF = fs.readFileSync(INDEX);
const MIME = { ".js": "application/javascript; charset=utf-8", ".mjs": "application/javascript; charset=utf-8",
  ".vrm": "application/octet-stream", ".glb": "model/gltf-binary", ".html": "text/html; charset=utf-8",
  ".json": "application/json; charset=utf-8", ".png": "image/png", ".css": "text/css; charset=utf-8" };
const HOP = new Set(["content-encoding", "transfer-encoding", "connection", "content-length", "keep-alive"]);

function sendFile(res, file) {
  if (!file || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
    res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" }); return res.end("not found");
  }
  res.writeHead(200, { "Content-Type": MIME[path.extname(file).toLowerCase()] || "application/octet-stream" });
  return res.end(fs.readFileSync(file));
}

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, "http://localhost");
  if (!u.pathname.startsWith("/api/")) {
    if (u.pathname === "/" || u.pathname === "/index.html") { res.writeHead(200, { "Content-Type": MIME[".html"] }); return res.end(INDEX_BUF); }
    if (u.pathname.startsWith("/assets/") && ASSETS) return sendFile(res, path.join(ASSETS, u.pathname.slice("/assets/".length)));
    if (u.pathname.startsWith("/demo/")) return sendFile(res, path.join(OUTDIR, "demo-" + path.basename(u.pathname)));
    return sendFile(res, null);
  }
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = Buffer.concat(chunks);
  try {
    const headers = {};
    if (req.headers["content-type"]) headers["content-type"] = req.headers["content-type"];
    const r = await fetch(TARGET + req.url, { method: req.method, headers, body: (req.method === "GET" || req.method === "HEAD") ? undefined : body });
    const ab = Buffer.from(await r.arrayBuffer());
    const h = {};
    r.headers.forEach((v, k) => { if (!HOP.has(k.toLowerCase())) h[k] = v; });
    res.writeHead(r.status, h); res.end(ab);
  } catch (e) {
    res.writeHead(502, { "Content-Type": "application/json; charset=utf-8" });
    res.end(JSON.stringify({ error: "proxy: " + (e && e.message || e) }));
  }
});
server.listen(PORT, "127.0.0.1", () => console.log(`[v10] :${PORT} -> ${TARGET}  index=${path.basename(INDEX)} assets=${ASSETS}`));