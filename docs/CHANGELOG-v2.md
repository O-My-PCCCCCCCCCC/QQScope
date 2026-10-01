# QQScope 变更记录（v2 → v10 + 便携打包）

> 本文件是**已提交到仓库**的版本变更记录，内容与项目记忆
> `.dsh/skills/qqscope/SKILL.md` 第 7–10 节保持一致（`.dsh/` 不进仓库，故此处留一份公开副本）。
>
> 范围：2026-10-01 从 v2 重构一路做到 v10 + 便携打包。每条含「做了什么 / 关键坑与教训 / 实测证据路径」。
> 事实来源：代码 docstring、`web/selftest/check_v*.mjs`、`web/selftest/REPORT*.md`、
> `data/decrypt/1605289411/SYNC_REPORT.md`、`data/framework/FRAMEWORK_REPORT.md`、
> `tools/asr/_run/VOICE_REPORT.md`、`docs/SPEC-重构接口.md`，以及只读取自 `http://127.0.0.1:15555` 的真实接口。
>
> 未核实项一律标注「未核实」，不做推测。

## 1. v2 → v10 演进记录（2026-10-01 追加，从代码 / 自测脚本 / 实测报告核实）

> 本节是**追加记录**，不改上面的历史。版本号 = 「用户一轮反馈 → 一次前后端大版本」。
> 证据优先级：代码 docstring / 自测断言脚本 / 实测报告 > 截图 > 口述。
> 版本号与 `docs/SPEC-重构接口.md` 章节的对应：**第 8 章 = v2**（重构基线 + v2.1 增量）、
> **第 9 章 = v3**（kei 全息 HUD）、**第 10 章 = v4**（媒体）；v5 之后 SPEC 未再补章节。
> `web/selftest/check_v2.mjs` 守的是「v2 契约红线 + v3 皮肤断言」，`check_v3..v10.mjs` 各自守当版新增面。

### v2 · 重构落地 + v2.1 增量（两个读取窗口 / 统一主库 / 多对象导出）

**做了什么**

- 架构定型：窗口 A 离线数据包（nt_msg.db 解密解析）+ 窗口 B 在线机器人框架（OneBot）→ 汇聚 `core/store.py` 统一主库 → Web 主页（FastAPI :15555 + 单文件前端）→ 多对象批量导出 HTML/TXT/MD + zip。
- 新增 `core/paths.py`（统一路径、便携化）、`core/store.py`（accounts/contacts/messages）、`core/sources/pack_source.py`、`core/sources/bot_source.py`、`core/export.py`、`server/app.py`、`web/index.html`（零依赖原生 JS）、`scripts/build_web.py`、`scripts/verify.py`、`docs/SPEC-重构接口.md`（接口冻结）。
- v2.1 增量（用户 6 条反馈）：dashboard-layout 深色 SaaS 换肤（**后来被 v3 作废**）、会话显式分「私聊 / 群聊 / 其他」三组、服务端头像缓存 `/api/avatar` + `/api/avatar/group` + `/api/profile` + `/api/contact`、NapCat 重建（落到 `tools/napcat/`）、QQ 动态（`/api/feeds` + `core/sources/qzone.py`）、导航扩展。

**关键坑 / 教训**

- `server/reader.py` 里的 uv 绝对路径 `E:\06-开发环境\...` 已不存在 → 旧「自动刷新」必挂；新架构改用 `core/sources/pack_source.py`。
- 本机有 Windows 系统代理，httpx 默认 `trust_env=True` 会劫持 127.0.0.1 → 所有本机 OneBot/接口请求必须 `trust_env=False`。
- 后端必须用 `tools\nt_msg_db_util\.venv\Scripts\python.exe`（3.14.7，含 fastapi/httpx/sqlcipher3）；dsh 的 3.12 runtime 没有 httpx。
- **密钥换代**：QQ 9.9.32 → 9.9.36 后旧 `data/keys` 失效；新密钥**同时**能开 profile_info.db / group_info.db / misc.db（v2 才发现的）——昵称/群名补全因此可行。
- 构建注入占位符必须把 `/*__QQSCOPE_BOOT__*/ null` 整段吃掉：只替换注释 token 会残留 `null` → 语法错 → `window.QQSCOPE` 未赋值 → 渲染脚本首行抛异常、后续全部失效（v1 经典 bug，v2 仍列为红线）。
- `40013` 方向语义（官方 db_docs）：0=对方，1/2=自己发送（含特殊类型），3=系统，4/5 极少特殊；**统一按 `sender_qq == 本账号 → direction=1` 写库** 比只看 40013=1 更准。
- 第三方 `tools/nt_msg_db_util/msgdb/c2c/parser.py` **没有 content_type=4 分支**（语音落到兜底变成空文本）——`tools/` 禁改，分类逻辑必须写在自己的代码里。

