# REPORT-v15 · 多账号数据隔离（每个 QQ 号的信息互相不可见）

- 任务：`task-7` / T-G 多账号数据隔离审计 + 修复 + 自动化隔离测试
- 结论：**5 个已确认泄漏点全部修复**，另系统排查出并修复 **11 处账号边界缺口**；
  新增 `scripts/isolation_test.py`（诱饵账号矩阵，**52/52 PASS**）并接入 `scripts/verify.py`；
  新增 `web/selftest/check_v15.mjs`（**44 PASS / 0 FAIL**）。
- 回归：`check_v2..v15` 源码合计 **302 PASS / 0 FAIL**（基线 258/0，只增不减）；
  `scripts/verify.py --full` **11/11 exit=0**（基线 10/10 + 新增 1 项隔离矩阵）。
- 红线：**未触碰真实 `data/qqscope.db`**（只读 `mode=ro` 备份，诱饵数据只在临时副本）；
  **未触碰 `tools/napcat/`、未动 node PID 39760、未重启 15555**。

---

## ① 完整排查表

### core/store.py
| 路径 | 账号边界 | 结论 → 处理 |
|---|---|---|
| `upsert_account` / `list_accounts` / `delete_account` | 账号清单 / 按账号删除 | OK 设计如此，保留 |
| `upsert_contacts` / `insert_messages` | 用行内 `account_qq` | OK 写入方负责，保留 |
| `get_contact` / `set_contact_meta` | `(account_qq,kind,peer_id)` 联合键 | OK 成立 |
| `list_contacts(account_qq=None)` | 缺 account 时返回全部账号联系人 | 修 → 强制账号，缺则 `ValueError` |
| `list_messages` | `WHERE account_qq=? AND kind=? AND peer_id=?` | OK 成立 |
| `count_messages(account_qq=None)` | 缺 account 时全局计数 | 修 → 强制账号，缺则 `ValueError` |
| `search_messages(query,limit)` | 无 account 过滤（已确认泄漏①） | 修 → 必填 `account_qq`，SQL 加过滤 |
| `refresh_contact_stats` / `daily_stats` / `hour_hist` / `top_contacts` | 入参 account 必填，SQL 带过滤 | OK 成立 |
| `overview(account_qq=None)` | 缺 account 时全局总览 | 修 → 强制账号，缺则 `ValueError` |

### core/export.py
| 路径 | 账号边界 | 结论 → 处理 |
|---|---|---|
| `export(account_qq, targets, …)` | 会话读取 / 媒体解析均按账号 | OK 成立 |
| 导出任务目录名 | `exp_<ts>_<hex>` 不含账号，历史任务可跨账号列出 | 修 → 写 `_meta/<job>.json{account_qq}`，接口层按归属过滤 |

### core/media.py
| 路径 | 账号边界 | 结论 → 处理 |
|---|---|---|
| `get_index` / `resolve` / `rel_path` / `fill_media` | 缓存键与目录 `<data_root>/<qq>/nt_qq/nt_data` 均含账号 | OK 成立 |
| `media_stats(account)` / `pending(account)` | SQL `WHERE account_qq=?` | OK 成立 |
| `index_stats()` | 遍历所有账号目录，经 `/api/media/stats` 泄漏目录/文件数 | 修 → `index_stats(account_qq=None)`，路由传当前账号 |

### core/sources/names.py
| 路径 | 账号边界 | 结论 |
|---|---|---|
| `fetch_profile_bundle/apply_to_store` | 按 `account_qq` 解密该账号 nt_db，回填 `set_contact_meta(account_qq,…)` | OK 成立 |

### core/live_sync.py
| 路径 | 账号边界 | 结论 → 处理 |
|---|---|---|
| 写入 / `_refresh_peers` / `_poll_peer` / `_touch_contact` | 全部用 `account_qq=self.uin` | OK 成立 |
| `events(account=None)` | 无 account 时 `WHERE 1=1` 返回全部账号增量 | 修 → 无 account 返回空（路由层 400） |

