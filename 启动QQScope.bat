@echo off
chcp 65001 >nul
title QQScope
cd /d "%~dp0"

set "PY=%~dp0tools\nt_msg_db_util\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [错误] 找不到运行环境：%PY%
  echo 先执行：uv sync  （在 tools\nt_msg_db_util 目录下）
  pause
  exit /b 1
)

echo ============================================
echo   QQScope  正在构建前端并启动...
echo ============================================
"%PY%" scripts\build_web.py
if errorlevel 1 (
  echo [警告] 前端构建失败，仍然尝试启动后端
)

start "" http://127.0.0.1:15555
"%PY%" server\app.py
pause
