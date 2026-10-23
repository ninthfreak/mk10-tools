@echo off
setlocal
cd /d "%~dp0"
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
if not exist ".venv\installed.ok" (
  echo First run: setting things up. This needs internet and takes about a minute.
  if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv || goto :nopython
  )
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :failed
  echo ok> ".venv\installed.ok"
)
rem A command that prints a report needs the Python that has a console.
if /i "%~1"=="nowplaying" (
  ".venv\Scripts\python.exe" launch.pyw %*
  echo.
  pause
  exit /b 0
)
start "" ".venv\Scripts\pythonw.exe" launch.pyw %*
exit /b 0

:nopython
echo.
echo Python 3.10 or newer is needed. Get it from https://www.python.org/downloads/
echo During install, tick "Add python.exe to PATH". Then run this again.
pause
exit /b 1

:failed
echo.
echo Installing the requirements failed. The messages above say why.
pause
exit /b 1
