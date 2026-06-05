@echo off
chcp 65001 >nul
title GitHub 自动登录工具
echo ================================================
echo   GitHub 自动登录工具 - 一键启动
echo ================================================
echo.

:: 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python！请先安装 Python 3.8+
    echo 下载地址: https://www.python.org/downloads/
    echo 安装时请勾选 "Add Python to PATH"
    pause
    exit /b
)

echo [1/3] 检查 Python... OK
echo [2/3] 安装依赖...
pip install requests flask -q
echo [3/3] 启动服务...
echo.
echo ================================================
echo   浏览器即将自动打开 http://localhost:5000
echo   关闭此窗口即可停止服务
echo ================================================
echo.

:: 延迟打开浏览器
start "" http://localhost:5000
python github_login_web.py
