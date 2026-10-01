# QQScope 重构 · 接口冻结文档（v2）

> 所有并行开发都以本文为准。改接口必须先改本文。

## 0. 重构目标

两个「读取窗口」各自采集数据 → 汇聚到统一主库 → Web 主页统一管理 → 多对象批量导出 HTML/TXT/MD。

```
窗口 A：数据包读取（离线）          窗口 B：机器人框架读取（在线）
 nt_msg.db → 解密 → 解析 → 入库      NapCat/OneBot HTTP → 会话+历史 → 入库
              └──────────┬──────────────────────┘
                    data/qqscope.db（统一主库）
                         ↓
              Web 主页（FastAPI + 单文件前端）
                         ↓
              多对象批量导出 HTML / TXT / MD (+ zip)
```

## 1. 目录与写入范围（谁写哪里，不要越界）

| 模块 | 路径 | 负责人 |
|---|---|---|
| 统一数据层 | `core/paths.py` `core/store.py` | Lead（已冻结） |
| 窗口 A 数据源 | `core/sources/pack_source.py` | pipe-pack |
| 窗口 B 数据源 | `core/sources/bot_source.py` | pipe-bot |
| 导出引擎 | `core/export.py` | exporter |
| 前端 | `web/index.html` `web/js/*` `web/assets/*` | webui |
| 后端 API | `server/app.py` | Lead |
| 构建脚本 | `scripts/build_web.py` | Lead |

**任何人不要动 `tools/` 下的第三方工具**（`nt_msg_db_util`、`napcat-win`）。

## 2. 数据模型（`core/store.py`，已冻结）

- `kind`：`'c2c'`（私聊）| `'group'`（群聊）
- `peer_id`：会话标识字符串。私聊 = 对方 uid（拿不到 uid 就用 QQ 号字符串）；群聊 = 群号字符串
- `peer_qq`：会话 QQ 号（int，未知填 0）
- `direction`：`1` = 自己发出，`0` = 收到
- `ts`：Unix 秒（int）
- `source`：`'pack'` | `'bot'`

已实现函数（直接 import 用，不要重复实现）：
```python
from core import store
store.init()
store.upsert_account(account_qq, label=None, source=None)
store.list_accounts();  store.delete_account(qq)
store.upsert_contacts([{...}])          # 只更新非 None 字段
store.list_contacts(account_qq, kind, query, order, limit)
store.set_contact_meta(account_qq, kind, peer_id, name, remark, avatar)
store.insert_messages([{...}])          # 自动去重，返回新增条数
store.list_messages(account_qq, kind, peer_id, limit, offset, order, since, until)
store.count_messages(account_qq, kind, peer_id)
store.search_messages(query, limit)
store.refresh_contact_stats(account_qq) # 导入后调用
store.overview(account_qq)
```

## 3. 数据源契约（窗口 A / 窗口 B 必须实现同样的四个函数）

```python
SOURCE_ID   = "pack"        # 或 "bot"
SOURCE_NAME = "数据包读取"    # 或 "机器人框架读取"
SOURCE_KIND = "pack"        # 或 "bot"

def status() -> dict:
    """{id, name, kind, ready: bool, message: str, detail: {...}}"""

def probe(opts: dict) -> dict:
    """只探测不写入。返回 {ok, message, accounts:[...] / conversations:[...]}"""

def sync(opts: dict, progress=None) -> dict:
    """执行采集并写入 store。
    返回 {ok, message, imported: int, accounts: [qq...], elapsed: float, log: str}
    progress(stage: str, pct: int, msg: str) 可选回调"""

def conversations(opts: dict) -> list[dict]:
    """列出可采集的会话：[{account_qq, kind, peer_id, peer_qq, name, remark, avatar}]"""
```

### 窗口 A `pack` 的 opts
```python
{
  "data_root": r"C:\Users\Administrator\Documents\Tencent Files",  # 可选，默认取 paths.DEFAULT_DATA_ROOT
  "keys": {"1605289411": "<key>"},     # 可选；缺省时按 keys/<qq>.key → qq-export/db_key.txt 顺序找
  "accounts": [1605289411],            # 可选；缺省自动发现
  "include_group": True, "include_c2c": True
}
```
流程：复制 `nt_qq/nt_db/nt_msg.db`(+wal/shm) → 剥 1024 字节头 → sqlcipher3 解密 → `3.export.py` 结构化导出 → 解析入库。
- 解密参数：`cipher_page_size=4096`（必须在 key 前）→ `key` → `kdf_iter=4000` → `cipher_hmac_algorithm=HMAC_SHA1` → `cipher_kdf_algorithm=PBKDF2_HMAC_SHA512`
- 已实测可用密钥：`E:\01-项目\Workspace\qq-export\db_key.txt`（对应主号 1605289411，QQ 9.9.36）
- 源表列名映射：`40001`=msg_id `40050`=timestamp `40013`=direction `40020`=sender_uid `40033`=sender_qq `40021`=peer_uid `40030`=peer_qq/group_qq `40011`=msg_type `40800`=protobuf blob
- 文本抽取可直接复用 `tools/nt_msg_db_util/msgdb/`（`c2c/parser.py`、`group/exporter.py` 的 `parse_row`）
- 中间产物放 `data/decrypt/<qq>/`，不要写别处

### 窗口 B `bot` 的 opts
```python
{
  "base": "http://127.0.0.1:3000",   # OneBot HTTP 地址
  "token": "",
  "top_friends": 30, "top_groups": 30, "per_peer": 200
}
```
- 复用现成实现：`server/onebot.py`（`_api` / `cq_to_text` / `ob_msg_to_row` / `sync`）已写好，改造为写 `store` 即可
- 账号身份用 `get_login_info` → `user_id`；用 `d = 1 if sender_uin == self_uin else 0` 判方向
- 支持 NapCat：`tools/napcat-win\napcat\launcher.bat` 可启动本地框架（需用户扫码登录）
- 连不上要给出人话错误：地址不通 / token 错 / 未登录 / 接口不支持

## 4. 导出引擎契约（`core/export.py`）

```python
def export(account_qq, targets, formats, mode="per_peer", opts=None) -> dict
```
- `targets`: `[{"kind":"c2c","peer_id":"u_xxx"}]` 或 `[{"kind":"group","peer_id":"1038804640"}]`
- `formats`: `["html","txt","md"]` 的任意子集
- `mode`: `"per_peer"`（每人一个文件）| `"merged"`（全部合成一个文件）
- 返回：`{"ok":True,"job":"<jid>","dir":"<绝对路径>","files":[{"name","size","peer","fmt"}],"zip":"<zip绝对路径>"}`
- 输出目录：`data/export/<job>/`，并把该目录打成 `data/export/<job>.zip`
- 文件名规范：`<安全化的名字>_<kind>_<peer_id>.<ext>`，必须做 Windows 文件名净化

### HTML 导出要求
- **单文件自包含**（内联 CSS，不引用任何外部资源），双击即用
- 顶部信息条：对象名 / QQ / 消息数 / 时间范围 / 导出时间 / 数据来源
- 聊天气泡样式：自己右侧（强调色）、对方左侧（灰底）；显示时间戳、发送者名
- 提供「仅看自己发的」筛选按钮（纯内联 JS，无依赖）
- 深色/浅色都用同一套 CSS 变量，至少保证浅色可读

### TXT 导出要求
- 开头统计块（对象/QQ/条数/时间范围）
- 每行：`2026-08-15 22:03  我：内容` / `2026-08-15 22:04  张三：内容`
- UTF-8 **带 BOM**（保证 Windows 记事本不乱码）

### MD 导出要求
- `# 与 <名字> 的聊天记录`
- 统计表（用 markdown 表格）
- 时间分节：`## 2026-08-15`，下面用 `> **我** · 22:03` / `> **张三** · 22:04` 引用块
- 特殊字符转义（`|`、`*`、`_`、反引号）

## 5. HTTP API 契约（后端 ↔ 前端，冻结）

统一前缀 `/api`，所有响应 `application/json`，出错返回 `{"error":"人间可读的中文"}` + 非 200。

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| GET | `/api/health` | — | `{ok, version, time}` |
| GET | `/api/sources` | — | `{sources:[{id,name,kind,ready,message,detail}]}` |
| POST | `/api/sources/{id}/probe` | `{...opts}` | `{ok,message,data}` |
| POST | `/api/sources/{id}/sync` | `{...opts}` | `{ok,message,imported,accounts,elapsed}` |
| GET | `/api/accounts` | — | `{accounts:[...]}` |
| DELETE | `/api/accounts/{qq}` | — | `{ok}` |
| GET | `/api/overview?account=<qq>` | — | `{total,self,c2c,group,contacts,first_ts,last_ts,kinds}` |
| GET | `/api/contacts?account=<qq>&kind=&q=` | — | `{contacts:[...]}` |
| PATCH | `/api/contacts` | `{account_qq,kind,peer_id,name?,remark?,avatar?}` | `{ok}` |
| GET | `/api/messages?account=&kind=&peer_id=&limit=&offset=&since=&until=` | — | `{messages:[...],total: n, contact:{...}}` |
| POST | `/api/export` | `{account_qq,targets:[{kind,peer_id}],formats:[...],mode}` | `{ok,job,dir,files:[{name,size,peer,fmt}],zip,count}` |
| GET | `/api/export/list` | — | `{jobs:[{job,created,files,size}]}` |
| GET | `/api/export/download?job=&name=` | — | 文件流（`FileResponse`） |
| GET | `/api/ai/chat` | POST `{messages:[...]}` | `{content}`（代理 DeepSeek，Key 只在服务端） |
| GET | `/api/settings` / POST | 同上 | 设置读写 |

静态：`GET /` 返回 `app/dist/QQScope.html`（构建产物），没有则给出构建提示。

## 6. 验收标准

1. `python scripts/verify.py` 全绿（Lead 写）
2. 窗口 A 能对真实库跑通：主号 1605289411 → 入库 ≥ 260,000 行，自发 ≥ 2,800 条
3. 窗口 B 在无 NapCat 时给出明确错误，在可用时能拉会话列表并入库
4. 导出：任选 3 个会话 + 3 种格式 → 9 个文件 + 1 个 zip，HTML 双击能看且样式正常
5. 前端：单文件 `app/dist/QQScope.html`，无外部请求，两个窗口可见可操作，网络面板能看到对应 API 调用
6. 旧功能不丢：总览 KPI、情绪曲线、每日明细、社交对象、高频词、AI 解读、设置

