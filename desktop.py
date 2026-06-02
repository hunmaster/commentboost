"""
CommentBoost 데스크탑 앱 (PyWebView + Flask)

PyWebView로 Flask 대시보드를 네이티브 윈도우 창에서 실행합니다.
브라우저 URL바 없이 앱처럼 동작하며, HTTPS 없이 내부 통신합니다.
"""

import os
import sys
import time
import threading
import socket

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Flask 앱 임포트 전에 환경 설정
os.environ["DESKTOP_MODE"] = "1"


def find_free_port(start=5000, end=5100):
    """사용 가능한 포트 찾기"""
    for port in range(start, end):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    return start


def start_flask(port):
    """Flask 서버를 별도 스레드에서 실행"""
    from app import app, _start_scheduler

    _start_scheduler()

    # 데스크탑 모드: HTTP로 실행 (PyWebView 내부 통신)
    app.run(
        host="127.0.0.1",
        port=port,
        debug=False,
        use_reloader=False,
        threaded=True,
    )


def wait_for_server(port, timeout=15):
    """Flask 서버가 준비될 때까지 대기"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect(("127.0.0.1", port))
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.3)
    return False


# ─── 업데이트 체크 & 팝업 ───

def check_and_show_update():
    """
    앱 시작 전 업데이트 확인.
    업데이트가 있으면 tkinter 팝업을 표시하고, 사용자가 승인하면 업데이트 진행.
    Returns: True면 업데이트 완료 후 재시작 필요, False면 그냥 앱 실행
    """
    try:
        from src.updater import check_for_updates, get_current_version
    except ImportError:
        print("[Update] updater 모듈 로드 실패, 업데이트 건너뜀")
        return False

    print("[Update] 업데이트 확인 중...")
    update_info = check_for_updates()

    if not update_info.get("needs_update"):
        if update_info.get("error"):
            print(f"[Update] 확인 실패: {update_info['error']}")
        else:
            print("[Update] 최신 버전입니다.")
        return False

    current = get_current_version()
    current_ver = current.get("version", "0.0.0")
    latest_ver = update_info.get("latest_version", "?")
    changelog = update_info.get("changelog", "")

    print(f"[Update] 새 버전 발견: v{current_ver} → v{latest_ver}")

    # tkinter 팝업 표시
    return _show_update_popup(current_ver, latest_ver, changelog, update_info)


def _show_update_popup(current_ver, latest_ver, changelog, update_info):
    """tkinter 업데이트 팝업 표시. 업데이트 실행 시 True 반환."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        print("[Update] tkinter 사용 불가, 업데이트 건너뜀")
        return False

    result = {"do_update": False}

    root = tk.Tk()
    root.title("CommentBoost 업데이트")
    root.resizable(False, False)
    root.configure(bg="#1a1a2e")

    # 창 크기 및 중앙 배치
    win_w, win_h = 480, 400
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    x = (screen_w - win_w) // 2
    y = (screen_h - win_h) // 2
    root.geometry(f"{win_w}x{win_h}+{x}+{y}")

    # 아이콘 설정
    icon_path = os.path.join(os.path.dirname(__file__), "app_icon.ico")
    if os.path.exists(icon_path):
        try:
            root.iconbitmap(icon_path)
        except Exception:
            pass

    # 스타일
    bg = "#1a1a2e"
    card_bg = "#16213e"
    text_color = "#e0e0e0"
    accent = "#7c3aed"
    green = "#10b981"
    dim = "#888888"

    # 메인 프레임
    main_frame = tk.Frame(root, bg=bg, padx=30, pady=20)
    main_frame.pack(fill="both", expand=True)

    # 제목
    tk.Label(
        main_frame, text="신규 업데이트가 있습니다!",
        font=("맑은 고딕", 16, "bold"), fg="#ffffff", bg=bg,
    ).pack(pady=(10, 16))

    # 버전 정보 카드
    ver_frame = tk.Frame(main_frame, bg=card_bg, padx=20, pady=14,
                         highlightbackground="#333355", highlightthickness=1)
    ver_frame.pack(fill="x", pady=(0, 12))

    tk.Label(
        ver_frame, text=f"현재 버전:  v{current_ver}",
        font=("맑은 고딕", 11), fg=dim, bg=card_bg, anchor="w",
    ).pack(fill="x")
    tk.Label(
        ver_frame, text=f"최신 버전:  v{latest_ver}",
        font=("맑은 고딕", 11, "bold"), fg=green, bg=card_bg, anchor="w",
    ).pack(fill="x", pady=(4, 0))

    # 변경 사항
    if changelog:
        tk.Label(
            main_frame, text="변경 사항",
            font=("맑은 고딕", 10, "bold"), fg=text_color, bg=bg, anchor="w",
        ).pack(fill="x", pady=(4, 4))

        log_frame = tk.Frame(main_frame, bg=card_bg, padx=12, pady=10,
                             highlightbackground="#333355", highlightthickness=1)
        log_frame.pack(fill="x", pady=(0, 12))

        log_text = tk.Text(
            log_frame, height=5, wrap="word", bg=card_bg, fg=text_color,
            font=("맑은 고딕", 10), relief="flat", borderwidth=0,
        )
        log_text.insert("1.0", changelog)
        log_text.config(state="disabled")
        log_text.pack(fill="x")

    # 프로그레스 바 (처음엔 숨김)
    progress_frame = tk.Frame(main_frame, bg=bg)
    progress_label = tk.Label(progress_frame, text="", font=("맑은 고딕", 10), fg=text_color, bg=bg)
    progress_label.pack(fill="x", pady=(0, 4))
    progress_bar = ttk.Progressbar(progress_frame, length=400, mode="determinate")
    progress_bar.pack(fill="x")

    # 버튼 프레임
    btn_frame = tk.Frame(main_frame, bg=bg)
    btn_frame.pack(fill="x", pady=(8, 0))

    def on_update():
        result["do_update"] = True
        update_btn.config(state="disabled", text="업데이트 중...")
        skip_btn.config(state="disabled")
        btn_frame.pack_forget()
        progress_frame.pack(fill="x", pady=(8, 0))
        threading.Thread(target=_run_update, daemon=True).start()

    def on_skip():
        root.destroy()

    def _run_update():
        """업데이트 실행 (백그라운드 스레드)"""
        try:
            from src.updater import (
                get_update_server_url, _apply_update, APP_ROOT,
                _set_progress,
            )
            import requests
            import tempfile
            import zipfile

            # 1. 다운로드
            _update_label("업데이트 파일 다운로드 중...", 10)
            server_url = get_update_server_url()
            download_filename = update_info.get("download_url", "commentboost-latest.zip")
            download_url = f"{server_url}/download/{download_filename}"

            tmp_dir = tempfile.mkdtemp(prefix="commentboost_update_")
            zip_path = os.path.join(tmp_dir, "update.zip")

            resp = requests.get(download_url, stream=True, timeout=120)
            if resp.status_code != 200:
                _update_label(f"다운로드 실패 (HTTP {resp.status_code})", 0)
                _show_error_and_continue()
                return

            total_size = int(resp.headers.get("content-length", 0))
            downloaded = 0

            with open(zip_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        pct = int(10 + (downloaded / total_size) * 40)
                        _update_label(f"다운로드 중... {downloaded // 1024}KB / {total_size // 1024}KB", pct)

            # 2. 압축 해제
            _update_label("압축 해제 중...", 55)
            extract_dir = os.path.join(tmp_dir, "extracted")
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)

            entries = os.listdir(extract_dir)
            if len(entries) == 1 and os.path.isdir(os.path.join(extract_dir, entries[0])):
                source_dir = os.path.join(extract_dir, entries[0])
            else:
                source_dir = extract_dir

            # 3. 파일 적용
            _update_label("파일 업데이트 중...", 65)
            _apply_update(source_dir, APP_ROOT)

            # 4. 정리
            _update_label("정리 중...", 90)
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)

            # 5. 완료 → 재시작
            _update_label("업데이트 완료! 앱을 재시작합니다...", 100)
            time.sleep(1.5)

            root.after(0, root.destroy)
            _restart_after_update()

        except Exception as e:
            _update_label(f"업데이트 오류: {str(e)}", 0)
            _show_error_and_continue()

    def _update_label(text, pct):
        """스레드 안전하게 UI 업데이트"""
        def _do():
            progress_label.config(text=text)
            progress_bar["value"] = pct
        root.after(0, _do)

    def _show_error_and_continue():
        """에러 발생 시 3초 후 앱을 그냥 실행"""
        def _do():
            progress_label.config(fg="#ef4444")
            root.after(3000, root.destroy)
        root.after(0, _do)

    # 업데이트 버튼
    update_btn = tk.Button(
        btn_frame, text="업데이트 하기", font=("맑은 고딕", 12, "bold"),
        bg=accent, fg="#ffffff", activebackground="#6d28d9", activeforeground="#ffffff",
        relief="flat", padx=24, pady=8, cursor="hand2", command=on_update,
    )
    update_btn.pack(side="left", expand=True, padx=(0, 8))

    # 나중에 버튼
    skip_btn = tk.Button(
        btn_frame, text="나중에", font=("맑은 고딕", 11),
        bg="#333355", fg=text_color, activebackground="#444466", activeforeground="#ffffff",
        relief="flat", padx=24, pady=8, cursor="hand2", command=on_skip,
    )
    skip_btn.pack(side="left", expand=True)

    # ESC로 닫기
    root.bind("<Escape>", lambda e: on_skip())

    # 포커스
    root.lift()
    root.attributes("-topmost", True)
    root.after(100, lambda: root.attributes("-topmost", False))
    root.focus_force()

    root.mainloop()

    return result["do_update"]


