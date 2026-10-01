# QQScope

> 检测自己的 QQ 聊天记录，分析精神状态（情绪曲线、波动幅度、行为特征、话题漂移等），
> 辅助**自我观察**，**非医疗诊断**。数据全本地，不出本机。

版本 **2.0.0** · 后端 FastAPI `:15555` · 单文件前端 `app/dist/QQScope.html` · 支持便携免安装运行。

---

## 一句话架构

两个「读取窗口」各自采集 → 汇聚到统一主库 → Web 主页统一管理 → 多对象批量导出 HTML / TXT / MD。

```
窗口 A：数据包读取（离线）                窗口 B：机器人框架读取（在线）
 nt_msg.db → 解密 → 解析 → 入库            NapCat / OneBot HTTP → 会话+历史 → 入库
              └──────────────┬──────────────────────────┘
                     data/qqscope.db（统一主库 core/store.py）
                              ↓
              Web 主页（FastAPI :15555 + 单文件前端，零外部请求）
                              ↓
              多对象批量导出 HTML / TXT / MD（+ zip / + 媒体）
```

---

## 功能一览

| 模块 | 说明 |
| --- | --- |
| **两个读取窗口** | A 离线数据包（本机 QQ 数据库解密解析）；B 在线机器人框架（OneBot HTTP 拉好友/群/历史），两路汇聚同一主库 |
| **统一主库** | `data/qqscope.db`，账号 / 联系人 / 消息统一 schema，自动去重（`ux_msg`），旧库自动归档 |
| **总览** | KPI（总消息 / 自己发的 / 私聊 / 群聊 / 联系人 / 时间跨度）+ 情绪曲线 + 活跃时段 + 每日明细 / 社交对象 / 高频情绪词 + 媒体命中率 / 补下载卡 |
| **会话** | 私聊 / 群聊 / 其他分组 + 搜索 + 分段筛选；气泡预览（最近 200 条，滚动到顶加载更早）；内联备注改名；资料卡（点头像/名字/按钮显式打开） |
| **媒体管线** | 图片（灯箱 + 懒加载）、语音播放器、视频缩略图、文件卡片、表情；本地缓存命中率统计；缺失渲染占位徽章，不破图 |
| **语音转文字** | 本地 faster-whisper（文字为主 / 音频为辅 / 未转文字占位），支持 NapCat 官方 `fetch_ptt_text` 回填，失败如实分类 |
| **QQ 表情** | pack 源中文表情名 + bot 源 `[CQ:face,id=N]` 两套映射，只替换确认项，拿不准保留原样 |
| **聊天自动更新** | 会话增量轮询（默认 15s 可配）+ 新消息胶囊；focus 会话由 live 采集器提速到 5s；页面隐藏自动暂停 |
| **实时采集（live）** | 后台线程轮询 OneBot 历史接口增量入库：focus 5s / 活跃 top20 30s / 其余 10min，带限速与退避 |
| **QQ 动态** | QZone H5 接口读取动态（需要登录态 cookie），时间线卡片 + 点赞/评论数；拿不到登录态时明确提示 |
| **框架终端** | 网页内查看 NapCat 运行日志（增量 offset、脱敏）+ 框架状态条（进程/端口/登录态） |
| **连接 QQ（扫码门）** | 启动先判后端登录态：已登录直接进主界面；未登录显示二维码；框架没起引导启动 |
| **网页发消息** | 手动发单条文本，强制二次确认（`confirm:true`，否则 428）+ 限速 + 默认只允许发给自己 / 群聊默认禁止 + `dry_run` 测试通道 |
| **导出** | 多会话多选 + HTML / TXT / MD + 每人一个 / 合并 + 时间段筛选 + zip；媒体复制到 job 内相对引用 |
| **AI 解读** | 本地统计 + 可选 DeepSeek 问答，服务端代理（Key 只在服务端），发送前预览将发送的内容 |
| **3D 环绕背景** | 本地 three.js + VRM 模型（可关），素材缺失 / 无 WebGL / 低帧率自动降级为 CSS 背景 |
| **便携版** | `scripts/package.py` 打出免装 Python 的绿色包（见「便携版」章节） |

---

## 快速开始

双击 `启动QQScope.bat`（自动构建前端 → 起后端 → 打开浏览器）。

或者手动：

```bat
tools\nt_msg_db_util\.venv\Scripts\python.exe server\app.py
```

然后打开 <http://127.0.0.1:15555>。