## 7. 约定

- 所有 Python 文件 UTF-8，中文注释；日志/错误信息用中文
- 不要引入重量级依赖；后端只用 `fastapi`/`uvicorn`/`httpx`（`tools/nt_msg_db_util/.venv` 里都有）
- 前端零依赖（原生 JS），不用构建工具，`web/index.html` 是源码，`scripts/build_web.py` 注入数据后产出 `app/dist/QQScope.html`
- 端口：`15555`（已登记）
- 数据不出本机；AI 请求前必须能预览将发送的内容

---

# 第 8 章 · v2.1 增量（用户第一轮反馈后的重做）

> 用户 6 条反馈：①主界面优化极烂 ②UI 设计不好 ③群聊/私聊没分类 ④机器人框架不能用
> ⑤没有头像、没有联系人和自己的个人信息 ⑥读不到 QQ 动态
> 附加建议：参考项目文件夹里 UI 做得好的项目（**不要参考 ui-studio**，它还没修好）。

## 8.1 设计语言（从用户自己的项目里提炼，必须照此实现）

我扫了 `E:\01-项目` 下全部前端资源，结论：**7/12 是暗色**；最精致的两个
（`网页`(constellation)、`mode-status`）都是「暗底 + 靛蓝/紫强调 + 8~16px 圆角 + 系统字体栈」。
`DeepSeek-Harness-Desktop`（#4d6bfe + GitHub-dark 灰阶）和 `kay-vault-gh`（#0a0e1a + #dc2626）同族。

**冻结令牌（暗色为默认主题）**

```css
:root {
  /* 背景层级 —— 参考 constellation #0b0e17 / #141b36 与 kay-vault #0a0e1a #111827 #1a1f2e */
  --bg:        #0b0e17;   /* 页面底 */
  --surface:   #141b36;   /* 卡片 */
  --surface-2: #1a1f2e;   /* 悬浮/次级 */
  --surface-3: #242c38;   /* 输入框/边框区分 */
  --border:    #252b3d;
  --border-2:  #2f3750;

  /* 文字 —— 参考 DeepSeek-Harness #e6edf3 / #8b949e */
  --text:      #e6edf3;
  --text-2:    #9aa4b2;
  --text-3:    #6b7280;

  /* 强调 —— 参考 constellation #5b7cfa/#7a5cfa、mode-status #6366f1 */
  --accent:    #5b7cfa;
  --accent-2:  #7a5cfa;
  --accent-ink:#ffffff;

  /* 语义色 —— 参考 mode-status #10b981/#f59e0b/#ef4444 */
  --ok:        #10b981;
  --warn:      #f59e0b;
  --danger:    #ef4444;
  --info:      #7ec8e3;

  /* 形状 */
  --r-sm: 8px;  --r: 10px;  --r-md: 12px;  --r-lg: 14px;  --r-xl: 16px;  --r-full: 999px;

  /* 字体：必须用系统栈，禁止外部字体 */
  --font: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
          "Microsoft YaHei", system-ui, sans-serif;
  --mono: ui-monospace, "Cascadia Mono", Consolas, "Courier New", monospace;

  /* 间距基准 4px；字号比例 1.2 */
  --sp: 4px;
  --shadow: 0 4px 16px rgba(0,0,0,.35);
}
```

**必须支持浅色主题切换**（用户对配色主观，双主题兜底）：`[data-theme="light"]` 覆盖上面
的 `--bg/--surface/...`，映射到 `#f5f5f7 / #ffffff / #e6e9f0 / #111827` 系。
主题选择存 `localStorage`。

**布局要求（这是「优化极烂」的正解）**
- 左侧固定侧栏 220px（品牌 + 7 个导航项 + 底部主题切换），**不再是顶栏 + 横向 tab**
- 主区最大宽度不设限但内容区留 24px 内边距，卡片间距 16px
- 信息层级：页面标题 20px/600 → 区块标题 14px/600 + `--text-2` → 正文 13px → 辅助 12px `--text-3`
- 数字用 `--mono` 且 `font-variant-numeric: tabular-nums`，KPI 数字 28px/700
- 所有可点元素 hover 有 `background: var(--surface-2)` 反馈，`transition: 120ms ease-out`
- 空状态、加载态、错误态都必须有（不能白屏/空白）
- 响应式：<900px 侧栏收成抽屉

## 8.2 群聊 / 私聊分类（问题③）

会话列表必须**显式分三组**：`私聊` / `群聊` / `其他`（系统号等），每组有可折叠标题 + 计数徽章；
并提供 `全部 / 私聊 / 群聊` 分段筛选器。分组依据就是 `contacts.kind`（`c2c` / `group`）。
群聊条目要显示群号 + 成员数（若能拿到），私聊条目显示 QQ 号 + 备注。
**不允许把两种会话混在一个平铺列表里**（当前实现就是混的，这是用户点名的问题）。

## 8.3 头像与个人信息（问题⑤）

### 新增后端接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/avatar?qq=<qq>` | **返回图片流**。服务端从 `https://q1.qlogo.cn/g?b=qq&nk=<qq>&s=640` 抓取并缓存到 `data/avatars/<qq>.png`，下次直接读缓存。失败返回 404（前端必须有首字兜底，绝不能出现破图） |
| GET | `/api/avatar/group?group=<gid>` | 同上，群头像 `https://p.qlogo.cn/gh/<gid>/<gid>/640` |
| GET | `/api/profile?account=<qq>` | 自己的资料：`{account_qq, nickname, signature, avatar_url, qid?, friend_count, group_count, msg_total, active_days}` |
| GET | `/api/contact?account=&kind=&peer_id=` | 联系人资料卡：`{name, remark, peer_qq, kind, uid, avatar_url, msg_count, self_count, first_ts, last_ts, last_text, top_words?}` |

**头像必须服务端缓存**：前端 `<img src="/api/avatar?qq=...">` 走本地服务，不允许前端直连腾讯 CDN
（保持「单文件离线可用」的约束；离线模式下前端用首字母圆形头像兜底）。

### 自己的资料从哪来
`profile_info.db` 的 `profile_info_v6` 里就有本账号那一行（`1000`=uid, `1002`=QQ,
`20002`=昵称, `20009`=备注, `20011`=个性签名, `20004`=头像 URL）。
复用 `core/sources/names.py` 已实现的解密链路；若拿不到则优雅降级为「未知昵称」。

## 8.4 机器人框架重建（问题④）

**根因已查明**：`tools/napcat-win` 是给 **QQ 9.9.22-40990** 构建的旧 shell
（`napcat/qqnt.json` 里 `"version": "9.9.22-40990"`），而本机 QQ 已是 **9.9.36-53644**，
版本不匹配 → hook 注入失败 → 框架起不来。

**要求**：从 `https://github.com/NapNeko/NapCatQQ` 重新获取与当前 QQ 匹配的发行版
（最新 release 为 `v4.18.28`，资产含 `NapCat.Shell.Windows.Node.zip` 111MB /
`NapCat.Shell.zip` 28MB / `NapCat.Framework.zip` 29MB），放到 `tools/napcat/`（新目录，
旧的 `tools/napcat-win/` 保留不动作为回退），并提供：
- `tools/napcat/启动框架.bat` —— 一键启动（含管理员权限检测、路径自动探测）
- 框架配置文件里**必须开启 OneBot HTTP 服务**（端口建议 `3000`，与窗口 B 默认值一致）
- 真实验证到哪里算哪里：能启动到「等待扫码」即算达标；登录需要用户本人扫码，
  **不要代替用户登录，不要自动重启用户的主 QQ**
- 把「框架怎么用」写进 README（含扫码步骤、端口、token 在哪看）

## 8.5 QQ 动态（问题⑥）

已确认：**本地没有任何 QQ 动态 / QQ 空间的数据库或缓存**（`nt_db` 里只有消息、资料、
群、表情、文件等，无 feed/qzone 表）。所以动态**只能走网络接口**。

**首选路径**：NapCat/OneBot 的 `get_cookies` / `get_credentials` 能拿到登录态 cookie
（含 `p_skey`、`uin`），再用它请求 QZone 的 H5 接口。
**接口**：
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/feeds?account=&limit=&pos=` | 返回 `{feeds:[{id,ts,author_qq,author_name,content,images,praise,comment_count,forward_count}], next_pos}` |
| POST | `/api/sources/qzone/sync` | 把动态落库（新表 `feeds`），支持增量 |

**实现要求**
- 先做**可行性探测脚本**并把真实请求/响应落到 `data/qzone_probe/`，**探测结果如实报告**
- 拿不到登录态时，接口返回明确中文错误，前端「动态」页显示「需要先连接机器人框架获取登录态」
- 若 QZone 接口被风控/改版导致不可用，**必须如实说明**，不要伪造数据、不要用假数据糊弄 UI
- 涉及的 cookie/token **不得写入日志、不得打进前端产物**

## 8.6 前端新增页面

导航变为 8 项：`数据源 / 总览 / 会话 / 导出 / 动态 / AI 解读 / 设置`（+ 主题切换）。
「动态」页：时间线卡片（头像 + 昵称 + 时间 + 正文 + 图片九宫格 + 点赞/评论数），空态引导去连框架。
「会话」页右栏增加「资料卡」：点头像/名字弹出，显示头像、昵称、备注、QQ、消息数、
时间跨度、最近一条。

---

# 第 9 章 · v3 视觉规范（用户指定参考项目后定稿）

> 用户第二次否掉 UI，并指定参考 `3D-hmtl-static-rendering`（本地 `D:\用户\下载\work1\kei-showcase`，
> 标题「凯伊 | KEI 全息档案」）。**v2 的「圆角 SaaS + 靛蓝」方向作废**，以本章为准。

## 9.1 令牌（逐字取自 kei-showcase/styles.css）

```css
:root{
  --bg-0:#05080f; --bg-1:#0a1120; --bg-2:#101a2d;
  --line:rgba(122,186,240,.16);
  --line-strong:rgba(140,210,255,.42);
  --line-soft:rgba(122,186,240,.08);
  --cyan:#5ad2ff; --cyan-dim:#2f9fd0; --ice:#cdefff;
  --violet:#8b7dff; --magenta:#ff6bd6;
  --text:#dceaf8; --text-dim:#8ba0ba; --text-faint:#61748c;
  --font:"Segoe UI","Microsoft YaHei","PingFang SC",system-ui,sans-serif;
  --mono:"Cascadia Mono",Consolas,"SF Mono","Courier New",monospace;
}
```
浅色映射（纸感，参考 `window-showcase`）：
`--bg-0:#f2f3f0; --bg-1:#e8eae6; --bg-2:#daddd8; --text:#202726; --text-dim:#3e4846;
--text-faint:#6c7673; --line:rgba(32,39,38,.13); --line-strong:rgba(32,39,38,.25);
--accent:#5f7974;`（直角与发丝边风格不变，主色换 `--sage:#5f7974`）

