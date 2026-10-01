# QQScope 前端（web/）

零依赖原生 JS 单页应用，源码为 `web/index.html`（HTML + CSS + JS 全部内联，无任何外部请求）。
Lead 的 `scripts/build_web.py` 读取本文件，注入 boot 快照与分析引擎后输出 `app/dist/QQScope.html`。

## 1. 注入占位符（build_web.py 契约）

源码中必须是**独立成行**的：

```js
window.__QQSCOPE_BOOT__ = /*__QQSCOPE_BOOT__*/ null;
```

```js
/*__QQSCOPE_ENGINE__*/
```

替换规则（与 Lead 确认）：

- boot：用正则 `/\*__QQSCOPE_BOOT__\*\/[ \t]*null` 替换为 boot JSON 字面量，
  保留前缀 `window.__QQSCOPE_BOOT__ = ` 与结尾 `;`，**不要只替换注释 token**（否则残留 `null` 造成语法错，即旧项目那个经典 bug）。
- engine：把单独一行的 `/*__QQSCOPE_ENGINE__*/` 替换为 `app/js/analysis.js` 原文（它自带 IIFE，会挂到 `window.QQScopeEngine`）。

## 2. boot JSON schema（前端已兼容解析）

```json
{
  "generated": "2026-10-01 12:00:00",
  "mode": "offline-snapshot",
  "settings": { "data_root": "...", "port": 15555,
                "ai": { "provider": "deepseek", "base": "api.deepseek.com/v1", "key": "", "model": "deepseek-chat" } },
  "accounts": [{ "account_qq": 1605289411, "label": "主号", "source": "pack",
                 "contacts": 12, "messages": 271200, "self_messages": 2856,
                 "first_ts": 1785846557, "last_ts": 1790000000 }],
  "contacts": [{ "account_qq": 1605289411, "kind": "c2c", "peer_id": "u_xxx", "peer_qq": 3246197489,
                 "name": "张三", "remark": "同桌", "avatar": null,
                 "msg_count": 658, "self_count": 121, "first_ts": 0, "last_ts": 0, "last_text": "" }],
  "messages": [{ "t": 1786752000, "d": 1, "k": "c2c", "p": "u_xxx", "x": "你好",
                 "account_qq": 1605289411, "n": "我" }]
}
```

- `messages` 是扁平数组（引擎风格 `{t,d,k,p,x}` + 路由字段 `account_qq`、`n`）；`--embed 0` 时可为空数组，此时总览图表依赖后端 `/api/report`。
- 有内嵌 `messages` 时，前端会用 `QQScopeEngine.computeReport` 本地计算情绪曲线 / 活跃时段 / 每日 / 高频词，离线单文件也能用。
- `peer_id` 可能不是数字（私聊可能是 uid `u_xxx`）；名字取 `remark || name || peer_qq || peer_id`。

## 3. 后端接口依赖

> v4 之后新增的接口（media / voice / live / framework / send / feeds / avatar / logs …）尚未回写
> `docs/SPEC-重构接口.md`，以本表与 `server/routes_*.py` 为准。

| 页面 / 模块 | 用到的接口 |
| --- | --- |
| 全局 | `GET /api/health`、`GET /api/accounts`、`GET /api/logs`、`GET /api/settings` |
| 连接门（v10） | `GET /api/login/status`（404 时退回 `GET /api/framework/status`）、`GET /api/framework/qrcode`、`POST /api/framework/start`、`POST /api/framework/stop`、`POST /api/live/start`、`POST /api/live/stop` |
| 数据源 | `GET /api/sources`、`POST /api/sources/{pack\|bot\|qzone}/probe`、`POST /api/sources/{pack\|bot}/sync`、`GET /api/progress/{id}`、`POST /api/sources/pack/rescan-media`、`GET /api/data/quality` |
| 会话 | `GET /api/contacts`、`GET /api/messages`、`PATCH /api/contacts`、`GET /api/contact`、`GET /api/avatar?qq=`、`GET /api/avatar/group?group=`、`GET /api/profile` |
| 媒体 | `GET /api/media/{msg_id}`、`GET /api/media/stats`、`GET /api/media/pending`、`POST /api/media/backfill`、`GET /api/media/backfill/status`、`POST /api/media/backfill/stop` |
| 语音 | `POST /api/voice/transcribe`（需 `confirm:true`）、`GET /api/voice/progress`、`GET /api/voice/stats`、`POST /api/voice/official/sync`、`GET /api/voice/official/status`、`GET /api/voice/official/stats` |
| 导出 | `POST /api/export`、`GET /api/export/list`、`GET /api/export/download?job=`（zip）、`...&name=<file>`（单文件） |
| 总览 | `GET /api/overview?account=`、`GET /api/report?account=` |
| 动态 | `GET /api/feeds?account=&limit=&pos=`、`POST /api/sources/qzone/sync` |
| 发消息 | `POST /api/send`（必须 `confirm:true`）、`GET /api/send/history` |
| 框架终端 | `GET /api/framework/logs?limit=&offset=&q=`、`GET /api/framework/status` |
| 实时采集 | `GET /api/live/status`、`POST /api/live/focus`、`GET /api/live/events?account=&since=&since_id=` |
| AI | `POST /api/ai/chat` |
| 设置 | `GET /api/settings`、`POST /api/settings` |

