@echo off
chcp 65001 >nul
echo ========================================
echo   游戏玩家流失分析系统 - 启动中...
echo ========================================
echo.
echo 正在启动本地服务器...
echo 服务器地址: http://localhost:8080
echo.
echo 浏览器将自动打开 analysis.html
echo 按 Ctrl+C 可停止服务器
echo.
echo ========================================
echo.

cd /d "%~dp0front\static_html"

python -m http.server 8080
