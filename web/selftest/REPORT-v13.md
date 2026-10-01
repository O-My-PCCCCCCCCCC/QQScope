# task-5 / v13 复验报告：两个存量 bug 修复 + 自测脚手架修正

- 复验人：web-verifier（独立复验；未改产品文件）
- 时间：2026-10-01 18:58–19:20
- 范围：Lead 修的 2 个真实 bug（`web/index.html`，已重建 `app/dist/QQScope.html`）+ 授权的 2 个自测脚手架修正
- 结论：**两条修复都成立（真实 DOM/Edge 证据）；回归全绿；run_edge -Port 15558 从 5 失败变为全部通过。**

---

## 0. 一句话结论

| 项 | 结果 |
|---|---|
| 修复 1：探测按钮无反馈 | ✅ 已修复（成功/失败都出现可见文本，不再静默） |
| 修复 2：AI 页 `ensureReport is not defined` | ✅ 已修复（`data-js-errors=0`、`#jsErrorLog` 空、`#aiPreview` 有内容） |
| 回归 check_v2..v10（源码） | ✅ 223 PASS / 0 FAIL |
| 回归 check_v8 / check_v9（DOM） | ✅ 25/0、51/0 |
| check_web（修正后）4 个文件 | ✅ 全通过 exit=0 |
| `run_edge.ps1 -Port 15558` | ✅ 全部通过（v12 时 5 项失败） |
| `node scripts/check_html.js app/dist/QQScope.html` | ✅ 3 块语法 OK，exit=0 |

---

## 1. 被验修复项（源码/产物只读确认）

命令：
```powershell
Select-String -Path web\index.html -Pattern 'btnBotProbe|function ensureReport|function probeBot' -Encoding UTF8
```

- `probeBot` 绑定（`web/index.html` L4099）：
  `if ($("btnBotProbe")) $("btnBotProbe").addEventListener("click", function () { probeBot(false); });`
- `ensureReport` 定义（L2341–2353），紧跟在 `loadReport()` 之后；并发复用 `state.reportPending`。
- 产物 `app/dist/QQScope.html`：`function ensureReport(` 存在；`addEventListener("click", probeBot)` 无残留。

---

## 2. 修复 1 复验：探测按钮（真实后端 DOM 证据）

做法：自建 `proxy_server.mjs :15596 -> http://127.0.0.1:15555`，把从**当前产物**生成的夹具页经代理用真实后端跑（`/api/sources/bot/probe` 是连通性探测，不是发消息）。

| 场景 | 夹具 | `#botResult` 实测 | JS 错误 |
|---|---|---|---|
| 框架开着（NapCat OneBot :3000） | `demo-probe-ok.html` | **`连接成功：霖ケ（QQ 1605289411），好友 58 个，群 55 个`** | `data-js-errors=0` |
| 框架没开（:3999 无人监听） | `demo-probe-fail.html` | **`连接 http://127.0.0.1:3999 超时：目标无响应，请确认地址/端口正确、NapCat 已启动，并检查防火墙是否放行`** | `data-js-errors=0` |

- 修复前（v12）：`#botResult` 为空、界面无任何反馈（请求其实发出去了）——现在两种情形都有可见文本，**修复成立**。
- 说明：本机防火墙对关闭端口表现为 SYN-drop（超时），不是 RST，所以复现出的是「连接超时」而非「目标计算机拒绝连接」；这不影响「不再静默无反馈」的验收。
- mock 侧（run_edge）：`dom-demo-sources.html` 的 `#botResult = 连接成功：MockBot（QQ 10001），好友 30 个，群 12 个`。

---

## 3. 修复 2 复验：AI 解读页

命令（Edge `--dump-dom`）：
- mock 后端：`http://127.0.0.1:15559/demo/ai-v13.html`（当前产物 + mock `/api/report`）
- 真实后端：`http://127.0.0.1:15596/demo/ai-v13.html`（proxy → 15555）