**实测证据**：`docs/SPEC-重构接口.md` 第 8 章、`data/decrypt/1605289411/SYNC_REPORT.md`、`data/framework/FRAMEWORK_REPORT.md`、`web/selftest/check_v2.mjs`、`docs/截图/v2-旧版/`。

### v3 · 全息 HUD 换肤（kei-showcase）+ 私聊头像修复

**做了什么**

- 用户第二次否 UI，指定参考 `D:\用户\下载\work1\kei-showcase`（「凯伊 | KEI 全息档案」）→ **v2 的「圆角 SaaS + 靛蓝」方向作废**。
- 逐字照搬 kei 令牌：`--bg-0:#05080f` / `--bg-1:#0a1120` / `--bg-2:#101a2d` / `--cyan:#5ad2ff` / `--cyan-dim:#2f9fd0` / `--ice:#cdefff` / `--violet:#8b7dff` / `--magenta:#ff6bd6` / `--line` 系。
- 十条风格要素：直角 `3px`（禁 8/10/12/14/16px）、发丝描边 `1px solid var(--line)`、11~12px 大写字距英文眉标、等宽数字 `tabular-nums`、辉光 `0 0 16px rgba(90,210,255,.18)`、扫描条激活态（左缘 cyan + 横向渐变）、氛围层 `.bg-glow` + `.scanlines`、`masthead / rail / console` 结构、中文导航 + 英文眉标、主色 cyan（violet/magenta 只点缀）。
- 浅色纸感主题 `[data-theme="light"]`（`--paper:#f2f3f0` / `--ink:#202726` / `--sage:#5f7974`），选择存 `localStorage`（`qqscope_theme`）。
- 修「私聊头像不显示」：私聊 `peer_id` 是 NT UID（`u_...`）非数字，`/^[0-9]+$/` 判断失败 → 全部退化成首字圆圈。统一改用 `pidOf(c)`：群聊取 `peer_id`，私聊取 `peer_qq`；并加 `QQAvatarErr` 首字兜底。

**关键坑 / 教训**

- NT UID 不是数字，任何「peer_id 当 QQ 号」的写法都会静默降级。
- UI 方向以**用户指定的参考项目**为准，不要自己发明风格；换肤要冻结令牌、写进断言脚本防回退。

**实测证据**：`docs/SPEC-重构接口.md` 第 9 章、`web/selftest/check_v2.mjs`（kei 令牌逐字红线）、`check_v3.mjs`、`run_v3.ps1`、`docs/截图/v3-{暗色,浅色}-*.png`。

### v4 · 媒体管线（图片 / 语音 / 视频 / 文件 / 表情 / 卡片）

**做了什么**

- 查清 `content_type` 语义表并落进 SPEC 第 10 章：1 文本 / 2 图片 / **4 语音 PTT** / 5 视频 / 6 视频表情 / 7 @提及 / 8 名片 / 10 小程序卡片 / 11 系统 / 16 旧协议合并转发；md5 解码规则（`md5_raw` base64→hex、`f45424` base64→32 位 hex、`thumbnail`/`f45421` base64→16 字节→hex）。
- `core/store.py` 的 messages 表新增 `content`（原始结构化 JSON）与 `media`（归一化 JSON）两列，幂等 `ALTER TABLE` 自动迁移。
- `core/media.py` 扫 `<data_root>/<qq>/nt_qq/nt_data` 建 md5→路径索引（Ptt/Pic/Video/File/Emoji，缓存 5 分钟），`file` 一律写相对 nt_data 的 POSIX 路径；`media.duration` 单位秒。
- 接口：`GET /api/media/{msg_id}`（响应头 `X-Media-Kind` / `X-Media-Source: cache|missing`；本地没有 → **404 + JSON，绝不 500**）、`/api/media/stats`、`/api/media/pending`、`POST /api/sources/pack/rescan-media`（只重扫媒体列）。
- 前端媒体渲染器：`parseMedia` / `mediaHTML`，图片灯箱（`QQLightboxOpen/Close`）、`<audio controls preload="none">`、视频缩略图、文件卡片、`media-badge` 占位徽章（`·未缓存`）、`QQMediaErr` 替换破图、`IntersectionObserver` + `data-media-probe` 懒探测、总览「媒体命中率」卡。
- 导出：命中媒体复制到 `data/export/<job>/media/`，HTML 用**相对路径**引用；缺失渲染占位徽章不破图；TXT `[图片]` / `[语音 3秒]` / `[文件: x.zip (34.4 MB)]`；MD `> 🖼 图片` 等；`opts["media_max_mb"]` 默认 500，超了标 `truncated: true`。

