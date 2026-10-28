@echo off
cd /d "%~dp0"
where uv >nul 2>&1
if errorlevel 1 (
  echo Please install uv, then run this launcher again.
  pause
  exit /b 1
)
echo Merchant Desk: http://127.0.0.1:8765
echo Keep this window open while using the desk.
uv run --frozen python -m desk
pause
