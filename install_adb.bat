@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

echo ============================================
echo   Android Platform Tools 자동 설치
echo ============================================
echo.

set "INSTALL_DIR=D:\platform-tools"
set "ZIP_FILE=%TEMP%\platform-tools.zip"
set "DOWNLOAD_URL=https://dl.google.com/android/repository/platform-tools-latest-windows.zip"

:: 이미 설치되어 있는지 확인
if exist "%INSTALL_DIR%\adb.exe" (
    echo [확인] ADB가 이미 설치되어 있습니다: %INSTALL_DIR%\adb.exe
    echo.
    "%INSTALL_DIR%\adb.exe" version 2>nul
    echo.
    echo 재설치하시겠습니까?
    choice /C YN /M "[Y] 재설치  [N] 취소"
    if errorlevel 2 goto :done
    echo.
    echo 기존 파일을 삭제하고 재설치합니다...
    rmdir /s /q "%INSTALL_DIR%" 2>nul
)

:: D:\ 드라이브 존재 확인, 없으면 C:\ 사용
if not exist "D:\" (
    echo [알림] D:\ 드라이브가 없습니다. C:\platform-tools 에 설치합니다.
    set "INSTALL_DIR=C:\platform-tools"
)

echo.
echo [1/3] Android Platform Tools 다운로드 중...
echo       URL: %DOWNLOAD_URL%
echo.

:: PowerShell로 다운로드
powershell -Command "& { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $ProgressPreference = 'SilentlyContinue'; try { Invoke-WebRequest -Uri '%DOWNLOAD_URL%' -OutFile '%ZIP_FILE%' -UseBasicParsing } catch { Write-Host '[오류] 다운로드 실패:' $_.Exception.Message; exit 1 } }"

if not exist "%ZIP_FILE%" (
    echo.
    echo [오류] 다운로드에 실패했습니다.
    echo        인터넷 연결을 확인해주세요.
    goto :error
)

:: 파일 크기 확인
for %%A in ("%ZIP_FILE%") do set "FILE_SIZE=%%~zA"
if "%FILE_SIZE%"=="" goto :error
if %FILE_SIZE% LSS 1000000 (
    echo [오류] 다운로드된 파일이 너무 작습니다 ^(%FILE_SIZE% bytes^). 다시 시도해주세요.
    del "%ZIP_FILE%" 2>nul
    goto :error
)

echo       다운로드 완료! (%FILE_SIZE% bytes)
echo.
echo [2/3] 압축 해제 중... (위치: %INSTALL_DIR%)

:: 기존 폴더 삭제
if exist "%INSTALL_DIR%" rmdir /s /q "%INSTALL_DIR%" 2>nul

:: PowerShell로 압축 해제 (임시 폴더에 풀고 이동)
powershell -Command "& { try { Expand-Archive -Path '%ZIP_FILE%' -DestinationPath '%TEMP%\adb_extract' -Force; if (Test-Path '%TEMP%\adb_extract\platform-tools') { Move-Item '%TEMP%\adb_extract\platform-tools' '%INSTALL_DIR%' -Force } else { Move-Item '%TEMP%\adb_extract' '%INSTALL_DIR%' -Force } } catch { Write-Host '[오류] 압축 해제 실패:' $_.Exception.Message; exit 1 } }"

:: 임시 파일 정리
del "%ZIP_FILE%" 2>nul
rmdir /s /q "%TEMP%\adb_extract" 2>nul

:: 설치 확인
if not exist "%INSTALL_DIR%\adb.exe" (
    echo.
    echo [오류] 설치에 실패했습니다. adb.exe를 찾을 수 없습니다.
    goto :error
)

echo       압축 해제 완료!
echo.
echo [3/3] 설치 확인 중...
echo.
echo ─────────────────────────────────────
"%INSTALL_DIR%\adb.exe" version
echo ─────────────────────────────────────
echo.
echo ============================================
echo   설치 완료!
echo ============================================
echo.
echo   ADB 경로: %INSTALL_DIR%\adb.exe
echo.
echo   프로그램 설정에서 ADB 경로를 아래와 같이 입력하세요:
echo   %INSTALL_DIR%\adb.exe
echo.
echo ============================================
goto :done

:error
echo.
echo ============================================
echo   설치 실패 - 수동 설치 방법
echo ============================================
echo.
echo   1. 아래 링크에서 직접 다운로드:
echo      https://developer.android.com/tools/releases/platform-tools
echo   2. "SDK Platform-Tools for Windows" 클릭
echo   3. 다운로드된 zip 파일을 D:\platform-tools 에 압축 해제
echo   4. 프로그램 설정에서 ADB 경로 입력:
echo      D:\platform-tools\adb.exe
echo.
echo ============================================

:done
echo.
pause
