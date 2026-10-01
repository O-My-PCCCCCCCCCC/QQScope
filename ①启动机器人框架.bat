@echo off
chcp 936 >nul
title QQScope - 启动机器人框架
echo 正在切换到框架目录并启动...
echo   目录: %~dp0tools\napcat
echo.
call "%~dp0tools\napcat\启动框架.bat" %*