所有请求只走相对路径 `/api/...`；后端未就绪时页面显示「接口未就绪 / 后端未就绪」横幅与空状态，不会白屏。
带回退的接口：总览优先 `/api/report`，缺了降级 `/api/messages` → 内嵌快照 → 空状态提示（无情绪分时隐藏曲线，不画假平线）；
`GET /api/sources` 有 30 秒缓存 + 10 秒前端超时；`/api/media/*` 本地缺失返回 404 + JSON；`/api/media/stats`、`/api/voice/stats` 必须带 `account=`。

## 4. 页面清单（当前导航）

导航顺序：**总览 / 会话 / 导出 / 动态 / 数据源 / AI 解读 / 设置**（hash 默认落地总览）。

- **连接 QQ（连接门）**：不占用导航项，`body.gate-mode` 下独占整页。加载后判后端登录态：已登录直接进主界面（门内为空、无二维码）；未登录 + 框架在跑显示二维码（3s 刷新）；框架未运行引导启动；接口不可用重试 2 次后进主界面 + 顶部横幅。该状态下 `renderRoute()` 直接返回，**不发任何数据请求**。
- **总览**：KPI ≥6 张卡（总消息 / 自己发的 / 私聊 / 群聊 / 联系人 / 时间跨度）+ 情绪曲线 canvas + 活跃时段 canvas + 每日明细 / 社交对象 / 高频情绪词 + 媒体命中率卡 + 图片补下载（启动 / 停止 / 进度）。
- **会话**：账号 + 会话列表（搜索、私聊 / 群聊 / 其他分组、内联 SVG 与 `/api/avatar` 头像、消息数、最后时间、媒体摘要），右侧气泡预览（最近 200 条 + 加载更多 / 滚动到顶自动加载），内联备注改名（`PATCH contacts.remark`）；资料卡只在点头像 / 名字 / `#btnShowCard` 时打开；自动刷新（15s 可配）+ 新消息胶囊 + 「最后更新 HH:MM:SS」；切会话时 `POST /api/live/focus`，focus 会话轮询缩到 ≤5s。
- **导出**：会话多选（搜索 / 类型筛选 / 全选 / 全不选 / 反选 / 全选私聊 / 全选群聊）+ 时间段 chips 与自定义起止 + HTML/TXT/MD 多选 + 每人一个 / 合并 + 结果表格（单文件下载 / 全部打包 zip）+ 历史导出。
- **动态**：QZone 时间线卡片（头像 + 昵称 + 时间 + 正文 + 图片 + 点赞 / 评论数）；拿不到登录态时显示「需要先连接机器人框架获取登录态」。
- **数据源**：默认折叠摘要（可展开），窗口 A（扫描 / 导入 / 进度 / 结果摘要 / 密钥徽章）、窗口 B（OneBot 地址 + Token、测试连接 / 拉取、登录账号 / 好友 / 群）、三态徽章「已就绪 / 未配置 / 报错」+ 最后检测时间 + 超时点击重试；**控制台**双标签（运行日志 / 框架 NapCat 终端）。
- **AI 解读**：范围可选，发送前在 `<pre id="aiPreview">` 显示将要发送的内容预览，经 `/api/ai/chat` 代理。
- **设置**：数据根目录、AI provider/base/key/model、3D 背景开关、测试连接、端口与运行信息。

## 5. 本地自测

见 `web/selftest/README.md`。一键：

```powershell
powershell -ExecutionPolicy Bypass -File web\selftest\run_edge.ps1
```
---

## 6. v10 · 3D 环绕背景素材（不入库）

登录页/主界面的 kei 全息背景素材来自参考项目 `kei-showcase`，用脚本复制，**不进 git**（`web/assets/` 已在根 `.gitignore` 排除）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\fetch_assets.ps1
# 或指定源目录： -Source "D:\用户\下载\work1\kei-showcase"
```

复制到 `web/assets/vendor/`：

| 文件 | 用途 | 大小 |
|---|---|---|
| `three.min.js` | three.js r 本地运行时（禁止 CDN） | ~0.65 MB |
| `GLTFLoader.js` | 解析 `.vrm`（glTF/GLB） | ~0.11 MB |
| `kei.vrm` | 背景模型（VRM，GLTFLoader 直接加载） | ~43 MB |

- 前端运行时才按需 `<script>` 注入（静态 HTML 里没有 `<script src=`），`file://` 打开 dist 或素材缺失时静默降级到 CSS `.bg-glow`。
- 后端需把 `web/assets` 挂到 `/assets`（`GET /assets/vendor/kei.vrm`），模型走异步加载 + 进度条，不阻塞首屏。
---

## 7. v10 · 连接 QQ（扫码门）+ 3D 环绕背景

