# task-4 / v12 前端全量回归复核报告（v2–v10 + v8/v9 结项）

- 复核人：web-verifier（独立复核，未改任何产品源码）
- 时间：2026-10-01 18:46–18:56
- 对象：`web/index.html`（v10 源码，18:14）、`app/dist/QQScope.html`（单文件产物）
- 结论：**check_v2..v10 源码 223 PASS / 0 FAIL；v8 可结项；v9 可结项**。
  run_edge 的 5 项失败已定位：1 项自测脚本假阳性 + 2 个真实缺陷（均自 v4 起的存量 bug，不属 v8/v9 回归）+ 2 项断言文案过期（v3 时期写的期望）。
  `verify.py --full` **9/9 PASS，exit=0**。

---

## 1. check_v2..v10 断言（源码 + 产物）

命令（源码）：`node web\selftest\check_vN.mjs web\index.html`
命令（产物）：`node web\selftest\check_vN.mjs app\dist\QQScope.html`

| 断言 | 源码 PASS/FAIL | 产物直接跑 PASS/FAIL | 说明 |
|---|---|---|---|
| check_v2 | **51 / 0** | 48 / 3 | 产物 3 项失败全是「BOOT/ENGINE 占位符 / 源码无字面 http」，属源码态断言；check_v2 的 [构建产物] 段（在 51/0 内）已覆盖产物：BOOT 注释已替换、ENGINE 已替换、除注入数据外无外链 —— 全 PASS |
| check_v3 | **22 / 0** | 19 / 3 | 同上（占位符 + 字面 http） |
| check_v4 | **23 / 0** | 21 / 2 | 占位符 + 「不直连外部 CDN」（产物 boot JSON 内含 URL 字符串） |
| check_v5 | **14 / 0** | 13 / 1 | 占位符两行逐字 |
| check_v6 | **20 / 0** | 20 / 0 | |
| check_v7 | **18 / 0** | 18 / 0 | |
| check_v8 | **14 / 0** | 14 / 0 | |
| check_v9 | **24 / 0** | 23 / 1 | 占位符两行逐字 |
| check_v10 | **37 / 0** | 37 / 0 | |
| **合计** | **223 / 0** | — | 源码全绿，exit 均 0 |

**产物类失败的判定**：`app/dist/QQScope.html` 是构建产物，占位符按设计必须被替换、boot JSON 里的 URL 只是数据 —— 因此「源码占位符 / 源码无字面 http」这类断言在产物上必然 FAIL，不是回归。产物对应的正确断言是：
- `check_v2.mjs` 的「构建产物」段：BOOT 注释已替换、ENGINE 占位符已替换、产物无 `<script src=`/`<link>`/`@import`/`<img http`/`href=http`、除注入数据外无 `http(s)://` → **全 PASS**；
- `check_web.mjs` 的 `isBuilt` 分支（见 §3 假阳性）。

结论：**产物与源码一致，无 v2–v10 断言回退。**

---

## 2. 一键验收脚本（run_edge）

命令：
```powershell
powershell -ExecutionPolicy Bypass -File web\selftest\run_edge.ps1 -Port 15558
```
结果：**5 项失败 ❌**（原始日志：`web\selftest\out\run_edge_v12.log`）。逐条定位：

| 步骤 | 项 | 结果 | 定位 |
|---|---|---|---|
| [1/5] check_web 静态断言 | 4 项 | FAIL | **自测脚本假阳性**，非产品问题（见 §3-3） |
| [4/5] sources 关键内容 | 缺失 `MockBot` | FAIL | **真实缺陷**：`probeBot` 被当成 click 监听器直接调用（见 §6-1） |
| [4/5] sessions 关键内容 | 缺失 `加载更多历史消息 / 私聊（2）/ 群聊（1）` | FAIL | **断言文案过期**：v3 时期文案；现行渲染为「加载更早（剩余 N 条）」「私聊 2」「群聊 1」，功能在 |
| [4/5] ai JS 报错 | `data-js-errors=1` | FAIL | **真实缺陷**：`ensureReport is not defined`（见 §6-2） |
| [4/5] botfail 关键内容 | 缺失 `目标计算机拒绝连接` | FAIL | 文案过期 + 受 §6-1 影响，探测结果根本没渲染 |
| [4/5] 其余（sources/sessions/export/overview/botfail 无 JS 报错、export/overview 内容） | 8 项 | PASS | |
| [5/5] 截图 | 9 张 | **9/9 PASS** | |