## 9.2 十条风格要素（缺一不可）

1. **直角**：`border-radius:3px`。**禁止** 8/10/12/14/16px 圆角。
2. **发丝描边**：`1px solid var(--line)`，hover → `var(--line-strong)`。
3. **大写字距标签**：`11~12px` + `letter-spacing:.10em~.18em`，配英文眉标。
4. **等宽数字**：编号/计数/时间戳用 `var(--mono)` + `tabular-nums`。
5. **辉光**：激活 `box-shadow:0 0 16px rgba(90,210,255,.18)`。
6. **扫描条激活态**：`border-left-color:var(--cyan)` +
   `background:linear-gradient(90deg,rgba(90,210,255,.16),transparent 82%)`。
7. **氛围层**：`.bg-glow`（径向青色辉光）+ `.scanlines`（极淡扫描线，`pointer-events:none`）。
8. **结构**：`masthead`（品牌 + 编号 + 操作）/ `rail`（左侧导航）/ 内容区 / `dossier`（右侧详情）/ `console`（底部状态栏）。
9. **术语**：导航中文 + 英文眉标（`数据源 / SOURCES`、`会话 / SESSIONS`、`导出 / EXPORT`、`动态 / FEEDS`、`总览 / OVERVIEW`）。
10. **主色 cyan `#5ad2ff`**；violet / magenta 只做点缀。

## 9.3 已修 bug：私聊头像不显示

`avatarHTML(name,key,size,kind,peerId)` 的调用点传了 `c.peer_id`；私聊的 `peer_id` 是
**NT UID**（`u_r4udLzP_…`）非数字 → `/^[0-9]+$/` 不通过 → 不生成 `<img>`，全部退化成首字圆圈。
修复：统一改用
```js
function pidOf(c){ return c.kind==="group" ? String(c.peer_id||"")
                                           : String(c.peer_qq||c.peer_id||""); }
```
群聊用 `peer_id`（群号），私聊用 `peer_qq`（数字 QQ）。

---

# 第 10 章 · v4 媒体支持（语音/图片/文件/表情/视频）

> 用户反馈：「语音导出不来，你那里显示的是空文件，还有发的文件之类的，还有图片，表情包之类的」。

## 10.1 已查明的根因（Lead 实测）

第三方解析器 `tools/nt_msg_db_util/msgdb/c2c/parser.py` **没有 `content_type=4` 分支**，
而 `content_type=4` 就是**语音（PTT / .amr）**，所以语音消息落到兜底分支，产出
`{"text":"","type":"text"}` —— 这正是「显示空文件」的原因。群聊里这类消息有 1,620 条。

**`tools/` 是第三方，禁止修改。** 分类必须在我们自己的代码里做。

## 10.2 content_type 语义表（Lead 用真实库解码确认，msg_type=2 语境）

| ct | 含义 | 关键字段 | 本地缓存目录 |
|---|---|---|---|
| 1 | 文本 | `text` | — |
| 2 | 图片 | `filename` `md5_raw` `f45424` `filesize` `img_width/height` | `Pic/<YYYY-MM>/Ori/<md5>.<ext>` |
| 3 | 文件传输 | `filename` `filesize` `file_uuid` | `File/Thumb/<md5>_750.jpg`（原始文件通常未下载） |
| **4** | **语音 PTT** | `filename`(xxx.amr) `md5_raw` `filesize` `expire_ts` | `Ptt/<YYYY-MM>/Ori/<md5>.amr` |
| 5 | 视频 | `filename`(xxx.mp4) `md5_raw` `thumbnail` `f45415`(时长ms) | `Video/<YYYY-MM>/Thumb/<md5>_0.png` |
| 6 | 视频表情/小视频 | `video_flag` `video_text` `video_width/height` | 一般无 md5 |
| 7 | @提及 / 引用段 | `reply_msg_seq` `reply_source_record_id` | — |
| 8 | 名片分享 | `nc_uid_1` `nc_nickname_1` | — |
| 10 | 小程序/JSON 卡片 | `fwd_meta` | — |
| 11 | 系统消息 | `sys_content` `sys_f80810` | — |
| 16 | 旧协议合并转发 | `legacy_fwd_xml` `legacy_fwd_resid` | — |

**md5 解码规则（Lead 已验证命中）**
- `md5_raw`：`base64.b64decode(v).hex()` → 32 位 hex
- `f45424`：`base64.b64decode(v).decode()` → 本身就是 32 位 hex（图片用）
- `thumbnail` / `f45421`：base64 → 16 字节 → hex（视频缩略图用）

**命中率实测**（群聊扫描 30,000 条）：语音 **12/12 = 100%**、视频 **101/101 = 100%**、
图片 846 命中 / 7,847 未命中（本地 `Pic` 只有 1,419 个文件，QQ 清理过缓存）、
文件 7/268、表情 470/2,280。全部本地缓存合计 4,683 个可索引文件。

## 10.3 数据层（已由 Lead 落地，`core/store.py`）

`messages` 表新增两列（幂等自动迁移，老库会 `ALTER TABLE` 补列）：

| 列 | 说明 |
|---|---|
| `content` | 原始结构化 JSON（来自 `3.export.py` 的 `content` 列，原样存） |
| `media` | 归一化媒体 JSON（下方 schema），无媒体时为 NULL |

```json
{
  "kind": "voice|image|video|file|sticker|card",
  "md5": "f726c859f4fa4da3bdbcdda5d488df09",
  "name": "f726c859....amr",
  "size": 25190,
  "duration": 3,
  "file": "Ptt/2026-08/Ori/f726...amr",
  "fallback": "[语音 3\" ]"
}
```
- `file` 是**相对 `nt_data` 的路径**，`null` 表示本地没有（前端显示占位、不破图）
- `duration` 单位**秒**（原始是毫秒要除 1000）
- 没有媒体但有语义的（名片/小程序/转发/系统）也要写 `media`，`kind="card"` + `fallback`

## 10.4 新增接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/media/{msg_id}` | **按 store 的 `messages.id` 返回媒体文件流**。响应头 `X-Media-Kind` / `X-Media-Source: cache|missing` / `Content-Type` 按扩展名。本地没有 → **404 + JSON**（前端必须优雅降级） |
| GET | `/api/media/stats?account=` | 各媒体类型命中率：`{voice:{hit,miss},image:{...},file:{...},video:{...},sticker:{...}}` |
| GET | `/api/media/pending?account=&kind=&limit=` | 本地缺失的媒体清单（供以后补下载用） |
| POST | `/api/sources/pack/rescan-media` | 只重扫媒体列（不重新解密整库），用于补齐老数据的 `media` |

## 10.5 导出增强（`core/export.py`）

- `export(account_qq, targets, formats, mode="per_peer", opts=None)`
- 新增 `opts["media"]`（默认 `True`）：把命中的媒体**复制到 `data/export/<job>/media/`**，
  文件名 `m<msg_id>_<安全原名>`；HTML 用**相对路径** `media/xxx` 引用（zip 解压后可直接播放/查看）
- HTML 气泡按类型渲染：图片 `<img>`（点击放大）、语音 `<audio controls>`、视频缩略图、
  文件卡片（图标 + 文件名 + 大小 + 下载链接）、表情 `<img>`；
  **本地缺失的**渲染成占位徽章 `[图片·未缓存]`，绝不出现破图
- TXT：`[图片]` / `[语音 3秒]` / `[文件: xxx.zip (34.4 MB)]` / `[表情]`
- MD：`> 🖼 图片` / `> 🎤 语音 3秒` / `> 📎 文件: xxx.zip`，有本地文件的补相对链接 `[打开](media/xxx)`
- 返回体新增 `media: {total, copied, missing}` 统计
- **注意体积**：单个 job 的媒体复制总量超过 `opts["media_max_mb"]`（默认 500）时停止复制，并在返回里标注 `truncated: true`

## 10.6 前端

- 消息气泡按 `media.kind` 渲染：图片缩略图（懒加载 + 点击灯箱）、语音播放器（显示秒数）、
  视频缩略图、文件卡片、表情图；本地缺失 → 占位徽章
- 媒体走 `GET /api/media/{msg_id}`，**不直连腾讯 CDN**
- 会话页/总览页可显示「本地媒体命中率」
- 保持零外部请求、零依赖、单文件产物、占位符格式不变

---

# 第 11 章 · v5–v10 接口增补（2026-10-01 冻结）

> **核对日期与依据**：2026-10-01 19:00（Asia/Shanghai）。
> 逐条比对 `server/app.py`（自带路由 + `_mount_optional_routers()` + `_mount_assets()`）与
> `server/routes_{feeds,framework,live,media,profile,send,voice,voice_official}.py` 的**真实路由装饰器、函数签名与返回字面量**；
> 语义 / 边界 / 限制以被调用的 `core/` 模块实现为准（`media_fetch` / `voice` / `voice_official` /
> `live_sync` / `framework_log` / `send` / `normalize` / `sources.qzone`）。
> 动态行为只读抽样自运行中的 `http://127.0.0.1:15555`（`trust_env=False`），未触发任何写操作。
>
> **本章只增补，不改动第 1–10 章**。第 1–10 章若与本章冲突，**以本章为准**（冲突清单见 11.10，只报告不改旧章节）。
> 通用约定同第 5 章：统一前缀 `/api`，JSON 响应；出错返回 `{"error": "人间可读的中文"}` + 非 200。
>
> **挂载方式（v5–v10 的关键变化）**：`server/app.py::_mount_optional_routers()` 会自动 import 并按文件名排序
> `include_router` 所有 `server/routes_*.py` —— **新增路由模块不需要改 app.py**。另有
> `server/app.py::_mount_assets()` 把 `web/assets` 挂到 `/assets`（v10 的 3D 素材；素材目录不存在时
> 跳过挂载，前端自动降级为 CSS `.bg-glow`，其它功能不受影响）。

## 11.0 本章覆盖的接口总览