### 连接门（不是二次登录）
QQScope 没有自己的账号体系，**唯一一次扫码 = NapCat 登录 QQ**。启动流程：

1. 加载 → `GET /api/login/status`（404 时退回 `GET /api/framework/status`）
2. `logged_in:true` → **直接进主界面**，DOM 里不渲染二维码（门的 innerHTML 为空）
3. `logged_in:false` + 框架在跑 → 显示二维码 `GET /api/framework/qrcode?t=<mtime>`（3s 刷新）
4. 框架没启动 → 引导「启动框架」/ 双击 `①启动机器人框架.bat`
5. 接口失败 → 重试 2 次 → 仍失败进主界面 + 顶部「未连接框架」横幅

连接门只在 `body.gate-mode` 下显示；该状态下 `renderRoute()` 直接返回，**不发任何数据请求**。
进入主界面后才 `POST /api/live/start` 并拉 `/api/accounts`、`/api/sources` 等。

### 退出登录（只清认证键）
- 主界面「退出登录」→ 二次确认 → 只 `removeItem('qqscope_authed')` / `removeItem('qqscope_authed_nick')`
  （主题、备注、peer 名等 localStorage 全部保留）→ `POST /api/live/stop` → 回连接门
- 连接门「退出并停止框架」→ 二次确认 → `POST /api/framework/stop`（只停本服务启动的 PID）

### 3D 环绕背景
- 素材本地：`/assets/vendor/{three.min.js,GLTFLoader.js,kei.vrm}`（43MB，`.gitignore` 排除，`scripts/fetch_assets.ps1` 一键复制）
- 运行时 `fetch` + 间接 `eval` 加载脚本（静态 HTML 无 `script src`，无外链），`GLTFLoader` 异步加载模型 + 进度条
- 连接页默认开、主界面默认关（`qqscope_holo_login` / `qqscope_holo_main`）；设置页与连接页角落可切换
- 降级：WebGL 不可用 / 素材缺失 / 加载失败 / `prefers-reduced-motion` / 帧率连续低于 24fps → 静默回退 CSS `.bg-glow`（`body.holo-fallback`），不白屏
- 页面隐藏暂停渲染；窗口 resize 自适应

### 自测
- `node web/selftest/check_v10.mjs web/index.html`（源码 37 项；带 DOM dump 参数可到 44 项）
- `node web/selftest/v10_server.mjs <port> <target> <builtHtml> <outDir> <assetsDir>`：静态 + `/assets` + `/api` 反向代理
- `node web/selftest/v10_cdp.mjs --url ... --out ... [--wait expr] [--eval js] [--settle ms]`：真实时间无头 Edge（等模型就绪 / 注入点击 / 截图 / 收集 `data-js-errors`）
- 截图见 `docs/截图/v10-*.png`

---

## 8. v3–v9 前端改动归属（供回归定位）

| 版本 | 主题 | 断言脚本 | 主要新增 |
| --- | --- | --- | --- |
| v3 | kei 全息 HUD 换肤 + 私聊头像修复 | `check_v2.mjs`（含 v2 契约红线）、`check_v3.mjs` | kei 令牌逐字、直角 3px、发丝描边、`.bg-glow`/`.scanlines`、浅色纸感、`pidOf()` |
| v4 | 媒体渲染 | `check_v4.mjs` | `parseMedia`/`mediaHTML`、灯箱、`<audio>`、文件卡片、`media-badge`、`/api/media/*`、命中率卡 |
| v5 | 语音文字为主 / 音频为辅 | `check_v5.mjs` | `voice_text`/`voice_lang`/`voice_engine`、`.voice-text`、`.voice-play`、未转文字占位、末尾懒探测 |
| v6 | 名片 / 小程序卡片 + 资料卡不自动打开 + 媒体性能 | `check_v6.mjs` | `.media-card`、`prettyCardTitle`、`m-sys`、`selectConversation` 不 openDrawer、`data-media-src` + lazy |
| v7 | QQ 表情 + 运行日志控制台 + 数据源状态 | `check_v7.mjs` | `QQ_EMOJI`（≥100）、`renderTextWithEmoji`、`#logPanel`/`#consoleFab`、`/api/logs`、`fmtHMS`、三态徽章 |
| v8 | 自动更新 + 数据源折叠 + 图片补下载 | `check_v8.mjs` | `startAutoRefresh`、增量 `since=`+`order=ASC`、`#newMsgPill`、`#srcSummary`/`#srcDetail`、`/api/media/backfill*` |
| v9 | 总览首页 + 框架终端 + 发消息 + 导出时间段 + live focus | `check_v9.mjs` | 导航顺序与默认总览、`#logTabs` 框架标签、`/api/send`（dry_run + 428/403）、`#expRange`、`/api/live/focus`、群聊发言人头像修复 |
| v10 | 3D 环绕背景 + 连接门 | `check_v10.mjs` | 见第 6–7 节 |

运行方式：`node web/selftest/check_vN.mjs`（源码级）；追加 DOM dump 文件路径参数可加 DOM 断言。
