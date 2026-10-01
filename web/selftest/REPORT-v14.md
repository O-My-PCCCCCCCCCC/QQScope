# QQScope v14 · 「退出登录」缺陷修复报告（task-6）

- 负责人：logout-fixer
- 缺陷单：task-6 T-F 修复「退出登录」：真正断开 QQ 登录 + 清空上次会话记录 + 框架同步退出
- 结论：4 条根因全部修复；源码 223 PASS 基线未回退；新增 check_v14 = 35 PASS / 0 FAIL；已重建 app/dist/QQScope.html。
- **红线声明：未对运行中的 NapCat 做任何写操作**（未调 /api/framework/stop、未调 /bot_exit、未 taskkill、未 kill/重启 node PID 18896/39760；未重启 15555）。

## 1. 根因确认

用户原话：「退出登录没做好，退出登录之后为啥还保留着上次登录的会话记录？框架为啥也没有同步退出登录？」

| # | 根因 | 核对依据 |
|---|------|----------|
| 1 | 前端 `logoutUser()` 不清数据：只做 `removeItem('qqscope_authed'/'qqscope_authed_nick')` + `POST /api/live/stop` + `stopAllDataTimers()` + `entered=false; manualLock=true` + `showGate()`。从不重置 `state.accounts/contacts/messages/overview/report/currentQq`，也不清主界面 DOM | 旧 `web/index.html` 的 LoginGate 模块（check_v10 当时还把它写成「退出登录只清认证键」的特性） |
| 2 | 「退出登录」从不退出 QQ：只停了 QQScope→NapCat 的实时同步，没有结束 NapCat 登录；`/api/login/status` 仍 `logged_in:true` → 连接门渲染「已连接 霖ケ」+「进入」，用户视角＝没退 | 旧 `logoutUser` 无任何 framework 调用 |
| 3 | 「退出并停止框架」停不掉用户框架：`/api/framework/stop` 只肯停 `_spawned_pid()`（`data/framework/spawned.json`）。实测该文件**不存在**，用户是双击 `①启动机器人框架.bat` 启动 → `_can_stop()=false` → 409 拒绝 | 实测 `data/framework/spawned.json` 不存在；真实后端 409「这个框架不是 QQScope 启动的」；日志 19:20:47/19:20:51 两条「拒绝停止框架（非本服务启动）」 |
| 4 | NapCat 没有「登出 QQ」的 OneBot 动作：`tools/napcat/napcat/napcat.mjs` 动作表只有 `Exit:"bot_exit"` 与 `Reboot:"set_restart"`，无 `set_offline`/`logout` 类 | 动作名全表 + wrapper.node 字节搜索（0 命中）；故「退出 QQ 登录」只能靠结束框架进程实现 |

**为什么用户的框架停不掉（一句话）**：用户框架是 `①启动机器人框架.bat` 外部启动的，不在 `spawned.json` 里，`_can_stop()` 依赖该记录 → `false` → 旧 `/stop` 一律拒绝，而 NapCat 又没有登出动作，所以「既没退 QQ、也停不了框架」。

只读实测（真实环境，未写入）：

- `spawned.json`：不存在。
- `napcat_procs()`：`[{pid:18896, index.js（父）}, {pid:39760, napcat.mjs（子）}]`。
- `netstat`：3000 / 6099 两个端口都由 **39760** 持有。
- `_can_stop()` = False，`_can_stop(strict=True)` = False。
- `/api/login/status`（运行中的 15555，旧代码）：`logged_in=true, account=1605289411, nickname=霖ケ, pid=39760, can_stop=false`。

## 2. 改动清单

### 2.1 后端 `server/routes_framework.py`

- 新增 `POST /api/framework/logout`（body `{"force": false}`），按卡分级：
  - **Tier 1 优雅**：OneBot 端口在听 → `core.framework_log._onebot(cfg, "/bot_exit", timeout=3)`；成功且 `_wait_ports_free()` 端口全释放 → `{ok:true, mode:"bot_exit", message:"已退出 QQ 登录（框架已结束）"}`。
  - **Tier 2 自有框架**：Tier1 失败且 `_can_stop(strict=True)` → 复用 taskkill → `mode:"stop"`。
  - **Tier 3 显式强制**：`force=true` 且 `is_framework_proc(pid, force=True)` → kill → `mode:"force_stop"`。
  - **兜底 external**：`{ok:false, mode:"external", pid, can_force, message:"框架由外部启动（PID …），QQScope 不会自动结束它；可点「强制停止框架」或手动关掉框架窗口"}`，**绝不自动杀**。
