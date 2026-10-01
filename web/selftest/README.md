# 前端自测（web/selftest/）

零依赖。用 Node 生成自测产物 + 一个只实现冻结接口的 mock 后端，再用无头 Edge 验收。

## 一键验收

```powershell
powershell -ExecutionPolicy Bypass -File web\selftest\run_edge.ps1 -Port 15556
```

脚本会：

1. `build.mjs` 生成 `out/QQScope.selftest.html`（内嵌 boot 快照 + 引擎，file:// 可用）与 `out/QQScope.api.html`（boot=null，给 mock 后端用）。
2. `check_web.mjs` 断言：无 `http(s)://`、无 `<script src=`、无 `<link`/`@import`、占位符独立成行、内联脚本语法 OK。
3. 启动 `mock_server.mjs`（端口默认 15556，只监听 127.0.0.1）。
4. 用 `msedge --headless=new --dump-dom` 打开各页面，断言关键内容渲染且 `#jsErrorLog` 为空（`data-js-errors` 不存在/为 0）。
5. 用 `--screenshot` 生成 1440x2000 截图到 `out/shot-*.png`，并做「非空白页面」像素检查。

## 单独运行

```powershell
node web\selftest\build.mjs
node web\selftest\check_web.mjs web\index.html web\selftest\out\QQScope.selftest.html
node web\selftest\mock_server.mjs 15556     # 另开一个终端
```

## 产物（out/）

- `QQScope.selftest.html` / `QQScope.api.html`：自测构建产物（**不是**交付物，交付物是 Lead 的 `app/dist/QQScope.html`）。
- `dom-demo-*.html`：各页面渲染后的 DOM（含 `#jsErrorLog`），用于核对无 JS 报错。
- `shot-*.png`：截图。
- `mock.log` / `mock.err.log`：mock 后端日志。

## 说明

- mock 后端只实现了 `docs/SPEC-重构接口.md` 第 5 节 + Lead 新增的 `/api/report`、`/api/progress/{id}`，用于验证前端 **fetch 路径与降级逻辑**，不代表真实后端行为。
- 源码 `web/index.html` 在未构建时 boot 为 `null`、engine 为空，直接 file:// 打开会显示「后端未就绪」静态骨架（这正是降级验收项）。