### core/send.py
| 路径 | 账号边界 | 结论 → 处理 |
|---|---|---|
| `send_text(account_qq,…)` | 有校验但被 `if account_qq` 短路（已确认泄漏⑤） | 修 → 严格 `!= uin` 即拒绝 |
| `send_history(account_qq=None)` | 无 account 返回全部账号发送历史 | 修 → 路由强制账号 |

### server/app.py
| 路径 | 账号边界 | 结论 → 处理 |
|---|---|---|
| `GET /api/accounts` | 本机有归档的 QQ 清单 | OK 设计需要，不含消息 |
| `DELETE /api/accounts/{qq}` | 按 qq | OK |
| `GET /api/overview` | account 可选 → 全局 | 修 → 缺 account 400 |
| `GET /api/report` | account 必填、SQL 带过滤 | OK |
| `GET /api/contacts` | account 可选 → 全部账号联系人 | 修 → 缺 account 400 |
| `PATCH /api/contacts` | 必须带 `account_qq` | OK（写操作） |
| `GET /api/messages` | account/kind/peer_id 必填 | OK |
| `POST /api/export` | `account_qq` 必填 | OK |
| `GET /api/export/list` | 无 account，列全部任务目录 | 修 → 必填 account + `_meta` 过滤 |
| `GET /api/export/download` | 只按 job/name | 修 → 必填 account + 归属校验（不属于→404） |
### server/routes_media.py
| 路径 | 账号边界 | 结论 → 处理 |
|---|---|---|
| `GET /api/media/stats`、`/pending` | account 必填 | OK |
| `GET /api/media/{msg_id}` | 只按 `messages.id`，不校验归属（已确认泄漏②） | 修 → 必填 `account`；错账号 404 |
| `GET /api/data/quality` | account 可选 → 全局 | 修 → 缺 account 400 |
| `POST /api/media/backfill` | account 必填 | OK |
| `GET /status`、`POST /stop` | 任务表全局，按 job id 访问 | 修 → 带 account 时校验归属 |

### server/routes_feeds.py
| 路径 | 账号边界 | 结论 → 处理 |
|---|---|---|
| `GET /api/feeds` | account 可选；无 account 时回退全局 qzone uin（已确认泄漏④） | 修 → 强制 account，删除全局 uin 兜底 |
| `GET /api/sources/qzone/status` | 登录态，无账号数据 | OK |

### server/routes_profile.py
| 路径 | 账号边界 | 结论 |
|---|---|---|
| `GET /api/avatar`、`/api/avatar/group` | 按 QQ/群号的公开头像，全局缓存 | OK 可接受（公开信息，不含聊天内容） |
| `GET /api/profile` | `account` 必填，`_bundle(account)` 按账号 | OK |
| `GET /api/contact` | account/kind/peer_id 必填，`get_contact(account,…)` | OK |

### server/routes_send.py
| 路径 | 账号边界 | 结论 → 处理 |
|---|---|---|
| `POST /api/send` | body 的 `account_qq` 可选，发送用登录号（已确认泄漏⑤） | 修 → 缺/非法 400；≠登录号 400(`account_mismatch`) |
| `GET /api/send/history` | account 可选 → 全局 | 修 → 缺 account 400 |

### server/routes_live.py
| 路径 | 账号边界 | 结论 → 处理 |
|---|---|---|
| `GET /api/live/status` | 单登录框架状态（含 uin） | OK 设计如此 |
| `POST /focus`、`POST /start` | 无 account | 修 → 必填 account 且等于登录号 |
| `GET /events` | account 可选 | 修 → 缺 account 400（core 兜底空） |

### server/routes_voice.py / routes_voice_official.py
| 路径 | 账号边界 | 结论 → 处理 |
|---|---|---|
| `POST /api/voice/transcribe`、`official/sync` | account 必填 | OK |
| `GET /api/voice/progress` | 全局单任务、无 account | 修 → 记录 account，按 account 过滤 |
| `GET /api/voice/stats`、`official/stats` | account 必填 | OK |

### server/routes_framework.py
| 路径 | 账号边界 | 结论 |
|---|---|---|
| 登录门 / 框架启停 / 二维码 | 单登录框架全局状态 | OK 设计如此 |