- 抽出 `_kill_framework_proc(pid)`：`framework_stop` 与 `framework_logout` 共用同一条 taskkill 路径（没有第二套杀进程逻辑）。
- 新增 `_fw_pid_verified()`：只认「node.exe 且命令行含 napcat」；优先端口真正在听的 PID（实测=39760），否则退回列表第一个；找不到返回 0。
- 新增 `_logout_kill()`：**kill 之前再过一次 `is_framework_proc(pid, force=True)`**；成功后清 `spawned.json`、等端口释放。
- `/api/login/status` 增加 `pid` 与 `can_force`（`can_force` = 该 PID 通过 `is_framework_proc` 身份校验，可安全强制停止）。
- 没有新增「能 kill 任意 PID」的路径；kill 全部收敛到 `is_framework_proc` 校验之后。

### 2.2 前端 `web/index.html`

- 新增顶层 `resetAppData()`：
  - 清空 `state.accounts / currentQq / contacts / messages / overview / report / profile / feeds / expContacts / expSelected / live / backfillJob / logs …`，并置 `state._appDataStarted=false`（允许重新进入时重新拉取）。
  - 清空/隐藏 39 个主界面 DOM 容器：`convItems`、`convHead`、`bubbleList`、`expList`、`expHistory`、`expResult`、`feedList`、`aiPreview`、`aiBox`、`kpiGrid`、`reportHint`、`dailyTable`、`peerList`、`wordCloud`、`packAccounts`、`packResult`、`botInfo`、`botResult`、`sourcesStatus`、`dataSummary`、`runInfo`、`syncBarMain` …；隐藏 `authChip` / `btnLogout`。
  - 用现有 render 函数重渲染空态（`renderConvItems/renderOverview/renderExportList/renderFeeds/...`），纯本地、不发任何网络请求。
- `logoutUser()`：确认弹窗文案 `EXIT_COPY` 写清三件事；`onOk` = `resetAppData()` → `doLogout(false)`（仍是二次确认，不自动执行）。
- `doLogout(force)`：只 `removeItem("qqscope_authed"/"qqscope_authed_nick")`（**无 `localStorage.clear()`**）→ 停全部定时器 → 锁门 `showGate` → `POST /api/live/stop` → `POST /api/framework/logout {force}` → 按返回 `mode` 提示：
  - `bot_exit` / `stop` / `force_stop` → 「已退出登录，框架已结束；下次使用请重新双击 ①启动机器人框架.bat 并扫码」
  - `external` → 顶部横幅 + `#btnForceFwStop`「强制停止框架」按钮。
- 新增 `showExternalLogoutBanner(pid)` + `forceStopFramework()`：点「强制停止框架」→ `openConfirm` **二次确认**（文案含「后端会先校验 PID 身份：node.exe 且命令行含 napcat，校验不过不会执行」「本地归档数据会保留」）→ `doLogout(true)`。
- `poll()` 期间保留 external 横幅（`extPid`），避免 3 秒轮询把提示冲掉。
- `stopFramework()`「退出并停止框架」也改走新接口（`doLogout(false)`，不再直接 `/api/framework/stop`）。
- 旧后端兜底：`/api/framework/logout` 返回 404 时退回 `/api/framework/stop`（`requestFrameworkLogout`）。

确认弹窗 `EXIT_COPY` 原文（覆盖用户三个疑问）：

> **这会真正退出 QQ 登录：**
> · 断开当前 QQ 登录、停止实时同步（NapCat 框架会一并结束）；
> · 下次使用需要重新启动框架并（用手机 QQ）重新扫码登录；
> · **本地归档数据会保留**，不会被清除（数据在本机 data/ 目录；要删除请用账号管理里的删除）。

### 2.3 构建

- `python scripts\build_web.py` 已重建 `app/dist/QQScope.html`（0.33 MB；账号 1 个 / 会话 128 个 / 内嵌消息 0 条 / 引擎已注入）。
- **未重打包便携包**（由 Lead 执行）。

### 2.4 新增测试

