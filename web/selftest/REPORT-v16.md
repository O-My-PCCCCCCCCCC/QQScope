# REPORT-v16 · 多账号数据隔离（真实双账号全量矩阵 + 前端 DOM + 写路径）

- 任务：task-9 / T-I（重做 task-7）。用户判定上一轮证据太弱：诱饵账号只有 1 联系人 + 2 消息，大量接口「本来就没数据」→ 空着 PASS。
- 本轮结论：换成**真实账号 A 的完整数据集克隆成 B** 的双账号夹具，对每个读接口做 {A, B, 缺account} 三视角的
  **完整性（逐 id / 逐字段与副本真值比）+ 纯净性（零对方标记 / 零对方 id）**，并覆盖写路径与前端无头 Edge DOM。
- 数字：scripts/isolation_test.py **106/106 PASS**；web/selftest/check_v16_dom.mjs **12/12 PASS**；
  web/selftest/check_v16.mjs **22/22 PASS**；check_v2..v15 保持 **302/0**，check_v2..v16 合计 **324/0**；
  scripts/verify.py --full **11/11**（隔离矩阵项 exit=0 :: 106/106）。
- 本轮**新发现并修复 4 处真实泄漏**（详见第 ⑤ 节）。
- 红线：真实 data/qqscope.db 只读 mode=ro 备份；未碰 tools/napcat；未重启 15555；DOM 测试后端用 :15557 且只停自起 PID。

---

## ① 真实双账号夹具（只用副本）

1. 真实 data/qqscope.db 用 `file:...?mode=ro` + `sqlite3.backup()` 复制到临时根（QQSCOPE_ROOT 指向它）。
2. 先在副本里把 A(1605289411) 的**完整数据集**克隆成 B(3060648699)，**peer_id 保持不变**（真实场景就是同 peer）：
   - contacts：B 的 name/remark/last_text 打前缀（B· / B#）；
   - messages：B 的 text 打 B# 前缀（NULL 保持 NULL），content/media 原样复制（语音文本存在 media JSON 里，随之复制）；
   - feeds：B 的 id 打 B# 前缀、content 打 B# 前缀、author_name 打 B· 前缀。
3. 额外造 3 个**只属于 B** 的独特 peer：u_bonly_1/2/3（A 完全没有）。
4. 克隆后规模：A messages=283048 contacts=128；B messages=283054 contacts=131（含 3 个 B-only，每个 2 条消息）。
   矩阵用共享 peer = group/958416277（A 侧 1194 条），保证 A/B 两边都「有数据可返回」，空着 PASS 不再可能。
5. DOM 测试另起一份副本（scripts/isolation_serve.py），给 A 打 A·/A#、给 B 打 B·/B#，
   这样 A/B 双向都能断言「零对方标记」。

---

## ② 全量接口矩阵（每个接口：A 完整 / A 纯净 / B 完整 / B 纯净 / 缺席拒绝）

| 接口 | A 完整性 | A 纯净性 | B 完整性 | B 纯净性 | 缺席 |
|---|---|---|---|---|---|
| GET /api/contacts | 逐 peer，128/128 | 零 B·/B#/B-only | 131/131 | 含 B·，零 A | 400 |
| GET /api/messages（共享 peer） | 逐 id，1194/1194 | 零 B id | 1194/1194 | 含 B#，零 A id | 422 |
| GET /api/report | 逐字段，5392/5392 | 零 B 标记 | 5398/5398 | 含 B#，零 A | 422 |
| GET /api/overview | 逐字段核 DB；A.total=283048（≠A+B） | 零 B | 逐字段核 DB | 含 B 量级 | 400 |
| GET /api/profile | friend/group/msg 逐字段 | 零 B | 逐字段 | 含 B 归属 | 400 |
| GET /api/contact（共享 peer） | name/remark 逐字段 | 零 B· | 逐字段 | 含 B· | 400 |
| GET /api/feeds | 逐 id，71/71 | 零 B# | 71/71 | 含 B#，零 A id | 400 |
| GET /api/live/events | 逐 id，1000/1000 | 零 B id | 1000/1000 | 零 A id | 400 |
| GET /api/send/history | 逐 id，4/4 | 零 B | 4/4 | 零 A id | 400 |
| GET /api/media/stats | 逐 kind，6/6 | index 只含本账号目录 | 逐 kind，6/6 | 同上 | 422 |
| GET /api/media/pending | 逐 id，200/200 | 零 B id | 200/200 | 零 A id | 422 |
| GET /api/data/quality | 逐字段 | 零 B | 逐字段 | 含 B-only 计数 | 400 |
| GET /api/voice/stats | 归属 A + 纯净 | 零 B | 归属 B | 零 A | 422 |
| GET /api/voice/official/stats | 归属 A + 纯净 | 零 B | 归属 B | 零 A | 422 |
| GET /api/media/{id} | A 的 id 在 A 视角 200 | B 视角 404 | B 的 id 在 B 视角 200 | A 视角 404 | 缺 account 400 |
| POST /api/send | A + dry_run 200 | — | B 身份不一致 400 account_mismatch | B + dry_run 也 400 | 缺 account 400 |
| GET /api/export/list | 只含 A 任务 | 不含 B 任务 | 只含 B 任务 | 不含 A 任务 | 400 |
| GET /api/export/download | A 任务 A 视角 200 | A 任务 B 视角 404 | B 任务 B 视角 200 | B 任务 A 视角 404 | 400 |
| GET /api/media/backfill/status | 无任务 ok=false 且纯净 | — | — | — | 400 |

