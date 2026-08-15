@echo off
REM LAAP Brain API 状态检查脚本
REM 用法: laap-status.bat

"D:\laap-AGI\.venv\Scripts\python.exe" -c "import requests; r=requests.get('http://localhost:11546/health', timeout=2); print('RUNNING' if r.status_code==200 else 'STOPPED')" 2>nul
if errorlevel 1 echo STOPPED
