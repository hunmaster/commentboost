@echo off
chcp 65001 >nul 2>&1
title CommentBoost 업데이트

echo.
echo ============================================
echo   CommentBoost 업데이트
echo ============================================
echo.

:: git 확인
git --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [오류] Git이 설치되어 있지 않습니다.
    echo.
    echo 수동 업데이트 방법:
    echo   1. 최신 ZIP 파일을 다운로드합니다.
    echo   2. .env 파일과 config/, data/ 폴더를 백업합니다.
    echo   3. 새 파일을 덮어씁니다.
    echo   4. 백업한 .env, config/, data/를 복원합니다.
    echo.
    pause
    exit /b 1
)

echo [1/3] 최신 버전 다운로드 중...
git pull origin main
if %errorlevel% neq 0 (
    echo [오류] 업데이트 실패. 인터넷 연결을 확인해주세요.
    pause
    exit /b 1
)

echo.
echo [2/3] 패키지 업데이트 중...
python -m pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo [경고] 일부 패키지 업데이트 실패. 기존 버전으로 실행됩니다.
)

echo.
echo [3/3] 브라우저 업데이트 확인 중...
python -m playwright install chromium --with-deps 2>nul

echo.
echo ============================================
echo   업데이트 완료!
echo ============================================
echo.
echo   start.bat으로 프로그램을 다시 실행하세요.
echo.
pause