**关键坑 / 教训**

- QQ 会定期清理本地缓存 → 命中率天然低（实测图片尤其低）；语音/视频缓存命中率接近 100%。「本地没有」是常态，必须优雅降级，不能破图/报错。
- 媒体一律读本地 nt_data 缓存，**不直连腾讯 CDN**；token/cookie 不落日志。

**实测证据**：`docs/SPEC-重构接口.md` 第 10 章、`web/selftest/check_v4.mjs`、`run_v4.ps1`、`docs/截图/v4-媒体-*.png`。

### v5 · 语音转文字（本地 ASR，文字为主 / 音频为辅）

**做了什么**

- `core/voice.py`：把 `messages.media(kind='voice', file)` 送 `tools/asr/.venv` 的 `worker.py` 子进程（pilk 解 SILK → 16k WAV → numpy → faster-whisper **small int8**），结果写回 media JSON：`voice_text` / `voice_lang` / `voice_engine` / `voice_status`。
- 多进程并行（按时长贪心分片，worker 独立子进程），父进程边跑边读 JSONL 写库，天然断点续跑（`voice_status='ok'`/`empty` 跳过）；失败如实分类 `missing / decode / asr / empty`，绝不把失败算成功。
- 接口：`POST /api/voice/transcribe`、`GET /api/voice/progress`、`GET /api/voice/stats`。
- 前端：文字为主（`.voice-text` 可选中复制）+ 音频为辅（小播放按钮 `.voice-play`，点击才展开 `<audio preload="none">`）；未转文字占位标签（`·未转文字`）、非中文提示（`voice_lang` 判定）、404 禁用按钮 + `voice-missing`；列表最后一条懒探测（`lastProbeN >= 60` 才做，避免 120 会话各发一次请求）。

**关键坑 / 教训（重要）**

- **事故**：一次性脚本 `tools/asr/full_run.py` 开头把全部 voice 行的 `voice_status/voice_text` 重置 → 已完成的 ~500 条在 store 里被清空；靠 `tools/asr/_run/out_*.jsonl`（110 个结果文件）按时间合并恢复了 512 条。**教训：批量重跑脚本绝不允许直接清状态**（`core/voice.py::run()` 本身是幂等的）。
- 加了安全阀：`core/voice.py::run()/transcribe_one()` 默认拒绝执行，必须 `confirm=True` 或环境变量 `QQSCOPE_ASR_ALLOW=1`；`tools/asr/_run/asr.lock` 写 PID 互斥；`POST /api/voice/transcribe` 必须带 `{"confirm": true}`，否则 409；CLI 要 `--yes`。
- 性能实测：A 旧配置（4 worker × 8 线程）1.66× 实时 vs B 新配置（16 worker × 2 线程 + VAD 预筛 + 去时间戳）2.83×；本机 Xeon E5-2673 v4 全量 8.10h 音频 ~7.5× 实时（~65 min）——**<25 分钟目标在这台机器上达不到**，瓶颈是 whisper-small int8 吞吐，不是配置。截断 >120s 音频到 90s（62 条占 70% 音频）可压到 ~31 min，但仍到不了 25 min。
- VAD 快速预筛：解码后算语音占比，`< 0.12` 直接记 `empty` 不进 whisper；实测误杀 0/20、拦下长音乐 19/20（语音 0.59~1.0 vs 音乐 0.0~0.083，分界干净）。
- 要给用户的结论：转写是**一次性离线任务**，文字存在 `data/qqscope.db` 的 `messages.media.voice_text` 里；别的设备只读文本，**不需要 ASR / 模型 / 显卡 / Python 环境**，拷库即可。

