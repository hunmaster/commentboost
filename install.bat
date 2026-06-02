@echo off
chcp 65001 >nul 2>&1
title CommentBoost 설치

echo.
echo ============================================
echo   CommentBoost 설치 스크립트
echo ============================================
echo.

:: Python 확인
echo [1/4] Python 확인 중...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo Python이 설치되어 있지 않습니다.
    echo Python 3.11을 자동으로 설치합니다...
    echo.

    :: Python 설치 (winget 사용)
    winget install Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements >nul 2>&1
    if %errorlevel% neq 0 (
        echo.
        echo [오류] Python 자동 설치에 실패했습니다.
        echo 아래 링크에서 직접 설치해주세요:
        echo   https://www.python.org/downloads/
        echo.
        echo 설치 시 "Add Python to PATH" 체크를 꼭 해주세요!
        echo.
        pause
        exit /b 1
    )

    :: PATH 갱신
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python311;%LOCALAPPDATA%\Programs\Python\Python311\Scripts;%PATH%"
    echo Python 설치 완료!
) else (
    for /f "tokens=*" %%i in ('python --version') do echo   %%i 확인됨
)

echo.

:: pip 패키지 설치
echo [2/4] Python 패키지 설치 중...
echo   (Flask, Playwright, Notion SDK 등)
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [오류] 패키지 설치 실패. 인터넷 연결을 확인해주세요.
    pause
    exit /b 1
)
echo   패키지 설치 완료!
echo.

:: Playwright 브라우저 설치
echo [3/4] Chromium 브라우저 설치 중...
echo   (댓글 자동 작성에 사용됩니다)
python -m playwright install chromium
if %errorlevel% neq 0 (
    echo [오류] 브라우저 설치 실패.
    pause
    exit /b 1
)
echo   브라우저 설치 완료!
echo.

:: 설정 파일 생성
echo [4/4] 기본 설정 파일 생성 중...
if not exist "config" mkdir config
if not exist "data" mkdir data

if not exist ".env" (
    if exist ".env.example" (
        copy .env.example .env >nul
        echo   .env 파일 생성 완료 (기본 설정)
    ) else (
        echo   .env.example이 없습니다. 수동으로 .env 파일을 생성해주세요.
    )
) else (
    echo   .env 파일이 이미 존재합니다 (유지)
)

echo.
echo ============================================
echo   설치 완료!
echo ============================================
echo.
echo   실행 방법: start.bat 을 더블클릭하세요.
echo   (데스크탑 앱이 자동으로 열립니다)
echo.
pause