> **前置**：`tools\nt_msg_db_util\.venv` 必须存在（内含 fastapi / uvicorn / httpx / sqlcipher3）。
> 缺失时 `启动QQScope.bat` 会提示你到 `tools\nt_msg_db_util` 目录执行 `uv sync`。
> 端口写在 `data/server/settings.json` 的 `port`（默认 15555）。

---

## 两个读取窗口怎么用

### 窗口 A · 数据包读取（离线）

读取电脑上 QQ 自己的本地数据库，全离线：

1. 确认 QQ 数据目录（默认 `C:\Users\<你>\Documents\Tencent Files`）
2. 点「扫描」— 找出本机所有带 `nt_msg.db` 的账号
3. 点「导入」— 自动完成：复制库 → 剥离 1024 字节头 → SQLCipher 解密 → protobuf 解析 → 写入主库

**密钥说明**：NTQQ 的数据库是 SQLCipher 加密的，密钥按账号区分，且**QQ 大版本升级后密钥会变**。
本项目按以下顺序找密钥（逐个尝试，谁成功用谁）：

1. 请求里显式传入的 `keys`
2. `data/keys/<QQ>.key`
3. 历史密钥文件 `data/keys/legacy.key`（即 `paths.LEGACY_KEY_FILE`，可用环境变量 `QQSCOPE_LEGACY_KEY` 指向别处）

