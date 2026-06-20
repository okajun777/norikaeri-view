@echo off
cd /d "%~dp0"

set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
    echo Python not found.
    pause
    exit /b 1
)

echo 閲覧用サイトを docs フォルダに書き出し中...
%PY% export_view_site.py
if errorlevel 1 (
    echo 失敗しました。
    pause
    exit /b 1
)

echo.
echo docs フォルダを開きます。
start "" explorer "%~dp0docs"
pause
