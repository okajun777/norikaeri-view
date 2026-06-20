@echo off
cd /d "%~dp0"
setlocal

set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
    echo Python not found.
    pause
    exit /b 1
)

where git >nul 2>&1
if errorlevel 1 (
    echo Git が見つかりません。https://git-scm.com/ からインストールしてください。
    pause
    exit /b 1
)

echo [1/3] 閲覧用サイトを書き出し...
%PY% export_view_site.py
if errorlevel 1 goto :fail

if not exist ".git" (
    echo.
    echo 初回: このフォルダを Git リポジトリにします。
    git init
    git branch -M main
    echo.
    echo GitHub で private リポジトリを作成し、次を実行してください:
    echo   git remote add origin https://github.com/あなたのID/リポジトリ名.git
    echo   deploy-github.bat
    pause
    exit /b 0
)

git remote get-url origin >nul 2>&1
if errorlevel 1 (
    echo.
    echo remote が未設定です:
    echo   git remote add origin https://github.com/あなたのID/リポジトリ名.git
    pause
    exit /b 1
)

echo [2/3] docs をコミット...
git add docs/ .github/workflows/pages.yml firebase.json export_view_site.py
git add -u docs/ 2>nul
git commit -m "Update view site" 2>nul
if errorlevel 1 (
    echo コミットする変更がありません。
) else (
    echo [3/3] GitHub へ push...
    git push -u origin main
    if errorlevel 1 git push -u origin master
)

echo.
echo 初回のみ GitHub で設定:
echo   Settings - Pages - Build and deployment - Source: GitHub Actions
echo.
echo 数分後: https://あなたのID.github.io/リポジトリ名/
echo.
pause
exit /b 0

:fail
echo 失敗しました。
pause
exit /b 1