### web/index.html（前端）
| 路径 | 账号边界 | 结论 → 处理 |
|---|---|---|
| `normContact` 读 `qqscope_remark_<kind>|<pid>` | 键不含账号（已确认泄漏③） | 修 → `remarkKey(accountQq,kind,pid)`；旧裸键忽略 |
| `saveRemark` 兜底 `setItem(...)` | 同上 | 修 → 写带账号键 |
| `mediaHTML` 的 `/api/media/{id}` | 不带 account | 修 → 追加 `?account=` |
| `requestLiveFocus` `/api/live/focus` | 不带 account | 修 → 带 account |
| 导出历史 / 下载 URL | 不带 account | 修 → 带 account |
| 补下载 `status`、`stop` | 不带 account | 修 → 带 account |
| `onAccountChange()` | 只清部分 state，不清 DOM，未清 live 游标 | 修 → `clearAccountScopedView()` 清内存+DOM+live 游标 |
| `invalidateData()` | 不清 `state.contacts` / 消息 DOM | 修 → 调用 `clearAccountScopedView()` |
| 其余账号相关请求 | 均已带 `account` | OK |
| 全局偏好键（theme/auto_refresh/authed） | 非账号数据 | OK，登出已清 `qqscope_authed*` |
---

## ② 修复清单

**后端（强校验优先，缺 account 一律 400 / raise，绝不兜底成当前登录账号）**
1. `core/store.py`：`search_messages` 新增必填 `account_qq` + SQL 过滤；`list_contacts` / `count_messages` / `overview` 缺 account 直接 `ValueError`。
2. `server/app.py`：`/api/overview`、`/api/contacts` 缺 account → 400；`/api/export/list`、`/api/export/download` 强制 account 并按 `_meta` 校验归属。
3. `server/routes_media.py`：`/api/media/{msg_id}` 必填 account + 归属校验（错账号 404）；`/api/data/quality` 缺 account 400；`media.index_stats(account)`；backfill status/stop 归属校验。
4. `server/routes_feeds.py`：`/api/feeds` 强制 account，删除全局 qzone uin 兜底。
5. `server/routes_send.py` + `core/send.py`：`/api/send` 必填 account 且必须等于框架当前登录号，否则 400(`account_mismatch`)；`/api/send/history` 强制 account；`core.send` 去掉 `if account_qq` 短路。
6. `server/routes_live.py` + `core/live_sync.py`：`events` 缺 account 400（core 兜底空）；`focus`/`start` 必填 account 且等于登录号。
7. `server/routes_voice.py`：转写任务记录 account，`progress` 按 account 过滤。
8. `core/media.py`：`index_stats(account_qq=None)`。
9. `core/export.py`：导出写 `data/export/_meta/<job>.json{account_qq}`，新增 `job_meta()`。

**前端**
10. 账号相关 localStorage 键改 `qqscope_remark_<account_qq>_<kind>|<peer_id>`，忽略旧裸键。
11. 切账号 / `invalidateData()` 清空 `state.contacts` / `state.messages` / 会话与消息 DOM / `state.live.sinceId`。
12. 媒体、实时 focus、导出列表/下载、补下载 status/stop 全部带 `account=`；`normMsg` 保留 `account_qq`。

---

## ③ 隔离测试矩阵结果（`scripts/isolation_test.py`）

**机制**：启动时创建临时根，用 `sqlite3` 只读（`file:…?mode=ro`）+ `backup()` 把真实库复制到 `QQSCOPE_ROOT` 临时目录；
设 `QQSCOPE_DATA_ROOT` 到临时媒体目录、`QQSCOPE_LIVE_AUTOSTART=0`；在副本里插入诱饵账号 `999999999`
（联系人/消息/媒体/动态全部用 `DECOY_ONLY_9` / `u_decoy_9` 标记），再用进程内 FastAPI TestClient 调接口。
**绝不改真实库、不监听端口、不真实发消息**（发送只测拒绝分支 + `dry_run`）。

