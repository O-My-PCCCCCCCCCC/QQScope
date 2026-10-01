# QQScope v18b · 「退出登录必须真的退出 + 新增全部退出」修复报告（task-12）

- 负责人：logout-fixer
- 用户反馈：「为啥我登录了一个账号之后就退不出去了？」「没有一个全部退出的东西，全部退出是 web 退出，框架退出」
- 结论：根因 3 条全部复现并修复；check_v18b（源码 + 假后端无头 Edge 抓包）= **44 PASS / 0 FAIL**；check_v2..v17 = **355/1**，唯一 FAIL 与本任务无关（见第 6 节，isolation-auditor 的 core/scripts 在途改动）。
- **红线声明：未对运行中的 NapCat 做任何写操作**（未发 bot_exit / taskkill，未调真实 /api/framework/logout、/api/framework/stop；未重启 15555）。

## 1. 根因复现确认

| # | 根因 | 复现/核对 |
|---|------|-----------|
| 1 | 主界面「退出登录」默认 `force=false` → Tier1 `bot_exit`；NapCat 不响应就落 `external` 兜底，只弹横幅 + 一个按钮，**框架其实还登录着** | 旧 `doLogout` 的 `mode==="external"` 分支只 `showExternalLogoutBanner`；用假后端返回 `{ok:false,mode:"external",can_force:true}` 时页面停在「已连接 霖ケ」，不退出 |
| 2 | task-10 后前端按框架登录号绑定 `currentQq`：只要框架还登录，重开/轮询就会自动进主界面 → 用户「退不出去」 | `applyLoginIdentity` / `fetchStatus(?account=)` / `poll()` 的 `s.logged_in` 自动 `enterApp`；`manualLock` 只防本轮，刷新页面即失效 |
| 3 | 没有「全部退出」：web 退出（清标记）与框架退出（停 NapCat）是两个分散入口 | 顶部只有「退出登录」，连接门只有「退出并停止框架」；用户要一个合成动作 |

## 2. 改动清单

### 2.1 后端 `server/routes_framework.py` — `POST /api/framework/logout`
- **默认（force=false）一次到位**：先 Tier1 `bot_exit`；`_wait_ports_free(timeout≈2.4s)` + `is_framework_proc` 复核，2 秒内没退出就**自动升级** Tier2/3 taskkill（不再停在 external）。
- **force=true**：跳过优雅动作，直接强制结束（仍先过 `is_framework_proc` 身份校验）。
- **必须先过身份校验**：`verified = is_framework_proc(pid, force=True)`；身份不过时连 `bot_exit` 都不发，直接明确失败。
- **失败不假装成功**：找不到进程 / 身份校验不过 / kill 异常 → `{ok:false, mode:"external", can_force:false, reason, manual, message}`，附「双击关闭机器人框架.bat / 任务管理器结束 node」的手动办法。
- 自有框架仍走 `mode="stop"`；外部框架 `mode="force_stop"`；kill 复用 `_logout_kill`（kill 前再校验一次身份）。

### 2.2 前端 `web/index.html`
- **新增显眼「全部退出」**：主界面 masthead `#btnFullExit`（`btn mini danger`，`authChip` 旁，进入后显示/退出后隐藏）+ 连接门 `#btnGateFullExit`（QR/已连接态显示）。
- `fullExitConfirm()` = 二次确认（`FULL_EXIT_COPY`）→ `resetAppData()` → `doLogout(true)`；一次动作 = ①停实时采集 ②`POST /api/framework/logout{force:true}` ③清 web 登录标记与展示层 ④回连接门。
- `logoutUser()`（退出登录）：文案 `EXIT_COPY` 写明「优雅退出 2 秒未生效会自动强制结束」；`doLogout(false)`。
- `doLogout` 新逻辑：
  - 成功（`ok!==false` 且 mode∈bot_exit/stop/force_stop）→ 「已退出登录并结束框架；下次请重新双击 ①启动机器人框架.bat 并扫码」。
  - `external && can_force && !force` → **自动升级** `requestFrameworkLogout(true)`（旧后端/未升级后端也一次到位）。
  - 真失败 → `showLogoutFailure()`：`conn-banner err` 红字 + 原因 + PID + 手动办法，并同时写 `loginHint`（红）；`logoutFail` 常驻，`poll()` 每个周期重渲染，不会被 `renderConnected` 的 setHint 冲掉。
- `stopFramework()`（连接门「退出并停止框架」）同套逻辑（`FULL_EXIT_COPY` + `doLogout(true)`）。
- `prepareLogout()` 清 `logoutFail`；`enterApp()`/框架未登录时清 `logoutFail`；`resetAppData()` 隐藏 `#btnFullExit`。
- 未新增 `/api/live/stop` 调用点（仍只有 doLogout + poll 两处，v17 约束不回退）。

### 2.3 测试与产物
- 新增 `web/selftest/check_v18b.mjs`：源码断言 + **自带假后端 + 无头 Edge 抓包**（`--evidence` 可 dump 证据），输出 `PASS n / FAIL n`。
- `python scripts\build_web.py` 重建 `app/dist/QQScope.html`（0.34 MB）。

## 3. 断言结果

| 检查 | 结果 |
|------|------|
| `node check_v18b.mjs`（源码 + 假后端 Edge 抓包） | **PASS 44 / FAIL 0** |
| `node check_v18b.mjs app/dist/QQScope.html` | **PASS 44 / FAIL 0** |
| `node check_v14.mjs` / `check_v14.mjs app/dist/QQScope.html` | PASS 35 / FAIL 0 |
| 后端安全断言（假端口 + stub kill/bot_exit） | **PASS 12 / FAIL 0** |
| `check_v2..v17` 合计 | **355 PASS / 1 FAIL**（v16 唯一 FAIL，外部原因见 §6） |
| `python scripts\verify.py --full` | 12/13（唯一 FAIL=多账号隔离矩阵，外部原因见 §6） |

