@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0dist\FC27Manager\FC27Manager.exe" (
  start "" "%~dp0dist\FC27Manager\FC27Manager.exe"
  exit /b 0
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 scripts\fc27_ui.py --port 0 --open
) else (
  python scripts\fc27_ui.py --port 0 --open
)
if errorlevel 1 (
  echo 启动失败。请确认安装 Python 3.13，并查看上方提示。
  pause
)
