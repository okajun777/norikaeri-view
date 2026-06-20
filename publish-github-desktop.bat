@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY where py >nul 2>&1 && set "PY=py -3"
if not defined PY exit /b 1

echo 閲覧用サイトを更新中...
%PY% export_view_site.py
git add docs/
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "Update view site"
    git push origin main
    echo.
    echo 公開URL（1〜2分後に反映）:
    echo   https://okajun777.github.io/norikaeri-view/
) else (
    echo 変更なし
)
pause
