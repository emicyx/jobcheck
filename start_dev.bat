@echo off
chcp 65001 >nul
REM JobCheck 一键启动开发环境：后端(8000) + 前端(5173)
REM 关闭对应窗口即停止对应服务；若换端口需同步改 frontend/vite.config.ts 的代理目标

cd /d %~dp0backend
start "JobCheck-Backend" cmd /k "python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

cd /d %~dp0frontend
start "JobCheck-Frontend" cmd /k "npm run dev"

echo.
echo 两个服务已在独立窗口启动：
echo   后端 http://127.0.0.1:8000
echo   前端 http://localhost:5173
echo.
echo 浏览器打开 http://localhost:5173 即可使用。
pause
