@echo off
REM LAAP Brain API 启动脚本
REM 用法: start-laap-service.bat
REM
REM 环境变量 (可选, 脚本已设默认):
REM   LAAP_TRSI_ENABLED=1          M4 受限递归引擎
REM   LAAP_EVO_ENABLED=1           M2 代码进化调度器
REM   LAAP_EVO_INTERVAL=3600       进化检查间隔(秒)
REM   LAAP_QUANT_DAILY=1           每日量化管线(真实成交序列收集)
REM   LAAP_QUANT_DAILY_INTERVAL=86400  每日管线间隔(秒)

set "LAAP_ROOT=D:\laap-AGI"
set "VENV_PYTHON=%LAAP_ROOT%\.venv\Scripts\python.exe"
set "PYTHONPATH=%LAAP_ROOT%"

cd /d "%LAAP_ROOT%"

REM 检查是否已在运行
"%VENV_PYTHON%" -c "import requests; r=requests.get('http://localhost:11546/health', timeout=2); exit(0 if r.status_code==200 else 1)" 2>nul
if %errorlevel% equ 0 (
    echo [LAAP] Service already running on port 11546
    exit /b 0
)

echo [LAAP] Starting LAAP Brain API on port 11546...

REM 设置环境变量 (M4 + M2 + 每日量化管线)
if not defined LAAP_TRSI_ENABLED set "LAAP_TRSI_ENABLED=1"
if not defined LAAP_EVO_ENABLED set "LAAP_EVO_ENABLED=1"
if not defined LAAP_EVO_INTERVAL set "LAAP_EVO_INTERVAL=3600"
if not defined LAAP_QUANT_DAILY set "LAAP_QUANT_DAILY=1"
if not defined LAAP_QUANT_DAILY_INTERVAL set "LAAP_QUANT_DAILY_INTERVAL=86400"

REM 使用 start 命令在后台启动
start "LAAP Brain API" /B "%VENV_PYTHON%" -m laap_brain.api --port 11546

REM 等待服务就绪
echo [LAAP] Waiting for service to be ready...
for /L %%i in (1,1,30) do (
    "%VENV_PYTHON%" -c "import requests; r=requests.get('http://localhost:11546/health', timeout=2); exit(0 if r.status_code==200 else 1)" 2>nul
    if %errorlevel% equ 0 (
        echo [LAAP] Service started successfully
        exit /b 0
    )
    timeout /t 1 /nobreak >nul
)

echo [LAAP] Warning: Service may not have started properly
exit /b 1