check_v18b 覆盖（节选）：
- 存在 `#btnFullExit` / `#btnGateFullExit` 且 danger+「全部退出」；`enterApp` 显示、`resetAppData` 隐藏、连接门绑定。
- `fullExitConfirm` 走 `openConfirm`（二次确认），onOk = `resetAppData()` + `doLogout(true)`。
- `doLogout` 里 `/api/live/stop` 在 `requestFrameworkLogout` 之前；`/api/framework/logout` 带 `{force}`；`prepareLogout` 锁门 `showGate`。
- external+can_force → `requestFrameworkLogout(true)` 自动升级；失败 → `conn-banner err` + `reason` + `manual` + `logoutFail` 常驻。
- `FULL_EXIT_COPY` 含「框架」「数据保留/不会被清除」；`live/stop` 全文件仍 2 处。
- 后端：身份校验在优雅动作之前、四种 mode、失败带 reason/manual。

## 4. 真实 DOM / 抓包证据（假后端 + 无头 Edge，绝不触碰 15555）

`check_v18b.mjs --evidence` 实测（源码页 served by 假后端）：

### 场景 1：点「全部退出」→ 成功（mode=force_stop）
- 确认弹窗：「『全部退出』会同时退出 Web 与 QQ 框架：· 断开 QQ 登录并结束 NapCat 框架、停止自动采集与实时同步；· 下次…重新扫码登录；· **本地归档数据会保留**，不会被清除…」
- 抓包顺序：`/api/live/stop {}` → `/api/framework/logout {"force":true}`。
- before：会话 3 条 / 消息气泡 721 字符 / authChip 可见 / 门隐藏。
- after：会话 **0** / 气泡 **0** / authChip **隐藏** / **门重新显示** / 门上有「全部退出」入口 / `data-js-errors=0`。
- 最终横幅：「已退出登录并结束框架；下次使用请重新双击 ①启动机器人框架.bat 并扫码」。

### 场景 2：点「退出登录」→ 假后端先 external{can_force:true}
- 抓包：`/api/live/stop {}` → `/api/framework/logout {"force":false}` →（自动升级）→ `/api/framework/logout {"force":true}`。
- 结果：loggedIn=false，门重新显示，会话 0 / 气泡 0 / authChip 隐藏，`data-js-errors=0`。
- 即：**「退出登录」不再停在 external 横幅，而是自动强杀，一次到位**。

### 场景 3：失败路径（假后端 external{can_force:false}）
- after2（跨过一个 poll 周期 3.4s 后仍在）：横幅 class 含 `err`、可见；
  正文 =「退出登录失败：框架没有结束，QQ 可能仍处于登录态。原因：模拟：身份校验未通过（不是 napcat）（框架 PID 39760）手动处理：到项目目录双击「关闭机器人框架.bat」。本地归档数据会保留，不会被清除。」
  `#loginHint` =「退出登录失败：框架没有结束。模拟：身份校验未通过（不是 napcat） 手动处理：…」；门仍显示；`data-js-errors=0`。
- 即：**失败红字 + 具体原因 + 手动办法，常驻不静默**。

### 后端安全断言（12/12）
T1 bot_exit 生效→`bot_exit` 且不 kill；T2 bot_exit 无效→自动 `force_stop` 且只 kill 目标 PID；T3 端口没开→直接 `force_stop` 不发 bot_exit；T4 `force=true`→直接 `force_stop`；T5 身份不过→`ok:false/mode:external/can_force:false + reason/manual`，0 次 kill；T6 找不到→失败带「找不到」；T7 kill 抛异常→失败带原因。

## 5. 红线声明

- **未对运行中的 NapCat 做任何写操作**：所有退出相关验证都在假后端 / stub kill / 假端口上完成；未调用真实 `/api/framework/logout`、`/api/framework/stop`，未发 `/bot_exit`，未 taskkill。
- 未重启 15555；未修改 `data/qqscope.db` 用户数据（`build_web.py` 仅按设计读库注入快照）。
- 交付时只读复核：15555 已被 Lead 重启为新代码（`/api/login/status` 已返回 `can_force`；当前 `pid=33248`，`can_force=true`）；NapCat 子进程已由 39760 变为 33248（父进程 18896 不变），当前 `logged_in=false`（等待扫码）、`live running=false`。任务期间本人未重启 15555、未对框架发任何写请求；最终重启/扫码由 Lead/用户完成。

## 6. 与本次改动无关的两个外部 FAIL（请 Lead 转 isolation-auditor 跟进）

1. `check_v16` 的 `delete_account 一并删 feeds` FAIL：断言只读 `core/store.py`，而该文件的 `delete_account()` 已被任务-7（多账号隔离）改为「删账号目录 + 从注册表移除」，不再有 `DELETE FROM feeds WHERE account_qq=?`。我的改动不涉及 core/**，`check_v16` 其余 21 条全 PASS。
2. `verify.py --full` 的「多账号数据隔离矩阵」FAIL：`scripts/isolation_test.py` 在途状态（先报 `NameError: name 'store' is not defined`，后报 `sqlite3.OperationalError: no such table: t3060648699.feeds`），属任务-7 的脚本/核心改造。与退出登录无关的 12 项（含 web 源码自检、构建单文件前端、v5-v10 接口注册）全部 PASS。

除上述两项外，`check_v2..v17` 的 355 PASS 与我上一轮 356 PASS 基线逐项一致；`check_v18b` 为新增 44/0。