---

## 3. v8 验收点逐条判定

静态断言：`node web\selftest\check_v8.mjs web\index.html ...DOM...` → **25 PASS / 0 FAIL**

| # | 验收点 | 证据 | 判定 |
|---|---|---|---|
| 1 | 数据源页默认折叠为摘要行 | DOM `dom-v12-sources.html`：`class="src-summary"` × 3、`id="srcDetail" hidden` 为真；源码断言 `_srcDetailOpen:false`、`srcSummary`/`srcDetail hidden`、`toggleSrcDetail` | ✅ |
| 2 | 「最后检测 HH:MM:SS」时间戳 | DOM：`最后检测 18:50:45`；截图 `v8-数据源折叠.png` | ✅ |
| 3 | 新消息胶囊 `N 条新消息 ↓` | 源码 `newMsgPill`/`showNewMsgPill`/`hideNewMsgPill`；DOM `dom-v12-v8-pill.html`：`data-pill-on="1"`、`data-pill-text="1 条新消息 ↓"`（自检合成数据，非真实消息）；截图 `v8-自动更新-自检-新消息胶囊.png` | ✅ |
| 4 | 自动刷新开关存在且可关 | 源码 `autoRefresh:true, refreshSec:15`、`qqscope_refresh_sec`、`start/stop/autoRefreshTick`；DOM 会话页有 `id="autoRefreshToggle"` + `id="refreshInterval"`；截图 `v8-自动更新.png` | ✅ |
| 5 | `visibilitychange` 暂停轮询 | 源码断言 `visibilitychange` + `stopAutoRefresh()` 命中 | ✅ |
| 6 | 图片补下载入口 + 进度显示 | 源码 `startBackfill/stopBackfill/renderBackfill`、`/api/media/backfill`、「已补/成功/失败」；DOM 总览有 `id="btnBackfill"` + `id="backfillLine"`；截图 `v8-图片补下载.png` | ✅ |
| 7 | 证据截图覆盖 | `docs/截图/`：`v8-数据源折叠.png`、`v8-自动更新.png`、`v8-自动更新-自检-新消息胶囊.png`、`v8-图片补下载.png`、`v8-群聊头像.png` | ✅ |

**v8 判定：可结项。**

---

## 4. v9 验收点逐条判定

静态断言：`node web\selftest\check_v9.mjs web\index.html ...DOM...` → **51 PASS / 0 FAIL**

