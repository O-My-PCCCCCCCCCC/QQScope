# REPORT-v17 · 身份绑定（UI 账号跟框架登录号一致；采集与展示彻底分开）

- 任务：task-10（含 Lead 的紧急修正：用户警告「自动填充聊天记录的你别给我搞坏了」）。
- 现场根因：数据层归属本来是对的（core/live_sync.py 用 self.uin 入库），
  但 UI 的 currentQq 取 state.accounts[0].qq，跟框架登录号脱钩；登录号不在库里时也没有「未导入」态，
  于是框架登 1438830763 时界面还在显示 1605289411 的归档。
- 结论：展示层做身份绑定；**采集层永远绑定框架登录号并持续运行，切账号/退出查看绝不 /api/live/stop**。
- 数字：scripts/isolation_test.py 113/113；check_v17.mjs 32/32；check_v16_dom.mjs 15/15（含网络层证据）；
  check_v2..v16 保持 324/0，check_v2..v17 合计 356/0；verify.py --full 11/11（隔离项 113/113）。

---

## 一、一度写错、按用户警告修正的点（最重要）

上一版任务卡 B)4 写的「登录号与查看号不一致时不要启动 live，并停掉已有的」会掐断自动采集，已改为：

| 维度 | 正确行为（本版实现） |
|---|---|
| 自动采集 live sync | 永远以框架登录号为准，必须持续运行；查看账号变化不启动/停止采集 |
| /api/live/start | enterApp 里无条件用「框架登录号」调用（loginAccountOf() 或 status.account），与 currentQq 无关 |
| /api/live/stop | 全仓仅 2 处：poll 里「框架未登录/断开」、用户显式登出 doLogout；切账号/退出查看均不调用 |
| 不一致时的界面 | 只提示「实时采集仍在为 <登录号> 进行」，不 stop、不打断采集 |
| sync_allowed=false | 只表示按钮（发送/动态同步）禁用语义，前端未用它做任何 live 启停 |

---

## 二、修复清单

### A) 后端 /api/login/status（server/routes_framework.py）
- 新增可选 `?account=<被查看的账号>`。
- 新增 `account_in_store`（框架登录号是否在 accounts 表里，查库）。
- 新增 `view_account_mismatch`（传入的查看号 ≠ 登录号）。
- 传入 account 且 != 登录号时：`sync_allowed=false`、`sync_block_reason="account_mismatch"` + 人话 `message`。
- 不传 account 时保持旧语义（`sync_allowed` 只反映框架是否已登录）。
- 采集层 core/live_sync.py 本轮未改动（入库 account_qq 本来就是 self.uin）。

### B) 前端展示层（web/index.html）
1. 新增 `state.loginQq / loginNick / _identityApplied`；`applyLoginIdentity(status)` 在账号列表就绪后把 currentQq 绑到登录号（登录号在库时），并重载该账号数据。
2. 登录号不在库：`currentQq=null` + 顶部红色横幅「【未导入】当前框架登录 X，该账号尚未导入数据；实时采集仍在为它进行，导入后即可看到」+「去数据源导入」按钮；不再把其它账号当当前账号。
3. 账号选择器给 != 登录号的账号加后缀「未连接此账号」；选中它只禁用发送（sendDisabledReason）与动态同步提示，**不禁用/不停止实时采集**；横幅改为「实时采集仍在为框架登录号 X 进行；当前仅离线查看归档 Y（发送/动态同步已停用）」。
4. live 调用一律带真实登录号：requestLiveFocus / liveTick / enterApp 都用 `loginAccountOf()`；查看非登录号时不设 focus、不拉该号的 live/events，但绝不 stop 采集。
5. 登出/断线：resetAppData 清展示层（DOM/游标/currentQq/loginQq/_identityApplied），等 /api/login/status 回来重新绑定。
---

## 三、断言结果

### 1) scripts/isolation_test.py — 113/113 PASS
- 原有真实双账号全量矩阵（task-9）106 项保持。
- 新增 5.6 身份绑定 6 项：
  - 登录=A(在库)/查看=B -> sync_allowed=false + sync_block_reason=account_mismatch + account_in_store=true + view_account_mismatch=true；
  - 登录=A/查看=A -> sync_allowed=true（正对照）；
  - 登录=A/不传 account -> 旧语义 sync_allowed=true、view_account=null；
  - 登录=X=1438830763(不在库)/查看=A -> account_in_store=false + mismatch + sync_allowed=false；
  - 未登录/查看=A -> logged_in=false + sync_allowed=false + account_in_store=false；
  - 无论登录号是谁，/api/contacts?account=A 与 ?account=B 仍各返回各自数据（不会把 A 当 B）。
