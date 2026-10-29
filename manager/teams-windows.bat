@echo off
rem Double-click this to see what MK10 Manager can read from Teams (run it during a call). Keeps the window open so you can read it.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run run-windows.bat once first to set things up.
  echo.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" launch.pyw teams
echo.
pause