| 分组 | 断言要点 | 结果 |
|---|---|---|
| A 缺 account | contacts/overview/feeds/live events/send history/data quality/export list/media → 400 | 9/9 PASS |
| B contacts | A 看不到诱饵；诱饵账号看得到（正对照） | 2/2 PASS |
| C messages/report/overview | A 查诱饵 peer 为空；诱饵账号非空；A 的 overview 计数与 SQL 一致且不含诱饵 | 6/6 PASS |
| D profile/contact | A 查诱饵资料 404；诱饵账号 200 且含诱饵；profile 归属正确 | 4/4 PASS |
| E feeds | A 不含诱饵；诱饵账号含诱饵；`scope=mine` 不再回退全局 uin | 3/3 PASS |
| F live events | A 不含诱饵；诱饵账号含诱饵 | 2/2 PASS |
| G send history | A 不含诱饵；诱饵账号含诱饵 | 2/2 PASS |
| H media | 错账号读诱饵媒体 404；正确账号 200 文件流；stats/pending 不串号 | 6/6 PASS |
| I data quality | 归属正确、诱饵 c2c 计数 ≥1 | 2/2 PASS |
| J 导出 | 导出 A 的产物无诱饵文本；`_meta` 归属 A；list/download 按账号（错账号 404、缺 account 400） | 6/6 PASS |
| K /api/send | 缺 account 400；账号一致 + dry_run 200；账号≠登录号 400 `account_mismatch` | 3/3 PASS |
| L store 危险 API | `search_messages` 缺 account raise、(A) 搜不到诱饵、(诱饵) 搜得到；`list_contacts()/overview()/count_messages()` 缺 account raise | 6/6 PASS |
| **合计** | | **52/52 PASS** |

`scripts/verify.py` 已把该脚本作为「多账号数据隔离矩阵」一项接入（子进程、临时副本）。
---

## ④ 回归结果

| 项目 | 基线 | 本次 | 结果 |
|---|---|---|---|
| check_v2..v14（源码断言） | 258 PASS / 0 FAIL | 258 PASS / 0 FAIL | 未回退 |
| check_v15（新增隔离断言） | — | 44 PASS / 0 FAIL | 新增 |
| check_v2..v15 合计 | 258 / 0 | **302 PASS / 0 FAIL** | 通过 |
| verify.py --full | 10/10 exit=0 | **11/11 exit=0** | 通过 |
| isolation_test.py 独立运行 | — | **52/52 PASS** exit=0 | 通过 |
| python scripts/build_web.py | — | 0.33 MB，占位符已替换 | 通过 |
| web/selftest/check_web.mjs 内联脚本语法 | — | 全部通过 | 通过 |

关键回归输出：

    v2=51 v3=22 v4=23 v5=14 v6=20 v7=18 v8=14 v9=24 v10=37 v14=35 v15=44
    TOTAL check_v2..v15 PASS=302 FAIL=0
    多账号数据隔离矩阵  exit=0 :: 52/52 通过
    数据现状  账号=[1605289411] 总消息=281987 自发=7966
    ===== 总结 ===== 11/11 通过 全绿

---
## ⑤ 红线声明

- 真实 data/qqscope.db 全程只读：隔离测试用 mode=ro 只读连接 + sqlite3.backup() 复制到临时目录，所有诱饵写入都在副本。
- 复核结果 real accounts=[1605289411]、decoy in real db=False（messages/contacts/feeds 中诱饵计数均为 0）。
- 未触碰 tools/napcat/，未 kill/restart node PID 39760，未重启 15555。
- /api/send 的账号不一致分支在到达真正发送前即被拒绝，测试中还用 monkeypatch 隔离了登录查询，未向真人/群发送任何消息。
- 未修改任何真实用户数据；verify.py --full 的导出实测写入 data/export/ 属该脚本既有行为。
## 残余说明（非泄漏，按设计保留）

1. GET /api/accounts 会列出本机有归档的 QQ 号（账号选择器必需），只暴露有哪些号，不含任何消息或联系人内容。
2. GET /api/live/status 与 routes_framework 反映单个框架登录态（NapCat 单登录语义），不是多账号数据读取。
3. data/avatars 下头像缓存按 QQ 号或群号全局存放：头像属公开信息，不含聊天内容。
4. 补下载任务注册表为进程内单实例，job id 随机，status/stop 已加账号归属校验。
5. 历史无 _meta 的旧导出任务在按账号列表/下载时不再展示（宁可不显示也不串号）；文件仍在磁盘上，未删除。