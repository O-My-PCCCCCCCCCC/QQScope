#!/usr/bin/env node
/* QQScope v16 静态断言：真实双账号隔离矩阵 + 本轮新修泄漏
 * 用法：node check_v16.mjs
 * 纯源码断言，不发网络、不碰真库/框架。
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..", "..");
let passes = 0, fails = 0;
const ok = (n, c, d) => { if (c) { passes++; console.log(`  PASS  ${n}`); } else { fails++; console.log(`  FAIL  ${n}${d ? "  -> " + d : ""}`); } };
const section = (t) => console.log(`\n[${t}]`);
const read = (rel) => { const p = path.join(root, rel); return fs.existsSync(p) ? fs.readFileSync(p, "utf8") : ""; };

const storeSrc = read("core/store.py");
const sendSrc = read("server/routes_send.py");
const mediaSrc = read("server/routes_media.py");
const qzoneSrc = read("core/sources/qzone.py");
const feedsSrc = read("server/routes_feeds.py");
const appSrc = read("server/app.py");
const isoSrc = read("scripts/isolation_test.py");
const serveSrc = read("scripts/isolation_serve.py");
const domSrc = read("web/selftest/check_v16_dom.mjs");

section("v16 真实双账号夹具（升级后的 isolation_test.py）");
ok("隔离测试是真实双账号克隆版", /clone_a_to_b/.test(isoSrc) && /完整数据集/.test(isoSrc));
ok("B 数据打 B·/B# 标记且 peer_id 保持不变", /BNAME = "B·"/.test(isoSrc) && /BTXT = "B#"/.test(isoSrc) && /SELECT \?, kind, peer_id, peer_qq,/.test(isoSrc));
ok("有 3 个 B-only peer", /BONLY = \[/.test(isoSrc) && /u_bonly_1/.test(isoSrc));
ok("完整性逐 id/逐字段比对", /完整性\(逐 id\)/.test(isoSrc) && /完整性\(逐字段\)/.test(isoSrc) && /got == truth|== truth/.test(isoSrc));
ok("纯净性零对方 id/标记", /纯净性/.test(isoSrc) && /got & B_ids|got & A_ids|pure_A/.test(isoSrc));
ok("缺席即拒绝断言", /缺席/.test(isoSrc) && /st == 400|st == 422/.test(isoSrc));
ok("写路径：DELETE / PATCH / export 全覆盖", /DELETE/.test(isoSrc) && /PATCH/.test(isoSrc) && /run_export/.test(isoSrc));

section("v16 本轮新发现并修复的泄漏（源码断言）");
ok("delete_account 删账号目录+注册表行（task-11）", /def delete_account\(/.test(storeSrc) && /shutil\.rmtree\(d/.test(storeSrc) && /DELETE FROM accounts WHERE account_qq=\?/.test(storeSrc));
const iLogin = sendSrc.indexOf("login = _login_qq()");
const iDry = sendSrc.indexOf("演练模式");
ok("send 身份校验在 dry_run 之前（dry_run 不再绕过）", iLogin >= 0 && iDry > iLogin, `iLogin=${iLogin} iDry=${iDry}`);
ok("backfill status 缺 account -> 400", /def media_backfill_status\([\s\S]{0,300}?if not account/.test(mediaSrc));
ok("backfill stop 缺 account -> 400", /def media_backfill_stop\([\s\S]{0,300}?if not account/.test(mediaSrc));
ok("QZone 全局凭据 + 异账号显式拒绝", /QZONE_ACCOUNT_MISMATCH/.test(qzoneSrc) && /全局单例/.test(qzoneSrc));
ok("routes_feeds 把 QZONE_ACCOUNT_MISMATCH 映射为 400", /QZONE_ACCOUNT_MISMATCH/.test(feedsSrc) && /account_mismatch/.test(feedsSrc));
ok("app 通用 sync 路由同样映射 400", /QZONE_ACCOUNT_MISMATCH/.test(appSrc));

section("v16 前端 DOM 复核脚本（真实双账号后端 + 无头 Edge）");
ok("存在 check_v16_dom.mjs", domSrc.length > 0);
ok("存在 isolation_serve.py（:15557 双账号后端）", serveSrc.length > 0 && /ISO_READY/.test(serveSrc));
ok("serve 脚本 A/B 都打标记（B 不含 A·）", /_mark\(con, A, "A·", "A#"\)/.test(serveSrc) && /_mark\(con, B, "B·", "B#"\)/.test(serveSrc));
ok("DOM 脚本走账号选择器切号", /accountSelect/.test(domSrc) && /dispatchEvent\(new Event\('change'\)\)/.test(domSrc));
ok("DOM 脚本断言 A/B 双向零串号", /A 视角 DOM 零 B/.test(domSrc) && /B 视角 DOM 零 A/.test(domSrc));
ok("DOM 脚本检查 localStorage 账号维度", /qqscope_remark_/.test(domSrc) && /remarkKeys/.test(domSrc));
ok("DOM 脚本检查 data-js-errors", /data-js-errors/.test(domSrc));
ok("DOM 脚本只停自起 PID + 端口占用保护", /srv\.pid/.test(domSrc) && /已有服务在听/.test(domSrc));

console.log(`\n===== check_v16 结果：PASS ${passes} / FAIL ${fails} =====`);
process.exit(fails ? 1 : 0);