"""
자동 업데이트 모듈 - 앱 시작 시 버전 체크 및 원클릭 업데이트 지원

업데이트 흐름:
1. 앱 시작 → 서버에 최신 버전 확인 (비동기)
2. 새 버전 있으면 대시보드에 알림 표시
3. 사용자 승인 → ZIP 다운로드 → 코드만 덮어쓰기
4. 앱 자동 재시작

보존 대상 (절대 덮어쓰지 않음):
  .env, .license, config/, data/
"""

import os
import json
import shutil
import zipfile
import tempfile
import threading
import time
import subprocess
import sys
import requests

# 현재 앱 루트 디렉토리 (PyInstaller EXE에서는 exe가 있는 폴더 기준)
if getattr(sys, 'frozen', False):
    APP_ROOT = os.path.dirname(sys.executable)
else:
    APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_FILE = os.path.join(APP_ROOT, "version.json")

# 업데이트 서버 URL (랜딩 서버)
DEFAULT_UPDATE_SERVER = "https://commentboost-app.fly.dev"

# 절대 덮어쓰면 안 되는 파일/디렉토리 목록
PRESERVE_PATHS = {
    ".env",
    ".env.local",
    ".license",
    "config",
    "data",
    # 인증서 파일
    "cert.pem",
    "key.pem",
    # Windows 폴더 설정
    "desktop.ini",
}

# 보존해야 할 파일 확장자 (사용자 데이터)
PRESERVE_EXTENSIONS = {".db", ".sqlite", ".sqlite3"}