- `web/selftest/check_v14.mjs`：`node check_v14.mjs [web/index.html]`，输出 `===== check_v14 结果：PASS n / FAIL n =====`。
- 临时证据脚本（`web/selftest/out/`，构建产物目录、非交付）：`v14_server.mjs`（假后端）、`v14_dom.mjs`（CDP 驱动）、`_check_backend_v14.py`（假端口后端断言）。
- 未改 `core/framework_log.py`（复用已有 `_onebot`/`is_framework_proc`/`login_gate`/`load_framework_cfg`，无需改动）。

## 3. 断言结果

| 检查 | 命令 | 结果 |
|------|------|------|
| v14 源码断言 | `node web\selftest\check_v14.mjs` | **PASS 35 / FAIL 0** |
| v14 产物断言 | `node web\selftest\check_v14.mjs app\dist\QQScope.html` | **PASS 35 / FAIL 0** |
| v2..v10 源码基线 | `check_v2..check_v10` | **223 PASS / 0 FAIL**（51+22+23+14+20+18+14+24+37） |
| web 静态检查 | `check_web.mjs web\index.html app\dist\QQScope.html` | 全部通过 |
| v14 后端分支（假端口，kill 全 stub） | `python web\selftest\out\_check_backend_v14.py` | **PASS 16 / FAIL 0** |

check_v14 关键断言（节选）：

- `logoutUser()` 文本内直接调用 `resetAppData()`；`resetAppData` 清空 `state.accounts/currentQq/contacts/messages/overview/report` 且清空 `convItems/bubbleList/expList/aiPreview/feedList`、隐藏 `authChip`、`_appDataStarted=false`。
- 出现 `POST /api/framework/logout`；`doLogout` 处理 `bot_exit/stop/force_stop/external` 四种 mode；external → 横幅 `#btnForceFwStop`。
- `force=true` 分支存在；`forceStopFramework` 走 `openConfirm` 二次确认且 `onOk` 为 `doLogout(true)`。
- 确认文案含「断开 QQ 登录 / 实时同步」「重新启动框架 + 扫码」「数据 + 保留 + 不会被清除」「data/ + 账号管理」。
- `logoutUser()` 仍为 `openConfirm` 二次确认；无 `localStorage.clear()`；仍只 `removeItem` 两个认证键。
- 后端：`@router.post("/api/framework/logout")`、Tier1 `_onebot(.../bot_exit`、四种 mode、kill 前 `is_framework_proc(pid, force=True)`、`framework_stop` 与 logout 共用 `_kill_framework_proc`、login/status 补 `pid+can_force`。

## 4. 真实 DOM 证据（CDP + 假后端，未碰 15555）

假后端 `web/selftest/out/v14_server.mjs` 只服务 `web/index.html` + 假 `/api/*`，`/api/framework/logout` 只返回假响应（external / force_stop），**不代理 15555，也不触碰 NapCat**。

### 4.1 external 分支（同源假后端，`http://127.0.0.1:15641/#sessions`）

| 指标 | 退出前 | 退出后 |
|------|--------|--------|
| `__QQSCOPE_LOGIN__.state().entered` | true | false |
| 连接门 `#loginGate` | 隐藏 | 显示（`gateHidden=false`） |
| 会话列表条数 / 文本 | 3 / 「私聊2 同桌…李四…群聊1 摸鱼群」 | **0 / 「👤暂无账号数据」** |
| 消息区 `#bubbleList` 长度 | 721 | **0** |
| AI 预览长度 | 176 | **0** |
| 顶栏昵称 `#meName` | 霖ケ | **未知昵称** |
| `#authChip` 隐藏 | 否 | **是** |
| localStorage 认证键 | `[qqscope_authed, qqscope_authed_nick]` | **`[]`** |
| 横幅 | 「已连接：霖ケ · 正在进入…」 | 「已退出本地会话；但框架由外部启动（PID 39760）…」+「强制停止框架」按钮 |
| `data-js-errors` | 0 | 0 |

确认弹窗（退出前捕捉）标题「退出登录」，正文即 EXIT_COPY 三件事（见 2.2）。

### 4.2 force 分支（同上，点「强制停止框架」）