| 指标 | 修复前（v12） | 修复后（mock） | 修复后（真实后端） |
|---|---|---|---|
| `data-js-errors` | **1** | **0** | **0** |
| `#jsErrorLog` | `[unhandledrejection] ensureReport is not defined` | 空 | 空 |
| `#aiPreview` | 停在「（等待数据加载…）」 | 有内容（len=289） | 有内容（len=294） |
| `#aiPreview` 内容 | — | `你是 QQScope 的精神状态解读助手…账号：主号（Mock）（QQ 1605289411）…消息总数 261 条` | `…账号：霖ケ（QQ 1605289411）…消息总数 280029 条（自己发出 7845 条）` |

截图：`docs/截图/v13-AI解读-修复后.png`（978694 B，真实后端）。
**修复成立。**

---

## 4. 自测脚手架修正（授权范围内）

### 4-1 `web/selftest/check_web.mjs`（假阳性修复）

- 改前（L33）：`const isBuilt = !/__QQSCOPE_ENGINE__/.test(html);`
  → `web/index.html` 的 `escRe()` 里含字符串 `"\/*__QQSCOPE_ENGINE__*/"`，构建后该字面量仍在，产物被误判为「源码态」，于是对产物断言「占位符独立成行」→ 必 FAIL（v12 的 4 项假阳性）。
- 改后：
  ```js
  const isBuilt = !/^\s*\/\*__QQSCOPE_ENGINE__\*\/\s*$/m.test(html);
  ```
  只认「独立成行的 ENGINE 占位符」是否存在。
- **源码侧断言未削弱**：源码态仍断言 boot/engine 占位符独立成行 + 格式精确；产物态仍断言占位符已替换、boot 值为 null/对象/数组。
- 结果：`node web\selftest\check_web.mjs web\index.html web\selftest\out\QQScope.selftest.html web\selftest\out\QQScope.api.html app\dist\QQScope.html` → **全部通过 ✅ exit=0**（改前 selftest/api 各 2 项 FAIL）。

### 4-2 `web/selftest/run_edge.ps1`：sessions 文案

- 改前 must：`'加载更多历史消息','今天有点累','私聊（2）','群聊（1）'`
- 改后 must：`'加载更早（剩余','今天有点累','私聊 2','群聊 1'`
- 实测当前渲染（`dom-demo-sessions.html`）：
  - 分组头：`<span class="cg-title">私聊</span><span class="cg-badge">2</span>` / 群聊 1
  - 加载按钮：`↑ 加载更早（剩余 <b id="loadMoreRemain">0</b> 条）`
  - 因为标题/计数被拆成相邻 span、HTML 里没有字面「私聊 2」，运行时另加了 `Plain()`（`<[^>]+>`→空格、`\s+`→单空格）作为**兜底匹配**；原 HTML 匹配保留，不削弱。
- 结果：sessions：无 JS 报错 PASS、关键内容渲染（7 项）PASS。

### 4-3 `web/selftest/run_edge.ps1`：另两处（因为改用了 `-Port 15558` 且 mock 能力所限）

1. **sources 夹具 base**：`127.0.0.1:$Port` → `127.0.0.1:15556`。
   原因：`mock_server.mjs` 的 `/api/sources/bot/probe` 硬编码「base 含 `:15556` 才返回 MockBot 成功，否则返回 502 连不上」；用 15558 会把成功分支走成失败。mock_server 不在授权修改名单内，故在夹具侧对齐 mock 的判据（mock 并不真的连接该地址）。
   实测：`dom-demo-sources.html` → `#botResult = 连接成功：MockBot（QQ 10001）…`，sources 8 项 PASS。