共 **39 条**接口记录。其中 §8.5 已规划 2 条（`/api/feeds`、`/api/sources/qzone/sync`）、
§10.4 已冻结 4 条（`/api/media/{msg_id}`、`/api/media/stats`、`/api/media/pending`、`/api/sources/pack/rescan-media`），
本章对其**补全参数与边界**；其余 **33 条为首次写入本 SPEC**。

| # | 方法 | 路径 | 所属模块 | 对应版本 |
|---|---|---|---|---|
| 1 | GET | `/api/report` | app.py | v2（旧章节漏记） |
| 2 | POST | `/api/sources/{sid}/conversations` | app.py | v2（旧章节漏记） |
| 3 | GET | `/api/logs` | app.py | v7 |
| 4 | GET | `/api/media/stats` | routes_media | v4（§10.4 补全） |
| 5 | GET | `/api/media/pending` | routes_media | v4（§10.4 补全） |
| 6 | GET | `/api/media/{msg_id}` | routes_media | v4（§10.4 补全） |
| 7 | GET | `/api/data/quality` | routes_media | v4 |
| 8 | POST | `/api/sources/pack/rescan-media` | routes_media | v4（§10.4 补全） |
| 9 | POST | `/api/media/backfill` | routes_media | v8 |
| 10 | GET | `/api/media/backfill/status` | routes_media | v8 |
| 11 | POST | `/api/media/backfill/stop` | routes_media | v8 |
| 12 | POST | `/api/voice/transcribe` | routes_voice | v5 |
| 13 | GET | `/api/voice/progress` | routes_voice | v5 |
| 14 | GET | `/api/voice/stats` | routes_voice | v5 |
| 15 | POST | `/api/voice/official/sync` | routes_voice_official | v6+（官方回填） |
| 16 | GET | `/api/voice/official/status` | routes_voice_official | v6+ |
| 17 | POST | `/api/voice/official/stop` | routes_voice_official | v6+ |
| 18 | GET | `/api/voice/official/stats` | routes_voice_official | v6+ |
| 19 | GET | `/api/avatar` | routes_profile | v2 |
| 20 | GET | `/api/avatar/group` | routes_profile | v2 |
| 21 | GET | `/api/profile` | routes_profile | v2 |
| 22 | GET | `/api/contact` | routes_profile | v6（v2 规划，实际落地） |
| 23 | GET | `/api/live/status` | routes_live | v9 |
| 24 | POST | `/api/live/focus` | routes_live | v9 |
| 25 | POST | `/api/live/start` | routes_live | v9 |
| 26 | POST | `/api/live/stop` | routes_live | v9 |
| 27 | GET | `/api/live/events` | routes_live | v9 |
| 28 | GET | `/api/login/status` | routes_framework | v10 |
| 29 | GET | `/api/framework/status` | routes_framework | v9 |
| 30 | GET | `/api/framework/logs` | routes_framework | v9 |
| 31 | GET | `/api/framework/qrcode` | routes_framework | v10 |
| 32 | POST | `/api/framework/start` | routes_framework | v10 |
| 33 | POST | `/api/framework/stop` | routes_framework | v10 |
| 34 | POST | `/api/send` | routes_send | v9 |
| 35 | GET | `/api/send/history` | routes_send | v9 |
| 36 | GET | `/api/feeds` | routes_feeds | v2（§8.5 规划） |
| 37 | POST | `/api/qzone/sync` | routes_feeds | v2 |
| 38 | POST | `/api/sources/qzone/sync` | routes_feeds | v2（§8.5 规划） |
| 39 | GET | `/api/sources/qzone/status` | routes_feeds | v2 |

> 另有 2 个非 `/api` 的静态行为，属 v10 新增，记录在 11.11：
> `GET /`（返回构建产物 `app/dist/QQScope.html`）与 `GET /assets/*`（3D 素材静态目录）。

## 11.1 媒体（v4 补全 + v8 补下载）

### GET `/api/media/stats`

- **查询参数**：`account`（必填，int，账号 QQ）。缺省 → FastAPI 422。
- **返回**：
  ```json
  {
    "account": 1605289411,
    "media": {
      "image":   { "hit": 11940, "miss": 57177, "total": 69117 },
      "sticker": { "hit": 1079,  "miss": 4898,  "total": 5977 },
      "voice":   { "hit": 663,   "miss": 1,     "total": 664 },
      "video":   { "hit": 917,   "miss": 18,    "total": 935 },
      "file":    { "hit": 45,    "miss": 394,   "total": 439 },
      "card":    { "hit": 0,     "miss": 28084, "total": 28084 }
    },
    "index": { "data_root": "...", "accounts": {...}, "files": 5380, "per_dir": {...}, "cached_at": 1790850866 }
  }
  ```
- **语义/边界**：`media` 按 `kind` 分组统计**本地缓存命中率**（`hit + miss = total`）；`kind` 集合由 store 里实际出现的
  media 决定，可多于上例。`index` 是 `core/media.index_stats()` 的本地 nt_data 扫描概况（进程内 5 分钟缓存）。
  统计失败 → 500 `{"error": "媒体统计失败：…"}`。

### GET `/api/media/pending`

- **查询参数**：`account`（必填）、`kind`（可选，按类型过滤）、`limit`（默认 200）。
- **返回**：`{"account": <qq>, "kind": ""|"image", "count": N, "items": [ … ]}`
- **语义/边界**：列出**本地缺失**的媒体（`media.file` 为空或解析不到本地文件），供补下载前预览。
  查询失败 → 500。

### GET `/api/media/{msg_id}`

- **路径参数**：`msg_id`（int，= **`store.messages.id` 主键**，不是 QQ msgId）。
- **成功**：`200` + 文件流（`media_type` 按扩展名推测），响应头固定：
  `X-Media-Kind: <kind>`、`X-Media-Source: cache`、`Cache-Control: private, max-age=3600`。
- **失败**：**一律 404 + JSON**，响应头 `X-Media-Source: missing`（能确定类型时带 `X-Media-Kind`）：
  ```json
  { "error": "本地没有缓存文件：[图片·未缓存]", "source": "missing" }
  ```
  404 的成因（分别有不同 `error` 文案）：消息不存在 / 这条消息没有媒体内容 / 媒体信息已损坏 / 本地没有缓存文件。
- **语义/边界**：**媒体一律读本地 nt_data 缓存，绝不直连腾讯 CDN**；本地没有就是 404，
  **绝不 500**（前端必须优雅降级成占位徽章）。只有「读 store 出 SQL 错误」才 500。

### GET `/api/data/quality`

- **查询参数**：`account`（可选；不传则统计全库）。
- **返回**（`core/normalize.duplicate_stats`）：
  ```json
  { "account_qq": 1605289411, "c2c_contacts": 65, "distinct_peer_qq": 65,
    "duplicate_contacts": 0, "uid_contacts": 0, "uid_messages": 0,
    "uid_messages_without_qq": 0, "healthy": true }
  ```
- **语义/边界**：检查「同一账号下 c2c 会话是否按 QQ 号重复」。`duplicate_contacts` 应为 0；
  `uid_contacts` / `uid_messages` 是尚未归一化成 QQ 号的残留（应为 0）。失败 → 500。

### POST `/api/sources/pack/rescan-media`

- **请求体**：可选 dict（pack 源 opts，如 `{"data_root": "...", "accounts": [1605289411]}`）；无 body 也接受。
- **返回**：`core.sources.pack_source.rescan_media(opts)` 的结果（媒体列重扫摘要）。
- **语义/边界**：**复用已解密的中间产物，不重新解密整库**（用于给老数据补 `content` / `media` 列）。
  失败 → 500 `{"error": "媒体重扫失败：…"}`。
- **注意**：本路径由 `routes_media.py` 显式声明，**早于** app.py 的通用 `/api/sources/{sid}/sync` 之外，
  是独立路径，不会被通用路由抢。

### POST `/api/media/backfill`

- **请求体**（全部可选，除 account）：
  ```json
  { "account": 1605289411, "account_qq": 1605289411,
    "kinds": ["image"], "limit": 200, "max_bytes": 2147483648, "dry_run": false }
  ```
  | 字段 | 默认 | 允许范围 / 说明 |
  |---|---|---|
  | `account` / `account_qq` | 无（必填） | 1 ~ 10^12；缺失 → 400 `{"error":"缺少 account"}` |
  | `kinds` | `["image"]` | 白名单 `media_fetch.KINDS = image / sticker / voice / file / video`；字符串也接受（自动包成数组）；非法值回落成 `["image"]` |
  | `limit` | 200 | 1 ~ 500（`MAX_LIMIT`） |
  | `max_bytes` | 2 GiB | 1 ~ 50 GiB |
  | `dry_run` | false | true 时只列清单（含 `has_cdn` 预览），**不下载** |
- **返回**：`{"ok": true, "job": "<12位hex>", "status": "running", "account": …, "kinds": […], "limit": …, "max_bytes": …, "dry_run": …}`
- **状态码**：已在跑另一个任务 → **409** `{"error":"已有补下载任务在运行，请先等待或停止","job": "<id>"}`；
  缺 account → 400。
- **语义/边界（重点）**：
  - **全局并发 1**：`routes_media._ACTIVE_JOB` 保证同时只有一个补下载任务；任务在后台守护线程跑。
  - **限速**：每条之间 `core.media_fetch.RATE_SLEEP = 0.6s`（实际是 `stop_event.wait(sleep)`，可被中断）。
  - **可中断**：`POST /api/media/backfill/stop` 置 `stop_event`，**当前这一条下载完才停**；报告中 `stopped: true`。
  - **上限**：累计字节达到 `max_bytes` → 提前停止，`reason_hint = "达到总字节上限，已提前停止"`。
  - **任务保留**：内存里最多 20 个任务快照（旧的先淘汰）。
  - **能力边界**：`KINDS` 白名单虽含 `voice` / `file` / `video`，但 `_fetch_one` 对这三类的 `content`
    若没有 `cdn_url` 会直接返回 `reason="unsupported"`（注释原文：*该类型 content 无 cdn_url，需
    get_record/get_file（本期只验证图片）*）；**当前真正可用的下载路径是 `image`（`sticker` 同图片）**。
  - 报告与进度里**绝不含 rkey / token**（只用于拼 URL）。

### GET `/api/media/backfill/status`

