@echo off
rem Double-click this to see what Windows says is playing. Keeps the window open so you can read it.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run run-windows.bat once first to set things up.
  echo.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" launch.pyw nowplaying
echo.
pause
