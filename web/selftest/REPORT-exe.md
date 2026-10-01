# REPORT-exe — 单文件 exe 打包自测（task-8）

- 交付：`dist-package\QQScope.exe`，**146.1 MB**（9.7 KB 壳 + 146.1 MB 7z-ZIP 负载追加，尾部 16B = `QQSCOPEP` + Int64 负载长度）
- 固定解压目录：exe 同目录 `QQScope_data\`（`.payload.stamp` 存负载长度 → 二次运行秒开；强制重解压时跳过 `data\` 下已有文件，不覆盖用户数据）
- 方案说明：7z SFX 试 2 次不可行（`OneKey_x\7z.exe` 旁无 sfx 模块；`D:\应用\7-Zip\7z.sfx` 无 `InstallPath`，只解压临时目录）；本机无 PyInstaller → **退化用 csc 编译壳 exe（自解压+启动），未冻结后端**
- 实测（`E:\portable-test\exe-test3`，端口 15557，只停自起 PID）：
  - 解压：2592 文件 / 444.5 MB / **8.0 s**，退出码 0
  - `GET /api/health` → **200** `{"ok":true,"version":"2.0.0"}`
  - `GET /` → 340249 B，**含 `gate-mode`**（7 处）
  - 自动启动：壳调用 `①启动QQScope.bat`，实测 CWD=解压目录正确
  - 数据保护：删 stamp 强制重解压后 `data\mykeep.txt` 内容仍在
  - 15555 未启动未碰（现存监听 PID 33656 为既有进程，未动）；用户 node 18896 未动
- 关键命令：
  - `7z.exe a -tzip -mx=5 dist-package\_sfx\payload.zip .\*`（cwd=`QQScope-Portable-v2`，15 s）
  - `csc /target:exe /out:stub.exe /r:System.dll /r:System.IO.Compression.dll QQScopeLauncher.cs`
  - 追加负载 + `QQSCOPEP`+Int64(len) 尾部 → `QQScope.exe`
  - 校验：`QQScope.exe --extract-only` → `python\python.exe server\app.py`（settings.json port=15557）
- 启动器源码：`dist-package\_sfx\QQScopeLauncher.cs`