- **查询参数**：`job`（可选）。不传 → 返回当前（或最近一个）任务；一个都没有 → `{"ok": false, "error": "没有补下载任务"}`。
- **返回快照**：
  ```json
  { "ok": true, "job": "a54933f74068", "status": "running|done|stopped|failed",
    "account": 1605289411, "kinds": ["image"], "limit": 300, "dry_run": false,
    "done": 300, "total": 300, "success": 272, "failed": 28, "bytes": 104104513,
    "current": { "id": 657969, "kind": "image", "ok": true, "reason": null, "http": null, "bytes": 46788 },
    "elapsed": 857.29, "error": null,
    "result": { "requested": 300, "success": 272, "failed": 28, "bytes": 104104513,
                "stopped": false, "by_reason": { "not_found": 28 },
                "by_kind": { "image": { "success": 272, "failed": 28 } }, "items": [ … ] } }
  ```

### POST `/api/media/backfill/stop`

- **查询参数**：`job`（可选；不传则停当前任务）。没有可停任务 → 404 `{"error":"没有可停止的任务"}`。
- **返回**：`{"ok": true, "job": "<id>", "status": "stopping"}`；最终状态由 status 接口变为 `stopped`。

## 11.2 语音（v5 本地 ASR / 官方回填）

### POST `/api/voice/transcribe`

- **请求体**：
  ```json
  { "confirm": true, "account_qq": 1605289411, "limit": 50, "recent": 50,
    "workers": 16, "model_size": "small", "threads": 2 }
  ```
  | 字段 | 默认 | 说明 |
  |---|---|---|
  | `confirm` | 无（**必填 true**） | 缺少 → **409**（见下） |
  | `account_qq` / `account` | 无（必填） | 整数；缺失 → 400，非数字 → 400 |
  | `limit` / `recent` | 无 | 传 `recent` 时按 `ts` 倒序取最新 N 条（`newest=true`）；`limit` 作为上限 |
  | `workers` | `voice.DEFAULT_WORKERS = 16` | 并行 worker 数 |
  | `model_size` | `"small"` | faster-whisper 模型 |
  | `threads` | `voice.DEFAULT_THREADS = 2` | 每 worker 线程数 |
- **返回**：新任务 → `{"ok": true, "job": "voice-<unix秒>"}`；
  已有任务在跑 → `200 {"ok": true, "job": "<同一个 job>", "state": {…}, "message": "已有转写任务在运行，返回同一个 job"}`
  （**幂等**）。
- **状态码**：`confirm` 非 true → **409**
  `{"error": "本地 ASR 已暂停（用户反馈机器卡）。确认要继续请传 {\"confirm\": true}；同一时间只允许一个任务，且不要与 CLI 同时跑。"}`
- **语义/边界**：接口只**启动后台守护线程**后立即返回；真正的安全阀在 `core/voice.py`
  （`run()` / `transcribe_one()` 默认拒绝执行，必须 `confirm=True` 或环境变量 `QQSCOPE_ASR_ALLOW=1`；
  `tools/asr/_run/asr.lock` 写 PID 互斥，禁止与 CLI 叠加跑）。断点续跑：`voice_status` 为 `ok` / `empty` 的自动跳过。
  失败如实分类（`missing` / `decode` / `asr` / `empty`），绝不把失败算成功。VAD 预筛：解码后语音占比
  `< DEFAULT_VAD_SKIP_RATIO = 0.12` 直接记 `empty`。

### GET `/api/voice/progress`

- **参数**：无。
- **返回**：
  ```json
  { "job": "voice-1790850000", "stage": "idle|启动|转写|完成|失败", "running": false,
    "done": 523, "total": 664, "current": "<当前文件>",
    "started_at": 1790849000, "finished_at": 1790850000, "result": { … }, "error": null }
  ```
- **语义/边界**：进程内单任务快照；服务重启后归零（历史结果在 store 的 `messages.media` 里，不丢）。

### GET `/api/voice/stats`

- **查询参数**：`account`（必填）。
- **返回**：
  ```json
  { "account": 1605289411, "voice_total": 664, "local_files": 663, "transcribed": 523,
    "failed": 107, "pending": 34, "lang_mismatch": 15, "avg_seconds": 20.53,
    "transcribed_seconds": 10676.8, "asr_seconds": 31609.0,
    "engine": "faster-whisper-small-int8", "model": "small",
    "status_counts": { "pending": 34, "decode": 1, "empty": 99, "ok": 523, "suspect": 7 } }
  ```
- **语义/边界**：成功 / 失败 / 语言跑偏**分开计**；`lang_mismatch` 是识别成非中文的条数。失败 → 500。

### POST `/api/voice/official/sync`

- **请求体**：
  ```json
  { "account_qq": 1605289411, "peer_limit": 40, "per_peer": 100, "limit": null }
  ```
  | 字段 | 默认 | 允许范围 |
  |---|---|---|
  | `account_qq` / `account` | 无（必填） | 1 ~ 10^12；缺失 → 400 |
  | `peer_limit` | 40 | 1 ~ 200（扫描多少个会话） |
  | `per_peer` | 100 | 1 ~ 500（每会话取多少条） |
  | `limit` | null（不限） | 1 ~ 5000；0 / 缺省视为 null |
- **返回**：`{"ok": true, "job": "<12位hex>", "status": "running", "account_qq": …, "peer_limit": …, "per_peer": …, "limit": …}`
- **状态码**：已有官方任务在跑 → **409** `{"error":"已有官方转写任务在运行，请先等待或停止","job":"<id>"}`；缺 account → 400。
- **语义/边界**：用 NapCat 的 `fetch_ptt_text`（腾讯服务端转写，实测单条约 0.05s）回填近期会话里的
  `[CQ:record]` 语音。**限速 1s/条、串行（并发 1）、单条超时 70s、可中断**（`core/voice_official.RATE_SLEEP=1.0`、`CALL_TIMEOUT=70.0`）。
  官方结果写入 `media.voice_text` / `voice_lang='zh'` / `voice_engine='qq-official'` / `voice_status='ok'`；
  **覆盖前**把本地 whisper 原文快照到 `voice_text_local`（连同 `voice_lang_local` / `voice_engine_local` / `voice_status_local`），
  逐条对比不丢数据；`voice_engine='qq-official'` 的行直接跳过（幂等）。失败如实分类
  `expired` / `not_ready` / `no_match` / `network`。token 只从 `data/framework/onebot.json` 读，绝不进日志 / 响应。

### GET `/api/voice/official/status`

- **查询参数**：`job`（可选；不传 → 当前或最近一个）。没有 → `{"ok": false, "error": "没有官方转写任务"}`。
- **返回快照**：
  ```json
  { "ok": true, "job": "<id>", "status": "running|done|stopped|failed",
    "account_qq": 1605289411, "peer_limit": 40, "per_peer": 100, "limit": null,
    "scanned": 1234, "voice_found": 66, "done": 66, "total": 66,
    "ok_count": 61, "failed_count": 5, "filled": 61,
    "current": { … }, "elapsed": 71.2, "error": null, "result": { … } }
  ```

### POST `/api/voice/official/stop`

- **查询参数**：`job`（可选）。没有可停任务 → 404。
- **返回**：`{"ok": true, "job": "<id>", "status": "stopping"}`（当前这条做完即停）。

### GET `/api/voice/official/stats`

- **查询参数**：`account`（必填）。
- **返回**：`{"account": 1605289411, "voice_total": 664, "official": 9, "local": 626, "both": 6, "official_only": 3, "local_only": 620}`
- **语义/边界**：`official` = 已有官方结果的条数，`both` = 官方与本地都有，`official_only` = 只有官方，
  `local_only` = 只有本地（需靠本地 whisper 兜底，因为语音有约 7 天时效）。失败 → 500。
## 11.3 头像与资料（v2 规划、v6 落地）

### GET `/api/avatar`

- **查询参数**：`qq`（int，默认 0）。`qq <= 0` → 404 `{"ok": false, "detail": "缺少合法的 qq"}`。
- **成功**：`200` + 图片流，响应头 `Cache-Control: public, max-age=86400`、`X-Avatar-Source: cache|fetched`。
- **失败**：404 `{"ok": false, "detail": "头像获取失败（网络不可达 / CDN 无此头像），请使用首字兜底头像"}`
  —— **前端必须有首字兜底，绝不出现破图**。
- **语义/边界**：服务端抓 `https://q1.qlogo.cn/g?b=qq&nk={qq}&s=640` 并缓存到 `data/avatars/<qq>.png`
  （首次 `fetched`，之后 `cache`）。抓取约束：超时 8s（连接 4s）、全局限并发 8、同一目标加锁（防缓存击穿）、
  文件 1 KB ~ 8 MB、按魔数校验 PNG/JPEG/GIF/WEBP、先写 `.tmp` 再原子替换。`httpx` 一律 `trust_env=False`
  （本机 Windows 系统代理会劫持请求）。**前端只写 `/api/avatar?qq=…`，不直连腾讯 CDN。**

### GET `/api/avatar/group`

- 同上，参数名 `group`（群号，>0），源 `https://p.qlogo.cn/gh/{gid}/{gid}/640`，
  缓存 `data/avatars/group_<gid>.png`。

### GET `/api/profile`

- **查询参数**：`account`（必填，>0；否则 400 `{"ok": false, "detail": "缺少 account"}`）。
- **返回**：
  ```json
  { "account_qq": 1605289411, "nickname": "霖ケ", "signature": "_linkai_",
    "avatar_url": "https://q1.qlogo.cn/g?b=qq&nk=1605289411&s=640",
    "qid": null, "uid": "u_8-Rg7t9qh5U3kbUAm76ipg", "friend_count": 62, "group_count": 62,
    "msg_total": 279836, "active_days": 59,
    "first_ts": 1782792733, "last_ts": 1790852546, "profile_ok": true }
  ```
  （上面是 2026-10-01 19:xx 对 1605289411 的**真实返回**；`msg_total` / `active_days` / `last_ts`
  会随 live 采集持续变化，不是固定值。）
- **语义/边界**：**任何失败都优雅降级，绝不 500**。昵称取 `profile_info.db/profile_info_v6`
  （解密链路复用 `core/sources/names.py`），拿不到就退回 `账号 <qq>`；`qid` **恒为 `null`**
  （`profile_info.db` 里没有 QID 列，如实置空）；`profile_ok` 表示昵称/资料包是否解密成功；
  `friend_count` / `group_count` 拿不到时用 `store.list_contacts` 兜底。资料包进程内缓存 5 分钟。

### GET `/api/contact`

