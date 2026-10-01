#!/usr/bin/env node
/* QQScope v17 断言：身份绑定（展示层跟登录号绑定；采集层永远为登录号持续运行）
 * 用法：node check_v17.mjs [web/index.html]
 * 纯静态源码断言，不发网络、不碰真库/框架。
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

const applyBody = fnBody(src, "applyLoginIdentity");
const bannerBody = fnBody(src, "renderNotImportedBanner");
const selBody = fnBody(src, "renderAccountSelect");
const focusBody = fnBody(src, "requestLiveFocus");
const tickBody = fnBody(src, "liveTick");
const startPollBody = fnBody(src, "startLivePoll");
const resetBody = fnBody(src, "resetAppData");
const loadAccBody = fnBody(src, "loadAccounts");
const pollBody = fnBody(src, "poll");
const enterBody = fnBody(src, "enterApp");
const onAccBody = fnBody(src, "onAccountChange");
const applyLiveBody = fnBody(src, "applyLiveIdentity");
const liveSync = read("core/live_sync.py");
const srv = read("server/routes_framework.py");

section("v17 前端：展示层 - 登录号优先绑定");
ok("存在 applyLoginIdentity()", /function applyLoginIdentity\(/.test(src));
ok("账号列表就绪后自动绑定登录号", /lq && inAccounts/.test(applyBody) && /state\.currentQq = lq/.test(applyBody));
ok("loadAccounts 后调用 applyLoginIdentity", /applyLoginIdentity\(/.test(loadAccBody));
ok("poll 成功后调用 applyLoginIdentity", /applyLoginIdentity\(s\)/.test(pollBody));

section("v17 前端：展示层 - 未导入态不展示他人数据");
ok("存在 renderNotImportedBanner()", /function renderNotImportedBanner\(/.test(src));
ok("未导入横幅元素存在", /id="identityBanner"/.test(src));
ok("登录号不在库 -> 清空 currentQq", /lq && !inAccounts/.test(applyBody) && /state\.currentQq = null/.test(applyBody));
ok("横幅含未导入 + 去数据源导入", /未导入/.test(bannerBody) && /去数据源导入/.test(bannerBody) && /#sources/.test(bannerBody));
ok("登录号不在库时明确「实时采集仍在进行」", /实时采集仍在为它进行/.test(bannerBody));

section("v17 前端：展示层 - 选择器打标 + 只禁用发送/动态");
ok("选择器打标「未连接此账号」", /未连接此账号/.test(selBody));
ok("打标依据 loginQq != 账号 qq", /state\.loginQq && a\.qq !== num\(state\.loginQq/.test(selBody));
ok("选非登录号提示只能离线查看（不禁用采集）", /只能离线查看归档/.test(onAccBody) && /实时采集仍在为/.test(bannerBody));
ok("不匹配账号禁用发送", /isViewingLoginAccount\(\)/.test(fnBody(src, "sendDisabledReason")));

section("v17 前端：采集层 - 永远绑定登录号且持续运行");
ok("enterApp 无条件以登录号启动 live", /var lq = loginAccountOf\(\) \|\| num\(status && status\.account, 0\)/.test(enterBody) && /apiPost\("\/api\/live\/start",\s*\{\s*account:\s*lq\s*\}\)/.test(enterBody));
ok("启动 live 不受 isViewingLoginAccount 限制", !/isViewingLoginAccount[\s\S]{0,80}\/api\/live\/start/.test(enterBody));
ok("applyLiveIdentity 内没有任何 /api/live/stop 调用", !/apiPost\("\/api\/live\/stop"/.test(applyLiveBody));
ok("requestLiveFocus 用 loginAccountOf()", /account:\s*loginAccountOf\(\)/.test(focusBody));
ok("liveTick 用 loginAccountOf()", /var qq = loginAccountOf\(\)/.test(tickBody));
ok("切账号 onAccountChange 内没有 /api/live/stop", !/\/api\/live\/stop/.test(onAccBody));
const stopCount = (src.match(/apiPost\("\/api\/live\/stop"/g) || []).length;
ok("全文件仅 2 处 live/stop（框架断开 + 显式登出）", stopCount === 2, "count=" + stopCount);
ok("sync_allowed 不参与 live 启停", !/sync_allowed/.test(enterBody) && !/sync_allowed/.test(applyLiveBody) && !/sync_allowed/.test(tickBody));

section("v17 前端：登出/换号只清展示层");
ok("resetAppData 重置 loginQq/_identityApplied", /state\.loginQq = null/.test(resetBody) && /state\._identityApplied = false/.test(resetBody));
ok("resetAppData 清 identityBanner", /identityBanner/.test(resetBody));
ok("断线(框架未登录)才走 resetAppData/停 live", /resetAppData\(\)/.test(pollBody) && /\/api\/live\/stop/.test(pollBody));
ok("fetchStatus 带被查看账号 account", /\/api\/login\/status" \+ \(viewQq/.test(src) && /encodeURIComponent\(viewQq\)/.test(src));

section("v17 数据层：入库 account_qq 恒为框架登录号");
ok("live_sync._poll_peer 用 self.uin 入库", /account_qq=self\.uin/.test(liveSync));
ok("live_sync 写入 account_qq=self.uin（不取查看账号）", !/account_qq=state|account_qq=currentQq/.test(liveSync));

section("v17 后端：/api/login/status 身份判定");
ok("login_status 接受 ?account=", /def login_status\(account: int \| None = None\)/.test(srv));
ok("返回 account_in_store（查 accounts 表）", /gate\["account_in_store"\]/.test(srv) && /FROM accounts WHERE account_qq=\?/.test(srv));
ok("返回 view_account_mismatch", /gate\["view_account_mismatch"\]/.test(srv));
ok("不匹配时 sync_allowed=false + sync_block_reason=account_mismatch", /gate\["sync_allowed"\] = False/.test(srv) && /sync_block_reason"\] = "account_mismatch"/.test(srv));
ok("不传 account 保持旧语义", /view_qq = int\(account\) if account else 0/.test(srv));

console.log(`\n===== check_v17 结果：PASS ${passes} / FAIL ${fails} =====`);
process.exit(fails ? 1 : 0);