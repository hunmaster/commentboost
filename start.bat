@echo off
chcp 65001 >nul 2>&1
title CommentBoost

echo.
echo ============================================
echo   CommentBoost 시작
echo ============================================
echo.

:: Python 확인
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [오류] Python이 설치되어 있지 않습니다.
    echo install.bat을 먼저 실행해주세요.
    echo.
    pause
    exit /b 1
)

:: .env 파일 확인
if not exist ".env" (
    echo [경고] .env 파일이 없습니다.
    echo install.bat을 먼저 실행하거나, .env.example을 복사해서 .env로 만들어주세요.
    echo.
    pause
    exit /b 1
)

:: 필요한 디렉토리 생성
if not exist "config" mkdir config
if not exist "data" mkdir data

echo 데스크탑 앱을 시작합니다...
echo.
echo 앱 창이 자동으로 열립니다.
echo 종료하려면 앱 창을 닫거나 이 창에서 Ctrl+C를 누르세요.
echo ────────────────────────────────────────────

:: 데스크탑 앱 실행 (PyWebView)
python desktop.py
if %errorlevel% neq 0 (
    echo.
    echo [대체] 데스크탑 모드 실행 실패. 브라우저 모드로 시작합니다...
    echo 대시보드: https://localhost:5000
    start /b cmd /c "timeout /t 2 /nobreak >nul && start https://localhost:5000"
    python app.py
)
