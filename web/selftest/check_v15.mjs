#!/usr/bin/env node
/* QQScope v15 断言：多账号数据隔离（每个 QQ 号的信息互相不可见）
 * 用法：node check_v15.mjs [web/index.html]
 * 纯静态源码断言：不发任何网络请求、不碰真库、不碰框架。
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..", "..");
const SRC = process.argv[2] || path.join(root, "web", "index.html");
let passes = 0, fails = 0;
const ok = (n, c, d) => { if (c) { passes++; console.log(`  PASS  ${n}`); } else { fails++; console.log(`  FAIL  ${n}${d ? "  -> " + d : ""}`); } };
const section = (t) => console.log(`\n[${t}]`);
const src = fs.readFileSync(SRC, "utf8");

function fnBody(text, name) {
  const start = text.indexOf("function " + name + "(");
  if (start < 0) return "";
  const open = text.indexOf("{", start);
  if (open < 0) return "";
  let depth = 0, j = open;
  for (; j < text.length; j++) {
    const c = text[j];
    if (c === "{") depth++;
    else if (c === "}") { depth--; if (depth === 0) { j++; break; } }
    else if (c === '"' || c === "'" || c === "`") {
      const q = c; j++;
      while (j < text.length && text[j] !== q) { if (text[j] === "\\") j++; j++; }
    }
  }
  return text.slice(start, j);
}
const read = (rel) => { const p = path.join(root, rel); return fs.existsSync(p) ? fs.readFileSync(p, "utf8") : ""; };

section("v15 前端：localStorage 键带账号维度");
ok("存在 remarkKey() 且键含账号", /function remarkKey\(/.test(src) && /"qqscope_remark_"\s*\+\s*num\(accountQq/.test(src));
const normBody = fnBody(src, "normContact");
ok("normContact 只读带账号的 remarkKey", /remarkKey\(/.test(normBody) && /localStorage\.getItem\(remarkKey/.test(normBody));
ok("normContact 不再读裸 qqscope_remark_ 键", !/getItem\("qqscope_remark_" \+ kind/.test(normBody));
const saveBody = fnBody(src, "saveRemark");
ok("saveRemark 兜底写带账号的 remarkKey", /localStorage\.setItem\(remarkKey/.test(saveBody));
ok("全文件无裸写 setItem(\"qqscope_remark_\" + keyOf", !/setItem\("qqscope_remark_" \+ keyOf/.test(src));

section("v15 前端：切账号清空 contacts/messages/DOM");
ok("存在 clearAccountScopedView()", /function clearAccountScopedView\(/.test(src));
const clearBody = fnBody(src, "clearAccountScopedView");
ok("清 state.contacts / state.messages", /state\.contacts\s*=\s*\[\]/.test(clearBody) && /state\.messages\s*=\s*\[\]/.test(clearBody));
ok("清 DOM：convItems / bubbleList", /"convItems"/.test(clearBody) && /"bubbleList"/.test(clearBody));
const invalidBody = fnBody(src, "invalidateData");
ok("invalidateData 调用 clearAccountScopedView", /clearAccountScopedView\(\)/.test(invalidBody));
const accBody = fnBody(src, "onAccountChange");
ok("onAccountChange 调用 clearAccountScopedView", /clearAccountScopedView\(\)/.test(accBody));
ok("onAccountChange 重置 state.live（含 sinceId）", /state\.live\s*=\s*\{[^}]*sinceId/.test(accBody));
ok("onAccountChange 直接清 bubbleList DOM", /bubbleList/.test(accBody));

section("v15 前端：账号相关请求始终带 account");
const mediaBody = fnBody(src, "mediaHTML");
ok("mediaHTML 媒体 URL 带 ?account=", /\/api\/media\//.test(mediaBody) && /\?account=/.test(mediaBody));
const focusBody = fnBody(src, "requestLiveFocus");
ok("requestLiveFocus 带 account", /account:\s*num\(state\.currentQq/.test(focusBody));
ok("导出历史列表带 account", /\/api\/export\/list\?account=/.test(src));
ok("导出下载链接带 account", /\/api\/export\/download\?job=[\s\S]{0,160}?account=/.test(src));
ok("补下载状态带 account", /\/api\/media\/backfill\/status\?account=/.test(src));
ok("normMsg 保留 account_qq", /function normMsg\(/.test(src) && /account_qq:\s*num\(m\.account_qq/.test(src));

section("v15 后端：store 危险 API 强校验");
const storeSrc = read("core/store.py");
ok("search_messages 必须 account_qq", /def search_messages\([\s\S]{0,160}?account_qq/.test(storeSrc) && /search_messages 必须指定 account_qq/.test(storeSrc));
ok("search_messages SQL 带 account_qq 过滤", /WHERE account_qq=\? AND text LIKE \?/.test(storeSrc));
ok("list_contacts 缺 account raise", /list_contacts 必须指定 account_qq/.test(storeSrc));
ok("overview 缺 account raise", /overview 必须指定 account_qq/.test(storeSrc));
ok("count_messages 缺 account raise", /count_messages 必须指定 account_qq/.test(storeSrc));

section("v15 后端：读接口缺 account 一律 400 + 归属校验");
const mediaSrc = read("server/routes_media.py");
ok("routes_media /api/media/{id} 有 account 参数", /def media_file\([\s\S]{0,200}?account/.test(mediaSrc));
ok("routes_media 校验 row.account_qq == account", /row\["account_qq"\][\s\S]{0,40}?!= account/.test(mediaSrc));
ok("routes_media data_quality 缺 account -> 400", /data_quality[\s\S]{0,400}?if not account/.test(mediaSrc));
ok("media.index_stats 按账号", /media\.index_stats\(account\)/.test(mediaSrc));
ok("routes_media backfill/stop 校验账号归属", /def media_backfill_stop\([\s\S]{0,400}?任务不属于该账号/.test(mediaSrc));
const feedsSrc = read("server/routes_feeds.py");
ok("routes_feeds 缺 account -> 400", /if not account:[\s\S]{0,120}?account_required/.test(feedsSrc));
ok("routes_feeds 不再用全局 qzone uin 兜底", !/account = int\(cs\.get\("uin"\)/.test(feedsSrc));
const appSrc = read("server/app.py");
ok("app /api/contacts 缺 account -> 400", /def contacts\([\s\S]{0,260}?if not account:/.test(appSrc));
ok("app /api/overview 缺 account -> 400", /def overview\([\s\S]{0,200}?if not account:/.test(appSrc));
ok("app export_list 必须 account", /def export_list\(account/.test(appSrc) && /job_meta/.test(appSrc));
ok("app export_download 带 account 且校验归属", /def export_download\([\s\S]{0,120}?account/.test(appSrc) && /任务不存在或不属于该账号/.test(appSrc));
const liveSrc = read("server/routes_live.py");
ok("routes_live 有 _check_account", /def _check_account\(/.test(liveSrc));
ok("routes_live events 缺 account -> 400", /def api_live_events\([\s\S]{0,300}?if not account:/.test(liveSrc));
const liveSync = read("core/live_sync.py");
ok("core.live_sync.events 无 account 返回空", /if not aqq:/.test(liveSync));

section("v15 后端：发送 / 导出 / 媒体索引 / 语音边界");
const sendSrc = read("server/routes_send.py");
ok("routes_send 必须 account_qq", /account_qq <= 0/.test(sendSrc));
ok("routes_send 校验 account == 登录号", /account_qq != login/.test(sendSrc));
ok("routes_send history 必须 account", /def api_send_history\([\s\S]{0,260}?if not account:/.test(sendSrc));
ok("core.send 严格校验 account == uin", /if _to_int\(account_qq\) != uin:/.test(read("core/send.py")));
const exportSrc = read("core/export.py");
ok("core.export 写任务元数据 _meta", /EXPORT_DIR \/ "_meta"/.test(exportSrc) && /def job_meta\(/.test(exportSrc));
ok("core.media index_stats 支持 account", /def index_stats\(account_qq=None\)/.test(read("core/media.py")));
ok("routes_voice progress 带 account", /def voice_progress\(account/.test(read("server/routes_voice.py")));

console.log(`\n===== check_v15 结果：PASS ${passes} / FAIL ${fails} =====`);
process.exit(fails ? 1 : 0);