| # | 验收点 | 证据 | 判定 |
|---|---|---|---|
| 1 | 默认落地页是「总览」 | 源码 `PAGES=["overview",...]`、`parseHash()||"overview"`；DOM `dom-v12-home.html`：`data-home-view="view-overview"`、`data-first-nav="overview"`、`data-kpi="6"`；截图 `v9-首页总览.png` | ✅ |
| 2 | CONSOLE 双标签 QQScope / 框架(NapCat)，框架页调 `/api/framework/logs` | 源码 `logTabs`、`data-tab="framework"`、「框架(NapCat)」、`/api/framework/logs?limit=300&offset=`、`state.fwOffset`；DOM `dom-v12-consolefw.html`：`data-fw-tab="on"`（框架页激活）；真实后端 `GET /api/framework/logs?limit=5` → 200 且返回真实 NapCat 行（见 §5）；截图 `v9-框架终端.png` | ✅ |
| 3 | 会话页发送输入框 + 二次确认弹窗（含群聊额外提示） | 源码 `sendText/sendCount/btnSend`、`sendConfirm/openSendConfirm`、`confirm:true`、「这是群聊 / N 人会看到」；DOM `dom-v9-send.html`：`data-confirm-open="1"`、确认文案「…确定发送…」、`data-last-bubble` 含 `[dry_run·未真实发送]`、`data-send-err=""`；截图 `v9-发消息-确认弹窗.png` | ✅ |
| 4 | 导出页时间范围 + 私聊/群聊分组 + 全选快捷 | 源码 `expRange/expSince/expUntil`、`expRangeValue`、`exp-group-head`、「私聊 / C2C」、`expAllC2C/expAllGroup`、`opts.since/until`；DOM `dom-v12-export.html`：`data-exp-range="最近 7 天"`、`data-exp-hint="时间范围：2026-09-24 ~ 至今"`、`data-exp-groups="2"`、`data-exp-quick="11"`；截图 `v9-导出时间段.png` | ✅ |
| 5 | 真实发消息（**未重发**，只核验既有证据） | ① 截图 `docs/截图/v9-发消息.png` 存在（1192107 B）；② `GET /api/send/history?account=1605289411&limit=20` → 200，4 条历史（含 `【QQScope】真实发送验证`、`【QQScope 自测】网页端发送测试…`）；③ `POST /api/send` 缺 `confirm` → **HTTP 428** `{"error":"发送真实消息需要 confirm:true"}`（只读探测，未带 confirm） | ✅ |
| 6 | 框架终端真返回 NapCat | `GET /api/framework/logs?limit=5` → 200，`lines[0].text` 为真实收信日志（如 `霖ケ \| 接收 <- 群聊 [蔚蓝档案交流群] …`） | ✅ |
| 7 | 群聊发言人头像修复（check_v9 v9 项） | 源码断言命中 `c2c|`+sender_qq 分支；既有 DOM `dom-avatar-group.html`（17:30，真实群聊）中 200 条 other 气泡、`<img src="/api/avatar?qq=…">` 多发言人、气泡内 0 个 `/api/avatar/group` 群头像请求；截图 `v8-群聊头像.png` | ✅ |

> 说明：mock 后端未实现 `/api/framework/*`，所以 mock DOM 下框架页按设计降级为「框架状态接口未就绪」——这同时验证了降级路径；真实后端（15555）有真实 NapCat 行。

**v9 判定：可结项。**

---

## 5. 真实后端只读探测（15555，未重启、未发消息）

探测脚本：`web\selftest\out\probe_v12.py`（httpx `trust_env=False`）

| 请求 | 结果 |
|---|---|
| `GET /api/health` | 200 `{"ok":true,"version":"2.0.0"}` |
| `GET /api/login/status` | 200 `logged_in=true, account=1605289411, nickname=霖ケ, pid=39760, sync_allowed=true` |
| `GET /api/framework/status` | 200 `running=true, pid=39760, version=4.18.28, logged_in=true` |
| `GET /api/framework/logs?limit=5` | 200，真实 NapCat 收信行 |
| `GET /api/send/history?account=1605289411&limit=20` | 200，4 条发送历史 |
| `POST /api/send`（**不带 confirm**） | **428** `发送真实消息需要 confirm:true`（未发送任何消息） |

---

## 6. 发现的问题

### 6-1【真实缺陷·存量】「探测连接」按钮无反馈（v4 起，现网 v10 仍在）
- 现象：`run_edge` sources/botfail 两项缺 `MockBot` / `目标计算机拒绝连接`；`#botResult` 为空，但 mock.log 显示 `POST /api/sources/bot/probe` 确实发出。
- 根因：`web/index.html` 中 `function probeBot(silent)` 把 UI 更新全放在 `if (!silent)` 内；而绑定是
  `$("btnBotProbe").addEventListener("click", probeBot)` —— 点击时 `silent` = MouseEvent（真值），于是探测**静默执行、界面不显示任何结果**。
- 溯源：v3 的 `probeBot()` 无参、正常；v4 起变成 `probeBot(silent)` 且直接绑定 click（`dom-v4-media-demo.html` 已如此）。
- 影响：用户点「探测连接」看不到成功/失败；botfail 场景同理。