**实测证据**：`tools/asr/_run/VOICE_REPORT.md`、`web/selftest/check_v5.mjs`、`run_v5.ps1`、`docs/截图/v5-语音文字-*.png`。

### v6 · 名片 / 小程序卡片 + 「资料卡不自动打开」+ 媒体性能

**做了什么**

- `content_type` 8（名片）/ 10（小程序/JSON 卡片）渲染成 `.media-card`（`mc-eyebrow` / `mc-title`），`prettyCardTitle` / `isPackageName` 把 `com.tencent.*` 包名美化成人话；会话摘要（`mediaSummary`）也走同一套美化；系统消息 `m-sys` 居中。
- 修「资料卡（dossier）总是自己弹出来」：`selectConversation()` 内主动 `closeDrawer()`、**绝不调用 `openDrawer`**；只有 `#btnShowCard` 显式打开；不再用 localStorage 记资料卡开关。
- 媒体性能：图片/视频改 `data-media-src` + `loading="lazy"`（不再直接把 src 写死）；`IntersectionObserver` 双保险且 `rootMargin ≤ 300px`（`MEDIA_ROOT_MARGIN`）；音频一律 `preload="none"` 不在渲染时预加载。

**关键坑 / 教训**

- 抽屉/资料卡不能跟着选中会话自动弹（用户明确讨厌）；自动打开类交互要默认关闭、只响应显式点击。
- 120 个会话的列表渲染必须靠懒加载，否则一次请求 120 个媒体探测，页面直接卡死。

**实测证据**：`web/selftest/check_v6.mjs`、`run_v6.ps1`、`docs/截图/v6-名片卡片.png` `/ v6-小程序包名美化.png / v6-资料卡不自动打开-*.png / v6-语音进度.png`。

### v7 · QQ 表情渲染 + 运行日志控制台 + 数据源状态 + 同步可视化

**做了什么**

- 前端 `QQ_EMOJI` 映射表（≥100 词，pack 源解析出的**中文表情名** → Unicode emoji）+ `emojiToText` / `renderTextWithEmoji`：**只替换命中项，未命中保留原样**，不猜；消息气泡和列表摘要都走。
- `core/qq_face.py`：处理 bot 源的 CQ 码 `[CQ:face,id=N]`，把 QQ 经典表情 faceIndex → emoji；只收录**确认过**的 id，拿不准返回 None，调用方退回 `[表情N]`。两套表（前端中文名 / 后端 faceId）互不冲突。
- 运行日志控制台面板：`#logPanel` / `#consoleFab`，数据来自 `GET /api/logs?limit=200`（level 过滤、清屏、滚到底）；接口未就绪显示「日志接口未就绪」不白屏。
- 数据源状态真实化：最后检测时间 `fmtHMS`、三态徽章（已就绪/未配置/报错）+「检测中…」加载态、10 秒后自动重试一次、窗口 B 显示真实登录账号/好友数/群数/OneBot 地址/上次拉取、按钮进行中文案（扫描中…/拉取中…/探测中…/导入中…）。
- 同步可视化（进度 + 状态）。

**关键坑 / 教训**

- pack 源的「中文表情名」与 bot 源的「CQ face id」是两套完全不同的东西，不要试图合并成一张表。
- 表情表只放确认过的映射；猜错比不渲染更糟。

**实测证据**：`web/selftest/check_v7.mjs`、`run_v7.ps1`、`docs/截图/v7-表情渲染.png / v7-控制台面板.png / v7-数据源状态.png / v7-同步状态.png / v7复核-*.png`。

### v8 · 聊天自动更新 + 数据源折叠 + 图片补下载

**做了什么**