- 新增 5.7 采集层真数据核对 1 项：
  - 让 LiveSync.uin=框架登录号 B 跑一次 _poll_peer（假 OneBot 历史），落库 text=LIVE_BIND_PROBE 的 account_qq == B 且 A 侧 0 行。

### 2) web/selftest/check_v17.mjs — 32/32 PASS
断言 6 条绑定逻辑：登录号优先绑定；未导入态不展示他人数据；选择器打标且只禁用发送/动态；live 用登录号；
采集层不被切账号中断（applyLiveIdentity 无 /api/live/stop、全文件仅 2 处 stop、sync_allowed 不参与 live 启停）；
登出/换号只清展示层并等重新绑定；以及 /api/login/status 的 account_in_store / view_account_mismatch / sync_block_reason。

### 3) web/selftest/check_v16_dom.mjs — 15/15 PASS（含网络层）
在原有 12 项（A/B 双向零串号、切账号重拉、清旧 DOM、localStorage、jsErr=0）之外，新增 3 项 CDP 网络抓包：
- live/start 用框架登录号 A（与查看账号无关）；
- 切账号后 /api/live/stop 请求数 = 0（采集不被掐断）；
- 切到非登录账号后不再拉它的 /api/live/events、也不再发 /api/live/focus。

### 4) 回归
| 项目 | 基线 | 本次 | 结果 |
|---|---|---|---|
| check_v2..v16 | 324 PASS / 0 FAIL | 324 PASS / 0 FAIL | 未回退 |
| check_v17（新增） | — | 32 PASS / 0 FAIL | 新增 |
| check_v2..v17 合计 | 324 / 0 | 356 PASS / 0 FAIL | 通过 |
| scripts/isolation_test.py | 106/106 | 113/113 | 通过 |
| check_v16_dom.mjs | 12/12 | 15/15 | 通过 |
| scripts/verify.py --full | 11/11 | 11/11（隔离项 113/113） | 通过 |
| python scripts/build_web.py | — | 产物重建、占位符已替换 | 通过 |

---

## 四、红线声明与「自动采集未中断」证据

- 真实 data/qqscope.db 全程只读（mode=ro 备份到临时根），未写入。
- 未重启 15555（Lead 在做现场录制，等 Lead 统一重启）；未碰 tools/napcat；测试实例只监听 127.0.0.1:15557，
  有端口占用保护，结束只 taskkill 自己 spawn 的 PID。
- 自动采集未被改动 / 未中断的证据：
  1. 采集层 core/live_sync.py 本轮 git diff 为空 —— 没有改任何采集代码；入库 account_qq 本来就是 self.uin。
  2. 全前端 /api/live/stop 调用只有 2 处：框架未登录/断开（poll）与用户显式登出（doLogout）；
     check_v17 断言 stopCount === 2，且 onAccountChange / applyLiveIdentity 内 0 处。
  3. 无头浏览器网络抓包：live/start 请求体 account = 框架登录号（不是被查看账号）；切账号后 /api/live/stop 计数 0。
  4. 数据层核对：LiveSync(登录号 B) 落库 account_qq=B、A 侧 0 行（isolation_test 5.7 真数据断言）。
  5. sync_allowed=false 只用于界面禁用语义，前端未把它接入任何 live 启停路径（check_v17 断言）。

## 五、已知边界（如实说明）
- DOM 复核走应用自带 ?authed=1 测试通道，并 monkeypatch 框架/数据源探测以避免碰 NapCat；证明的是展示层身份绑定与采集不中断，不覆盖真实扫码流程。
- 前端目前没有 qzone 动态同步调用点；「动态同步禁用」以选择器打标 + 横幅/提示表达，后端 qzone.sync 的全局凭据错配已在 task-9 改为显式拒绝。
- 改动要等 Lead 统一重启 15555 后对用户现场生效；前端产物已重建，便携包由 Lead 重打。