密钥失效时用 [QQBackup/qq-win-db-key](https://github.com/QQBackup/qq-win-db-key) 的
`research/windows_ntqq_get_key.ps1` 重新提取，把结果写进 `data/keys/<QQ>.key`（16 字符，不要换行）。

> 密钥候选链里**没有** `data/keys/nt_msg_main.key` / `nt_msg_xiaohao.key`；这两个文件只是早期版本的留存，
> 只有当你的账号号正好等于文件名时才会被读到。当前主号用的是 `data/keys/1605289411.key`。

### 窗口 B · 机器人框架读取（在线）

通过 OneBot HTTP 接口（NapCat / Lagrange 等）拉好友、群和历史消息。

本项目内置了可用的 **NapCat v4.18.28 无头一键版**（`tools/napcat/`，自带 QQ 9.9.31 内核与 Node，
绿色独立运行，不会启动 / 关闭 / 注入你正在用的 QQ，也不动 `tools/napcat-win/` 旧目录）：

1. **启动框架**：双击 `①启动机器人框架.bat`，或 `tools\napcat\启动框架.bat`。
   脚本会自动：检测管理员权限 → 从注册表探测本机 QQ 路径（只展示，不启动）→
   自动补齐官方包缺失的 `crypto.dll` / `ssl.dll` → 启动 NapCat 无头模式。
2. **扫码登录（必须你本人操作）**：首次启动会在控制台打印二维码，并保存到
   `tools\napcat\napcat\cache\qrcode.png`；用手机 QQ 扫描并在手机上确认。
   也可以打开 WebUI `http://127.0.0.1:6099/webui?token=<webui_token>` 扫码。
   > 同账号双端提醒：NapCat 会以你扫码的账号单独登录一次；如果本机 QQ 正在用同一个账号，
   > 扫码后可能互相挤下线。请自行决定让哪一端在线，本工具不会自动关闭你的 QQ。
3. **确认 OneBot HTTP 已起**：登录成功后监听 `http://127.0.0.1:3000`。
4. **回到 QQScope**：在数据源页面选择 机器人框架，填 `http://127.0.0.1:3000` 和同一个 token，
   点 测试连接 → 拉取。也可以直接用页面顶部的「连接 QQ」扫码门查看登录状态。

**端口 / token / 日志在哪看**

| 项目 | 位置 |
| --- | --- |
| OneBot HTTP | `http://127.0.0.1:3000`，token 见 `data/framework/onebot.json` 的 `token` |
| 框架配置 | `tools\napcat\napcat\config\onebot11.json`（已预设 3000 端口 + token） |
| WebUI 控制台 | `http://127.0.0.1:6099/webui`，token 见 `data/framework/onebot.json` 的 `webui_token` |
| 运行日志 | 网页「数据源 → 控制台 → 框架(NapCat)」标签，或 `tools\napcat\napcat\logs\` 与控制台窗口 |
| 二维码图片 | `tools\napcat\napcat\cache\qrcode.png` |

**启动失败排查**

| 现象 | 处理 |
| --- | --- |
| 报 `The specified module could not be found`，指向 `wrapper.node` | 缺 `crypto.dll` / `ssl.dll`。`启动框架.bat` 会尝试自动从本机 QQ 复制；若失败，手动把 QQ 的 `versions\*\resources\app\` 下这两个文件复制到 `tools\napcat\` |
| 端口 3000 / 6099 被占用 | 先关掉占用程序；WebUI 端口被占用时会自动 +1，以控制台日志为准 |
| 二维码过期 / 不刷新 | 二维码约 2 分钟过期，重启 `①启动机器人框架.bat`，或在 WebUI 里点刷新 |
| 提示版本过低 / 注入失败 | QQ 官方强制升级了协议版本，去 NapCat Release 下载更新的 `NapCat.Shell.Windows.Node.zip` 重新解压覆盖 `tools\napcat\` |
| 想回退旧框架 | `tools\napcat-win\` 保留未动；本项目启动脚本不会碰它 |

**备用方案（若 NapCat 对当前 QQ 版本始终不可用）**

- **Lagrange.Core / Lagrange.OneBot**：纯协议实现，不依赖本机 QQ，也不用挂 QQ 客户端；
  仓库镜像在 `research/lagrange-core-src/`。同样属于非官方客户端，有风控风险。
- **LLOneBot**：LiteLoaderQQNT 插件，需要先给现有 QQ 装 LiteLoaderQQNT，再在 QQ 内运行。
- **NapCat.Framework**：同属 LiteLoaderQQNT 插件模式，适合已经有 LiteLoader 的环境。

> ⚠️ 协议端登录属于**非官方客户端**，有封号风险。建议先用小号试通全流程。

---

## 便携版（免安装）

```bat
python scripts\package.py            :: 组装 + 打 zip
python scripts\package.py --no-zip   :: 只组装目录
python scripts\package.py --name XXX :: 自定义包名
```

产出：

```
dist-package/QQScope-Portable-v2/      便携目录（可直接拷到别的电脑）
dist-package/QQScope-Portable-v2.zip   压缩包（实测约 154 MB，含 3D 素材）
```

包内结构：

| 路径 | 说明 |
| --- | --- |
| `①启动QQScope.bat` | 一键启动（首次自动建目录 + 写入机器人 token + 缺前端产物才构建） |
| `②启动机器人框架.bat` | 启动随包 NapCat（可选，只用离线记录可以不启） |
| `python\` | 便携 Python 运行时（基础解释器 + 标准库 + 一份现成依赖，**不用另装 Python**） |
| `使用说明.txt` | 三步走 / 数据迁移备份 / token 位置 / 常见问题 / 封号风险 |
| `core\ server\ scripts\ app\ web\` | 程序本体（含 `web/assets` 3D 素材约 44 MB；不含 `web/selftest`） |
| `tools\napcat\` | 机器人框架（约 325 MB，已剔除 logs / cache） |
| `tools\nt_msg_db_util\` | 本地 QQ 数据库解密/解析工具链（不含 `.venv`） |
| `data\` | **空目录**（keys/decrypt/export/pack/backup/server/framework），首次运行自动创建，**默认不含任何聊天数据**，避免隐私泄露 |

说明：

- 便携运行时 = 「基础 Python 安装」+「`tools/nt_msg_db_util/.venv` 里那份已实测的 site-packages」。
  venv 本身不可重定位（`pyvenv.cfg` 写死 home），但这样组合是可重定位的，且完全不需要联网 pip。
- 包内含 **3D 环绕背景素材**（`web/assets/vendor/`：`three.min.js` / `GLTFLoader.js` / `kei.vrm`，约 44 MB）；后端启动时把 `web/assets` 挂到 `/assets`，所以便携版也能正常加载 3D 背景（素材缺失时自动降级为 CSS 背景，其它功能不受影响）。
- 迁移/备份：所有数据都在 `data\` 里（`data\qqscope.db` 主库、`avatars\` 头像缓存、`media_cache\` 媒体缓存、`keys\` 密钥）。
  换电脑把旧机器 `data\` 整个拷过去即可；备份直接复制 `data\qqscope.db`（建议先关程序）。
- 解压到桌面 / 文档等**可写目录**，不要放 `C:\Program Files`；NapCat 与 sqlcipher 可能被杀软误报，需要加白名单。

---

## 界面功能

| 页面 | 内容 |
| --- | --- |
| 总览 | KPI 卡 + 情绪曲线 + 活跃时段 + 每日明细 / 社交对象 / 高频情绪词 + 媒体命中率与补下载 |
| 会话 | 分组列表 + 气泡预览 + 加载更多 + 内联备注改名 + 资料卡；自动刷新与新消息胶囊 |
| 导出 | 会话多选（搜索 / 类型筛选 / 全选 / 反选 / 全选私聊 / 全选群聊）+ 时间段 + 格式 + 模式 + 历史导出 |
| 动态 | QQ 动态时间线卡片；需要登录态时给出明确引导 |
| 数据源 | 窗口 A / 窗口 B 三态徽章、扫描 / 导入 / 拉取 / 进度、控制台（运行日志 + 框架日志） |
| AI 解读 | 范围选择 + 发送内容预览 + 经 `/api/ai/chat` 代理 |
| 设置 | 数据根目录、AI provider/base/key/model、3D 背景开关、端口与运行信息 |

启动流程：先过「连接 QQ」门（判定后端登录态）——已登录直接进主界面，未登录显示二维码，
框架没起引导启动；接口不可用时重试后进主界面并在顶部横幅提示。连接页**不发任何数据请求**。

---

## 导出

「导出」页可以勾选任意多个会话，选格式（HTML / TXT / MD）、模式（每人一个文件 / 合并成一个）
和时间段，一键导出并打包 zip。产物在 `data/export/<job>/`。

- **HTML**：单文件自包含（内联 CSS/JS，零外部请求），双击即用，带聊天气泡和「仅看自己发的」筛选；
  命中媒体复制到同 job 的 `media/` 用相对路径引用
- **TXT**：UTF-8 带 BOM，Windows 记事本不乱码
- **MD**：带统计表 + 按天分节 + 引用块
- 本地缺失的媒体渲染成占位徽章（`[图片·未缓存]` 等），**绝不出现破图**

---

## 目录结构

```
启动QQScope.bat          # 一键启动：构建前端 → 起后端 → 开浏览器
①启动机器人框架.bat       # 一键启动 NapCat（= tools\napcat\启动框架.bat）

core/                        # 数据层（纯标准库优先）
  paths.py                   # 统一路径（便携化：一切从包根推导，可用环境变量覆盖）
  store.py                   # 统一主库（accounts / contacts / messages）
  export.py                  # HTML / TXT / MD 导出引擎（+ 媒体 + zip）
  normalize.py               # c2c peer_id 归一化 + 数据健康度
  media.py / media_fetch.py  # 本地媒体索引 / 缺失媒体补下载（rkey + fileid 直链）
  voice.py / voice_official.py  # 本地 ASR / 官方 fetch_ptt_text 回填
  send.py                    # 网页端发送文本（写操作，安全阀在这里）
  live_sync.py               # 实时增量采集（轮询版）
  framework_log.py           # NapCat 日志读取 / 脱敏 / 框架状态
  qq_face.py                 # QQ 经典表情 faceId → emoji
  sources/                   # 两个读取窗口
    pack_source.py           #   窗口 A：本地数据包（离线）
    bot_source.py            #   窗口 B：机器人框架（在线）
    qzone.py                 #   QQ 动态（需要登录态 cookie）
    names.py                 #   昵称 / 群名 / 备注补全
    _pack_extract.py         #   venv 子进程解密助手
    _bot_selftest.py         #   假 OneBot 服务自测

server/
  app.py                     # FastAPI：统一 API + 托管前端 + /api/logs
  routes_media.py            # /api/media/*、补下载、数据质量
  routes_voice.py            # /api/voice/*（本地 ASR）
  routes_voice_official.py   # /api/voice/official/*（官方回填）
  routes_send.py             # /api/send（写操作，confirm:true）
  routes_live.py             # /api/live/*（实时采集）
  routes_framework.py        # /api/framework/*、/api/login/status
  routes_feeds.py            # /api/feeds、/api/sources/qzone/*
  routes_profile.py          # /api/avatar、/api/profile、/api/contact
  onebot.py                  # 旧版 v1 的 OneBot 客户端（仅被旧 server.py 用；新架构已重写为 core/sources/bot_source.py）
  reader.py / server.py      # 旧版 v1 后端（保留参考，新架构不再使用）

web/                         # 前端源码（零依赖原生 JS）
  index.html                 # 源码（HTML + CSS + JS 全内联）
  README.md                  # 注入契约 / boot schema / 接口依赖 / v10 章节
  assets/vendor/             # 3D 素材（three.min.js / GLTFLoader.js / kei.vrm，43MB，不入库）
  selftest/                  # 一键验收 + 分版本断言 + mock 后端
app/
  js/analysis.js             # 情绪分析引擎（纯 JS，词典唯一真源）
  dist/QQScope.html          # 构建产物（单文件）
scripts/
  build_web.py               # 前端构建（注入数据 + 引擎）
  verify.py                  # 端到端验收
  package.py                 # 便携包构建
  fetch_assets.ps1           # 复制 3D 素材
  import_and_pack.py         # 旧版数据管道（保留）
  qzone_probe.py             # QQ 动态可行性探测
docs/
  SPEC-重构接口.md            # 接口冻结文档（改接口先改它）
  截图/                       # 各版本验收截图
.dsh/skills/qqscope/SKILL.md # 项目记忆（v2→v10 演进记录 + 运行方式；.dsh/ 不进仓库）
tools/
  nt_msg_db_util/            # 第三方：NTQQ 解密/解析工具链（勿改）
  napcat/                    # 第三方：NapCat v4.18.28 无头一键版（勿改）
  napcat-win/                # 第三方：旧框架目录（保留回退）
dist-package/                # 便携包产物
data/                        # 运行数据（已 gitignore）：qqscope.db / keys / avatars / media_cache / export ...
```

---

## 构建 / 验收

```bat
:: 构建单文件前端
python scripts\build_web.py
python scripts\build_web.py --embed 20        :: 内嵌前 20 个会话消息（离线单文件可用）
python scripts\build_web.py --out xxx.html    :: 指定产物路径

:: 后端 / 整体验收（含真实数据源导入 + 导出实测）
tools\nt_msg_db_util\.venv\Scripts\python.exe scripts\verify.py --full
:: 快速校验（不跑真实导入）
tools\nt_msg_db_util\.venv\Scripts\python.exe scripts\verify.py

:: 前端一键验收（mock 后端 + 无头 Edge，默认端口 15556）
powershell -ExecutionPolicy Bypass -File web\selftest\run_edge.ps1

:: 前端分版本源码断言（可选，逐版红线）
node web\selftest\check_v2.mjs
node web\selftest\check_v10.mjs
```

`scripts/verify.py --full` 的验收线：真实库入库 ≥ 260,000 行、自发 ≥ 2,800 条，且导出 3 会话 × 3 格式全成功。

---

## 相关约定

- **数据全本地，不出本机**；走 AI 之前会先让你预览将发送的内容
- 文案必须注明「辅助自我观察，非医疗诊断」
- 前端页面**不直连外部资源**：无 CDN / 远程字体 / 远程图片；头像与动态图片统一经本地后端
  （`/api/avatar` 服务端抓取并缓存）；3D 素材走本地 `/assets/vendor/`
- 后端把 `web/assets` 挂到 **`/assets`**（v10 的 3D 环绕背景素材）；素材目录不存在时**不挂载**，前端自动降级为 CSS 背景（`.bg-glow`），其它功能不受影响
- 写操作三件套：二次确认 + 限速 + 可审计（发消息默认只允许发给自己、群聊默认禁止）
- 密钥 / token / cookie / rkey 一律不写日志、不进接口响应
- 端口 `15555`（OneBot 3000 / NapCat WebUI 6099）

---

## 截图

按版本存放在 `docs/截图/`：`v2-旧版/`、`v3-{暗色,浅色}-*.png`、`v4-媒体-*.png`、
`v5-语音文字-*.png`、`v6-*.png`、`v7-*.png`、`v8-*.png`、`v9-*.png`、`v10-*.png`。

---

## 已知限制 / 未核实

- 本地 QQ 会定期清理缓存，历史媒体命中率天然偏低（实测图片尤其低）；补下载依赖 NapCat 在线拿 rkey
- 本地 ASR 是全量一次性任务，全量约 8 小时音频在本机（Xeon 20 核）约 65 分钟；**文字存库后其他设备只读文本，不需要跑 ASR**
- QQ 动态需要登录态 cookie，拿不到时接口返回明确中文错误，绝不伪造数据
- 便携包目前只在本机组装与结构校验，**未做异机（无 Python/QQ 的电脑）首次启动实测**
- 协议端登录属于非官方客户端，有封号风险，建议先用小号试通全流程

---

## 致谢 / 第三方

- [QQBackup/nt_msg_db_util](https://github.com/QQBackup/nt_msg_db_util) — NTQQ 数据库解密与解析
- [QQBackup/qq-win-db-key](https://github.com/QQBackup/qq-win-db-key) — 密钥提取脚本
- [NapCatQQ](https://github.com/NapNeko/NapCatQQ) — 机器人框架
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — 本地语音识别
- [three.js](https://threejs.org/) — 3D 背景运行时
- UI 视觉参考：`kei-showcase`（全息 HUD 令牌，v3 起）+ 本地暗色项目（v2 阶段），风格数据来自 StyleKit（MIT）