矩阵断言数：isolation_test.py 共 106 项，全部 PASS。
---

## ③ 写路径结果（本轮新增，上一轮完全没测）

| 写路径 | 断言 | 结果 |
|---|---|---|
| 克隆 B 后 | A 的 contacts/messages/feeds/accounts 全字段指纹与克隆前一致 | PASS |
| PATCH /api/contacts 改 B 的备注 | B 同名 peer 备注变为 B·PATCHED_ISO；A 同名 peer 备注逐字段不变 | PASS |
| POST /api/export 只导 A | HTML/TXT/MD/zip 全量文本零 B·/B#/B-only | PASS |
| POST /api/export 只导 B | 产物含 B#（正对照） | PASS |
| /api/export/list A/B | A 只看到 A 任务、B 只看到 B 任务（按 _meta 归属） | PASS |
| /api/export/download | A 任务 B 视角 404、缺 account 400 | PASS |
| /api/media/{A_id}?account=B | 404（跨账号 id 探测被拒）；反向同理 | PASS |
| /api/send account=B（框架登录 A） | 400 account_mismatch（未到达真正发送） | PASS |
| DELETE /api/accounts/B | B 的 accounts/messages/feeds 全部为 0；A 指纹逐字段不变 | PASS |
| 重新克隆 B 后 DELETE /api/accounts/A | A 消失；B 的消息数逐条不变（反向不影响） | PASS |
| store.search_messages | A 搜不到 B-only；B 搜得到；缺 account 直接 raise | PASS |
| QZone 全局凭据 + 异账号 | qzone.sync(account_qq=B) 显式 QZONE_ACCOUNT_MISMATCH；POST /api/sources/qzone/sync 返回 400 | PASS |

---

## ④ 前端 DOM 复核（用户看得到的那一层）

工具：web/selftest/check_v16_dom.mjs + scripts/isolation_serve.py（真实双账号副本后端 :15557，只停自起 PID）+ 无头 Edge + CDP。
入口：http://127.0.0.1:15557/?authed=1（应用自带的测试通道，不依赖真实登录）。

| 断言 | 结果 |
|---|---|
| 前端加载出 A/B 两个账号 | PASS |
| A 视角 unitCode=QQS-1605289411 | PASS |
| A 视角逐页（会话/总览/动态/导出/AI/设置）DOM 出现 A·/A#（正对照） | PASS |
| A 视角 DOM 零 B·/B#（无 B 数据） | PASS |
| 通过账号选择器切到 B 后 unitCode=QQS-3060648699 | PASS |
| 切账号后会话列表 DOM 与 A 快照不同（真的重新拉数据） | PASS |
| 切账号后旧 DOM 被清（B 列表含 B·，不再含 A·） | PASS |
| B 视角逐页 DOM 出现 B·/B#（正对照） | PASS |
| B 视角 DOM 零 A·/A#（无 A 数据残留） | PASS |
| localStorage 无「不含账号维度」的业务键 | PASS |
| remark 键均带账号维度（qqscope_remark_账号_...） | PASS |
| data-js-errors = 0 | PASS |

覆盖点：会话列表、气泡区（点开会话加载消息）、总览 KPI、动态卡片、导出列表、AI 预览、资料卡所在页面。
---

## ⑤ 本轮新发现并修复的泄漏清单

1. store.delete_account 漏删 feeds
   - 原实现只 DELETE messages / contacts / accounts，feeds 原样留在库里；DELETE /api/accounts/B 之后 B 的动态仍在。
   - 本轮写路径矩阵的「DELETE B 后 B 的 feeds 必须为 0」直接抓到（修复前 b_feeds > 0）。
   - 修复：delete_account 增加 DELETE FROM feeds WHERE account_qq=?（feeds 表不存在时忽略）。

2. /api/send 的 dry_run 分支绕过账号身份校验
   - 原实现 `if dry_run: return` 在 `login = _login_qq()` 与 account==login 校验之前，导致 dry_run:true 时任意
     account_qq 都返回 200，隔离校验被绕过。
   - 修复：把登录身份校验前移到 dry_run 之前；dry_run 与真实发送一样必须先通过 account_qq == 框架登录号。
   - 测试：send A + dry_run 200；send B（框架登录 A）+ dry_run 400 account_mismatch。

