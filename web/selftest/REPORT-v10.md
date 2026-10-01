# task-33（v10 · 3D 环绕背景 + 连接 QQ 扫码门）实测报告

## 1. 改动文件（增量修改，未重写 web/index.html）

- web/index.html：新增连接门 + 3D 背景模块，init() 改为先判登录态再决定是否拉数据；renderRoute()/visibilitychange 在 gate-mode 下不发数据请求。
- web/README.md：新增 v10 章节。
- web/selftest/check_v10.mjs：v10 断言（源码 37 项；带 DOM dump 44 项）。
- web/selftest/v10_server.mjs：静态 + /assets + /api 反向代理验收服务。
- web/selftest/v10_cdp.mjs：真实时间无头 Edge CDP 驱动。
- scripts/fetch_assets.ps1：从 kei-showcase 复制 3D 素材。
- .gitignore：新增 web/assets/（43MB 模型不入库）。
- 素材：web/assets/vendor/{three.min.js, GLTFLoader.js, kei.vrm}。

## 2. 连接门状态机（不是二次登录）

| 后端登录态 | 行为 | 实测 |
|---|---|---|
| logged_in:true | 直接主界面，不渲染二维码 | OK：gate=login-gate hidden、gateInner=0、无 qrImg、authChip=已连接 霖ケ、data-js-errors=0 |
| 框架在跑未登录 | 显示二维码（3s 刷新 mtime） | OK：DOM 有二维码容器；截图用真实 /api/framework/qrcode（147x147 PNG） |
| 框架未运行 | 引导启动框架 / bat | OK：state=框架未运行、二维码隐藏 |
| 已连接但手动退出 | 显示已连接 + 进入按钮，无二维码 | OK：已连接：霖ケ、qrBox hide |
| 接口未就绪 | 重试 2 次 -> 进主界面 + 顶部横幅 | OK：门隐藏、conn-banner=框架状态接口未就绪 |

连接页不发数据请求：gate-mode 下 renderRoute() 直接 return、visibilitychange 也直接 return。

## 3. 退出登录

- 主界面「退出登录」-> 二次确认弹窗 -> 确认后：
  - localStorage：qqscope_authed、qqscope_authed_nick 被清；qqscope_theme=dark、qqscope_remark_demo 仍在（实测 authed=null, authKeys=[], theme="dark", remarkKeys=1）。
  - POST /api/live/stop -> 实测页面内 fetch('/api/live/status') 返回 running=false；随后已 POST /api/live/start 恢复。
  - 回到连接门（后端仍登录时显示已连接锁定态）。
- 连接门「退出并停止框架」-> 二次确认；为避免断开现场 NapCat，只截图确认弹窗、未点确认执行。

## 4. 3D 环绕背景

- 本地素材，运行时 fetch + 间接 eval 加载；静态 HTML 无 script src、无 http(s)、零外链。
- 无头 Edge（SwiftShader）实测：
  - 主界面 3D 关：navLoadMs≈849、enterMs=15
  - 主界面 3D 开：navLoadMs≈913（首屏不被 43MB 模型阻塞）；holoReadyMs≈2.5s
  - 模型 FPS≈10（软件渲染），看门狗自动把 resScale 1 -> 0.55；持续低于 24fps 会关闭 3D
  - 连接页首次冷加载模型 holoReadyMs≈10-13s
- 降级（file:// 素材不可用）：holo.failed=true、why=Failed to fetch、body.holo-fallback、data-js-errors=0（截图 v10-3D降级.png）。
- 主界面默认关 3D；设置页 #setHoloMain 与连接页角落按钮切换，状态存 qqscope_holo_login / qqscope_holo_main。

## 5. 断言

- 基线：check_v2..v9 源码全绿 = 186 项 / 0 失败
- 新增：check_v10 源码 37/37；带 3 个 DOM dump（已登录主界面 / 待扫码 / 降级）44/44
- check_web.mjs：源码与 build_web.py --out 产物均通过
- 浏览器实测 data-js-errors=0、#jsErrorLog 为空

## 6. 截图（docs/截图/）

v10-连接QQ-待扫码.png、v10-已连接直接进主界面.png、v10-3D背景.png、v10-主界面.png、
v10-退出确认弹窗.png、v10-退出后回到登录页.png、v10-退出并停止框架.png、
v10-框架未运行.png、v10-接口未就绪.png、v10-3D降级.png

## 7. 说明 / 遗留

- 「待扫码」截图走 ?gate=1&loginDemo=qr（现场 NapCat 已登录，无法在不掉线前提下展示真实未登录）；二维码图片取真实 /api/framework/qrcode，失败回退本地 canvas 假码。
- 「退出并停止框架」只展示二次确认，未真实调用 POST /api/framework/stop；/api/live/stop 已真实实测并恢复。
- 无 GPU 的 headless 下 3D FPS 偏低属 SwiftShader 环境限制；主界面默认关 3D、连接页帧率看门狗降级即为此设计。