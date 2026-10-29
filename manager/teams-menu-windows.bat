@echo off
rem Double-click this during a Teams call. You get 15 seconds to click into Teams and open a menu (the arrow
rem next to the microphone, say) before MK10 Manager reads what Teams shows. Presses nothing.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run run-windows.bat once first to set things up.
  echo.
  pause
  exit /b 1
)
echo Click into Teams now and open the menu you want read. Leave it open until this says it is done.
".venv\Scripts\python.exe" launch.pyw teams 15
echo.
pause