2. **botfail 夹具**：mock 对 `bot/probe` 永远返回成功，无法模拟「框架没开」。为保留失败渲染断言，在夹具里对该接口注入
   `window.fetch = ... Promise.reject(new Error('目标计算机拒绝连接'))`，让前端走真实失败分支。
   实测：`dom-demo-botfail.html` → `#botResult = 连接失败：目标计算机拒绝连接`，botfail 2 项 PASS、无 JS 报错。
   （真实失败路径另见 §2 的 proxy→15555→:3999 实测。）

### 4-4 修正后 run_edge 结果

命令：
```powershell
powershell -ExecutionPolicy Bypass -File web\selftest\run_edge.ps1 -Port 15558
```
结果：**[1/5] 静态断言全部通过 ✅；[4/5] 6 个页面 12 项断言全部 PASS；[5/5] 截图 9/9 PASS；`===== 前端验收：全部通过 ✅`**（日志 `web\selftest\out\run_edge_v13b.log`）。

---

## 5. 回归断言

命令与结果：

| 命令 | 结果 |
|---|---|
| `node check_vN.mjs web\index.html`（N=2..10） | v2 51、v3 22、v4 23、v5 14、v6 20、v7 18、v8 14、v9 24、v10 37 → **223 / 0**，exit 全 0 |
| `node check_vN.mjs app\dist\QQScope.html`（N=2..10） | 仅「源码占位符 / 源码无字面 http」类差异：v2 48/3、v3 19/3、v4 21/2、v5 13/1、v9 23/1；v6/v7/v8/v10 满 → 与 v12 一致，**无新增失败**（产物按设计占位符已替换；产物侧由 check_v2[构建产物]段 + check_web 覆盖） |
| `node check_v8.mjs web\index.html <4 DOM>` | **25 PASS / 0 FAIL** |
| `node check_v9.mjs web\index.html <5 DOM>` | **51 PASS / 0 FAIL** |
| `node check_web.mjs web\index.html selftest.html api.html dist.html` | **全部通过 ✅ exit=0** |
| `node scripts\check_html.js app\dist\QQScope.html` | 块1/2/3 语法 OK，exit=0 |
| `run_edge.ps1 -Port 15558` | **全部通过 ✅** |

v8/v9 用的 DOM：`out\dom-demo-{sessions,sources,overview}.html` + `out\dom-v13-v8-pill.html`（run_edge 本轮新生成）、`out\dom-v13-{home,consolefw,export}.html`（当前产物 + mock 新 dump）、`out\dom-avatar-group.html`、`out\dom-v9-send.html`（v9 既有证据，send 代码本轮未改）。

---

## 6. 红线遵守

- 未改 `web\index.html`、`app\dist\QQScope.html`、`server\`、`core\`；
- 只改了授权的 `web\selftest\check_web.mjs`、`web\selftest\run_edge.ps1`（改动见 §4）；
- 未发 QQ 消息：`/api/sources/bot/probe` 只是连通性探测；全程未调用 `/api/send`；
- 未 `POST /api/sources/*/sync`、未触发媒体下载、未改 `data/qqscope.db`；
- 未碰 node PID 39760（NapCat）；未重启 15555；
- Edge 全程单实例串行（一次 `--screenshot` 超时后已放弃，未并发）；
- 写入：`web\selftest\**`、`docs\截图\v13-AI解读-修复后.png`、本报告；文本 UTF-8 无 BOM。

---

## 7. 遗留 / 建议（不阻塞本次复验）

1. `mock_server.mjs` 的 bot/probe 成功判据硬编码 `:15556`，是脚手架债；建议后续改成参数化（env/argv）或按 `host:port` 解析，避免再被 `-Port` 换端口打脸。本轮未改（不在授权名单）。
2. run_edge 的 botfail 失败分支仍是「夹具注入 fetch reject」，不是端到端真失败；真失败路径已用 proxy→15555→:3999 的 DOM 单独验证（本机表现为超时文案）。
3. 本机对关闭端口是「超时」而非「拒绝连接」，因此 `目标计算机拒绝连接` 依赖 OS/防火墙行为，建议断言用「连接失败」这类稳定文案。
