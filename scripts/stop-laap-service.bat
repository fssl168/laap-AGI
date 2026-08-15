@echo off
REM LAAP Brain API 停止脚本
REM 用法: stop-laap-service.bat

echo [LAAP] Stopping LAAP Brain API...

REM 通过 netstat 找到占用 11546 端口的进程并终止
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :11546 ^| findstr LISTENING') do (
    echo [LAAP] Killing PID %%a
    taskkill /PID %%a /F >nul 2>&1
)

timeout /t 2 /nobreak >nul

REM 验证
"D:\laap-AGI\.venv\Scripts\python.exe" -c "import requests; r=requests.get('http://localhost:11546/health', timeout=2); exit(0 if r.status_code==200 else 1)" 2>nul
if %errorlevel% neq 0 (
    echo [LAAP] Service stopped
) else (
    echo [LAAP] Warning: Service still running
)

exit /b 0