- 会话自动更新：`autoRefresh: true, refreshSec: 15`（可配，存 `qqscope_refresh_sec`），`startAutoRefresh` / `stopAutoRefresh` / `autoRefreshTick`；增量拉取用 `since=max_ts` + `order=ASC`，`appendNewMessages` 只追加不重建 DOM；新消息胶囊 `#newMsgPill` 点击滚到底；`visibilitychange` 页面隐藏暂停；「最后更新 HH:MM:SS」+ 手动刷新。
- 数据源页折叠：`#srcSummary` / `#srcDetail hidden`（默认折叠 `_srcDetailOpen=false`，`toggleSrcDetail`）；`/api/sources` 30 秒缓存 + 前端 10 秒超时（`apiGetTimeout`），超时显示「检测超时，点击重试」（`btnSrcRetry`）——**禁止无限 spinner**；自动同步总开关 `#autoSyncOn` + 一键刷新全部 `btnRefreshAll`。
- 图片补下载：前端 `startBackfill` / `stopBackfill` / `renderBackfill`（已补 / 成功 / 失败），后端 `POST /api/media/backfill` + `GET /api/media/backfill/status` + `/stop`；总览页补下载卡（`#mediaStatsCard` / `#btnBackfill`）。

**关键坑 / 教训（核心结论，别走回头路）**

- NapCat 的 `get_image` / `get_record` / `get_file` **只接受两种参数**：① `msgId`+`elementId` 的编码 token（QQ 内部 ID，pack 数据里没有）；② NapCat 本地缓存里的文件名（但文件正是缺失的那个，必然 not found）。→ **「只靠 get_image」拉不回已被清理的历史媒体**（与最初设想不同，已如实上报）。
- 真正可用的路径：**`nc_get_rkey` 拿腾讯多媒体下载 rkey**（ttl ~57 分钟，`type 10` 私聊 / `20` 群）+ pack 的 `messages.content` 里本来存的 `fileid`（缺失图片 **97.7%** 都有）+ 拼 `https://multimedia.nt.qq.com.cn` + `cdn_url` + rkey 直链下载；`get_image` 只作兜底，成功了才记 `method=get_image`。
- rkey / token 都是凭据：只用于拼 URL，**绝不写日志、绝不进 API 响应**。

**实测证据**：`core/media_fetch.py` docstring（实测结论）、`web/selftest/check_v8.mjs`、`docs/截图/v8-图片补下载.png / v8-数据源折叠.png / v8-群聊头像.png / v8-自动更新.png`。

### v9 · 总览首页 + 框架终端 + 网页发消息 + 导出时间段 + live focus + 群聊头像修复

**做了什么**

- 导航顺序改为 `总览 / 会话 / 导出 / 动态 / 数据源 / AI / 设置`，hash 默认落地总览（`PAGES[0]="overview"`，`parseHash() || "overview"`）。
- 控制台双标签（运行日志 / **框架(NapCat)**）：`GET /api/framework/logs?limit=300&offset=` 增量读 + `GET /api/framework/status` 状态条。`core/framework_log.py` 清洗 ANSI 颜色码与 CRLF、只读尾部 256KB、单次最多 2000 行、offset 只在整行之后推进、超 20MB 只给提示；所有对外文本过 `scrub()`（token / 二维码 `k=` 参数脱敏）。
- 网页发消息（写操作）：`POST /api/send` 必须 `confirm: true` 否则 **428**；同一会话 3 秒最多 1 条、全局 30 秒最多 10 条；单条 ≤ 2000 字符；策略默认「只允许发给自己、群聊默认禁止」（`allow_others` / `allow_groups` / `dry_run`），违规 **403 policy_blocked**；前端二次确认弹窗（群聊额外提示「N 人会看到」）+ `dry_run` 测试通道（不真发）+ 428/403 人话处理。发送内容进 `/api/logs`（source='send'）可自查。`core/send.py` 明令：**绝不群发 / 批量 / 定时 / 自动重试，前端不得 setInterval 调 doSend**。
- 导出增强：时间段 chips（最近 7 天等）+ 自定义 `expSince` / `expUntil` + 起止校验（「开始日期不能晚于结束日期」）；导出列表按私聊/群聊分组（`expRowHTML` / `exp-group-head`）+ 全选私聊/全选群聊；`opts.since` / `opts.until` 传给后端。
- **live 增量采集**（`core/live_sync.py`，轮询版）：focus 会话 5s / 最近活跃 top20 30s / 其余 10 分钟；请求间隔 ≥ 0.16s、单轮 ≤ 60 个会话请求、连续失败指数退避、只读历史接口。接口 `GET /api/live/status|events`、`POST /api/live/focus|start|stop`。前端切会话就 `POST /api/live/focus`、focus 时轮询缩到 ≤5s（`effectiveRefreshSec`）、live 不可用回退 15s 轮询。
- 框架登录门后端：`/api/framework/{logs,status,qrcode,start,stop}` + `/api/login/status`。**只停本服务启动的 PID**：`data/framework/spawned.json` 认领 + 校验「PID 在跑 + 是 node.exe + 命令行含 napcat」，三缺一不杀（跨后端重启也能认领）。
- 修群聊发言人头像：发言人用 `c2c` + `sender_qq`（`/api/avatar?qq=<sender>`），不再把群头像当发言人；气泡里不得出现 `/api/avatar/group` 请求。