3. /api/media/backfill/status 与 /stop 缺 account 不拒绝
   - 任务注册表是进程内全局的，缺 account 时可查询/停止任意任务。
   - 修复：两个端点缺 account 一律 400，且带 account 时校验任务归属（不属于 -> 404/ok=false）。

4. QZone 全局凭据单例的静默混用（设计上全局 -> 改成显式禁用 + 提示）
   - qzone.sync 的 account_qq 默认取全局凭据 uin，但调用方可以传别的 account_qq：会用 A 的全局 cookie 抓动态，
     却把行写进 B，属静默混用。
   - 修复：qzone.sync 在 account_qq 与全局凭据 uin 不一致时显式抛 QZONE_ACCOUNT_MISMATCH；
     /api/qzone/sync、/api/sources/qzone/sync（含 app.py 通用路由）映射为 400 + 人话提示：
     「QZone 登录态是全局单例（当前 uin=...），不能用它同步账号 ... 的动态」。
   - 前端没有 qzone sync 调用点，无需改前端；用户看到的是明确失败提示，而不是串号数据。

（上一轮的 5 个泄漏点保持不变且被本轮全量矩阵再次验证；本轮这 4 条是「真实双账号 + 写路径 + DOM」设计才暴露的。）

---

## ⑥ 全局合理例外（不算泄漏，显式标注）

| 接口 / 路径 | 为什么全局是合理的 |
|---|---|
| GET /api/accounts | 账号选择器必须知道本机有哪些号；只暴露账号号与计数，不含消息内容 |
| GET/POST /api/settings | 端口 / AI / 发送策略 / 主题等全局设置，非账号业务数据 |
| GET /api/sources、POST /api/sources/{sid}/probe|conversations | 扫描本机 QQ 数据目录/框架以「发现有哪些账号」，不返回某账号的会话内容矩阵 |
| GET /api/login/status、/api/framework/* | NapCat 单登录框架状态，本质全局 |
| GET /api/live/status | 单登录实时采集状态，本质全局 |
| GET /api/avatar、/api/avatar/group | 按 QQ 号 / 群号的公开头像，不含聊天内容 |
| GET /api/sources/qzone/status | QZone 登录态（全局单例）登录与否 |
| GET /api/logs、/api/health、/api/progress/{sid} | 运行日志 / 健康 / 进度，非账号业务数据 |

---

## ⑦ 回归结果与红线声明

| 项目 | 基线 | 本轮 | 结果 |
|---|---|---|---|
| check_v2..v14 | 258 PASS / 0 FAIL | 258 PASS / 0 FAIL | 未回退 |
| check_v15 | 44 PASS / 0 FAIL | 44 PASS / 0 FAIL | 未回退（见下注） |
| check_v15 套件总计（v2..v15） | 302 PASS / 0 FAIL | 302 PASS / 0 FAIL | 未回退 |
| check_v16（本轮新增静态断言） | — | 22 PASS / 0 FAIL | 新增 |
| check_v2..v16 合计 | 302 / 0 | 324 PASS / 0 FAIL | 通过 |
| scripts/isolation_test.py（升级为真实双账号） | 52 / 104 | 106 PASS / 0 FAIL | 通过 |
| web/selftest/check_v16_dom.mjs | — | 12 PASS / 0 FAIL | 新增 |
| scripts/verify.py --full | 10/10 -> 11/11 | 11/11 exit=0（隔离项 106/106） | 通过 |
| python scripts/build_web.py | — | 产物重建，占位符已替换 | 通过 |

注：check_v15 的「backfill/stop 校验账号归属」是一条静态正则断言；为容纳新增的「缺 account 400」守卫，把正则
窗口从 400 字符放宽到 900 字符，断言语义不变（仍要求同时出现 if not account 与 任务不属于该账号）。

红线声明：
- 真实 data/qqscope.db 全程只读（mode=ro + sqlite3.backup() 到临时根）；复核 real accounts=[1605289411]、
  真实库中 account_qq=3060648699 行数 0、A#/B# 标记行数 0。
- 未触碰 tools/napcat/；未 kill/重启 NapCat；未重启 15555；DOM 测试后端只监听 127.0.0.1:15557，
  启动前先探测端口占用（已有服务则拒绝启动），结束只 taskkill 自己 spawn 的 PID。
- 未真实发送任何消息（send 只跑 dry_run 与拒绝分支，且测试内 monkeypatch 了登录查询）。

已知边界（如实说明）：
- DOM 复核走应用自带的 ?authed=1 测试通道，并 monkeypatch 框架/数据源探测以避免碰 NapCat；
  它证明的是「数据隔离 + 切账号 DOM」层，不覆盖真实扫码登录流程。
- 矩阵中 B 的消息 id 是克隆时新分配的（与 A 不重叠），因此「A 响应里出现 B id」可以被逐 id 精确抓到；
  这正是双账号夹具要解决的问题。