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

set VIEW_ONLY=1
set APP_HOST=0.0.0.0

echo Installing dependencies...
%PY% -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo Failed to install dependencies.
    pause
    exit /b 1
)

echo.
echo ========================================
echo  閲覧専用モード（家族共有）
echo  登録・編集はできません
echo ========================================
echo.
echo このPC:     http://127.0.0.1:5050/destinations
echo.

for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notmatch 'Loopback' -and $_.IPAddress -notmatch '^169\\.' } | Select-Object -First 1 -ExpandProperty IPAddress)"`) do set "LAN_IP=%%I"

if defined LAN_IP (
    echo 家族の端末: http://%LAN_IP%:5050/destinations
) else (
    echo 家族の端末: http://(このPCのIP):5050/destinations
    echo   ※ ipconfig で IPv4 アドレスを確認
)
echo.
echo 同じ Wi-Fi に接続してください
echo 終了はこの窓で Ctrl+C
echo.

start "" cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:5050/destinations"

%PY% app.py

pause