**关键坑 / 教训**

- 写操作三件套：二次确认 + 限速 + 可审计（内容写 /api/logs）。默认策略保守（只发给自己、群聊禁止）。
- **绝不 kill 不是自己启动的框架进程**（校验 PID 身份，宁可不杀）。
- 群聊里「发言人」和「会话主体」是两个人，头像 URL 别搞混。

**实测证据**：`web/selftest/check_v9.mjs`、`server/routes_send.py`、`core/live_sync.py`、`core/framework_log.py`、`docs/截图/v9-*.png`。

### v10 · 3D 环绕背景 + 连接 QQ 扫码门

**做了什么**

- **连接门（不是二次登录）**：QQScope 没有自己的账号体系，唯一一次扫码 = NapCat 登录 QQ。流程：加载 → `GET /api/login/status`（404 时退回 `GET /api/framework/status`）；`logged_in:true` → **直接进主界面**，门 innerHTML 为空、不渲染二维码；未登录 + 框架在跑 → 显示二维码 `GET /api/framework/qrcode?t=<mtime>`（3s 刷新）；框架没启动 → 引导启动框架；接口失败重试 2 次（`fetchWithRetry(2)`）仍失败 → 进主界面 + 顶部「未连接框架」横幅。
- `body.gate-mode` 下 `renderRoute()` 直接 return → **连接页不发任何数据请求**；进入主界面才 `POST /api/live/start` 并拉 `/api/accounts`、`/api/sources`（`startAppData`）。localStorage 的 `qqscope_authed` 只做加速，**登录态以接口为准**。
- 退出登录：只 `removeItem('qqscope_authed')` / `removeItem('qqscope_authed_nick')`（主题、备注等全部保留，**没有 localStorage.clear()**）→ `POST /api/live/stop` → 回连接门；门上另有「退出并停止框架」→ 二次确认 → `POST /api/framework/stop`。
- **3D 环绕背景**：本地素材 `/assets/vendor/{three.min.js, GLTFLoader.js, kei.vrm}`（43MB VRM 模型，来自 kei-showcase；`scripts/fetch_assets.ps1` 一键复制；`web/assets/` 已进 `.gitignore` 不入库）。运行时 `fetch` + 间接 `eval` 加载脚本（**静态 HTML 无 `script src`、零外链**），模型异步加载 + 进度条，不阻塞首屏。连接页默认开、主界面默认关（`qqscope_holo_login` / `qqscope_holo_main`），设置页与连接页角落可切换。
- 降级链：WebGL 不可用 / 素材缺失 / 加载失败 / `prefers-reduced-motion` / 帧率连续低于 24fps → 静默回退 CSS `.bg-glow`（`body.holo-fallback`），**不白屏**；页面隐藏暂停渲染（`stopLoop`）；帧率自适应 `lastFps` / `resScale` / `setPixelRatio`。
- 暴露测试接口 `__QQSCOPE_LOGIN__` / `__QQSCOPE_HOLO__`，供无头 Edge CDP 驱动断言。

**关键坑 / 教训**

- 无头 / 无 GPU（SwiftShader）下 3D 必降级：模型 FPS ≈10，看门狗会把 `resScale` 1→0.55 甚至直接关 3D——**这是设计，不是 bug**；连接页首次冷加载模型 `holoReadyMs` 可达 10~13s。
- 连接页绝不能发数据请求（否则会打真实后端 / 空转）；「已登录时不应出现此页」是硬约束。
- 「待扫码」截图只能走 `?gate=1&loginDemo=qr` 演示参数（现场 NapCat 已登录，无法在不掉线前提下展示真实未登录）；二维码图片取真实 `/api/framework/qrcode`。