- **查询参数**：`account`（必填，>0）、`kind`（必填，只能是 `c2c` / `group`）、`peer_id`（必填）。
  任一缺失/非法 → 400 `{"ok": false, "detail": "缺少 account / kind / peer_id（kind 只能是 c2c 或 group）"}`；
  store 里找不到该会话 → 404 `{"ok": false, "detail": "找不到该会话（account/kind/peer_id 不匹配）"}`。
- **返回**（联系人资料卡）：
  ```json
  { "name": "嘉豪", "remark": null, "peer_qq": 192256313, "kind": "c2c",
    "uid": "u_FW25098n9Lesk7FREZJdsA",
    "avatar_url": "https://q1.qlogo.cn/g?b=qq&nk=192256313&s=640",
    "msg_count": 90, "self_count": 47, "first_ts": 1789184154, "last_ts": 1790784422,
    "last_text": "…", "hourly": [ …24 个数… ],
    "top_words": [ { "word": "寿来", "count": 18 }, { "word": "不管", "count": 5 } ] }
  ```
  （上面是 2026-10-01 19:xx 对 `c2c/192256313` 的**真实返回**，`last_text` 与 `hourly` 已省略；
  数值会随采集变化。）
- **语义/边界**：
  - `msg_count` / `self_count` / `first_ts` / `last_ts` **优先按 `messages` 表重算**，为空才退回 `contacts` 行内统计；
  - `hourly` 是该会话 24 小时分布（本地时区，与 `store.hour_hist` 一致）；
  - `top_words` 取该会话**最近 20000 条**（`_TOP_SCAN`）有文本消息，中文 2-gram + 英文/数字词，
    过滤常见虚词/URL/@ 噪声，取 Top 12；
  - `name` 兜底顺序：`contacts.name` → 昵称库 → `群 <gid>` / `QQ <qq>` / `peer_id`；
  - 群聊头像走 `/api/avatar/group`，私聊头像走 `/api/avatar`。

## 11.4 运行日志（v7）

### GET `/api/logs`

- **查询参数**：`limit`（默认 200）、`level`（可选，精确匹配 `info|warn|error`）、`since`（可选，Unix 秒，只返回 `ts > since`）。
- **返回**：`{"logs": [ { "ts": 1790850000, "level": "info", "source": "http", "msg": "…" } ], "total": <缓冲区总条数>}`
- **语义/边界**：
  - 数据来自 `server/app.py` 的进程内环形缓冲 `LOG_BUFFER`（`collections.deque(maxlen=800)`），**不落盘**；
  - HTTP 中间件会为所有 `/api/*` 请求（`/api/logs` 自身除外）写一条日志，`4xx/5xx` 记为 `warn`；
  - `source` 取值示例：`http` / `pack` / `bot` / `qzone` / `live` / `framework` / `send`；
  - `msg` 截断到 500 字符；响应本身不带分页，只有 `total` 计数。

## 11.5 实时采集 live（v9）

### 采集节奏（所有 `/api/live/*` 共享，来自 `core/live_sync.py`）

| 项 | 值 | 说明 |
|---|---|---|
| `FOCUS_INTERVAL` | **5.0s** | 前端正在看的那个会话 |
| `TOP_INTERVAL` | **30.0s** | 最近活跃 top 20 会话 |
| `OTHER_INTERVAL` | **600.0s**（10 min） | 其余全部会话 |
| `TOP_N` | 20 | 活跃会话数 |
| `REQUEST_GAP` | **0.16s** | 每次 OneBot 请求之间的最小间隔 |
| `MAX_REQUESTS_PER_ROUND` | **60** | 单轮最多轮询多少个会话 |
| 退避 | 指数（`interval × 2^errors`，有 `BACKOFF_CAP`） | 连续失败会话自动降频 |
| 未登录时 | 每 10s 重查一次，**期间不发任何 OneBot 请求、不写库** | `WAIT_LOGIN_INTERVAL = 10.0` |

自动启动：后端启动即 `start()`。关闭方式：环境变量 `QQSCOPE_LIVE_AUTOSTART` 取
`0/false/no/off`，或 `data/server/settings.json` 里 `live.auto=false` / `live.enabled=false`。
所有动作写 `/api/logs`（`source='live'`）。

### GET `/api/live/status`

- **参数**：无。
- **返回**（节选，字段较多）：
  ```json
  { "running": true, "focus": { "kind": "c2c", "peer_id": "1605289411", "peer_qq": 1605289411 },
    "waiting_login": false, "sync_allowed": true, "require_login": true, "gate": { … },
    "account_qq": 1605289411, "nickname": "霖ケ",
    "poll_count": 1639, "requests_total": 1675, "inserted_total": 1753, "rounds": 1852,
    "last_poll_ts": 1790850874, "last_error": "…", "started_at": 1790849109,
    "peers": 128, "top_n": 20,
    "last_round": { "at": 1790850874, "requests": 1, "queued": 1, "inserted": 1,
                    "seconds": 2.31, "budget_hit": false },
    "intervals": { "focus": 5.0, "top": 30.0, "other": 600.0, "count": 40,
                   "request_gap": 0.16, "max_requests_per_round": 60 },
    "thread": "qqscope-live",
    "per_peer": [ { "kind": "group", "peer_id": "1101846917", "name": "…",
                    "top": true, "last_poll": 1790850874.2, "inserted": 28,
                    "errors": 0, "next_due_in": 30.1 } ] }
  ```
- **语义/边界**：`per_peer` 最多返回最近轮询的 **100** 个会话；`last_error` 为最近一次失败（人话中文）。

### POST `/api/live/focus`

- **请求体**：`{ "kind": "c2c"|"group", "peer_id": "…", "peer_qq": 123456 }`。
  传空 `kind` / `peer_id` → **清除 focus**。
- **返回**：设置成功 `{"ok": true, "focus": {"kind":"c2c","peer_id":"…"}, "message": "focus 已设为 c2c:…，将每 5s 轮询"}`；
  清除 `{"ok": true, "focus": null, "message": "已取消 focus"}`。
- **语义/边界**：前端「切会话」时调用，focus 会话按 5s 轮询（前端轮询间隔也跟着缩到 ≤5s）。
  设置异常 → 500。

### GET `/api/live/events`

- **查询参数**：`account`（可选）、`since`（默认 0，Unix 秒）、`since_id`（默认 0，`messages.id` 游标）、
  `limit`（默认 300，钳制 1 ~ 1000）。
- **返回**：
  ```json
  { "ts": 1790850900, "since": 0, "since_id": 12345, "next_since": 1790850899, "next_id": 12399,
    "count": 5, "messages": [ …store 行… ],
    "messages_by_peer": { "c2c:192256313": [ … ] },
    "contacts": [ …last_ts 变化的联系人… ] }
  ```
- **语义/边界**：**推荐用 `since_id`**（更精确）；`since_id > 0` 时按 `id > since_id` 取，否则按 `ts > since`。
  下一次请求把返回的 `next_id` / `next_since` 原样带上即可。`contacts` 只返回 `last_ts > since` 的，最多 200 条。
  读取失败 → 500。

### POST `/api/live/start` / POST `/api/live/stop`

- **参数**：无（body 忽略）。
- **返回**：`{"ok": true, "running": true|false, "message": "…", "status": { …同 /api/live/status… }}`
- **语义/边界**：幂等（已运行再 start 不报错）；异常 → 500。`/api/live/stop` 通过 `Event` 干净退出线程，
  **绝不重启或关闭 NapCat**。v10 的「退出登录」会调 `/api/live/stop`。

## 11.6 框架终端与登录门（v9 / v10）

### GET `/api/login/status`

- **参数**：无。
- **返回**：
  ```json
  { "framework_running": true, "logged_in": true, "account": 1605289411, "nickname": "霖ケ",
    "pid": 18896, "ports": { "3000": true, "6099": true },
    "qr": { "available": true, "mtime": 1790841142, "size": 594, "url": "/api/framework/qrcode" },
    "sync_allowed": true, "waiting_login": false, "verified": true,
    "message": "已登录：霖ケ", "can_stop": false, "spawned_pid": 0,
    "qrcode_url": "/api/framework/qrcode" }
  ```
- **语义/边界（登录门状态机的唯一判据）**：
  - 判定依据不猜：6099 在听 / 进程存在 → **框架活着**；3000 在听 → **已登录**（NapCat 只有登录成功才监听 OneBot HTTP）；
    `probe_onebot=True` 时再实查一次 `get_login_info`（1s 超时 + 1.5s 缓存），**只有端口在听时才发请求**；
  - `sync_allowed = framework_running 且 logged_in 且 OneBot 端口在听`；
  - 热路径目标 < 50ms：不查 WMI、不 spawn 子进程，超时立刻用端口兜底，**绝不卡首屏**；
  - 前端逻辑：`logged_in:true` → 直接进主界面（连接门不渲染二维码）；`false` + 框架在跑 → 显示二维码；
    框架没起 → 引导「启动框架」；接口失败重试 2 次后进主界面 + 顶部横幅。
  - `can_stop` 决定前端是否显示「退出并停止框架」（见 `/api/framework/stop`）。

### GET `/api/framework/status`

- **参数**：无。
- **返回**：
  ```json
  { "running": true, "pid": 39760, "framework": "napcat", "version": "4.18.28",
    "logged_in": true, "account": 1605289411, "nickname": "霖ケ",
    "onebot_base": "http://127.0.0.1:3000", "webui": "http://127.0.0.1:6099/webui",
    "uptime": 9717, "ports": { "3000": true, "6099": true },
    "log": { "file": "run.log", "size": 1632187, "size_err": 268, "warn": null },
    "server_ts": 1790850858 }
  ```
- **语义/边界**：配置读 `data/framework/onebot.json`（`base` / `webui` / `account_qq` / `framework` / `version`）。
  登录态先问 `get_login_info`（0.8s 超时），失败时**兜底读日志**（尾部 400 行里找「登录成功 / 已登录 /
  数据库辅助支持能力」，并用正则匹配账号行）。`uptime` 取自日志首行时间戳。

### GET `/api/framework/logs`

- **查询参数**：
  | 参数 | 默认 | 说明 |
  |---|---|---|
  | `limit` | 300 | 1 ~ 2000（`MAX_MEM_LINES`） |
  | `offset` | 0 | `run.log` **字节游标**；`0` = 初始加载只读尾部 |
  | `offset_err` | 无 | `run.err` 的字节游标（可选） |
  | `q` / `grep` | 无 | 关键字过滤，大小写不敏感（两者等价） |
