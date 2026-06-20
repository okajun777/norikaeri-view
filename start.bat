@echo off
cd /d "%~dp0"

set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
    echo Python not found. Install Python 3 from https://www.python.org/
    pause
    exit /b 1
)

echo Installing dependencies...
%PY% -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo Failed to install dependencies.
    pause
    exit /b 1
)

echo.
echo Starting app: http://127.0.0.1:5050
echo Press Ctrl+C in this window to stop.
echo.

start "" cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:5050"

%PY% app.py

pause
