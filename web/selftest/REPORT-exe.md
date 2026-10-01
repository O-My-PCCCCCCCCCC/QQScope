# REPORT-exe — 单文件 exe 打包自测（task-8 · 账号隔离修复后重打）

- 交付：`dist-package\QQScope.exe` = **152,741,711 B (145.7 MB)**（9.7 KB 壳 + 145.7 MB 7z-ZIP 负载追加，尾部 16B = `QQSCOPEP` + Int64(负载长度)）
- 固定解压目录：exe 同目录 `QQScope_data\`；`.payload.stamp` 记录负载长度 → 二次运行秒开；强制重解压跳过 `data\` 已有文件，不覆盖用户数据
- 重打流程（用当前最新代码）：`package.py` 刷新便携目录（venv python，59 s）→ `7z a -tzip -mx=5 payload.zip .\*`（13 s）→ `csc` 编译壳 `scripts\exe_launcher\QQScopeLauncher.cs` → 追加负载 + 尾标 → `dist-package\QQScope.exe`
- 冒烟（`E:\portable-test\exe-test-v2`，端口 15557，只停自起 PID 29836）：
  - 解压：2556 文件 / 444 MB / **12 s**，退出码 0
  - `GET /api/health` → **200** `{"ok":true,"version":"2.0.0"}`
  - `GET /` → 200（340264 B），含 **`gate-mode`**（7 处）
  - `GET /api/overview`（**不带 account**）→ **400** `缺少 account（多账号隔离：总览必须指定账号，禁止全局兜底）` —— 隔离修复已随包
- 安全：15555 / node 42176 / 18896 / 用户 NapCat 未碰；临时目录已清理；仅保留 `dist-package\QQScope.exe`