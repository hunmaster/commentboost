"""
CommentBoost 데스크탑 앱 빌드 스크립트 (PyInstaller)

사용법:
    python build.py

결과물:
    dist/CommentBoost/ (폴더) → zip으로 압축하여 배포
"""

import os
import sys
import subprocess
import shutil
import json

APP_NAME = "CommentBoost"
ENTRY_POINT = "desktop.py"
VERSION_FILE = "version.json"

# PyInstaller에 포함할 데이터 파일/폴더
DATA_FILES = [
    ("templates", "templates"),
    ("static", "static"),
    ("version.json", "."),
]

# 숨겨진 import (PyInstaller가 자동 감지 못하는 모듈)
HIDDEN_IMPORTS = [
    "flask",
    "flask_login",
    "flask_sqlalchemy",
    "sqlalchemy",
    "dotenv",
    "requests",
    "notion_client",
    "rich",
    "webview",
    "engineio.async_drivers.threading",
]

# 제외할 모듈 (빌드 크기 줄이기)
EXCLUDES = [
    "tkinter",
    "matplotlib",
    "numpy",
    "pandas",
    "scipy",
    "PIL",
    "cv2",
    "pytest",
]


def get_version():
    """version.json에서 버전 정보 읽기"""
    try:
        with open(VERSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("version", "1.0.0")
    except FileNotFoundError:
        return "1.0.0"


def build():
    """PyInstaller로 .exe 빌드"""
    version = get_version()
    print(f"[빌드] {APP_NAME} v{version} 빌드 시작...")

    # 이전 빌드 정리
    for d in ["build", "dist"]:
        if os.path.exists(d):
            try:
                shutil.rmtree(d, ignore_errors=True)
                print(f"[빌드] {d}/ 폴더 정리")
            except Exception:
                print(f"[빌드] {d}/ 폴더 정리 실패 (무시하고 계속)")
                pass

    # PyInstaller 명령 구성
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--noconfirm",
        "--clean",
        # 폴더 모드 (onedir) - Playwright 브라우저 포함 위해
        "--onedir",
        # 콘솔 창 숨기기
        "--noconsole",
        # 앱 아이콘
        "--icon", "app_icon.ico",
    ]

    # 데이터 파일 추가
    for src, dst in DATA_FILES:
        if os.path.exists(src):
            cmd.extend(["--add-data", f"{src}{os.pathsep}{dst}"])

    # config, data 디렉토리는 런타임에 생성하므로 포함하지 않음
    # .env는 사용자가 직접 설정

    # 숨겨진 import
    for mod in HIDDEN_IMPORTS:
        cmd.extend(["--hidden-import", mod])

    # 제외 모듈
    for mod in EXCLUDES:
        cmd.extend(["--exclude-module", mod])

    # 엔트리포인트
    cmd.append(ENTRY_POINT)

    print(f"[빌드] 명령: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=os.path.dirname(os.path.abspath(__file__)))

    if result.returncode != 0:
        print("[빌드] 빌드 실패!")
        sys.exit(1)

    dist_dir = os.path.join("dist", APP_NAME)

    # src/ 폴더 복사 (소스 코드)
    src_dst = os.path.join(dist_dir, "src")
    if os.path.exists("src"):
        shutil.copytree("src", src_dst, dirs_exist_ok=True)
        print("[빌드] src/ 폴더 복사 완료")

    # app.py 복사 (Flask 앱)
    shutil.copy2("app.py", dist_dir)
    print("[빌드] app.py 복사 완료")

    # .env.example 복사
    if os.path.exists(".env.example"):
        shutil.copy2(".env.example", os.path.join(dist_dir, ".env.example"))

    # config/, data/ 디렉토리 생성
    os.makedirs(os.path.join(dist_dir, "config"), exist_ok=True)
    os.makedirs(os.path.join(dist_dir, "data"), exist_ok=True)

    # config 예제 파일 복사
    for f in ["accounts.example.json", "proxies.example.txt"]:
        src_path = os.path.join("config", f)
        if os.path.exists(src_path):
            shutil.copy2(src_path, os.path.join(dist_dir, "config", f))

    # install_adb.bat 복사
    if os.path.exists("install_adb.bat"):
        shutil.copy2("install_adb.bat", os.path.join(dist_dir, "install_adb.bat"))
        print("[빌드] install_adb.bat 복사 완료")

    # 폴더 아이콘 설정 (app_icon.ico + desktop.ini)
    if os.path.exists("app_icon.ico"):
        shutil.copy2("app_icon.ico", os.path.join(dist_dir, "app_icon.ico"))
        desktop_ini = os.path.join(dist_dir, "desktop.ini")
        with open(desktop_ini, "w", encoding="utf-8") as f:
            f.write("[.ShellClassInfo]\n")
            f.write("IconResource=app_icon.ico,0\n")
        # desktop.ini와 폴더에 시스템 속성 설정 (Windows에서 폴더 아이콘 표시 필요)
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(desktop_ini, 0x02 | 0x04)  # HIDDEN | SYSTEM
            ctypes.windll.kernel32.SetFileAttributesW(dist_dir, 0x01)  # READONLY (폴더 커스텀 아이콘 트리거)
        print("[빌드] 폴더 아이콘 설정 완료 (app_icon.ico)")

    # Playwright 브라우저 번들링 안내
    print()
    print("=" * 60)
    print(f"[빌드] 완료: dist/{APP_NAME}/")
    print("=" * 60)
    print()
    print("Playwright 브라우저 설치 (배포 폴더에서 실행):")
    print(f"  cd dist/{APP_NAME}")
    print(f"  {APP_NAME}.exe  (또는 python desktop.py)")
    print()
    print("배포 방법:")
    print(f"  1. dist/{APP_NAME}/ 폴더를 zip으로 압축")
    print(f"  2. 랜딩페이지에 업로드")
    print(f"  3. 사용자: zip 다운로드 → 압축해제 → {APP_NAME}.exe 실행")
    print()
    print("주의: Playwright Chromium은 사용자 PC에서 최초 실행 시 자동 설치됩니다.")


if __name__ == "__main__":
    build()
