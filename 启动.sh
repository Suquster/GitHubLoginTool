#!/bin/bash
echo "================================================"
echo "  GitHub 自动登录工具 - 一键启动"
echo "================================================"
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "[错误] 未找到 Python3！请先安装: sudo apt install python3 python3-pip"
    exit 1
fi

echo "[1/3] 检查 Python... OK"
echo "[2/3] 安装依赖..."
pip3 install requests flask -q
echo "[3/3] 启动服务..."
echo ""
echo "================================================"
echo "  浏览器打开 http://localhost:5000"
echo "  按 Ctrl+C 停止服务"
echo "================================================"
echo ""

# 尝试打开浏览器
if command -v xdg-open &> /dev/null; then
    xdg-open http://localhost:5000 &
elif command -v open &> /dev/null; then
    open http://localhost:5000 &
fi

python3 github_login_web.py
