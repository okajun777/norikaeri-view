@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal

set "REPO=%CD%"
set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
    echo Python が見つかりません。
    pause
    exit /b 1
)

echo ============================================
echo  GitHub Desktop で公開する準備
echo ============================================
echo.

echo [1/4] 閲覧用サイトを書き出し...
%PY% export_view_site.py
if errorlevel 1 goto :fail

where git >nul 2>&1
if errorlevel 1 (
    echo Git が見つかりません。GitHub Desktop に同梱の Git を使うか Git をインストールしてください。
    pause
    exit /b 1
)

if not exist ".git" (
    echo [2/4] Git リポジトリを初期化...
    git init
    git branch -M main
) else (
    echo [2/4] 既存の Git リポジトリを使用
)

echo [3/4] コミット...
git add .
git add docs/ .github/ firebase.json export_view_site.py export-view.bat deploy-github.bat publish-github-desktop.bat start-view.bat 2>nul
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "閲覧用サイトを公開（GitHub Pages）"
    echo コミットしました。
) else (
    echo 変更なし（すでに最新です）
)

echo [4/4] GitHub Desktop を起動...
set "GH_DESKTOP="
if exist "%LOCALAPPDATA%\GitHubDesktop\GitHubDesktop.exe" set "GH_DESKTOP=%LOCALAPPDATA%\GitHubDesktop\GitHubDesktop.exe"
if not defined GH_DESKTOP if exist "C:\Program Files\GitHub Desktop\GitHubDesktop.exe" set "GH_DESKTOP=C:\Program Files\GitHub Desktop\GitHubDesktop.exe"

if defined GH_DESKTOP (
    start "" "%GH_DESKTOP%" "%REPO%"
) else (
    start "" "https://desktop.github.com/"
    echo GitHub Desktop が見つかりません。インストール後、もう一度この bat を実行してください。
)

echo.
echo ============================================
echo  GitHub Desktop であと 2 操作だけ
echo ============================================
echo.
echo  1. 「Publish repository」
echo     - Name: norikaeri-view （任意）
echo     - Keep this code private: ON 推奨
echo     - Publish repository をクリック
echo.
echo  2. 公開後、ブラウザで GitHub のリポジトリを開く
echo     Settings → Pages → Build and deployment
echo     Source: GitHub Actions
echo.
echo  数分後: https://あなたのID.github.io/norikaeri-view/
echo  （リポジトリ名に合わせて URL が変わります）
echo.
echo  記録を更新したら: この bat を再実行 → Desktop で Push
echo.
pause
exit /b 0

:fail
echo 失敗しました。
pause
exit /b 1
