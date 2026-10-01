# task-36（收尾原 task-35「便携打包」）实测报告

- 时间：2026-10-01 18:20–18:48
- 起因：上一会话在 18:16 因上下文超限（1,105,681 tokens）中断，当时 **task-35 打包 in_progress**、webui 刚交付 task-33(v10)
- 结论：**全部收口**。另发现并修掉一个真实功能 bug（3D 背景在真后端 404）

---

## 1. 起点状态（实测确认，不是推测）

| 项 | 状态 |
|---|---|
| `app/dist/QQScope.html`（17:52） | **不含 v10**（无 gate-mode / holoCanvas / loginGate） |
| `dist-package/QQScope-Portable-v2.zip`（18:06，153.8MB） | 包内 dist `gate-mode=False` → **便携包是 pre-v10 的旧 UI** |
| 15555 后端（启动于 17:55:53） | **半成品构建**：`live_sync.py`(17:56)、`framework_log.py`(18:03)、`routes_framework.py`(18:04) 均晚于进程启动 → 队友 names 两次要求重启未执行 |
| `scripts/verify.py` | 9/9 PASS 却 **exit=1**（GBK 下 `print("全绿 ✅")` 抛 UnicodeEncodeError）；`subprocess` 读 build_web 输出另抛 UnicodeDecodeError（reader 线程栈） |
| `server/app.py` | **没有挂载 `/assets`** → 真后端与便携包 `/assets/vendor/*` 全 404 |

## 2. 改了什么

1. `scripts/verify.py`
   - 启动即把 stdout/stderr `reconfigure(encoding="utf-8", errors="replace")`
   - 子进程调用补 `env["PYTHONIOENCODING"]="utf-8"` + `errors="replace"`
   - 去掉文件头 UTF-8 BOM（统一「无 BOM」约定）
2. `scripts/build_web.py`：同上加 UTF-8 兜底 + 去 BOM
3. `server/app.py`：新增 `_mount_assets()`，把 `web/assets` 挂到 `/assets`；**素材缺失时不挂载**，前端自动降级 CSS `.bg-glow`
4. `scripts/package.py`：`使用说明.txt` 增补「3D 环绕背景素材在 `web\assets\vendor\`，删掉也能用会自动降级」
5. 重建 `app/dist/QQScope.html`（v10）→ 重新打包 `dist-package/QQScope-Portable-v2(.zip)`
6. 重启 15555 主后端（只杀本项目 2 个 python PID 31960/40336，**NapCat 39760 全程未碰**）

## 3. 验收证据

### A1 验收脚本（`verify.py --full`）
```
[PASS] core.paths 可用 / core.store schema / 数据源 pack / 数据源 bot
[PASS] core.export 模块 / web 源码自检 / 构建单文件前端 / 数据现状 / 导出实测
===== 总结 =====
  9/9 通过
  全绿 ✅
exit=0
```
不再出现线程 `UnicodeDecodeError` 栈追溯。

### A2 单跑构建
`python scripts\build_web.py` → `exit=0`，产物 `app/dist/QQScope.html 0.32 MB`（账号 1 / 会话 128 / 引擎 22550 字符）

### A3 产物断言
| 检查 | 结果 |
|---|---|
| `check_v10.mjs web/index.html` | PASS 37 / FAIL 0 |
| `check_v10.mjs app/dist/QQScope.html` | PASS 37 / FAIL 0 |
| 占位符残留（BOOT/ENGINE） | 无 |
| `http(s)` 外链 / `<script src=>` | 无 |
| v10 标记 gate-mode / holoCanvas / loginGate / qrImg | 全部 True |

### A4 便携包
- zip：`153.9 MB`，2555 条目
- **包内 `app/dist/QQScope.html`：gate-mode/holoCanvas/loginGate/qrImg 全 True**
- `data/` 下**无任何有内容的文件**（仅 7 个空占位目录 keys/decrypt/export/pack/backup/server/framework）
- `使用说明.txt`：2275 B，**GBK 可解、UTF-8 不可解、无 BOM、无控制字符** → 记事本打开不乱码
- 包根：`app core data python scripts server tools web ①启动QQScope.bat ②启动机器人框架.bat 使用说明.txt`
- 硬编码路径 grep：**功能性命中 0**；仅剩
  - `scripts/fetch_assets.ps1` / `web/README.md` 里 `D:\用户\下载\work1\kei-showcase`（该脚本是「从 kei-showcase 复制素材」的开发用可选工具，且素材现已随包，不影响运行）
  - `python\DLLs\*.pyd` 内 CPython 编译期临时路径（二进制内调试串，无法也无需消除）
  - `scripts/package.py` 自身的替换表模式串（打包器逻辑，正常）

### A5 跨目录冒烟（解包到 `E:\portable-test\`，包内 python 起服务，端口 15557）
| 请求 | 结果 |
|---|---|
| `GET /api/health` | 200 `{"ok":true,"version":"2.0.0"}` |
| `GET /api/sources` | 200（空 data 下提示缺密钥，符合预期） |
| `GET /api/accounts` | 200 `[]` |
| `GET /` | 200，len=307822，v10 标记齐全 |
| `GET /assets/vendor/three.min.js` | **200，669884 B** |
| `GET /assets/vendor/kei.vrm` | **200，44209126 B** |
| 便携运行时依赖 | `import fastapi,uvicorn,httpx,sqlcipher3,google.protobuf` ok（0.141.1 / 0.52.3 / 0.28.1） |

### A6 主后端重启后（15555）
| 请求 | 结果 |
|---|---|
| `/api/health` | 200 |
| `/api/login/status` | 200 `framework_running=true, logged_in=true, account=1605289411, nickname=霖ケ, ports.3000=true` |
| `/api/live/status` | 200 `running=true, sync_allowed=true, require_login=true` |
| `/api/framework/status` | 200 `running=true, pid=39760, version=4.18.28, logged_in=true` |
| `/api/framework/logs?limit=3` | 200，返回真实 NapCat 行（`霖ケ \| [Notice] [输入状态] ...`） |
| `GET /` | 200，v10 标记齐全 |
| `/assets/vendor/{three.min.js,GLTFLoader.js,kei.vrm}` | **200 / 200 / 200**（669884 / 115527 / 44845856 B） |

## 4. 本轮修掉的关键 bug（值得记进项目记忆）

> **`server/app.py` 从未挂载 `/assets`，v10 的 3D 环绕背景在真实产品里一直是 404 → 静默降级。**
> v10 的验收是 `v10_server.mjs`（自建静态服务，自己实现了 `/assets` 转发）跑的，所以「自测绿、真机不生效」。
> 教训：**前端素材类断言要在真后端上跑一遍**，不能只用自建 mock 服务。

## 5. 遗留 / 建议

1. **便携包未做「异机」实测**（没有 Python/QQ 的电脑上首次启动）——本机换目录已通过，异机仍未验证。
2. `scripts/fetch_assets.ps1` 的默认源路径是开发机路径；素材已随包，可改为「必须先 `-Source`」。
3. 「退出并停止框架」按钮仍只弹二次确认，未真实调用 `POST /api/framework/stop`（v10 报告的既定选择，避免断开会话中的 NapCat）。
4. git HEAD 仍停在 2026-08-17，v2–v10 + 打包全部未提交（`data/`、`app/dist/`、`web/assets/`、`dist-package/`、`.dsh/` 已 gitignore）。
5. 冒烟用的 15557 便携实例已停止；15555 主后端与 NapCat 39760 保持运行。