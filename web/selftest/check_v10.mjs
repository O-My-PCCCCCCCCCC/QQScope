#!/usr/bin/env node
/* QQScope v10 断言：3D 环绕背景 + 连接门（扫码）+ 退出登录 + 登录后才同步
 * 用法：node check_v10.mjs [web/index.html] [dom-loggedin.html] [dom-qr.html] [dom-degrade.html]
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..", "..");
const args = process.argv.slice(2);
const SRC = args[0] || path.join(root, "web", "index.html");
const DOMS = args.slice(1);
let passes = 0, fails = 0;
const ok = (n, c, d) => { if (c) { passes++; console.log(`  PASS  ${n}`); } else { fails++; console.log(`  FAIL  ${n}${d ? "  -> " + d : ""}`); } };
const section = (t) => console.log(`\n[${t}]`);
const src = fs.readFileSync(SRC, "utf8");
const isBuilt = !/__QQSCOPE_ENGINE__/.test(src);

section("v10 源码：连接门结构");
ok("有连接门容器 #loginGate", /id="loginGate"/.test(src));
ok("有 3D 画布 #holoCanvas", /id="holoCanvas"/.test(src));
ok("标题是「连接 QQ」而不是「登录」", /id="loginTitle"[^>]*>连接 QQ</.test(src));
ok("文案说明「已登录时不会出现此页」", /已登录时不会出现此页/.test(src));
ok("二维码容器 #loginQrBox + #qrImg", /id="loginQrBox"/.test(src) && /id="qrImg"/.test(src));
ok("四状态渲染函数 renderQr/renderNoRun/renderConnected/renderDown",
  /function renderQr\(/.test(src) && /function renderNoRun\(/.test(src) && /function renderConnected\(/.test(src) && /function renderDown\(/.test(src));
ok("顶部有「已连接」chip + 退出登录按钮", /id="authChip"/.test(src) && /id="btnLogout"/.test(src) && /退出登录/.test(src));
ok("二次确认弹窗存在", /id="confirmScrim"/.test(src) && /function openConfirm\(/.test(src));

section("v10 源码：登录态判断来自后端（不是 localStorage 决定）");
ok("主接口 /api/login/status", /\/api\/login\/status/.test(src));
ok("接口 404 时退回 /api/framework/status", /\/api\/framework\/status/.test(src));
ok("已登录且非强制门/手动锁定时自动进入", /s\.logged_in && !forceGate && !manualLock/.test(src));
ok("接口失败重试 2 次（fetchWithRetry(2)）", /fetchWithRetry\(2\)/.test(src));
ok("接口仍失败则进主界面 + 横幅", /showConnBanner\([^)]*未连接框架/.test(src));
ok("localStorage 只做加速（qqscope_authed）", /localStorage\.setItem\("qqscope_authed"/.test(src));

section("v10 源码：扫码 / 框架控制");
ok("二维码走 /api/framework/qrcode（带 mtime 刷新）", /\/api\/framework\/qrcode/.test(src) && /qr\.mtime/.test(src));
ok("POST /api/framework/start", /\/api\/framework\/start/.test(src));
ok("POST /api/framework/stop（退出并停止框架）", /\/api\/framework\/stop/.test(src) && /退出并停止框架/.test(src));
ok("演示模式假二维码用 canvas.toDataURL（不外链）", /function fakeQr\(/.test(src) && /toDataURL/.test(src));

section("v10 源码：登录之后才同步");
ok("进入主界面才 POST /api/live/start", /\/api\/live\/start/.test(src));
ok("退出登录 POST /api/live/stop", /function logoutUser\(/.test(src) && /\/api\/live\/stop/.test(src));
ok("连接页 gate-mode 时 renderRoute 直接返回（不发数据请求）", /gate-mode"\)\) return;[\s\S]{0,40}连接页不发数据请求/.test(src));
ok("进入主界面后 startAppData 才拉账号/数据源", /function startAppData\(/.test(src) && /\.then\(loadAccounts\)/.test(src));

section("v10 源码：退出登录只清认证键");
ok("只 removeItem qqscope_authed / qqscope_authed_nick",
  /removeItem\("qqscope_authed"\)/.test(src) && /removeItem\("qqscope_authed_nick"\)/.test(src));
ok("没有 localStorage.clear()", !/localStorage\.clear\(/.test(src));

section("v10 源码：3D 背景与降级");
ok("运行时按需加载本地 three.min.js / GLTFLoader.js", /three\.min\.js/.test(src) && /GLTFLoader\.js/.test(src) && /\/assets\/vendor\//.test(src));
ok("模型走 /assets/vendor/kei.vrm", /kei\.vrm/.test(src));
ok(isBuilt ? "产物无外部资源引用（script src）" : "零外部素材：静态无 script src、无 http(s)://", !/<script[^>]+src\s*=/i.test(src) && (isBuilt || !/https?:\/\//.test(src)));
ok("WebGL 检测 + 失败降级 holo-fallback", /function webglOK\(/.test(src) && /holo-fallback/.test(src) && /function fail\(/.test(src));
ok("prefers-reduced-motion 降级", /prefers-reduced-motion/.test(src));
ok("visibilitychange 暂停渲染", /visibilitychange/.test(src) && /stopLoop\(\)/.test(src));
ok("帧率自适应：lastFps / resScale / setPixelRatio", /lastFps/.test(src) && /resScale/.test(src) && /setPixelRatio/.test(src));
ok("连接页默认开、主界面默认关", /qqscope_holo_login/.test(src) && /qqscope_holo_main/.test(src) && /\(mode === "gate"\) \? "1" : "0"/.test(src));
ok("模型加载不阻塞首屏（异步 ensureLibs/loadModel）", /function ensureLibs\(/.test(src) && /function loadModel\(/.test(src));
ok("设置页有 3D 开关", /id="setHoloMain"/.test(src));
ok("暴露测试接口 __QQSCOPE_LOGIN__ / __QQSCOPE_HOLO__", /__QQSCOPE_LOGIN__/.test(src) && /__QQSCOPE_HOLO__/.test(src));

section(isBuilt ? "v10 产物：注入已完成" : "v10 源码：占位符逐字不变");
if (isBuilt) {
  ok("boot 占位符已替换为对象/数组", !/\/\*__QQSCOPE_BOOT__\*\//.test(src) && /window\.__QQSCOPE_BOOT__\s*=\s*[\{\[]/.test(src));
  ok("engine 占位符已替换", !/__QQSCOPE_ENGINE__/.test(src));
} else {
  ok("boot 占位符独立成行", /\n\s*window\.__QQSCOPE_BOOT__\s*=\s*\/\*__QQSCOPE_BOOT__\*\/\s*null\s*;\s*\n/.test(src));
  ok("engine 占位符独立成行", /\n\s*\/\*__QQSCOPE_ENGINE__\*\/\s*\n/.test(src));
}

for (const f of DOMS) {
  if (!fs.existsSync(f)) { ok("DOM 存在：" + path.basename(f), false, "文件不存在"); continue; }
  const dom = fs.readFileSync(f, "utf8");
  const tag = path.basename(f);
  section("DOM：" + tag);
  const errMatch = dom.match(/data-js-errors="(\d+)"/);
  const errCount = errMatch ? Number(errMatch[1]) : 0;
  ok(`${tag}：无 JS 报错`, errCount === 0, "data-js-errors=" + errCount);
  const gateTag = (dom.match(/<div class="login-gate[^"]*"[^>]*>/) || [""])[0];
  const gateHidden = /hidden/.test(gateTag);
  const hasQr = /id="qrImg"/.test(dom);
  const qrVisible = /id="loginQrBox"/.test(dom) && !/id="loginQrBox"[^>]*hide/.test(dom);
  const hasLogout = /id="btnLogout"[^>]*>/.test(dom) && !/id="btnLogout"[^>]*hidden/.test(dom);
  const stateM = dom.match(/id="loginStateText"[^>]*>([\s\S]*?)<\/span>/);
  const stateText = stateM ? stateM[1].replace(/<[^>]+>/g, "").trim() : "";
  if (stateText.indexOf("等待扫码") >= 0) {
    ok(`${tag}：未登录显示二维码`, !gateHidden && qrVisible && hasQr);
  } else if (stateText.indexOf("框架未运行") >= 0) {
    ok(`${tag}：框架未运行引导且不显示二维码`, !gateHidden && !qrVisible);
  } else if (stateText.indexOf("已连接") >= 0 && !gateHidden) {
    ok(`${tag}：已连接态不显示二维码`, !qrVisible);
  } else if (/conn-banner[^>]*>(?:(?!<\/div>).)*未连接框架/.test(dom)) {
    ok(`${tag}：接口未就绪进主界面 + 提示`, gateHidden);
  } else {
    /* 已登录主界面：门必须隐藏且内部为空、无二维码 */
    ok(`${tag}：已登录直接主界面（门隐藏且无二维码）`, gateHidden && !hasQr && hasLogout, "gateHidden=" + gateHidden + " hasQr=" + hasQr + " logout=" + hasLogout);
  }
  if (/<body[^>]*class="[^"]*holo-fallback/.test(dom)) ok(`${tag}：3D 降级标记（body.holo-fallback）生效`, true);
}

console.log(`\n===== check_v10 结果：PASS ${passes} / FAIL ${fails} =====`);
process.exit(fails ? 1 : 0);