- **返回**：
  ```json
  { "lines": [ { "ts": 1790850000, "level": "info", "text": "…", "src": "run.log" } ],
    "offset": 1632187, "offset_err": 268, "size": 1632187, "size_err": 268,
    "file": "run.log", "running": true, "truncated": false, "warn": null,
    "grep": null, "server_ts": 1790850900 }
  ```
- **语义/边界（增量语义，重点）**：
  - `offset=0`（或缺省）→ **初始加载**：只读文件尾部 `limit` 行，并把 `offset` 设为当前文件大小；
  - `offset=N` → **增量**：只返回 `run.log` 第 N 字节之后的新行；`run.err` 只在**显式传 `offset_err`** 时增量，
    否则 `offset_err` 直接置成当前大小、**不回放旧 stderr**；
  - **offset 只在完整行之后推进**：写到一半的最后一行留到下次再读（不会出现半行乱码）；
  - offset 失效（文件被截断 / 超出当前大小）→ 自动退回「读尾部」；
  - 单次最多 2000 行；内存缓冲最多 2000 行；
  - 初始加载不带 `q` 时只读尾部 **256 KB**（`TAIL_BYTES`）；带 `q` 时最多回扫 **4 MB**（`SEARCH_BYTES`），
    不整读大文件；
  - 文件超过 **20 MB** 时只在 `warn` 里给提示，**不做轮转**；
  - 输出文本已清洗 ANSI 颜色码与 CRLF，并经 `core/framework_log.scrub()` **脱敏**（token、二维码 `k=` 参数）。
- **建议的前端用法**：首屏 `GET /api/framework/logs?limit=300`，之后带上返回的 `offset`（需要 stderr 时再带 `offset_err`）增量追加。

### GET `/api/framework/qrcode`

- **参数**：无。
- **成功**：`200` + `image/png`，响应头 `X-QR-Mtime`、`X-QR-Size`、
  `Cache-Control: no-store, no-cache, must-revalidate, max-age=0`、`Pragma: no-cache`、`Expires: 0`。
- **失败**：404
  ```json
  { "error": "暂无二维码，请点「启动框架」",
    "message": "暂无二维码：框架没在等待扫码（可能已登录，或还没启动）",
    "qr": { "available": false, "mtime": 0, "url": "/api/framework/qrcode" } }
  ```
- **语义/边界**：文件是 NapCat 未登录时写出的 `tools/napcat/napcat/cache/qrcode.png`。
  前端用 `t=<mtime>` 做 3 秒刷新（mtime 不变就不重复拉图）。**已登录时不应再要二维码**。

### POST `/api/framework/start`

- **请求体**：`{ "by": "web" }`（可选，仅用于审计日志）。
- **返回**：
  - 已在运行（端口在听或已登录）→ `200 {"ok": true, "already_running": true, "pid": …, "message": "框架已在运行，无需重复启动", "status": {…}}`
  - 进程在但端口没起 → `200 {"ok": true, "already_running": false, "starting": true, "pid": …, "message": "框架进程已在（端口还没起），正在启动中，请稍候…", "status": {…}}`
  - 启动成功 → `200 {"ok": true, "already_running": false, "pid": <新 PID>, "message": "框架已启动（PID …），请稍候查看二维码"}`
- **状态码**：**5 秒节流**（`_START_THROTTLE = 5.0`）内重复调用 → **429**
  `{"ok": false, "throttled": true, "retry_after": <秒>, "message": "操作太频繁，请 N 秒后再点"}`；
  找不到 `tools/napcat/node.exe` 或 `index.js` → **500**；`Popen` 失败 → **500**。
- **语义/边界**：**幂等，绝不产生第二个框架进程**；启动宽限期 120s（同一进程启动中不重复拉起）；
  日志重定向到 `data/framework/run.log` / `run.err`（与 `启动框架.bat` 一致，新会话重写）；
  PID 写入 `data/framework/spawned.json`（跨后端重启可认领）。Windows 下用 `CREATE_NO_WINDOW` 无窗口启动。

### POST `/api/framework/stop`

- **请求体**：`{ "by": "web" }`（可选）。
- **返回**：
  - 成功 → `200 {"ok": true, "stopped_pid": <pid>, "pid": <pid>, "message": "已停止 QQScope 启动的框架（PID …）",
    "ports": {"3000": false}, "process_gone": true, "taskkill": "…", "can_stop": false}`
  - 进程已停但端口没释放 → `200 {"ok": false, "…", "message": "已发送停止信号（PID …），但仍有残留：进程还在=…，端口=…"}`
- **状态码**：**记录 PID 不存在 / 不是框架进程 → 409**
  `{"ok": false, "error": "这个框架不是 QQScope 启动的，为安全起见不代你停止。请手动关闭它的窗口。", "message": "…", "stopped_pid": 0, "can_stop": false, "pid": 0}`；
  `taskkill` 抛异常 → 500。
- **语义/边界（红线）**：
  - **只停本服务通过 `/api/framework/start` 启动的那个 PID**，跨后端重启用 `spawned.json` 认领；
  - 真正 kill 之前**必须**校验：PID 现在还在跑 **且** 是 `node.exe` **且** 命令行含 `napcat` —— **三者缺一不杀**；
  - 停止后只校验「该 PID 原先持有的端口」是否释放（不把别人的端口算进来），全部释放才 `ok: true`；
  - 停干净了才抹掉 `spawned.json` 记录。**绝不 kill 用户自己启动的 NapCat / 主 QQ。**

## 11.7 网页发消息（v9 · 写操作）

### POST `/api/send`

> ⚠️ **本接口会把消息真的发给 QQ 联系人。** 2026-10-01 发生过一次「自测误发给真人」的事故，
> 之后加了下面的策略层。规则：**只做「用户手动点一下发一条」——绝不群发 / 批量 / 定时 / 自动重试。**

- **请求体**：
  ```json
  { "account_qq": 1605289411, "kind": "c2c", "peer_id": "192256313", "peer_qq": 192256313,
    "text": "你好", "confirm": true, "dry_run": false }
  ```
- **状态码与语义（按代码判定顺序）**：

  | 顺序 | 条件 | 状态码 | 响应 |
  |---|---|---|---|
  | 1 | `confirm` **不是** `true` | **428** | `{"error": "发送真实消息需要 confirm:true"}` |
  | 2 | `text` 非字符串或空白 | 400 | `{"error": "消息内容不能为空"}` |
  | 3 | `len(text) > 2000`（`MAX_LEN`） | 400 | `{"error": "单条消息不能超过 2000 字"}` |
  | 4 | `kind` 不是 `c2c` / `group` | 400 | `{"error": "kind 只能是 c2c 或 group"}` |
  | 5 | `dry_run: true`（body）或设置里 `dry_run=true` | 200 | `{"ok": true, "dry_run": true, "message_id": null, "would_send": {"kind","peer_id","peer_qq","text"(截断 200 字)}}` —— **不真发** |
  | 6 | `kind=group` 且设置 `allow_groups ≠ true` | **403** | `{"error": "安全策略：默认禁止向群聊发送。确认要开启请在设置里打开「允许发送到群聊」", "code": "policy_blocked"}` |
  | 7 | `kind=c2c` 且收件人 ≠ 当前登录账号 且设置 `allow_others ≠ true` | **403** | `{"error": "安全策略：默认只允许发给自己（<登录QQ>）。要给他人发消息，请在设置里打开「允许发给他人」", "code": "policy_blocked", "login_qq": <qq>}` |
  | 8 | 限速命中（同一会话 **3s 最多 1 条**；全局 **30s 最多 10 条**） | **429** | `{"error": "…请 N 秒后再试", "code": "rate_limited", "retry_after": <秒>}` |
  | 9 | 发送失败（`empty`/`too_long`/`bad_kind`/`bad_target`/`account_mismatch`） | 400 | `{"error": "…", "code": "…"}` |
  | 10 | 其它发送失败 | 502 | `{"error": "…", "code": "…"}` |
  | 11 | 成功 | 200 | `{"ok": true, "message_id": "…", "account_qq": …, "nickname": "…", "kind": "…", "peer_id": "…", "ts": …, "stored": …, "raw": …}` |

- **默认策略**（`data/server/settings.json` 的 `send`）：
  ```json
  { "allow_others": false, "allow_groups": false, "dry_run": false }
  ```
  即**默认只允许发给自己**，群聊默认禁止。
- **前端约定**：必须弹**二次确认**弹窗；群聊要额外提示「这是群聊，N 人会看到」（`confirm: true` 只在用户确认后加；
  目标必须是用户手动选择的会话）。前端有 `__QQSCOPE_SEND_DRY_RUN` 测试通道，禁止 `setInterval` 自动发送。
- **审计**：发送内容与结果写 `/api/logs`（`source='send'`），用户可自查发了什么。
- 真正的限速/账号校验/写库在 `core/send.py`（`SOURCE = "local_send"`，`PEER_MIN_INTERVAL = 3.0`、
  `GLOBAL_WINDOW = 30.0`、`GLOBAL_MAX = 10`、`MAX_LEN = 2000`）。

### GET `/api/send/history`

- **查询参数**：`account`（可选）、`limit`（默认 50，钳制 1 ~ 200）。
- **返回**：`{"messages": [ … ], "total": N, "source": "local_send"}`
- **语义/边界**：只列**本机通过网页发过**的消息（`source='local_send'`），不是全部自发消息。读取失败 → 500。

## 11.8 QQ 动态（v2 规划，v9 落地）

### GET `/api/feeds`

- **查询参数**：`account`（可选）、`limit`（默认 20，钳制 1 ~ 200）、`pos`（默认 0，偏移量）、
  `scope` ∈ `mine` / `friends` / `all`（默认 `all`，非法值回落 `all`）。
  `scope` 为 `mine`/`friends` 且没传 `account` 时，用当前登录 QQ 兜底。
- **返回**：
  ```json
  { "feeds": [ { "id": "…", "ts": 1790837912, "author_qq": 3647980032,
                 "author_name": "…", "content": "…", "images": ["https://…"],
                 "praise": 0, "praise_available": false,
                 "comment_count": 1, "forward_count": 0,
                 "comments": [ { "nickname": "…", "uin": …, "content": "…", "ts": … } ],
                 "comments_total": 1 } ],
    "total": 123, "scope": "all", "next_pos": 20,
    "status": "ok|need_login|empty|error", "reason": "", "message": "…", "need_login": true }
  ```
