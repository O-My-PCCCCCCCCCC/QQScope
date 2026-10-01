#!/data/data/com.termux/files/usr/bin/bash
# ============================================================
# QQScope · 手机端一键部署脚本（Android Termux）
# 在 Termux 里运行：bash qqscope-termux-setup.sh
# 功能：安装 Node/NapCat → 启动 NapCat → 扫码登录（任意 QQ）
#       → 配置 OneBot HTTP → 部署 QQScope 分析服务 → 打开仪表盘
# 数据 100% 存手机本地；协议框架 = NapCat（官方 Termux 支持）
# ============================================================
set -e

echo "=== QQScope 手机端部署 ==="

# 1. 基础环境
echo "[1/6] 更新包源并安装依赖..."
pkg update -y
pkg install -y nodejs-lts python git unzip curl || pkg install -y nodejs python git unzip curl

# 2. 下载并部署 NapCat（官方 NapCat-Termux 脚本）
echo "[2/6] 部署 NapCat..."
NC_DIR="$HOME/napcat"
mkdir -p "$NC_DIR" && cd "$NC_DIR"
curl -o napcat-termux.sh -L "https://raw.githubusercontent.com/NapNeko/NapCat-Termux/main/install.sh" || true
if [ -f napcat-termux.sh ]; then
  bash napcat-termux.sh
else
  # 备用：直接拉 NapCat 包
  echo "官方脚本拉取失败，尝试 npm 方式..."
  npm config set registry https://registry.npmmirror.com
  npm install napcat -g || true
fi

# 3. 下载 QQScope 分析服务
echo "[3/6] 部署 QQScope 分析服务..."
SVC_DIR="$HOME/qqscope"
mkdir -p "$SVC_DIR" && cd "$SVC_DIR"
if [ ! -f server.py ]; then
  echo "请把 QQScope 的 server/ 目录（server.py/reader.py/onebot.py）复制到 $SVC_DIR"
  echo "（PC 端：E:\\Workspace\\Project\\QQScope\\server\\）"
fi
pip install --break-system-packages -q fastapi uvicorn httpx 2>/dev/null || pip install -q fastapi uvicorn httpx || true

# 4. 生成启动脚本
cat > "$SVC_DIR/start.sh" << 'EOF'
#!/data/data/com.termux/files/usr/bin/bash
# 1) 启动 NapCat（OneBot HTTP 默认 3000 端口，WebUI 6099）
#    在 Termux 里先跑 napcat 并按提示扫码登录
# 2) 启动 QQScope 服务
cd ~/qqscope
nohup python server.py > qqscope.log 2>&1 &
echo "QQScope 已启动: http://127.0.0.1:15555 （电脑局域网访问: http://$(hostname -I | awk '{print $1}'):15555）"
echo "日志: ~/qqscope/qqscope.log"
EOF
chmod +x "$SVC_DIR/start.sh"

# 5. 使用说明
cat << 'EOF'
==========================================
[4/6] 部署完成！接下来：
 1) 启动 NapCat：在 Termux 输入  napcat  并按提示扫码登录（任意 QQ）
 2) 打开浏览器 http://127.0.0.1:6099 进 NapCat WebUI（默认密码看终端提示）
    → 网络配置 → 新建 OneBot11 HTTP 服务 → 端口 3000，取消鉴权（或记 token）
 3) 启动服务：cd ~/qqscope && bash start.sh
 4) 打开仪表盘 http://127.0.0.1:15555
    → 设置 → 数据源 → OneBot → 填 http://127.0.0.1:3000 → 保存
    → 主页点「拉取」即爬取历史消息并分析
 局域网：手机与电脑同一 WiFi 时，电脑访问 http://<手机IP>:15555
==========================================
EOF
echo "[6/6] 完成。"