**实测证据**：`web/selftest/REPORT-v10.md`、`check_v10.mjs`（源码 37 项 / 带 DOM dump 44 项）、`v10_server.mjs`、`v10_cdp.mjs`、`web/README.md` 第 6–7 节、`docs/截图/v10-*.png`。

### 便携打包（v10 之后 · task-35）

**做了什么**

- `python scripts/package.py` → `dist-package/QQScope-Portable-v2/` + `.zip`（实测 zip **161,309,268 B ≈ 154 MB**）。
- 便携 Python 方案：复制**基础 Python 安装**（`python.exe` + DLLs + 标准库 `Lib`，**不含**庞大的全局 site-packages）+ 把 `tools/nt_msg_db_util/.venv/Lib/site-packages`（fastapi/uvicorn/httpx/sqlcipher3/protobuf/pydantic… ~26MB）整体搬过去。理由：venv 本身**不可重定位**（`pyvenv.cfg` 写死 home），但「基础安装 + 现成依赖目录」可重定位，且完全不需要联网 pip、版本与开发机实测一致。
- 包内容：`python\`、`core\ server\ scripts\ app\ web\`（不含 `web/selftest`）、`tools\napcat\`（~325MB，剔除 `logs`/`cache`）、`tools\nt_msg_db_util\`（不含 `.venv`）、**空 `data\`**（keys/decrypt/export/pack/backup/server/framework，不带任何聊天数据以防隐私泄露）、`①启动QQScope.bat`、`②启动机器人框架.bat`、`使用说明.txt`。
- 启动脚本 **GBK + CRLF**；首次运行自动建目录 + 从随包 NapCat 配置读出 token 写进 `data\framework\onebot.json`；端口 15555 已占用时只提示「直接开浏览器」不报错；前端产物缺失才临时构建。
- 便携化改写：包里把 UI 占位符中的开发机绝对路径替换成中性文案（`E:\01-项目\QQScope` → `<包目录>`，`C:\Users\Administrator\Documents\Tencent Files` → 空）；**仓库源文件不动**。`使用说明.txt` 写清数据迁移/备份（拷 `data\`）、token 位置、常见问题（端口占用/二维码过期/密钥失效/缺 python/杀软误报）与封号风险。

**关键坑 / 教训**

- bat 必须 **GBK(936) + CRLF**：UTF-8+LF 会被 cmd 按 GBK 解析 → 整段乱码并当命令执行（v2 NapCat 启动脚本踩过）。
- 直接拷 venv 没用（写死 home）；要搬基础解释器 + 依赖目录。`core/paths.py::python_executable()` 的查找顺序：随包 `python\` → nt venv → 当前解释器。

**实测证据**：`scripts/package.py` docstring、`dist-package/QQScope-Portable-v2.zip`、包内 `使用说明.txt`。

---

## 2. 当前实测快照（2026-10-01 18:34，只读取自 :15555 真实接口）

> 数字会随采集变化；引用时务必写明时间。以下均来自本机运行中的后端（`trust_env=False`）。

- 账号：`1605289411`（昵称 霖ケ，`source=bot`）——`GET /api/accounts`
- 消息总量 **278,386**：自发 **7,771**、私聊 **11,394**、群聊 **266,992**；会话 **128** 个
  （注：SPEC 第 6 章验收线是「≥260,000 行 / 自发 ≥2,800」，现已远超）
- 时间跨度 `first_ts=1782792733`（2026-06-30 12:12:13）~ `last_ts=1790850846`（2026-10-01 18:34:06）
- 媒体命中（hit/miss）：image 11,940 / 57,177、voice 663 / 1、video 917 / 18、file 45 / 394、sticker 1,079 / 4,898、card 0 / 28,084；本地媒体索引 5,379 个文件（Ptt 705 / Pic 1,909 / Video 977 / File 45 / Emoji 3,670）
- 语音：总数 664、本地有 663、**已转文字 523**、failed 107、pending 34、lang_mismatch 15；引擎 `faster-whisper-small-int8`
- 官方语音回填（`fetch_ptt_text`）：official 9 / local 626 / both 6 / official_only 3
- 数据健康度：c2c_contacts 65、distinct_peer_qq 65、duplicate 0、uid_contacts 0 → `healthy: true`
- 框架：NapCat **4.18.28** 在跑（`logged_in: true`，账号 1605289411，端口 3000/6099 开放）
- live 采集：running，peers 128，top_n 20，inserted_total 1,753，间隔 focus 5s / top 30s / other 600s
- QQ 数据根：`C:\Users\Administrator\Documents\Tencent Files`；主号 `nt_msg.db` 约 303 MB（318,555,136 B）

## 3. 已知「文档 ↔ 实现」不一致（2026-10-01 核实，未改实现）

1. **根 README 的密钥顺序不准**：代码实际候选顺序是 `opts.keys` → `data/keys/<QQ>.key` → `paths.LEGACY_KEY_FILE`（默认 `data/keys/legacy.key`，可用环境变量 `QQSCOPE_LEGACY_KEY` 指向 `E:\01-项目\Workspace\qq-export\db_key.txt`）。README 写的 `data/keys/nt_msg_main.key` / `nt_msg_xiaohao.key` **不在代码候选链里**（这两个文件在磁盘上存在，但只有 `<QQ>.key` 会被读）。当前 pack status：`legacy_key_exists=false`，主号实际靠 `data/keys/1605289411.key`（已是新密钥；v2 的旧密钥 `-e<9C+;]2NQ+bNP6` 现存在 `nt_msg_main.key`）。
2. **`docs/SPEC-重构接口.md` 的「接口冻结」只到 v4**：v5–v10 新增的 `/api/voice/*`、`/api/media/backfill*`、`/api/live/*`、`/api/framework/*`、`/api/login/status`、`/api/logs`、`/api/data/quality`、`/api/send*`、`/api/feeds`（第 8.5 节有规划但正文未细列）等都没有补进 SPEC 第 5 节。
3. **`/api/report` 不在 SPEC 里**（Lead 后加，前端总览情绪曲线依赖它；缺它时前端降级并隐藏曲线）。
4. **`web/README.md` 缺 v5–v9 的功能说明**：只有 v10 章节（第 6–7 节）；语音「文字为主/音频为辅」、QQ 表情、自动刷新、数据源折叠、图片补下载、总览首页、框架终端、网页发消息、导出时间段、live focus 都没写。
5. **README「验收」只写了 `verify.py --full`**，没写前端一键 `web\selftest\run_edge.ps1` 与分版本 `check_v2..v10.mjs`。
6. **README 目录结构严重不全**：缺 `web/selftest/`、`web/assets/`（43MB 3D 素材，不入库）、`server/routes_*.py`、`core/` 的 paths/store/export 之外的模块（export 有写，media/voice/send/live_sync/framework_log/normalize/qq_face 等没有）、`scripts/package.py`、`docs/截图/`、`dist-package/`。
7. **README「界面零外部请求」需要限定为「前端页面不直连外部资源」**：产物与源码确实零外链，但运行时 `/api/avatar*` 由服务端抓腾讯 CDN 缓存、QZone 动态的图片 URL 是外部字符串、3D 素材走本地 `/assets/vendor/`（缺失则降级）。
8. **根 `README.md` 是 UTF-8 带 BOM**（本次已改为无 BOM）；文件内还有 1 处 CRLF、其余 LF。
9. **git 仓库 HEAD 停在 2026-08-17 14:04**（最后一次提交是初始化阶段），v2–v10 与便携打包的全部改动**未提交**；与「项目代码已完成」的状态不符（提醒：需要一次提交/推送）。
10. **`启动QQScope.bat` 依赖 `tools\nt_msg_db_util\.venv`**，缺失时只提示 `uv sync`；README「快速开始」没写这个前置。

## 4. 未核实 / 待确认

- WebUI 是否有「点赞 / 评论」动态的完整分页（`/api/feeds` 只验证了列表返回，`next_pos` 语义未实测）。
- `core/normalize.py` 的 uid→QQ 迁移是否已在当前主库执行过（现状 `uid_contacts=0`、`uid_messages=0`，看起来已迁移，但未查执行记录）。
- 便携包在**另一台没有 Python/QQ 的电脑**上的首次启动是否完全跑通（只在本机组装 + 校验了目录结构，未做异机实测）。
- 「退出并停止框架」的真实 stop 调用未在 v10 验收中执行（REPORT-v10 明确说明：只截图二次确认，避免断开现场 NapCat）。
