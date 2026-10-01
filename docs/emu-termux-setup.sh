#!/data/data/com.termux/files/usr/bin/bash
# QQScope · 模拟器/手机内 Termux 安装脚本（NapCat 框架 + 依赖）
export PREFIX=/data/data/com.termux/files/usr
export HOME=/data/data/com.termux/files/home
export PATH=$PREFIX/bin:$PATH
export TMPDIR=$PREFIX/tmp
export LD_LIBRARY_PATH=$PREFIX/lib
export ANDROID_ROOT=/system

echo "=== QQScope Termux 安装 ==="
echo "PATH: $PATH"
echo "[1/4] pkg 更新与依赖..."
pkg update -y -o Acquire::Retries=5 2>&1 | tail -3
pkg install -y nodejs-lts wget unzip 2>&1 | tail -3
echo "[2/4] 下载 NapCat-Termux 安装器..."
cd "$HOME"
if [ ! -f install.sh ]; then
  wget -O install.sh "https://raw.githubusercontent.com/NapNeko/NapCat-Termux/main/install.sh" 2>&1 | tail -2
fi
if [ -f install.sh ]; then
  echo "[3/4] 运行 NapCat-Termux 安装..."
  bash install.sh 2>&1 | tail -15
else
  echo "NapCat-Termux 安装器下载失败"
fi
echo "[4/4] 完成。"