- 点按钮后弹出**第二次确认**，正文：「将强制结束外部启动的 NapCat 框架进程（后端会先校验 PID 身份：node.exe 且命令行含 napcat，校验不过不会执行）。这会立刻断开 QQ 登录并停止实时采集。本地归档数据会保留，不会被清除。确定继续吗？」
- 确认后：横幅「已退出登录，框架已结束；下次使用请重新双击 ①启动机器人框架.bat 并扫码」，`#btnForceFwStop` 消失，会话 0 / 消息 0 / authChip 隐藏，`data-js-errors=0`。
- 假后端 `/api/__calls` 证据：`[{force:false},{force:false},{force:false},{force:true}]` —— 普通退出只发 `force:false`，只有点「强制停止框架」并二次确认后才发 `force:true`。

### 4.3 真实构建产物离线（`file:///.../app/dist/QQScope.html#sessions`）

| 指标 | 退出前 | 退出后 |
|------|--------|--------|
| 会话列表条数 | 128（含「可可爱的kei酱 bot」等真实快照） | **0（「暂无账号数据」）** |
| 消息区长度 | 253 | **0** |
| 顶栏昵称 | 霖ケ | **未知昵称** |
| `#authChip` | 可见 | **隐藏** |
| 横幅 | 「框架状态接口未就绪：Failed to fetch」 | 「已退出本地会话（实时同步已停止）…」 |
| `data-js-errors` | 0 | 0 |

## 5. 后端分支证据（假端口 3999/3998，kill 全部 stub，绝不碰真框架）

`_check_backend_v14.py` 把 OneBot/WebUI 指向没人监听的假端口，并把 `_kill_framework_proc` 换成记录器；同时用真实配置做只读身份探测：

- 真实环境 `_can_stop(strict=False) == False`（用户框架不是本服务启动）。
- 只读识别外部进程 18896/39760；真实配置 `_fw_pid_verified() == 39760`（端口持有者）。
- `logout{force:false}` + 假端口 → `ok=false, mode=external, pid=39760, can_force=true`，**kill 调用次数 0**。
- `logout{force:true}` + stub kill → `mode=force_stop`，只 kill 目标 PID，`taskkill` 输出透传，`ok=true`。
- `force=true` 但身份校验不过 → **0 次 kill**（拒绝）。
- 假端口 Tier1 **不发** `/bot_exit`。
- `/api/login/status` 返回 `pid` 与 `can_force` 字段。

## 6. 红线与安全核验

- **未对运行中的 NapCat 做任何写操作**：未调用 `/api/framework/stop`、未调用 `/api/framework/logout`（无论 force）、未发 `/bot_exit`、未 taskkill、未 kill/重启 node PID 18896 / 39760。
- 未重启 15555；未修改 `data/qqscope.db` 的用户数据（仅运行项目自带 `scripts\build_web.py`，它按设计读库注入快照）。
- 证据时间线：真实后端 `/api/logs` 中仅有的两条相关记录为 `19:20:47` / `19:20:51`「拒绝停止框架（非本服务启动）」，早于本次工作，且端点本身拒绝执行、未杀进程。
- 复核实时状态：`/api/live/status` = `running:true`；端口 3000/6099 仍由 39760 监听；`logged_in=true`；框架进程 18896/39760 均存活。
- 一个执行细节：验证 DOM 时误用了别人遗留在 15599 的 `proxy_server.mjs`（它代理到真实 15555）；该次页面并未成功触发任何退出 POST（确认弹窗未打开、`/api/logs` 无新记录、live 未停），随后立即改用自建假后端端口 15641 重新取证。真实框架全程未受影响。

## 7. 给 Lead 的上线提醒

1. 运行中的 15555 仍是**旧代码**：没有 `/api/framework/logout`，`/api/login/status` 也没有 `pid/can_force`。前端对旧后端有「404 → 退回 /api/framework/stop」兜底，所以不会报错，但「同步退出 QQ / 结束框架」必须等 15555 重启加载新 `server/routes_framework.py` 后才真正生效。**按要求我未重启 15555。**
2. `app/dist/QQScope.html` 已按新源码重建；便携包请 Lead 重打包。
3. 外部启动的框架永远不自动杀：用户点「退出登录」→ 本地会话已清 + 顶部横幅提示 → 需再点「强制停止框架」并二次确认，后端再过身份校验后才 kill。这与「绝不误杀外部进程」红线一致；若 Lead 希望「退出并停止框架」按钮一次到位（force=true），只需把 `stopFramework` 的 `doLogout(false)` 改成 `doLogout(true)`（对应确认弹窗已明确告知会停止框架）。