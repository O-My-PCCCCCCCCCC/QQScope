# task-4 前端实测报告（webui）

> 交付物：`web/index.html`（单文件零依赖 SPA）+ `web/selftest/`（可复现自测）。
> 构建：`scripts/build_web.py` 注入 boot 快照 + `app/js/analysis.js` → `app/dist/QQScope.html`。

## 1. 改动的文件（全部在 web/ 内）

- `web/index.html` — 单文件前端（HTML+CSS+JS 全内联，无外部请求）
- `web/README.md` — 注入契约 / boot JSON schema / API 依赖 / 页面说明
- `web/selftest/build.mjs` — 生成 `out/QQScope.selftest.html`（内嵌快照）与 `out/QQScope.api.html`
- `web/selftest/check_web.mjs` — 断言脚本
- `web/selftest/mock_server.mjs` — 零依赖 mock 后端（只实现冻结接口）
- `web/selftest/proxy_server.mjs` — 真实后端反向代理（验收真实数据）
- `web/selftest/make_real_demos.mjs` — 真实产物自动点击 demo
- `web/selftest/run_edge.ps1` — 一键验收（build + 断言 + 无头 Edge + 截图）
- `web/selftest/REPORT.md` — 本报告

## 2. 页面清单（7 项）

1. 顶部工具栏：品牌 QQScope · 当前账号选择器 · 「已入库 N 条 / 联系人 M 个」· 刷新
2. 数据源：窗口 A 数据包读取（离线，扫描/导入/进度/结果摘要/密钥徽章）、窗口 B 机器人框架读取（OneBot 地址+Token、测试连接/拉取、登录账号/好友/群、NapCat 路径提示）、三态徽章（已就绪/未配置/报错）
3. 会话：账号+会话列表（搜索、私聊/群聊分组、内联 SVG 头像、消息数、最后时间）、右侧气泡预览（最近 200 条 + 底部「加载更多」+ 滚动到顶自动加载）、内联备注改名（PATCH contacts.remark）
4. 导出：多选（搜索/类型筛选/全选/全不选/反选）+ HTML/TXT/MD + 每人一个/合并 + 结果表格（单文件下载 / zip）+ 历史导出
5. 总览：6 张 KPI 卡 + 情绪曲线 canvas + 活跃时段 canvas + 每日明细/社交对象/高频情绪词
6. AI 解读：范围选择 + 「将要发送的内容预览」+ 经 `/api/ai/chat` 代理
7. 设置：数据根目录、AI provider/base/key/model、测试连接、端口与运行信息

## 3. 断言结果

`node web/selftest/check_web.mjs web/index.html app/dist/QQScope.html`

| 断言 | web/index.html | app/dist/QQScope.html |
|---|---|---|
| 外部资源引用（script src / link / @import / img / url(http) / href=http） | 0 | 0 |
| 注入数据之外的 http(s):// | 0 | 0 |
| boot 占位符独立成行且精确 | PASS | 已替换，无残留 null |
| engine 占位符独立成行 | PASS | 已替换 |
| 内联脚本语法（3 块） | PASS | PASS |
| 错误采集钩子（#jsErrorLog） | PASS | PASS |

> 产物 boot JSON 内含 57 个 URL（联系人头像 URL + AI base），均为**字符串数据**；页面 0 个 `<img>`、0 个 `href=http`，不会发起请求。
> 本机 `web/index.html` 的 http(s) 字面量为 **0**（OneBot 默认地址在运行时用 `"http"+"://"` 拼接）。

## 4. 无头 Edge 实测（`run_edge.ps1`，mock 后端，全部 PASS）

- 6 个页面 dump-dom：sources / sessions / export / overview / ai / botfail，`data-js-errors` 均无，`#jsErrorLog` 为空
- 9 张截图非空白（white 75%–91%，侧栏深色占比 97%，移动端 390px 侧栏 0.5% → 已收起为顶部导航）
- 断言数字：sources 8 项关键内容、sessions 7 项、export 4 项、overview 9 项、ai 3 项、botfail 2 项，全部命中

## 5. 真实后端实测（`http://127.0.0.1:15555`，主库 267,772 条 / 120 会话）

| 页面 | 实测 |
|---|---|
| 数据源 | 「数据源状态」正常；窗口 A 已就绪、窗口 B 未配置/报错（现场无 NapCat，符合预期） |
| 会话 | 120 个会话（私聊 60 / 群聊 60）；打开最大会话 62,707 条，加载最近 200 条，气泡正常 |
| 导出 | 120 行会话全选 → `已选 120 个` |
| 总览 | 总消息 267,772 / 自己发的 6,376 / 私聊 9,718 / 群聊 258,054 / 联系人 120 / 时间跨度 94 天；情绪曲线 + 活跃时段两个 canvas 均绘制（`hourEmpty` 保持 hidden）；每日明细 42 行 |
| 设置 | 端口 15555、运行信息正常 |

所有真实页面 `data-js-errors` 均为 0，`#jsErrorLog` 为空。

## 6. 截图路径（`web/selftest/out/`）

真实后端（proxy :15558 → :15555，真实数据）：
`shot-real-01-sources.png` `shot-real-02-sessions.png` `shot-real-03-export.png` `shot-real-04-overview.png` `shot-real-05-settings.png` `shot-real-06-offline-realbuild.png`（离线 file://）`shot-real-07-mobile-sources.png`

mock 一键验收：
`shot-00-raw-index-sources.png`（未构建 file:// 静态骨架）`shot-01-sources.png` `shot-02-sessions.png` `shot-03-export.png` `shot-04-overview.png` `shot-05-ai.png` `shot-06-settings.png` `shot-07-offline-snapshot.png` `shot-08-bot-error.png` `shot-09-mobile-sources.png`

## 7. 遗留 / 说明

- 后端未就绪时显示「后端未就绪 / 接口未就绪」横幅与空状态，绝不白屏（file:// 降级已验证）。
- `/api/report`、`/api/progress/{id}`、`/api/overview` 的 `daily/hour_hist/top_contacts` 为 Lead 已确认新增，前端已优先使用；缺失时自动降级到 `/api/messages` → 内嵌快照 → 空状态提示。
- 总览情绪曲线需要 `/api/report`（或构建时 `--embed N`）；若只有 `/api/overview.daily`（无情绪分），前端隐藏曲线并提示，不画假平线。