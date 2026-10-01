#!/usr/bin/env node
/* QQScope v14 断言：退出登录 = 真正断开 QQ 登录 + 清空上次会话 + 框架同步退出
 * 用法：node check_v14.mjs [web/index.html]
 * 只做静态源码断言（不发任何网络请求、不碰真框架）。
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

/* 提取某个函数体（按花括号配对；跳过字符串/模板，足以覆盖本文件） */
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

const logoutBody = fnBody(src, "logoutUser");
const resetBody = fnBody(src, "resetAppData");
const pollBody = fnBody(src, "poll");
const stopBody = fnBody(src, "stopFramework");
const doLogoutBody = fnBody(src, "doLogout");
const forceBody = fnBody(src, "forceStopFramework");
const bannerBody = fnBody(src, "showExternalLogoutBanner");

section("v14 源码：退出登录真的清空上次会话");
ok("存在 resetAppData()", /function resetAppData\(/.test(src));
ok("logoutUser() 调用了 resetAppData（不是只清 localStorage）", /resetAppData\(\)/.test(logoutBody), logoutBody.slice(0, 120));
ok("resetAppData 清空 state.accounts / currentQq", /state\.accounts\s*=\s*\[\]/.test(resetBody) && /state\.currentQq\s*=\s*null/.test(resetBody));
ok("resetAppData 清空 state.contacts", /state\.contacts\s*=\s*\[\]/.test(resetBody));
ok("resetAppData 清空 state.messages", /state\.messages\s*=\s*\[\]/.test(resetBody));
ok("resetAppData 清空 state.overview / state.report", /state\.overview\s*=\s*null/.test(resetBody) && /state\.report\s*=\s*null/.test(resetBody));
ok("resetAppData 清空关键 DOM：会话列表/消息区", /"convItems"/.test(resetBody) && /"bubbleList"/.test(resetBody));
ok("resetAppData 清空关键 DOM：导出列表/AI 预览/动态", /"expList"/.test(resetBody) && /"aiPreview"/.test(resetBody) && /"feedList"/.test(resetBody));
ok("resetAppData 隐藏 authChip", /authChip/.test(resetBody));
ok("resetAppData 允许再次拉取数据（_appDataStarted 归零）", /state\._appDataStarted\s*=\s*false/.test(resetBody));

section("v14 源码：退出登录 = 真正断开 QQ 登录");
ok("调用新接口 POST /api/framework/logout", /apiPost\(\s*["']\/api\/framework\/logout["']/.test(src));
ok("logoutUser 的 onOk 走 doLogout（不再是仅清标记）", /onOk:[\s\S]{0,240}?doLogout\(false\)/.test(logoutBody));
ok("doLogout 保留 POST /api/live/stop（停实时同步）", /\/api\/live\/stop/.test(doLogoutBody));
ok("按 mode 分级提示：bot_exit / stop / force_stop", /"bot_exit"/.test(doLogoutBody) && /"stop"/.test(doLogoutBody) && /"force_stop"/.test(doLogoutBody));
ok("外部框架返回 external -> 顶部横幅 + 强制停止按钮", /"external"/.test(doLogoutBody) && /showExternalLogoutBanner\(/.test(doLogoutBody));
ok("横幅按钮 #btnForceFwStop 调 forceStopFramework", /btnForceFwStop/.test(bannerBody) && /forceStopFramework/.test(bannerBody));
ok("poll 期间保留外部框架横幅（不被 clearConnBanner 冲掉）", /extPid[\s\S]{0,40}showExternalLogoutBanner/.test(pollBody));
ok("「退出并停止框架」也改走新接口（doLogout）", /doLogout\((?:true|false)\)/.test(stopBody) && !/apiPost\(\s*["']\/api\/framework\/stop["']/.test(stopBody));
ok("旧后端兜底：/logout 404 时退回 /api/framework/stop", /apiPost\(\s*["']\/api\/framework\/stop["']/.test(src) && /404/.test(src));

section("v14 源码：强制停止必须二次确认 + force 分支");
ok("force=true 分支存在", /force:\s*!!force/.test(src) || /force:\s*true/.test(src));
ok("forceStopFramework 仍走二次确认弹窗（openConfirm）", /openConfirm\(/.test(forceBody));
ok("force 确认的 onOk 带 force=true 调 doLogout", /doLogout\(true\)/.test(forceBody));

section("v14 源码：确认弹窗写清用户的三个疑问");
const copyMatch = src.match(/var EXIT_COPY\s*=\s*([\s\S]*?);\s*\n/);
const copy = copyMatch ? copyMatch[1] : "";
ok("文案含「断开 QQ 登录 / 停止实时同步」", /断开[^"]*QQ\s*登录/.test(copy) && /实时同步/.test(copy), copy.slice(0, 80));
ok("文案含「需要重新启动框架并重新扫码」", /重新启动框架/.test(copy) && /扫码/.test(copy));
ok("文案含「本地归档数据保留、不会被清除」语义", /数据/.test(copy) && /保留/.test(copy) && /不会被清除/.test(copy));
ok("文案提到数据在本机 data/、要删用账号管理", /data\//.test(copy) && /账号管理/.test(copy));
ok("logoutUser 仍是二次确认（openConfirm，不直接自动登出）", /openConfirm\(/.test(logoutBody) && !/apiPost\(/.test(logoutBody));
ok("没有 localStorage.clear()（只清认证键）", !/localStorage\.clear\(/.test(src));
ok("仍 removeItem qqscope_authed / qqscope_authed_nick", /removeItem\("qqscope_authed"\)/.test(src) && /removeItem\("qqscope_authed_nick"\)/.test(src));

section("v14 后端：路由分级 + 身份校验（server/routes_framework.py）");
const srvPath = path.join(root, "server", "routes_framework.py");
if (!fs.existsSync(srvPath)) {
  ok("server/routes_framework.py 存在", false, "文件不存在");
} else {
  const srv = fs.readFileSync(srvPath, "utf8");
  ok("新增 @router.post(\"/api/framework/logout\")", /@router\.post\(\s*["']\/api\/framework\/logout["']\s*\)/.test(srv));
  ok("Tier1 使用 _onebot 发 /bot_exit", /_onebot\([^)]*\/bot_exit/.test(srv));
  ok("四种 mode 全部返回", /"bot_exit"/.test(srv) && /"stop"/.test(srv) && /"force_stop"/.test(srv) && /"external"/.test(srv));
  ok("kill 之前过 is_framework_proc（node.exe + napcat）", /is_framework_proc\(pid,\s*force=True\)/.test(srv) && /napcat/.test(srv));
  ok("复用 taskkill 路径（_kill_framework_proc）且 framework_stop 也用它", /def _kill_framework_proc/.test(srv) && /_kill_framework_proc\(pid\)/.test(srv));
  ok("/api/login/status 补 pid + can_force", /gate\["pid"\]/.test(srv) && /gate\["can_force"\]/.test(srv));
}

console.log(`\n===== check_v14 结果：PASS ${passes} / FAIL ${fails} =====`);
process.exit(fails ? 1 : 0);