### 6-2【真实缺陷·存量】AI 解读页 `ensureReport is not defined`（v4 起）
- 现象：`run_edge` [4/5] ai `data-js-errors=1`；`#jsErrorLog` = `[unhandledrejection] ensureReport is not defined`。
- 根因：`web/index.html` L1964 `else if (page === "ai") ensureReport().then(updateAiPreview);` 调用 `ensureReport`，但全仓（源码/引擎/产物）**没有任何定义**。v3 曾有本地定义
  `function ensureReport(){ if (state.overview||state.report) return Promise.resolve(true); return loadOverview(); }`，v4 起被删但调用点未同步。
- 影响：进入 AI 解读页即抛异常，AI 预览不刷新。

### 6-3【自测脚本 bug】check_web.mjs 的 `isBuilt` 误判
- 现象：`run_edge` [1/5] 对 `out/QQScope.selftest.html` / `out/QQScope.api.html` 误判为「源码」，报 4 项占位符 FAIL。
- 根因：`check_web.mjs` 用 `isBuilt = !/__QQSCOPE_ENGINE__/.test(html)` 判断；而 `web/index.html` 里的 `escRe()` 恰好含字符串 `"\\/*__QQSCOPE_ENGINE__*/"`，构建后该字面量仍在 → 永远判成源码态。
- 影响：只影响自测脚本，不影响产品；修法：把 isBuilt 判据改为「ENGINE 占位符独立成行存在」而非字符串出现。

### 6-4【流程注意】`verify.py --full` 会重建 `app/dist/QQScope.html`
- 本轮按任务要求跑了 3 次 `verify.py --full`，每次都经 `scripts/build_web.py` 重建产物；boot JSON 里 `generated = time.strftime(...)` 每次不同，故 SHA256 变化（335417→335711 B）。
- 已复验：重建后 `check_v2`（含 [构建产物] 段）51/0、`check_v10` 源码与产物均 37/0、`check_web` 产物分支全 PASS —— 内容等价，仅时间戳差异。
- 提醒：`dist-package/QQScope-Portable-v2.zip`（18:06 打包）现在相对新产物是「旧时间戳」，如需完全一致可重新打包；功能上无差异。

---

## 7. 回归检查（verify.py --full）

命令：
```powershell
tools\nt_msg_db_util\.venv\Scripts\python.exe scripts\verify.py --full
```
结果：**9/9 通过 · 全绿 ✅ · EXITCODE=0**（日志 `web\selftest\out\verify_v12c.log`）

```
[PASS] core.paths 可用 / core.store schema / 数据源 pack 模块 / 数据源 bot 模块
[PASS] core.export 模块 / web 源码自检 / 构建单文件前端 / 数据现状 / 导出实测
===== 总结 =====  9/9 通过  全绿 ✅
```
（`数据现状 总消息=279532 自发=7820`；期间 asr-fixer 的 whisper 批量任务仍在往库里写 ASR 消息，故条数逐次略增，属队友正常活动。）

---

## 8. 红线遵守

- ✅ 未发任何 QQ 消息（`POST /api/send` 仅做「缺 confirm」只读探测，得 428；未带 confirm、未带 dry_run）
- ✅ 未 `POST /api/sources/*/sync`、未触发媒体批量下载、未改 `data/qqscope.db`
- ✅ 未碰 node PID 39760（NapCat）；未重启 15555
- ✅ Edge 全程单实例串行；未改任何 `.py`/`.html`/`.json` 产品文件
- ⚠️ 写入仅限：`web\selftest\out\*`、本报告；另 `verify.py --full` 按任务要求重建了 `app/dist/QQScope.html`（见 6-4）

## 9. 交付物清单

- `web\selftest\REPORT-v12.md`（本文件）
- `web\selftest\out\run_edge_v12.log`、`verify_v12{,b,c}.log`、`probe_v12.py`
- 新 DOM 证据：`out\dom-v12-{home,consolefw,export,sources,v8-pill,avatar}.html`
- v8 截图 5 张 + v9 截图 5 张（`docs\截图\`）