def get_current_version():
    """현재 설치된 앱 버전 정보 반환"""
    try:
        with open(VERSION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": "0.0.0", "build": 0}


def get_update_server_url():
    """업데이트 서버 URL (환경변수 또는 기본값)"""
    return os.environ.get("UPDATE_SERVER_URL", DEFAULT_UPDATE_SERVER)


def check_for_updates():
    """
    서버에서 최신 버전 확인.
    Returns: dict with keys: needs_update, force_update, latest_version, changelog, download_url 등
    """
    current = get_current_version()
    server_url = get_update_server_url()

    try:
        resp = requests.post(
            f"{server_url}/api/version/check",
            json={
                "version": current.get("version", "0.0.0"),
                "build": current.get("build", 0),
            },
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            data["current_version"] = current.get("version", "0.0.0")
            data["current_build"] = current.get("build", 0)
            return data
        return {
            "needs_update": False,
            "error": f"서버 응답 오류 (HTTP {resp.status_code})",
        }
    except requests.exceptions.ConnectionError:
        return {"needs_update": False, "error": "업데이트 서버에 연결할 수 없습니다."}
    except Exception as e:
        return {"needs_update": False, "error": f"버전 확인 오류: {str(e)}"}


# ─── 비동기 버전 체크 (앱 시작 시 사용) ───

_update_info = None
_update_check_done = False


def check_updates_async():
    """백그라운드에서 업데이트 확인 (앱 시작 시 호출)"""
    global _update_info, _update_check_done

    def _check():
        global _update_info, _update_check_done
        # 시작 후 3초 대기 (앱 초기화 완료 후 체크)
        time.sleep(3)
        _update_info = check_for_updates()
        _update_check_done = True
        if _update_info.get("needs_update"):
            print(f"[Update] 새 버전 발견: v{_update_info.get('latest_version')} "
                  f"(현재: v{_update_info.get('current_version')})")
        elif _update_info.get("error"):
            print(f"[Update] 버전 확인 실패: {_update_info.get('error')}")
        else:
            print("[Update] 최신 버전 사용 중")

    t = threading.Thread(target=_check, daemon=True)
    t.start()


def get_update_status():
    """현재 업데이트 상태 반환 (대시보드 API용)"""
    current = get_current_version()
    result = {
        "current_version": current.get("version", "0.0.0"),
        "current_build": current.get("build", 0),
        "check_done": _update_check_done,
    }

    if _update_check_done and _update_info:
        result.update(_update_info)
    else:
        result["needs_update"] = False

    return result


# ─── 업데이트 실행 ───

_update_progress = {
    "status": "idle",       # idle, downloading, extracting, applying, restarting, error, done
    "progress": 0,          # 0~100
    "message": "",
    "error": None,
}


def get_update_progress():
    """업데이트 진행 상태 반환"""
    return dict(_update_progress)


def _set_progress(status, progress, message, error=None):
    _update_progress["status"] = status
    _update_progress["progress"] = progress
    _update_progress["message"] = message
    _update_progress["error"] = error


def download_and_apply(update_info, progress_callback=None):
    """
    업데이트 다운로드 → 백업 → 적용 공통 로직.
    desktop.py 팝업과 perform_update() 모두 이 함수를 사용.
    progress_callback(message, percent): 진행 상황 콜백 (없으면 _set_progress 사용)
    """
    report = progress_callback or (lambda msg, pct: _set_progress("downloading", pct, msg))

    server_url = get_update_server_url()
    download_filename = update_info.get("download_url", "commentboost-latest.zip")
    download_url = f"{server_url}/download/{download_filename}"

    # 1. ZIP 다운로드
    report("업데이트 파일 다운로드 중...", 10)
    tmp_dir = tempfile.mkdtemp(prefix="commentboost_update_")
    zip_path = os.path.join(tmp_dir, "update.zip")

    resp = requests.get(download_url, stream=True, timeout=120)
    if resp.status_code != 200:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError(f"다운로드 실패 (HTTP {resp.status_code})")

    total_size = int(resp.headers.get("content-length", 0))
    downloaded = 0
    with open(zip_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
            downloaded += len(chunk)
            if total_size > 0:
                pct = int(10 + (downloaded / total_size) * 40)
                report(f"다운로드 중... {downloaded // 1024}KB / {total_size // 1024}KB", min(pct, 50))

    # 2. 압축 해제
    report("압축 해제 중...", 55)
    extract_dir = os.path.join(tmp_dir, "extracted")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)

    entries = os.listdir(extract_dir)
    if len(entries) == 1 and os.path.isdir(os.path.join(extract_dir, entries[0])):
        source_dir = os.path.join(extract_dir, entries[0])
    else:
        source_dir = extract_dir

    # 3. 백업
    report("사용자 데이터 백업 중...", 60)
    _backup_user_data()

    # 4. 파일 적용
    report("파일 업데이트 중...", 65)
    _apply_update(source_dir, APP_ROOT)

    # 5. 정리
    report("정리 중...", 90)
    shutil.rmtree(tmp_dir, ignore_errors=True)

    report("업데이트 완료!", 100)


def perform_update():
    """대시보드에서 호출하는 업데이트 (백그라운드 스레드)."""
    def _do_update():
        try:
            download_and_apply(_update_info or {})

            _set_progress("restarting", 95, "앱을 재시작합니다...")
            time.sleep(2)
            _set_progress("done", 100, "업데이트 완료! 앱을 재시작합니다.")

            # 앱 재시작
            _restart_app()

        except Exception as e:
            _set_progress("error", 0, "", f"업데이트 오류: {str(e)}")

    t = threading.Thread(target=_do_update, daemon=True)
    t.start()
    return {"status": "started", "message": "업데이트를 시작합니다."}


def _backup_user_data():
    """업데이트 전 사용자 데이터(DB, config, .env) 백업"""
    backup_dir = os.path.join(APP_ROOT, "data", "_backup")
    os.makedirs(backup_dir, exist_ok=True)

    # DB 파일 백업
    db_path = os.path.join(APP_ROOT, "data", "users.db")
    if os.path.exists(db_path):
        try:
            ts = time.strftime("%Y%m%d_%H%M%S")
            shutil.copy2(db_path, os.path.join(backup_dir, f"users_{ts}.db"))
            # 오래된 백업 정리 (최근 5개만 유지)
            backups = sorted(
                [f for f in os.listdir(backup_dir) if f.startswith("users_") and f.endswith(".db")],
                reverse=True,
            )
            for old in backups[5:]:
                os.remove(os.path.join(backup_dir, old))
        except Exception as e:
            print(f"[Update] DB 백업 실패 (무시): {e}")

    # .env 백업
    env_path = os.path.join(APP_ROOT, ".env")
    if os.path.exists(env_path):
        try:
            shutil.copy2(env_path, os.path.join(backup_dir, ".env.bak"))
        except Exception:
            pass


def _apply_update(source_dir, target_dir):
    """
    소스에서 타겟으로 파일 복사 (보존 목록 제외).
    디렉토리 구조를 유지하며 코드 파일만 업데이트.
    """
    total_files = sum(len(files) for _, _, files in os.walk(source_dir))
    processed = 0

    for root, dirs, files in os.walk(source_dir):
        # 소스 기준 상대 경로
        rel_root = os.path.relpath(root, source_dir)
        if rel_root == ".":
            rel_root = ""

        # 보존 디렉토리 건너뛰기
        top_level = rel_root.split(os.sep)[0] if rel_root else ""
        if top_level in PRESERVE_PATHS:
            continue

        # 타겟 디렉토리 생성
        target_root = os.path.join(target_dir, rel_root) if rel_root else target_dir
        os.makedirs(target_root, exist_ok=True)

        for filename in files:
            # 보존 파일 건너뛰기
            rel_file = os.path.join(rel_root, filename) if rel_root else filename
            if rel_file in PRESERVE_PATHS or filename in PRESERVE_PATHS:
                continue
            # DB 등 사용자 데이터 파일 보존
            _, ext = os.path.splitext(filename)
            if ext.lower() in PRESERVE_EXTENSIONS:
                continue

            src_file = os.path.join(root, filename)
            dst_file = os.path.join(target_root, filename)

            try:
                shutil.copy2(src_file, dst_file)
            except (PermissionError, OSError):
                # 실행 중인 파일은 건너뛰기 (Windows 잠금)
                pass

            processed += 1
            if total_files > 0:
                pct = int(60 + (processed / total_files) * 30)  # 60~90%
                _set_progress("applying", min(pct, 90),
                              f"파일 업데이트 중... ({processed}/{total_files})")

    # _internal/templates → root/templates 동기화
    # Flask는 root/templates 를 읽으므로 _internal 번들과 동기화 필요
    _int_tmpl = os.path.join(target_dir, "_internal", "templates")
    _root_tmpl = os.path.join(target_dir, "templates")
    if os.path.isdir(_int_tmpl):
        os.makedirs(_root_tmpl, exist_ok=True)
        for _fn in os.listdir(_int_tmpl):
            try:
                shutil.copy2(os.path.join(_int_tmpl, _fn), os.path.join(_root_tmpl, _fn))
            except Exception:
                pass

    # _internal/static → root/static 동기화
    _int_static = os.path.join(target_dir, "_internal", "static")
    _root_static = os.path.join(target_dir, "static")
    if os.path.isdir(_int_static):
        for _root_s, _dirs_s, _files_s in os.walk(_int_static):
            _rel = os.path.relpath(_root_s, _int_static)
            _dst_root = os.path.join(_root_static, _rel) if _rel != "." else _root_static
            os.makedirs(_dst_root, exist_ok=True)
            for _fn in _files_s:
                try:
                    shutil.copy2(os.path.join(_root_s, _fn), os.path.join(_dst_root, _fn))
                except Exception:
                    pass

    # __pycache__ 삭제 — .pyc 캐시가 오래된 코드를 유지하는 문제 방지
    for root, dirs, files in os.walk(target_dir):
        for d in dirs:
            if d == "__pycache__":
                cache_dir = os.path.join(root, d)
                try:
                    shutil.rmtree(cache_dir)
                except Exception:
                    pass


def _restart_app():
    """앱 프로세스 재시작"""
    try:
        # Windows: start.bat 재실행
        if sys.platform == "win32":
            start_bat = os.path.join(APP_ROOT, "start.bat")
            if os.path.exists(start_bat):
                subprocess.Popen(
                    ["cmd", "/c", "start", "", start_bat],
                    cwd=APP_ROOT,
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
            else:
                subprocess.Popen(
                    [sys.executable, os.path.join(APP_ROOT, "app.py")],
                    cwd=APP_ROOT,
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
        else:
            # Linux/Mac: python app.py 재실행
            subprocess.Popen(
                [sys.executable, os.path.join(APP_ROOT, "app.py")],
                cwd=APP_ROOT,
            )

        # lock 파일 정리 (재시작 시 오탐 방지)
        lock_path = os.path.join(APP_ROOT, "..", "CommentBoost", "data", ".commentboost.lock")
        # AppData 경로 시도
        appdata_lock = os.path.join(os.environ.get("APPDATA", ""), "CommentBoost", "data", ".commentboost.lock")
        for lp in [lock_path, appdata_lock]:
            try:
                if os.path.exists(lp):
                    os.remove(lp)
            except Exception:
                pass

        # 현재 프로세스 종료
        time.sleep(1)
        os._exit(0)
    except Exception as e:
        _set_progress("error", 0, "", f"재시작 실패: {str(e)}. 수동으로 start.bat을 실행해주세요.")