def _restart_after_update():
    """업데이트 완료 후 앱 재시작"""
    import subprocess

    app_root = os.path.dirname(os.path.abspath(__file__))

    if sys.platform == "win32":
        # EXE로 실행 중인 경우
        exe_path = sys.executable
        if exe_path.lower().endswith((".exe",)) and "python" not in exe_path.lower():
            subprocess.Popen([exe_path], cwd=app_root,
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            # python desktop.py로 실행 중
            start_bat = os.path.join(app_root, "start.bat")
            if os.path.exists(start_bat):
                subprocess.Popen(["cmd", "/c", "start", "", start_bat],
                                 cwd=app_root, creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:
                subprocess.Popen([sys.executable, __file__],
                                 cwd=app_root, creationflags=subprocess.CREATE_NEW_CONSOLE)
    else:
        subprocess.Popen([sys.executable, __file__], cwd=app_root)

    time.sleep(0.5)
    os._exit(0)


def main():
    # ── 1단계: 업데이트 체크 ──
    try:
        updated = check_and_show_update()
        if updated:
            return  # 업데이트 후 재시작됨
    except Exception as e:
        print(f"[Update] 업데이트 체크 오류 (무시): {e}")

    # ── 2단계: 앱 실행 ──
    try:
        import webview
    except ImportError:
        print("[오류] pywebview가 설치되지 않았습니다.")
        print("  설치: pip install pywebview")
        input("Enter를 눌러 종료...")
        sys.exit(1)

    port = find_free_port()
    url = f"http://127.0.0.1:{port}"

    print(f"[CommentBoost] 데스크탑 앱 시작 중... (포트: {port})")

    # Flask 서버를 데몬 스레드로 실행
    flask_thread = threading.Thread(target=start_flask, args=(port,), daemon=True)
    flask_thread.start()

    # 서버 준비 대기
    if not wait_for_server(port):
        print("[오류] Flask 서버 시작 실패")
        sys.exit(1)

    print(f"[CommentBoost] 서버 준비 완료: {url}")

    # PyWebView 네이티브 창 생성
    window = webview.create_window(
        title="CommentBoost - 댓글 부스터",
        url=url,
        width=1400,
        height=900,
        min_size=(1024, 680),
        resizable=True,
        confirm_close=True,
        text_select=True,
    )

    # 결제 시 외부 브라우저로 열기 위한 JS API
    def open_external(url):
        """외부 브라우저에서 URL 열기 (결제 페이지 등)"""
        import webbrowser
        webbrowser.open(url)

    window.expose(open_external)

    print("[CommentBoost] 앱 창 열기...")
    webview.start(debug=False)
    print("[CommentBoost] 앱 종료")


if __name__ == "__main__":
    main()