- **语义/边界（重要）**：
  - `praise` **恒为 `0`** 且显式带 `praise_available: false` —— QZone 的列表/详情 cgi **不返回点赞数**，
    **前端不得把它当成真实点赞数**（这是如实的字段语义，不是 bug）；
  - `comments` 只给**最近 3 条**，`comments_total` 是全部条数；
  - `next_pos = pos + 本次返回条数`，**仅当「返回满 limit 且未到 total」时才非 null**，否则 `null`（没有更多）；
  - 空态明确区分：`need_login`（没登录态）/ `empty`（有登录态但本地还没同步到动态）/ `error`（异常），
    并始终带 `reason` + `message` 供前端做引导；
  - QZone 模块导入失败 → 503 `{"error": "…", "status": "error", "reason": "module"}`；读库失败 → 500 `reason="db"`。
  - **拿不到登录态时返回明确中文错误，绝不伪造数据、不用假数据糊弄 UI。**

### POST `/api/qzone/sync`

- **请求体**：`core.sources.qzone.sync(opts)` 的 opts（可含显式 `cookie` / `p_skey` 等，通常留空走设置/OneBot）。
- **返回**：qzone 同步结果；失败 → **502** 且带 `status` / `reason`（如 `need_login`）/ `need_login` 标记。
- **语义/边界**：本路径与 app.py 通用路由不冲突（`/api/qzone/...` 不在 `/api/sources/{sid}/...` 下）。

### POST `/api/sources/qzone/sync`

- **SPEC 8.5 路径**，与 `POST /api/qzone/sync` **等价**（同一个 `_do_sync`）。
- **匹配顺序注意**：`app.py` 的通用 `POST /api/sources/{sid}/sync` **注册更早**，实际会先匹配到它；
  `routes_feeds.py` 在导入时把 `core.sources.qzone` 注册进 `app.SOURCE_MODULES`，让通用路由分发到 qzone
  （本文件同时显式声明了一份同路径路由作为兜底）。**两条路径都能用，效果一致。**

### GET `/api/sources/qzone/status`

- **参数**：无。
- **返回**：
  ```json
  { "id": "qzone", "name": "QQ 动态（QZone）", "kind": "qzone", "ready": true,
    "message": "已获取 QZone 登录态",
    "detail": { "httpx": true, "endpoints": ["https://user.qzone.qq.com/proxy/domain/…", "https://h5.qzone.qq.com/proxy/domain/…"],
                "settings_file": "…data/server/settings.json", "probe_dir": "…data/qzone_probe",
                "cookie_sources": ["opts", "settings.qzone", "onebot:get_cookies (domain=qzone.qq.com)",
                                   "onebot:get_credentials", "napcat-config token"],
                "credential": { "status": "ok|need_login|error", "reason": "…", "source": "…", "uin": 1605289411 },
                "rate_limit": "1.0s" } }
  ```
- **语义/边界**：cookie 来源优先级 = `opts` → `settings.qzone` → OneBot `get_cookies(domain=qzone.qq.com)`
  → OneBot `get_credentials` → NapCat 配置 token。`credential.status` 为 `need_login` 时所有出口都返回明确中文错误。
  **cookie / p_skey / skey 永不写日志、永不进接口响应，一律脱敏成 `p_skey=***`。** 请求限速 1.0s。
  本机 `nt_db` 里没有任何动态数据，所以动态只能走网络（这也是它必须要有登录态的原因）。

## 11.9 第 5 章漏记的 v2 接口

### GET `/api/report`

- **查询参数**：`account`（**必填**，int）、`limit`（默认 30000）。
- **返回**：
  ```json
  { "meta": { "account_qq": 1605289411, "label": "霖ケ", "source": "bot",
              "contacts": 128, "messages": 278386, "self_messages": 7771,
              "self_qq": 1605289411 },
    "count": 3778,
    "messages": [ { "t": 1786752000, "d": 1, "k": "c2c", "p": "u_xxx", "x": "你好" } ] }
  ```
  （字段结构示例；`count = len(messages) <= limit`，`meta` 取账号行并注入 `self_qq`。
  上面的量级来自 2026-10-01 数据，会随采集增长。）
- **语义/边界**：**只返回「自己发的(`direction=1`) + 文本非空」的消息**，体积小，供前端 `QQScopeEngine.computeReport`
  出完整报告（情绪曲线 / 活跃时段 / 每日 / 高频词）——**情绪词典的唯一真源保持在 `app/js/analysis.js`，后端不重复实现**。
  消息按 `ts` 升序取前 `limit` 条（**没有分页**）；`meta` 是账号行 + 注入的 `self_qq`（账号行不存在时为空对象）。
- **版本归属**：Lead 在 v2 新增，前端总览优先用它；缺失时降级到 `/api/messages` → 内嵌快照 → 空状态。

### POST `/api/sources/{sid}/conversations`

- **路径参数**：`sid` ∈ `pack` / `bot` / `qzone`（qzone 也可，返回空数组）。
- **请求体**：数据源 opts（可选；非法 JSON 视为 `{}`）。
- **返回**：`{"conversations": [ { "account_qq":…, "kind":…, "peer_id":…, "peer_qq":…, "name":…, … } ]}`
- **状态码**：模块不存在/导入失败 → **503**；模块没有 `conversations()` → 400
  `{"error": "数据源 <sid> 不支持列出会话"}`；模块内部抛错（含 `BotError`）→ **502**（错误信息转人话中文）。
- **语义/边界**：这是「列出**可采集**的会话」的只读探测，不做任何写入；窗口 B 用它列出 OneBot 里的好友/群。
  qzone 有占位实现 `conversations()` 返回 `[]`（动态没有会话概念，仅为满足数据源契约）。

## 11.10 已知冲突（只报告，不改旧章节）

1. **第 5 章表格把 AI 接口写成 `GET /api/ai/chat`** —— 实际是 **`POST /api/ai/chat`**
   （`@app.post("/api/ai/chat")`，body `{messages, model?}`，返回 `{content}`；Key 只在服务端）。
   第 5 章那行的方法名是笔误，本章不改旧章节。
2. **第 1 章目录表的「前端」行写 `web/index.html` `web/js/*` `web/assets/*`** —— 实际前端是
   **单文件 `web/index.html`**（HTML+CSS+JS 全内联），**不存在 `web/js/` 目录**；引擎在 `app/js/analysis.js`
   由 `scripts/build_web.py` 构建时注入。`web/assets/` 存在但已在 `.gitignore` 中排除（3D 素材需
   `scripts/fetch_assets.ps1` 复制）。
3. **第 5 章表格未收录 v5–v10 的全部新接口**（本章 11.0 列出 39 条），也未收录 app.py 自带的
   `/api/logs`、`/api/report`、`/api/sources/{sid}/conversations`。第 5 章那 18 条本身与实际实现一致，不冲突。
4. **第 8.5 章只规划了 `GET /api/feeds?account=&limit=&pos=` 与 `POST /api/sources/qzone/sync`**
   —— 已实现，但实际返回还包含 `scope` / `total` / `next_pos` / `status` / `reason` / `message` /
   `need_login`，feed 项还包含 `praise_available` / `comments[近3条]` / `comments_total`；且实际匹配可能
   先落到 app.py 的通用 `/api/sources/{sid}/sync`（由 `SOURCE_MODULES` 分发到 qzone）。以本章 11.8 为准。
5. **第 10.4 章写 `GET /api/media/stats` 返回 `{voice:{hit,miss},image:{...}}`** —— 实际外层还有
   `account` 与 `index`（本地索引概况），且每个类型还带 `total`。以本章 11.1 为准。
6. **第 10.4 章写 `/api/sources/pack/rescan-media`** —— 路径与实现一致；补充：它由 `routes_media.py`
   显式声明，接收可选 opts，且**复用已解密产物、不重新解密整库**。
7. **第 10.5 章写导出返回体** —— 实际返回还含媒体统计与截断标记（`media: {total, copied, missing}`、
   `truncated`），两章需合读；`opts["media_max_mb"]` 默认 500。
8. **第 10.2 章的 content_type 语义表与「补下载能力」不是一回事** —— `core/media_fetch.KINDS` 白名单是
   `image / sticker / voice / file / video`，但注释明确 `voice` / `file` / `video` **本期未实现**（缺 `cdn_url`
   时直接返回 `reason="unsupported"`）。当前真正可下载的只有 `image`（`sticker` 同图片路径）。
9. **第 6 章验收线「入库 ≥ 260,000 行 / 自发 ≥ 2,800 条」** —— 实现与 `scripts/verify.py --full` 仍按此线判定，
   不算冲突；只是真实数字会随采集增长（2026-10-01 实测 278,386 / 7,771），引用时请注明时间。
10. **`scripts/verify.py` 不校验 v5–v10 新接口** —— 它只检查 paths / store schema / pack+bot 模块 /
    export / web 源码 / 构建 / 数据现状 / 导出实测；新接口（media / voice / live / framework / send / feeds）
    的回归目前靠 `web/selftest/check_v*.mjs` 与手工验证。**接口冻结后建议后续给新接口补自动化断言。**
11. **第 2 章数据模型写 `peer_id` 私聊 = uid** —— 实际 `core/normalize.py` 已做过一次迁移：
    私聊 `peer_id` 优先归一化成 **QQ 号字符串**，只有完全查不到 QQ 号时才保留 uid
    （现状 `/api/data/quality` 实测 `uid_contacts = 0`、`uid_messages = 0`）。第 2 章描述的是迁移前语义。

## 11.11 非 `/api` 的静态行为（v10）

### GET `/`

- 返回构建产物 `app/dist/QQScope.html`；产物不存在时返回一段 HTML 提示「前端还没构建。先运行：
  `python scripts/build_web.py`」（仍是 200）。

### GET `/assets/*`

- 由 `server/app.py::_mount_assets()` 用 `StaticFiles` 把 `web/assets` 挂到 `/assets`
  （前端用 `/assets/vendor/{three.min.js, GLTFLoader.js, kei.vrm}` 加载 3D 环绕背景）。
- **素材目录不存在时不挂载**，`/assets/*` 返回 404，前端自动降级成 CSS `.bg-glow`，其它功能不受影响。
- 该目录约 44 MB，已在 `.gitignore` 中排除；用 `powershell -ExecutionPolicy Bypass -File scripts\fetch_assets.ps1`
  从参考项目 `kei-showcase` 复制恢复。
