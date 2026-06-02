"""
YouTube 브라우저 자동화 모듈
- 시크릿 모드로 브라우저 실행
- 안티디텍트 브라우저 지문 적용
- YouTube 로그인
- 댓글 작성
- 댓글 URL 추출

IP 가이드라인 + 유튜브 바이럴 가이드라인 반영:
- 시크릿 모드 필수 사용
- 계정 전환 시 브라우저 완전 종료 후 재시작
- 프록시를 통한 IP 분리
- 안티디텍트 브라우저 지문으로 계정 간 연결 차단
"""

import os
import sys
import json
import time
import re
import random
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from rich.console import Console

console = Console()


class YouTubeBot:
    def __init__(self, fingerprint_manager=None, account_label=None, user_id=None):
        self.headless = os.getenv("HEADLESS", "false").lower() == "true"
        self.page_timeout = int(os.getenv("PAGE_LOAD_TIMEOUT", "30")) * 1000
        self.delay_after_comment = int(os.getenv("DELAY_AFTER_COMMENT", "5"))
        self.fingerprint_manager = fingerprint_manager
        self.account_label = account_label
        self.user_id = user_id  # 유저별 쿠키 격리용
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        # IP 분리 프록시 (USB 테더링 강제 송출)
        self._binding_proxy = None
        self._proxy_external_ip = None  # 프록시를 통해 본 외부 IP (검증용)

    @staticmethod
    def _find_chromium_executable():
        """Playwright Chromium 실행 파일 경로를 찾습니다. EXE 환경에서도 동작."""
        import glob
        # 1. ms-playwright 기본 경로 탐색
        base_paths = [
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright"),
            os.path.join(os.path.expanduser("~"), "AppData", "Local", "ms-playwright"),
        ]
        for base in base_paths:
            pattern = os.path.join(base, "chromium-*", "chrome-win", "chrome.exe")
            matches = sorted(glob.glob(pattern), reverse=True)
            if matches:
                return matches[0]
        return None

    @staticmethod
    def _auto_install_chromium():
        """Chromium이 없으면 자동 설치를 시도합니다."""
        import subprocess, sys
        console.print("[yellow]Chromium 자동 설치 시도 중...[/yellow]")
        try:
            # EXE 환경에서는 sys.executable이 CommentBoost.exe이므로
            # playwright CLI를 직접 찾아서 실행
            playwright_cmd = None

            # 1) _internal 폴더의 playwright 실행 파일
            exe_dir = os.path.dirname(sys.executable)
            internal_pw = os.path.join(exe_dir, "_internal", "playwright", "driver", "package", "cli.js")
            node_exe = os.path.join(exe_dir, "_internal", "playwright", "driver", "node.exe")
            if os.path.exists(internal_pw) and os.path.exists(node_exe):
                playwright_cmd = [node_exe, internal_pw, "install", "chromium"]
            else:
                # 2) pip 설치 환경
                import shutil
                pw_path = shutil.which("playwright")
                if pw_path:
                    playwright_cmd = [pw_path, "install", "chromium"]
                else:
                    # 3) python -m playwright (개발 환경)
                    playwright_cmd = [sys.executable, "-m", "playwright", "install", "chromium"]

            console.print(f"[dim]설치 명령: {' '.join(playwright_cmd[:3])}...[/dim]")

            # Windows: CMD 창이 뜨지 않도록 CREATE_NO_WINDOW + startupinfo 사용
            creation_flags = 0
            si = None
            if sys.platform == "win32":
                creation_flags = subprocess.CREATE_NO_WINDOW
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 0

            result = subprocess.run(
                playwright_cmd,
                capture_output=True, text=True, timeout=180,
                creationflags=creation_flags,
                startupinfo=si,
            )
            if result.returncode == 0:
                console.print("[green]Chromium 자동 설치 완료![/green]")
                return True
            else:
                err_detail = (result.stderr or result.stdout or "알 수 없는 오류")[:300]
                console.print(f"[red]Chromium 설치 실패: {err_detail}[/red]")
        except subprocess.TimeoutExpired:
            console.print("[red]Chromium 설치 시간 초과 (3분). 네트워크를 확인해주세요.[/red]")
        except Exception as e:
            console.print(f"[red]Chromium 설치 실패: {e}[/red]")
        return False

    @staticmethod
    def check_chromium_ready():
        """Chromium 설치 상태를 확인합니다. 미설치 시 자동 설치 시도.
        Returns: (ready: bool, message: str)
        """
        chromium_path = YouTubeBot._find_chromium_executable()
        if chromium_path and os.path.exists(chromium_path):
            return True, f"Chromium 준비됨: {chromium_path}"

        # 미설치 → 자동 설치 시도
        console.print("[yellow]Chromium이 설치되어 있지 않습니다. 자동 설치를 시작합니다...[/yellow]")
        if YouTubeBot._auto_install_chromium():
            chromium_path = YouTubeBot._find_chromium_executable()
            if chromium_path:
                return True, f"Chromium 자동 설치 완료: {chromium_path}"
        return False, (
            "Chromium 브라우저를 설치할 수 없습니다. "
            "해결 방법: 명령 프롬프트(CMD)에서 'playwright install chromium' 실행, "
            "또는 프로그램을 삭제 후 랜딩페이지에서 다시 다운로드해주세요."
        )

    def _setup_ip_isolation_proxy(self):
        """USB 테더링 인터페이스를 자동 감지해서 IP 분리 프록시 시작.

        Returns:
            Playwright proxy 설정 dict (성공 시) 또는 None (비활성 / 실패)
        """
        # 환경변수로 비활성화 가능
        if os.getenv("IP_ISOLATION", "true").lower() != "true":
            console.print("[dim]IP 분리 비활성 (IP_ISOLATION=false)[/dim]")
            return None

        try:
            from src.network_utils import find_usb_tethering_interface, get_external_ip_via_interface, get_external_ip_normal
            from src.binding_proxy import BindingProxy
        except Exception as e:
            console.print(f"[yellow]IP 분리 모듈 로드 실패: {e}[/yellow]")
            return None

        usb = find_usb_tethering_interface()
        if not usb:
            console.print("[yellow]⚠ USB 테더링 인터페이스 미감지 - IP 분리 비활성[/yellow]")
            console.print("[yellow]   휴대폰을 USB로 연결하고 테더링을 켜주세요[/yellow]")
            return None

        console.print(f"[cyan]USB 테더링 감지: {usb.name} ({usb.ip})[/cyan]")

        # 사전 검증: 인터페이스로 실제 외부 통신 가능한지
        normal_ip = get_external_ip_normal(timeout=4)
        bound_ip = get_external_ip_via_interface(usb.if_index, usb.ip, timeout=6)
        if not bound_ip:
            console.print(f"[red]⚠ {usb.name} 인터페이스로 외부 연결 실패 - IP 분리 비활성[/red]")
            return None
        if bound_ip == normal_ip:
            console.print(f"[red]⚠ 강제 송출이 작동하지 않습니다 (IP 동일: {bound_ip}) - IP 분리 비활성[/red]")
            return None

        console.print(f"[green]✓ IP 분리 검증 OK (일반 {normal_ip} → LTE {bound_ip})[/green]")
        self._proxy_external_ip = bound_ip

        # 프록시 시작
        self._binding_proxy = BindingProxy(if_index=usb.if_index, source_ip=usb.ip)
        if not self._binding_proxy.start(wait_seconds=5):
            console.print("[red]⚠ 바인딩 프록시 시작 실패[/red]")
            self._binding_proxy = None
            return None

        proxy_url = self._binding_proxy.url
        console.print(f"[green]✓ 바인딩 프록시 시작: {proxy_url}[/green]")
        return {"server": proxy_url}

    def start_browser(self):
        """안티디텍트 지문이 적용된 시크릿 모드 브라우저를 시작합니다.

        IP 분리 프록시(USB 테더링 강제 송출)가 활성화돼 있으면 자동으로 적용합니다.
        환경변수 IP_ISOLATION=true (기본값) 일 때만 작동.
        """
        console.print(f"[blue]브라우저 시작 (headless={self.headless})...[/blue]")

        try:
            self.playwright = sync_playwright().start()
        except Exception as e:
            raise RuntimeError(
                f"Playwright 초기화 실패: {e}. "
                "Chromium 브라우저가 설치되지 않았을 수 있습니다. "
                "CMD에서 'playwright install chromium' 실행 후 재시도하세요."
            ) from e

        # ── IP 분리 프록시 시작 (USB 테더링 강제 송출) ──
        proxy_config = self._setup_ip_isolation_proxy()

        launch_args = {
            "headless": self.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--disable-extensions",
                "--start-maximized",
                "--disable-gpu-compositing",
                "--use-gl=swiftshader",
                # WebRTC IP 누출 방지 (프록시 우회 차단)
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                "--webrtc-ip-handling-policy=disable_non_proxied_udp",
                # QUIC 비활성화 (UDP 기반이라 프록시 우회 가능)
                "--disable-quic",
            ],
        }
        if proxy_config:
            launch_args["proxy"] = proxy_config

        # EXE 환경: Chromium 실행 파일 경로를 명시적으로 설정
        chromium_path = self._find_chromium_executable()
        if chromium_path:
            launch_args["executable_path"] = chromium_path
            console.print(f"[dim]Chromium 경로: {chromium_path}[/dim]")
        else:
            console.print("[yellow]Chromium 경로를 찾을 수 없습니다. 자동 설치를 시도합니다...[/yellow]")

        try:
            self.browser = self.playwright.chromium.launch(**launch_args)
        except Exception as e:
            err_msg = str(e)
            console.print(f"[red]Chromium 브라우저 실행 실패: {err_msg[:200]}[/red]")
            # Chromium 미설치 시 자동 설치 후 재시도
            if self._auto_install_chromium():
                chromium_path = self._find_chromium_executable()
                if chromium_path:
                    launch_args["executable_path"] = chromium_path
                try:
                    self.browser = self.playwright.chromium.launch(**launch_args)
                    console.print("[green]Chromium 재시도 성공![/green]")
                    return self._setup_browser_context()
                except Exception as e2:
                    console.print(f"[red]Chromium 재시도도 실패: {e2}[/red]")
            self.playwright.stop()
            self.playwright = None
            raise RuntimeError(
                f"Chromium 브라우저를 실행할 수 없습니다: {err_msg[:200]}. "
                "해결 방법: CMD에서 'playwright install chromium' 실행, "
                "또는 프로그램을 삭제 후 다시 다운로드해주세요."
            ) from e

        self._setup_browser_context()

    def _setup_browser_context(self):
        """브라우저 컨텍스트와 페이지를 생성합니다."""
        # 안티디텍트 지문 기반 컨텍스트 설정
        if self.fingerprint_manager and self.account_label:
            context_args = self.fingerprint_manager.get_playwright_context_args(
                self.account_label
            )
        else:
            context_args = {
                "locale": "ko-KR",
                "timezone_id": "Asia/Seoul",
                "viewport": {"width": 1280, "height": 800},
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0.0.0 Safari/537.36"
                ),
            }

        self.context = self.browser.new_context(**context_args)
        self.context.set_default_timeout(self.page_timeout)

        # 안티디텍트 스크립트 주입 (모든 페이지에 적용)
        if self.fingerprint_manager and self.account_label:
            antidetect_script = self.fingerprint_manager.get_antidetect_scripts(
                self.account_label
            )
            self.context.add_init_script(antidetect_script)
            console.print(f"[green]안티디텍트 지문 적용됨: {self.account_label}[/green]")

        self.page = self.context.new_page()
        self.page.set_viewport_size({"width": 1280, "height": 900})
        console.print("[green]시크릿 모드 브라우저 시작됨[/green]")

    def close_browser(self):
        """
        브라우저를 완전히 종료합니다.

        IP 가이드라인: 계정 전환 시 모든 창을 완전히 닫아야 함
        - 시크릿 모드 창 전부 닫기
        - 비행기모드 ON/OFF (= 프록시 변경)
        - 새 시크릿 모드로 재시작
        """
        try:
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass
        finally:
            self.playwright = None
            self.browser = None
            self.context = None
            self.page = None

        # 바인딩 프록시 종료
        if self._binding_proxy:
            try:
                self._binding_proxy.stop()
            except Exception:
                pass
            self._binding_proxy = None

        console.print("[yellow]브라우저 완전 종료됨 (세션 초기화)[/yellow]")

    def _get_cookie_path(self, account_label=None):
        """계정별 쿠키 파일 경로를 반환합니다."""
        label = account_label or self.account_label or "default"
        # 파일명에 사용할 수 없는 문자 제거
        safe_label = re.sub(r'[^\w\-]', '_', label)
        # Fly.io 볼륨 마운트(/data) 우선, 없으면 로컬 config 폴더
        # AppData 우선, Fly.io 볼륨, 소스 폴더 순
        _appdata = os.path.join(os.environ.get("APPDATA", ""), "CommentBoost", "config") if sys.platform == "win32" else ""
        if _appdata and os.path.isdir(os.path.dirname(_appdata)):
            _base = _appdata
        elif os.path.isdir("/data"):
            _base = "/data/config"
        else:
            _base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
        # 유저별 격리: user_id가 있으면 하위 폴더로 분리
        if self.user_id:
            cookie_dir = os.path.join(_base, "sessions", str(self.user_id))
        else:
            cookie_dir = os.path.join(_base, "sessions")
        os.makedirs(cookie_dir, exist_ok=True)
        return os.path.join(cookie_dir, f"{safe_label}.json")

    def save_cookies(self):
        """현재 브라우저 쿠키를 파일에 저장합니다."""
        if not self.context:
            return False
        try:
            cookies = self.context.cookies()
            cookie_path = self._get_cookie_path()
            with open(cookie_path, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False)
            console.print(f"[green]쿠키 저장 완료: {cookie_path}[/green]")
            return True
        except Exception as e:
            console.print(f"[red]쿠키 저장 실패: {e}[/red]")
            return False

    def load_cookies(self):
        """저장된 쿠키를 브라우저에 로드합니다."""
        cookie_path = self._get_cookie_path()
        if not os.path.exists(cookie_path):
            return False
        try:
            with open(cookie_path, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            if not cookies:
                return False
            self.context.add_cookies(cookies)
            console.print(f"[green]저장된 쿠키 로드 완료 ({len(cookies)}개)[/green]")
            return True
        except Exception as e:
            console.print(f"[yellow]쿠키 로드 실패: {e}[/yellow]")
            return False

    def has_saved_cookies(self):
        """저장된 쿠키 파일이 있는지 확인합니다."""
        cookie_path = self._get_cookie_path()
        return os.path.exists(cookie_path)

    def check_login_status(self):
        """현재 YouTube 로그인 상태를 확인합니다.

        YouTube는 domcontentloaded 이후에도 JS로 Google 인증 서버에 추가 요청을
        보내 세션을 확립합니다. domcontentloaded 후 아바타를 명시적으로 폴링하여
        인증 완료 여부를 판단합니다.
        """
        try:
            self.page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=15000)
        except Exception:
            return False

        # YouTube JS 인증 완료 대기: 아바타가 나타날 때까지 폴링 (최대 12초)
        # 주의: 로그인 버튼으로 조기 판단하지 않음 — JS 인증 완료 전 일시적으로 보일 수 있음
        _AVATAR = "button#avatar-btn, ytd-topbar-menu-button-renderer img.yt-img-shadow"
        for _ in range(12):
            if self.page.query_selector(_AVATAR):
                return True
            time.sleep(1)
        return False

    def is_channel_terminated(self):
        """YouTube 채널이 정책 위반으로 삭제(terminated)되었는지 확인합니다.

        YouTube Studio(studio.youtube.com)를 방문해 계정 삭제 안내 문구를 감지합니다.
        Google 계정은 살아 있지만 YouTube 채널만 삭제된 경우에 해당합니다.
        """
        try:
            self.page.goto("https://studio.youtube.com", wait_until="domcontentloaded", timeout=20000)
        except Exception:
            return False

        # 최대 8초 대기하면서 페이지 텍스트 확인
        _TERMINATED_TEXTS = [
            "삭제되었습니다",          # 한국어: "Mikang이(가) YouTube에서 삭제되었습니다"
            "has been terminated",     # 영어
            "채널이 삭제",
            "account has been terminated",
            "violating YouTube's Terms",
        ]
        for _ in range(8):
            try:
                body_text = self.page.inner_text("body")
                if any(t in body_text for t in _TERMINATED_TEXTS):
                    return True
            except Exception:
                pass
            time.sleep(1)
        return False

    def run_warming_session(self, day_num=1, on_progress=None, interest_keywords=None):
        """계정 워밍업 세션: 자연스러운 YouTube 브라우징을 시뮬레이션합니다.

        day_num에 따라 행동 강도가 달라집니다:
          - 1~3일: 영상 시청만
          - 4~7일: 시청 + 간헐적 좋아요
          - 8일~: 시청 + 좋아요 + 구독

        Args:
            day_num: 워밍업 경과 일수
            on_progress: 콜백 함수 (video_idx, total_videos, topic) — 영상마다 호출
            interest_keywords: 계정별 관심사 키워드 리스트. 주어지면 이 키워드로 검색,
                               비어있으면 기본 토픽 사용.

        Returns:
            dict: {
                "watched": N, "liked": N, "subscribed": N,
                "error": str|None,  # "not_logged_in" | "suspended" | 기타 오류 | None
                "suspended": bool,
                "watched_items": [{"keyword":..., "title":..., "duration":..., "is_short":...}]
            }
        """
        # 사용자 지정 관심사 키워드 우선, 없으면 기본 토픽 사용
        if interest_keywords and len(interest_keywords) > 0:
            _TOPICS_KR = list(interest_keywords)
        else:
            _TOPICS_KR = [
                "맛집 추천", "여행 브이로그", "운동 루틴", "요리 레시피",
                "카페 투어", "드라마 리뷰", "음악 추천", "공부 브이로그",
                "신상 리뷰", "일상 브이로그", "영화 추천", "게임 하이라이트",
                "재테크 방법", "건강 팁", "패션 코디", "반려동물 일상",
            ]
        result = {
            "watched": 0, "liked": 0, "subscribed": 0,
            "error": None, "suspended": False,
            "longform": 0, "shortform": 0, "total_duration_sec": 0,
            "watched_items": [],
        }

        # ── Step 1: 로그인 상태 먼저 확인 (비로그인 상태 시청 방지) ──
        _AVATAR = "button#avatar-btn, ytd-topbar-menu-button-renderer img.yt-img-shadow"
        _SUSPENDED_TEXTS = [
            "계정이 정지", "account has been suspended",
            "이 계정은 정지", "suspended your account",
            "비활성화되었습니다",
        ]
        try:
            self.page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=15000)
            time.sleep(random.uniform(1, 2))
        except Exception as e:
            result["error"] = str(e)
            return result

        if not self.page.query_selector(_AVATAR):
            # 로그인 안 됨 → Google 계정 정지 여부 추가 확인
            try:
                self.page.goto("https://myaccount.google.com", wait_until="domcontentloaded", timeout=15000)
                time.sleep(1)
                body = self.page.inner_text("body") or ""
                if any(t in body for t in _SUSPENDED_TEXTS):
                    result["error"] = "suspended"
                    result["suspended"] = True
                    return result
            except Exception:
                pass
            result["error"] = "not_logged_in"
            return result

        # ── 내부 헬퍼: 광고 스킵 ──
        def _skip_ads():
            _SKIP_AD_SELS = [
                "button.ytp-skip-ad-button", ".ytp-ad-skip-button",
                ".ytp-ad-skip-button-modern", "button[class*='skip-ad']",
            ]
            for _ in range(12):
                _skipped = False
                for sel in _SKIP_AD_SELS:
                    try:
                        btn = self.page.query_selector(sel)
                        if btn:
                            btn.click(); time.sleep(0.8); _skipped = True; break
                    except Exception:
                        pass
                if _skipped:
                    break
                time.sleep(1)

        # ── 내부 헬퍼: 재생 확인 및 강제 시작 → 재생 중이면 True 반환 ──
        def _ensure_playing():
            try:
                paused = self.page.evaluate(
                    "() => { const v = document.querySelector('video'); return !v || v.paused; }"
                )
                if paused:
                    self.page.evaluate("() => { const v = document.querySelector('video'); if(v) v.play(); }")
                    time.sleep(0.8)
                return self.page.evaluate(
                    "() => { const v = document.querySelector('video'); return v && !v.paused && v.currentTime > 0; }"
                )
            except Exception:
                return True

        # ── 내부 헬퍼: 영상 시청 (60~80% 재생 후 이탈, 일시정지/재개 포함) ──
        def _watch_video(topic, is_short=False):
            """영상을 사람처럼 시청. 시청한 초(sec) 반환."""
            try:
                _raw = self.page.evaluate(
                    "() => { const v = document.querySelector('video'); "
                    "return v && isFinite(v.duration) && v.duration > 0 ? v.duration : 0; }"
                )
                duration = float(_raw) if _raw else 0.0
            except Exception:
                duration = 0.0

            if is_short:
                watch_secs = random.randint(10, 50)
            elif duration > 0:
                # 60~80% 지점에서 이탈, 최대 4분
                pct = random.uniform(0.60, 0.80)
                watch_secs = min(int(duration * pct), 240)
                watch_secs = max(watch_secs, 40)
            else:
                watch_secs = random.randint(45, 150)

            console.print(f"[dim]  워밍업: '{topic}' {watch_secs}초 시청 중...[/dim]")

            elapsed = 0
            # 일시정지 포함 여부 (30% 확률, 숏폼 제외)
            do_pause = not is_short and random.random() < 0.30
            pause_at = random.randint(watch_secs // 4, watch_secs // 2) if do_pause else 9999

            while elapsed < watch_secs:
                chunk = min(5, watch_secs - elapsed)
                time.sleep(chunk)
                elapsed += chunk

                # 일시정지 시뮬레이션
                if do_pause and elapsed >= pause_at and elapsed < pause_at + 10:
                    try:
                        self.page.evaluate("() => { const v = document.querySelector('video'); if(v) v.pause(); }")
                        pause_dur = random.randint(3, 8)
                        console.print(f"[dim]  워밍업: '{topic}' {pause_dur}초 일시정지[/dim]")
                        time.sleep(pause_dur)
                        self.page.evaluate("() => { const v = document.querySelector('video'); if(v) v.play(); }")
                        do_pause = False  # 1회만
                    except Exception:
                        pass

                # 재생 유지 확인
                try:
                    still = self.page.evaluate(
                        "() => { const v = document.querySelector('video'); return v && !v.paused; }"
                    )
                    if not still:
                        self.page.evaluate("() => { const v = document.querySelector('video'); if(v) v.play(); }")
                except Exception:
                    break

            return watch_secs

        # ── 내부 헬퍼: 댓글 섹션 스크롤 (30% 확률) ──
        def _maybe_scroll_comments():
            if random.random() > 0.30:
                return
            try:
                for _ in range(random.randint(2, 4)):
                    self.page.evaluate("() => window.scrollBy(0, window.innerHeight * 0.6)")
                    time.sleep(random.uniform(1.0, 2.5))
                # 다시 위로
                self.page.evaluate("() => window.scrollTo(0, 0)")
                time.sleep(random.uniform(0.5, 1.5))
            except Exception:
                pass

        # ── 내부 헬퍼: 숏폼 배치 시청 (5~10개 스와이프) ──
        def _watch_shorts_batch():
            try:
                self.page.goto("https://www.youtube.com/shorts", wait_until="domcontentloaded", timeout=15000)
                time.sleep(random.uniform(1.5, 3))
                count = random.randint(5, 10)
                watched = 0
                for _ in range(count):
                    _skip_ads()
                    if not _ensure_playing():
                        break
                    secs = random.randint(8, 45)
                    time.sleep(secs)
                    result["watched"] += 1
                    result["shortform"] += 1
                    result["total_duration_sec"] += secs
                    result["watched_items"].append({
                        "keyword": "숏폼",
                        "title": f"숏폼 #{watched+1}",
                        "duration": secs,
                        "is_short": True,
                    })
                    watched += 1
                    if on_progress:
                        try: on_progress(result["watched"], total_videos, "숏폼")
                        except Exception: pass
                    # 다음 숏폼으로 (아래 방향키)
                    try:
                        self.page.keyboard.press("ArrowDown")
                        time.sleep(random.uniform(0.5, 1.5))
                    except Exception:
                        break
                console.print(f"[dim]  워밍업: 숏폼 {watched}개 시청[/dim]")
            except Exception as e:
                console.print(f"[dim]  워밍업: 숏폼 배치 오류: {e}[/dim]")

        # ── 내부 헬퍼: 홈피드 탐색 (스크롤 + 추천 영상 클릭) ──
        def _browse_home():
            try:
                self.page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=15000)
                time.sleep(random.uniform(2, 4))
                # 홈피드 스크롤 (3~5번)
                for _ in range(random.randint(3, 5)):
                    self.page.evaluate("() => window.scrollBy(0, window.innerHeight * 0.7)")
                    time.sleep(random.uniform(1.0, 2.5))
                # 50% 확률로 추천 영상 1개 클릭해서 시청
                if random.random() < 0.50:
                    links = self.page.query_selector_all("a#video-title[href*='/watch?v=']")
                    if links:
                        pick = random.choice(links[:min(8, len(links))])
                        href = pick.get_attribute("href") or ""
                        title = pick.get_attribute("title") or "홈 추천"
                        if href:
                            url = f"https://www.youtube.com{href}" if href.startswith("/") else href
                            self.page.goto(url, wait_until="domcontentloaded", timeout=20000)
                            time.sleep(random.uniform(1.5, 2.5))
                            _skip_ads()
                            if _ensure_playing():
                                secs = _watch_video(title)
                                result["watched"] += 1
                                result["longform"] += 1
                                result["total_duration_sec"] += secs
                                result["watched_items"].append({
                                    "keyword": "홈 추천",
                                    "title": title,
                                    "duration": secs,
                                    "is_short": False,
                                })
                                _maybe_scroll_comments()
                                if on_progress:
                                    try: on_progress(result["watched"], total_videos, title)
                                    except Exception: pass
            except Exception as e:
                console.print(f"[dim]  워밍업: 홈피드 탐색 오류: {e}[/dim]")

        # ── Step 2: 세션 실행 ──
        try:
            watch_count = random.randint(3, 5)
            topics_used = random.sample(_TOPICS_KR, min(watch_count, len(_TOPICS_KR)))
            total_videos = watch_count + 3  # 홈피드 + 숏폼 배치 포함 예상치
            subscribed_this_session = False

            # 1) 홈피드 방문 + 탐색 (세션 시작마다)
            _browse_home()

            # 2) 검색 기반 롱폼 시청
            for idx, topic in enumerate(topics_used):
                try:
                    # 검색
                    search_url = f"https://www.youtube.com/results?search_query={topic.replace(' ', '+')}"
                    self.page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
                    time.sleep(random.uniform(1.5, 3))

                    # 검색 결과에서 영상 링크 수집
                    video_links = self.page.query_selector_all("a#video-title[href*='/watch?v=']")
                    if not video_links:
                        continue

                    pick = random.choice(video_links[:min(5, len(video_links))])
                    href = pick.get_attribute("href")
                    if not href:
                        continue
                    _picked_title = (pick.get_attribute("title") or pick.inner_text() or topic).strip()[:200]

                    video_url = f"https://www.youtube.com{href}" if href.startswith("/") else href
                    self.page.goto(video_url, wait_until="domcontentloaded", timeout=20000)
                    time.sleep(random.uniform(1.5, 2.5))

                    _skip_ads()
                    time.sleep(1)

                    _is_short = "/shorts/" in video_url
                    try:
                        _dur_raw = self.page.evaluate(
                            "() => { const v = document.querySelector('video'); "
                            "return v && isFinite(v.duration) ? v.duration : 0; }"
                        )
                        _dur = float(_dur_raw) if _dur_raw else 0.0
                        if _dur > 0:
                            _is_short = _dur <= 60
                    except Exception:
                        pass

                    if not _ensure_playing():
                        console.print(f"[dim]  워밍업: '{topic}' 재생 확인 실패 — 건너뜀[/dim]")
                        continue

                    watch_secs = _watch_video(topic, is_short=_is_short)
                    result["watched"] += 1
                    result["total_duration_sec"] += watch_secs
                    if _is_short:
                        result["shortform"] += 1
                    else:
                        result["longform"] += 1
                    result["watched_items"].append({
                        "keyword": topic,
                        "title": _picked_title,
                        "duration": watch_secs,
                        "is_short": _is_short,
                    })

                    # 댓글 스크롤
                    _maybe_scroll_comments()

                    # on_progress 콜백
                    if on_progress:
                        try:
                            on_progress(result["watched"], total_videos, topic)
                        except Exception:
                            pass

                    # 좋아요 (day 4 이상, 40% 확률)
                    if day_num >= 4 and random.random() < 0.40:
                        try:
                            like_btn = self.page.query_selector(
                                "button[aria-label*='좋아요'], "
                                "ytd-toggle-button-renderer[is-icon-button] button[aria-label*='like' i]"
                            )
                            if like_btn:
                                pressed = like_btn.get_attribute("aria-pressed")
                                if pressed != "true":
                                    like_btn.click()
                                    time.sleep(random.uniform(0.5, 1.5))
                                    result["liked"] += 1
                                    console.print("[dim]  워밍업: 좋아요 클릭[/dim]")
                        except Exception:
                            pass

                    # 구독 (day 8 이상, 세션당 1회, 30% 확률)
                    if day_num >= 8 and not subscribed_this_session and random.random() < 0.30:
                        try:
                            sub_btn = self.page.query_selector(
                                "ytd-subscribe-button-renderer button, "
                                "yt-smartimport-button-renderer button[subscribe-button-renderer]"
                            )
                            if sub_btn:
                                aria = sub_btn.get_attribute("aria-label") or ""
                                if "구독취소" not in aria and "unsubscribe" not in aria.lower():
                                    sub_btn.click()
                                    time.sleep(random.uniform(0.5, 1.5))
                                    result["subscribed"] += 1
                                    subscribed_this_session = True
                                    console.print("[dim]  워밍업: 구독 클릭[/dim]")
                        except Exception:
                            pass

                    # 40% 확률로 관련 영상 1개 추가 시청 (검색 없이 자연 탐색)
                    if random.random() < 0.40:
                        try:
                            related = self.page.query_selector_all(
                                "ytd-compact-video-renderer a#thumbnail[href*='/watch?v=']"
                            )
                            if related:
                                rel_pick = random.choice(related[:min(5, len(related))])
                                rel_href = rel_pick.get_attribute("href") or ""
                                if rel_href:
                                    rel_url = f"https://www.youtube.com{rel_href}" if rel_href.startswith("/") else rel_href
                                    self.page.goto(rel_url, wait_until="domcontentloaded", timeout=20000)
                                    time.sleep(random.uniform(1.5, 2.5))
                                    _skip_ads()
                                    if _ensure_playing():
                                        rel_secs = _watch_video("관련 영상")
                                        result["watched"] += 1
                                        result["longform"] += 1
                                        result["total_duration_sec"] += rel_secs
                                        result["watched_items"].append({
                                            "keyword": f"{topic} (관련)",
                                            "title": "관련 추천 영상",
                                            "duration": rel_secs,
                                            "is_short": False,
                                        })
                                        console.print("[dim]  워밍업: 관련 영상 추가 시청[/dim]")
                                        if on_progress:
                                            try: on_progress(result["watched"], total_videos, "관련 영상")
                                            except Exception: pass
                        except Exception:
                            pass

                    time.sleep(random.uniform(5, 15))

                except Exception as e:
                    console.print(f"[dim]  워밍업 영상 처리 오류: {e}[/dim]")
                    continue

            # 3) 숏폼 배치 (세션 마지막에 항상 실행)
            _watch_shorts_batch()

        except Exception as e:
            result["error"] = str(e)

        return result

    def manual_login(self, email=None, password=None, timeout=300):
        """
        수동 로그인 - 브라우저 화면에서 사용자가 직접 로그인합니다.
        이메일과 비밀번호를 자동 입력하고, 추가 인증(2FA 등)은 사용자가 직접 처리합니다.
        로그인 완료 후 쿠키를 저장합니다.

        Args:
            email: 이메일 (자동 입력용, 선택)
            password: 비밀번호 (자동 입력용, 선택)
            timeout: 최대 대기 시간 (초)

        Returns:
            bool: 로그인 성공 여부
        """
        console.print("[blue]수동 로그인 모드: 브라우저에서 직접 로그인해주세요[/blue]")

        # 기존 쿠키 파일 삭제 — 오염된 이전 세션이 자동 로그인에 재사용되지 않도록
        try:
            _old_cookie_path = self._get_cookie_path()
            if os.path.exists(_old_cookie_path):
                os.remove(_old_cookie_path)
                console.print(f"[dim]기존 쿠키 파일 삭제: {_old_cookie_path}[/dim]")
        except Exception:
            pass

        try:
            self.page.goto("https://accounts.google.com/signin")
            time.sleep(2)

            # 브라우저 창을 최상위로 (PyWebView 뒤에 숨는 문제 방지)
            try:
                self.page.bring_to_front()
            except Exception:
                pass

            # 이메일 자동 입력 (선택)
            if email:
                try:
                    email_input = self.page.wait_for_selector('input[type="email"]', timeout=5000)
                    email_input.fill(email)
                    self.page.click("#identifierNext")
                    console.print(f"[blue]이메일 자동 입력됨: {email[:5]}***[/blue]")
                    time.sleep(3)
                except Exception:
                    pass

            # 비밀번호 자동 입력 (선택)
            if password:
                try:
                    pw_input = self.page.wait_for_selector('input[type="password"]', timeout=5000)
                    pw_input.fill(password)
                    self.page.click("#passwordNext")
                    console.print("[blue]비밀번호 자동 입력됨[/blue]")
                    time.sleep(3)
                except Exception:
                    console.print("[yellow]비밀번호 자동 입력 실패 - 직접 입력해주세요[/yellow]")

            # 사용자가 로그인 완료할 때까지 대기
            console.print(f"[yellow]{timeout}초 내에 로그인을 완료해주세요...[/yellow]")
            for i in range(timeout // 3):
                time.sleep(3)

                # 모든 페이지에서 실제 URL을 JS로 확인 (Playwright page.url이 갱신 안 되는 문제 우회)
                login_detected = False
                active_page = self.page
                for p in self.context.pages:
                    try:
                        real_url = p.evaluate("window.location.href")
                        console.print(f"[dim]  페이지 URL(JS): {real_url[:80]}[/dim]")
                        if (
                            "myaccount.google.com" in real_url
                            or ("youtube.com" in real_url and "signin" not in real_url)
                            or "accounts.google.com/SignOutOptions" in real_url
                        ):
                            active_page = p
                            login_detected = True
                    except Exception:
                        continue

                if login_detected:
                    self.page = active_page
                    console.print("[green]로그인 감지! YouTube 세션 확립 중...[/green]")
                    try:
                        self.page.goto(
                            "https://www.youtube.com",
                            wait_until="domcontentloaded",
                            timeout=15000,
                        )
                    except Exception as e:
                        console.print(f"[yellow]YouTube 이동 실패 (무시): {e}[/yellow]")

                    # 아바타가 나타날 때까지 폴링 (최대 30초)
                    # YouTube JS 인증은 domcontentloaded 후 3~15초 소요될 수 있음
                    _AVATAR = "button#avatar-btn, ytd-topbar-menu-button-renderer img.yt-img-shadow"
                    yt_logged_in = False
                    for i in range(30):
                        if self.page.query_selector(_AVATAR):
                            yt_logged_in = True
                            break
                        if i == 10:
                            console.print("[dim]YouTube 인증 대기 중 (최대 30초)...[/dim]")
                        time.sleep(1)

                    if yt_logged_in:
                        console.print("[green]YouTube 세션 확립 완료! 쿠키 저장 중...[/green]")
                        self.save_cookies()
                        self.yt_login_result = "success"
                        return True
                    else:
                        # 30초 후에도 아바타 미확인 → sign-in 버튼 감지 시 YouTube 정지 판별
                        _yt_signin_detected = False
                        _SIGNIN_SELS = [
                            'a[href*="ServiceLogin"]',
                            'a[href*="accounts.google.com/signin"]',
                            'ytd-masthead ytd-button-renderer a',
                            '#buttons ytd-button-renderer a',
                        ]
                        try:
                            for _sel in _SIGNIN_SELS:
                                _el = self.page.query_selector(_sel)
                                if _el:
                                    try:
                                        _txt = (_el.inner_text() or "").strip()
                                        if '로그인' in _txt or 'sign in' in _txt.lower():
                                            _yt_signin_detected = True
                                            break
                                    except Exception:
                                        pass
                        except Exception:
                            pass

                        if _yt_signin_detected:
                            console.print("[red]YouTube 로그인 버튼 감지 — YouTube 계정 정지 의심[/red]")
                            self.yt_login_result = "yt_banned"
                            # 쿠키 저장 안 함 (정지 계정)
                            return True
                        else:
                            # sign-in 버튼 없음 → JS 인증 지연으로 간주, 쿠키 저장
                            console.print("[yellow]YouTube 아바타 미확인 (JS 인증 지연) — 구글 쿠키 저장 후 진행[/yellow]")
                            self.save_cookies()
                            self.yt_login_result = "success"
                            return True

            console.print("[red]로그인 시간 초과[/red]")
            return False

        except Exception as e:
            console.print(f"[red]수동 로그인 오류: {e}[/red]")
            return False

    def login_youtube(self, email, password):
        """YouTube(Google) 계정에 로그인합니다. 저장된 쿠키가 있으면 먼저 시도합니다."""
        console.print(f"[blue]YouTube 로그인 시도: {email[:5]}***[/blue]")

        # 저장된 쿠키로 먼저 시도
        if self.has_saved_cookies():
            console.print("[blue]저장된 쿠키로 로그인 시도...[/blue]")
            if self.load_cookies() and self.check_login_status():
                console.print("[green]쿠키 로그인 성공![/green]")
                return True
            console.print("[yellow]쿠키 로그인 실패, 일반 로그인 시도...[/yellow]")

        try:
            self.page.goto("https://accounts.google.com/signin")
            time.sleep(3)

            # ── 구글 세션 활성 감지: signin 접속 즉시 myaccount로 리다이렉트 ──
            # page.url 대신 JS evaluate로 현재 URL 확인 (Playwright 캐시 이슈 우회)
            try:
                current_url = self.page.evaluate("window.location.href")
            except Exception:
                current_url = self.page.url
            console.print(f"[dim]signin 후 URL: {current_url[:80]}[/dim]")

            if "myaccount.google.com" in current_url:
                console.print("[yellow]구글 세션 활성 감지 → YouTube 세션 확인 중...[/yellow]")
                # Google 앱 드로어 팝업 닫기 (ESC)
                try:
                    self.page.keyboard.press("Escape")
                    time.sleep(0.5)
                except Exception:
                    pass
                # YouTube로 직접 이동해서 세션 재확립 시도
                yt_nav_ok = False
                try:
                    self.page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=15000)
                    yt_nav_ok = True
                except Exception as _e:
                    console.print(f"[yellow]YouTube 이동 실패: {_e}[/yellow]")
                if not yt_nav_ok:
                    console.print("[red]YouTube 이동 불가 — 수동 로그인 필요[/red]")
                    return False
                # 아바타가 나타날 때까지 폴링 (최대 20초)
                _AVATAR = "button#avatar-btn, ytd-topbar-menu-button-renderer img.yt-img-shadow"
                avatar = None
                for _ in range(20):
                    avatar = self.page.query_selector(_AVATAR)
                    if avatar:
                        break
                    time.sleep(1)
                if avatar:
                    console.print("[green]YouTube 세션 재확립 성공! (구글 세션 활용)[/green]")
                else:
                    # 아바타 미확인이지만 구글 세션은 유효 → 쿠키 저장 후 진행
                    # YouTube JS 인증이 느린 경우에도 댓글 작업은 가능
                    console.print("[yellow]YouTube 아바타 미확인 — 구글 세션 쿠키로 진행[/yellow]")
                self.save_cookies()
                return True

            # 이메일 입력
            email_input = self.page.wait_for_selector('input[type="email"]', timeout=10000)
            email_input.fill(email)
            self.page.click("#identifierNext")
            time.sleep(3)

            # ── 이메일 입력 후 즉시 리다이렉트 확인 ──
            try:
                current_url = self.page.evaluate("window.location.href")
            except Exception:
                current_url = self.page.url
            if "myaccount.google.com" in current_url or "youtube.com" in current_url:
                console.print("[green]이메일 입력 후 자동 리다이렉트 → 로그인 완료[/green]")
                self.save_cookies()
                return True

            # 비밀번호 입력
            password_input = self.page.wait_for_selector(
                'input[type="password"]', timeout=10000
            )
            password_input.fill(password)
            self.page.click("#passwordNext")
            time.sleep(5)

            # 로그인 성공 확인
            try:
                current_url = self.page.evaluate("window.location.href")
            except Exception:
                current_url = self.page.url
            if "myaccount.google.com" in current_url or "youtube.com" in current_url:
                console.print("[green]YouTube 로그인 성공![/green]")
                self.save_cookies()
                return True

            # 추가 인증이 필요한 경우 (2FA 등)
            if "challenge" in current_url or "signin" in current_url:
                console.print(
                    "[yellow]추가 인증이 필요합니다. "
                    "브라우저에서 직접 완료해주세요.[/yellow]"
                )
                if not self.headless:
                    console.print("[yellow]120초 내에 인증을 완료해주세요...[/yellow]")
                    for i in range(24):
                        time.sleep(5)
                        current_url = self.page.url
                        if "myaccount.google.com" in current_url or "youtube.com" in current_url:
                            console.print("[green]인증 완료! 로그인 성공![/green]")
                            self.save_cookies()
                            return True
                    console.print("[red]인증 시간 초과[/red]")
                    return False

            console.print("[green]로그인 진행됨[/green]")
            return True

        except PlaywrightTimeout:
            console.print("[red]로그인 시간 초과[/red]")
            return False
        except Exception as e:
            console.print(f"[red]로그인 실패: {e}[/red]")
            return False

    def _watch_before_comment(self):
        """
        댓글 작성 전 영상 시청 (안전 모드).
        영상을 자연스럽게 1~3분 시청한 뒤 댓글 섹션으로 이동.
        """
        try:
            # 영상 재생 길이 파악
            _raw = self.page.evaluate(
                "() => { const v = document.querySelector('video'); "
                "return v && isFinite(v.duration) && v.duration > 0 ? v.duration : 0; }"
            )
            duration = float(_raw) if _raw else 0.0

            if duration > 0:
                # 영상 길이의 30~60% 시청, 최소 60초, 최대 180초
                pct = random.uniform(0.30, 0.60)
                watch_secs = int(min(max(duration * pct, 60), 180))
            else:
                watch_secs = random.randint(60, 120)

            console.print(f"[cyan]  [안전모드] 영상 {watch_secs}초 시청 후 댓글 작성...[/cyan]")

            # 광고 스킵 시도
            try:
                skip_btn = self.page.query_selector('.ytp-skip-ad-button, .ytp-ad-skip-button')
                if skip_btn:
                    skip_btn.click()
                    time.sleep(1)
            except Exception:
                pass

            elapsed = 0
            # 30% 확률로 일시정지 시뮬레이션
            do_pause = random.random() < 0.30
            pause_at = random.randint(watch_secs // 4, watch_secs // 2) if do_pause else 9999

            while elapsed < watch_secs:
                chunk = min(5, watch_secs - elapsed)
                time.sleep(chunk)
                elapsed += chunk

                # 일시정지 시뮬레이션 (1회)
                if do_pause and elapsed >= pause_at:
                    try:
                        self.page.evaluate("() => { const v = document.querySelector('video'); if(v) v.pause(); }")
                        pause_dur = random.randint(3, 8)
                        console.print(f"[dim]  [안전모드] {pause_dur}초 일시정지[/dim]")
                        time.sleep(pause_dur)
                        self.page.evaluate("() => { const v = document.querySelector('video'); if(v) v.play(); }")
                        do_pause = False
                    except Exception:
                        pass

                # 재생 유지 확인
                try:
                    still = self.page.evaluate(
                        "() => { const v = document.querySelector('video'); return v && !v.paused; }"
                    )
                    if not still:
                        self.page.evaluate("() => { const v = document.querySelector('video'); if(v) v.play(); }")
                except Exception:
                    break

            # 시청 완료 후 스크롤 올려서 댓글 섹션으로 이동
            console.print(f"[cyan]  [안전모드] 시청 완료 → 댓글 섹션으로 이동[/cyan]")
            self.page.evaluate("() => { document.scrollingElement.scrollTop = 0; }")
            time.sleep(random.uniform(1.0, 2.0))

        except Exception as e:
            console.print(f"[yellow]  [안전모드] 시청 중 오류 (무시): {e}[/yellow]")

    def post_comment(self, youtube_url, comment_text, typing_delay_ms=None, pre_watch=False):
        """
        유튜브 영상에 댓글을 작성하고 댓글 URL을 반환합니다.

        Args:
            pre_watch: True이면 댓글 작성 전 영상을 1~3분 자연스럽게 시청 (안전 모드)
        Returns:
            str: 댓글 URL 또는 None
        """
        # Shorts URL → 일반 watch URL 변환 (Shorts에서는 댓글 입력 UI가 다름)
        if "/shorts/" in youtube_url:
            video_id_match = re.search(r"/shorts/([a-zA-Z0-9_-]{11})", youtube_url)
            if video_id_match:
                youtube_url = f"https://www.youtube.com/watch?v={video_id_match.group(1)}"
                console.print(f"[yellow]Shorts → 일반 URL 변환: {youtube_url}[/yellow]")

        console.print(f"[blue]영상 접속: {youtube_url}[/blue]")

        try:
            self.page.goto(youtube_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(6)

            # 쿠키 동의 팝업 처리
            try:
                accept_btn = self.page.query_selector(
                    'button[aria-label*="Accept"], '
                    'button[aria-label*="동의"], '
                    'tp-yt-paper-button:has-text("동의")'
                )
                if accept_btn:
                    accept_btn.click()
                    time.sleep(1)
            except Exception:
                pass

            # 로그인 상태 확인 (로그인 안 되면 댓글 입력 불가)
            try:
                avatar = self.page.query_selector("#avatar-btn, button#avatar-btn")
                if not avatar:
                    console.print("[red]YouTube 로그인 상태 확인 불가 — 로그인이 필요합니다[/red]")
            except Exception:
                pass

            # ── 안전 모드: 댓글 작성 전 영상 시청 ──
            if pre_watch:
                self._watch_before_comment()

            # 댓글 섹션 로드 — YouTube는 document.scrollingElement을 사용
            self.page.evaluate("() => { document.scrollingElement.scrollTop = 500; }")
            time.sleep(3)
            # 댓글 placeholder가 나올 때까지 대기 (최대 15초)
            for _wait in range(5):
                has_placeholder = self.page.query_selector("#simplebox-placeholder")
                if has_placeholder:
                    break
                self.page.evaluate("() => { document.scrollingElement.scrollTop += 300; }")
                time.sleep(2)

            # 댓글이 비활성화된 영상인지 확인
            try:
                disabled_msg = self.page.query_selector(
                    "#message.ytd-comments-header-renderer, "
                    "ytd-message-renderer #text, "
                    "yt-formatted-string.ytd-comments-header-renderer"
                )
                if disabled_msg:
                    msg_text = disabled_msg.inner_text().strip()
                    if any(kw in msg_text for kw in ["사용 중지", "사용중지", "중지되었", "해제되었", "댓글을 사용", "비활성"]):
                        self._last_error = f"댓글 사용 중지 영상: {msg_text[:30]}"
                        console.print(f"[red]{self._last_error}[/red]")
                        return None
            except Exception:
                pass

            # placeholder가 없으면 댓글 비활성화
            if not has_placeholder:
                # 한 번 더 확인: 댓글 섹션 자체가 없는지
                no_comments = self.page.evaluate("""() => {
                    const msg = document.querySelector('ytd-comments #message, ytd-item-section-renderer #message');
                    return msg ? msg.textContent.trim() : '';
                }""")
                if no_comments and any(kw in no_comments for kw in ["사용", "중지", "해제", "비활성"]):
                    self._last_error = f"댓글 사용 중지 영상: {no_comments[:30]}"
                    console.print(f"[red]{self._last_error}[/red]")
                    return None

            # 댓글 입력란 클릭 (활성화) — 최대 2회 재시도
            comment_input = None
            for _click_attempt in range(2):
                try:
                    comment_placeholder = self.page.wait_for_selector(
                        "#simplebox-placeholder, ytd-comment-simplebox-renderer #placeholder-area",
                        timeout=15000,
                    )
                    comment_placeholder.click()
                    time.sleep(1)

                    # placeholder 클릭 후 contenteditable-root (실제 편집 DIV) 대기
                    # 주의: #contenteditable-textarea는 YT-FORMATTED-STRING이라 게시 버튼이 활성화 안 됨
                    comment_input = self.page.wait_for_selector(
                        "#contenteditable-root, "
                        'div[contenteditable="true"]',
                        timeout=10000,
                    )
                    break
                except PlaywrightTimeout:
                    if _click_attempt == 0:
                        console.print("[yellow]댓글 입력란 활성화 실패 — 재시도[/yellow]")
                        self.page.evaluate("window.scrollTo(0, 400)")
                        time.sleep(2)
                    else:
                        self._last_error = "댓글 입력란 활성화 실패 (로그인 상태/댓글 비활성 확인)"
                        console.print(f"[red]{self._last_error}[/red]")
                        return None

            if not comment_input:
                self._last_error = "댓글 입력란을 찾을 수 없음"
                console.print(f"[red]{self._last_error}[/red]")
                return None

            # ── 댓글 타이핑 (3단계 폴백) ──
            typing_success = False

            # 타이핑: page.evaluate 한 번으로 모든 작업 완료
            # ElementHandle을 사용하지 않음 (PyInstaller 환경에서 호환성 문제)
            _dbg = []
            time.sleep(1)  # YouTube DOM 안정화 대기

            # 방법 A: Playwright locator.fill() — 공식 권장 방식
            try:
                locator = self.page.locator('#contenteditable-root')
                locator.click()
                time.sleep(0.5)
                locator.fill(comment_text)
                time.sleep(0.5)
                typed = locator.text_content() or ""
                if typed.strip() and comment_text[:5] in typed:
                    typing_success = True
                    _dbg.append(f"OK(fill): '{typed[:20]}'")
                else:
                    _dbg.append(f"fill fail: '{typed[:15] if typed else ''}'")
            except Exception as e:
                _dbg.append(f"fill err: {e}")

            # 방법 B: locator.type() — 한 글자씩 키보드 입력
            if not typing_success:
                try:
                    locator = self.page.locator('#contenteditable-root')
                    locator.click()
                    time.sleep(0.3)
                    locator.press_sequentially(comment_text, delay=30)
                    time.sleep(0.5)
                    typed = locator.text_content() or ""
                    if typed.strip() and comment_text[:5] in typed:
                        typing_success = True
                        _dbg.append(f"OK(pressSeq): '{typed[:20]}'")
                    else:
                        _dbg.append(f"pressSeq fail: '{typed[:15] if typed else ''}'")
                except Exception as e:
                    _dbg.append(f"pressSeq err: {e}")

            # 방법 C: execCommand (JS 직접)
            if not typing_success:
                try:
                    self.page.evaluate("""(text) => {
                        const el = document.querySelector('#contenteditable-root');
                        if (!el) return;
                        el.focus(); el.click();
                        document.execCommand('selectAll'); document.execCommand('delete');
                        document.execCommand('insertText', false, text);
                    }""", comment_text)
                    time.sleep(0.5)
                    typed = self.page.evaluate("() => (document.querySelector('#contenteditable-root') || {}).textContent || ''").strip()
                    if typed and comment_text[:5] in typed:
                        typing_success = True
                        _dbg.append(f"OK(execCmd): '{typed[:20]}'")
                    else:
                        _dbg.append(f"execCmd fail: '{typed[:15] if typed else ''}'")
                        count = self.page.evaluate("() => document.querySelectorAll('#contenteditable-root').length")
                        _dbg.append(f"roots:{count}")
                except Exception as e:
                    _dbg.append(f"execCmd err: {e}")

            try:
                # 입력란 상태 확인
                ci_info = self.page.evaluate("""(el) => {
                    return el.tagName + '#' + el.id + ' ce=' + el.contentEditable +
                           ' visible=' + (el.offsetParent !== null) +
                           ' focused=' + (document.activeElement === el);
                }""", comment_input)
                _dbg.append(f"입력란: {ci_info}")

                comment_input.click()
                time.sleep(0.5)

                is_focused = self.page.evaluate("(el) => document.activeElement === el", comment_input)
                _dbg.append(f"포커스: {is_focused}")
                if not is_focused:
                    self.page.evaluate("(el) => el.focus()", comment_input)
                    time.sleep(0.3)

                self.page.evaluate("(el) => { el.textContent = ''; }", comment_input)
                time.sleep(0.3)
                td = typing_delay_ms if typing_delay_ms else random.randint(30, 80)
                # 한 글자씩 insertText (IME 불필요, CDP 직접 삽입)
                for ch in comment_text:
                    self.page.keyboard.insert_text(ch)
                    time.sleep(td / 1000.0 + random.uniform(0, 0.02))
                time.sleep(0.5)
                typed = self.page.evaluate("(el) => el.textContent || el.innerText || ''", comment_input).strip()
                _dbg.append(f"typed: '{typed[:20]}'")

                if typed and comment_text[:5] in typed:
                    typing_success = True
                    console.print(f"[dim]타이핑 완료 (insertText): {typed[:30]}...[/dim]")
                else:
                    active = self.page.evaluate("() => document.activeElement.tagName + '#' + document.activeElement.id")
                    _dbg.append(f"active: {active}")
                    console.print(f"[yellow]insertText 실패: {' | '.join(_dbg)}[/yellow]")
            except Exception as e:
                _dbg.append(f"error: {e}")
                console.print(f"[yellow]keyboard.type 실패: {' | '.join(_dbg)}[/yellow]")

            # _last_error에 디버그 정보 저장 (UI 로그에 표시됨)
            if not typing_success and _dbg:
                self._typing_debug = " | ".join(_dbg)

            # 방법 2: textContent + InputEvent 직접 발생
            if not typing_success:
                try:
                    comment_input.click()
                    time.sleep(0.3)
                    self.page.evaluate("(el) => { el.focus(); el.dispatchEvent(new FocusEvent('focus', {bubbles:true})); }", comment_input)
                    time.sleep(0.5)
                    self.page.evaluate("""([el, text]) => {
                        el.focus();
                        el.textContent = text;
                        el.dispatchEvent(new InputEvent('input', {bubbles: true, data: text, inputType: 'insertText'}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        const range = document.createRange();
                        const sel = window.getSelection();
                        range.selectNodeContents(el);
                        range.collapse(false);
                        sel.removeAllRanges();
                        sel.addRange(range);
                    }""", [comment_input, comment_text])
                    time.sleep(1)
                    typed = self.page.evaluate("(el) => el.textContent || el.innerText || ''", comment_input).strip()
                    if typed and comment_text[:5] in typed:
                        typing_success = True
                        console.print(f"[dim]타이핑 완료 (JS textContent): {typed[:30]}...[/dim]")
                    else:
                        console.print(f"[yellow]JS textContent 검증 실패: got='{typed[:30]}'[/yellow]")
                except Exception as e:
                    console.print(f"[yellow]JS textContent 실패: {e}[/yellow]")

            # 방법 3: element.type() — Playwright 내장 타이핑
            if not typing_success:
                try:
                    comment_input.click()
                    time.sleep(0.3)
                    self.page.keyboard.press("Control+a")
                    self.page.keyboard.press("Delete")
                    time.sleep(0.2)
                    td = typing_delay_ms if typing_delay_ms else random.randint(20, 50)
                    comment_input.type(comment_text, delay=td)
                    time.sleep(0.5)
                    typed = self.page.evaluate("(el) => el.textContent || el.innerText || ''", comment_input).strip()
                    if typed and comment_text[:5] in typed:
                        typing_success = True
                        console.print(f"[dim]타이핑 완료 (element.type): {typed[:30]}...[/dim]")
                except Exception as e:
                    console.print(f"[yellow]element.type 실패: {e}[/yellow]")

            # 방법 4: keyboard.type() — 페이지 레벨 키보드
            if not typing_success:
                try:
                    comment_input.click()
                    time.sleep(0.3)
                    self.page.keyboard.press("Control+a")
                    self.page.keyboard.press("Delete")
                    time.sleep(0.2)
                    self.page.keyboard.type(comment_text, delay=random.randint(20, 50))
                    time.sleep(0.5)
                    typed = self.page.evaluate("(el) => el.textContent || el.innerText || ''", comment_input).strip()
                    if typed and comment_text[:5] in typed:
                        typing_success = True
                        console.print(f"[dim]타이핑 완료 (keyboard.type): {typed[:30]}...[/dim]")
                except Exception as e:
                    console.print(f"[yellow]keyboard.type 실패: {e}[/yellow]")

            if not typing_success:
                dbg_info = getattr(self, '_typing_debug', '')
                self._last_error = f"댓글 타이핑 실패 — {dbg_info}" if dbg_info else "댓글 타이핑 실패 — 입력란 비어있음 (포커스 이탈)"
                console.print(f"[red]{self._last_error}[/red]")
                return None

            time.sleep(random.uniform(0.5, 1.5))

            # 댓글 게시 버튼 클릭 — 여러 셀렉터 시도
            submit_button = None
            submit_selectors = [
                "#submit-button ytd-button-renderer button",
                "#submit-button ytd-button-renderer",
                "ytd-comment-simplebox-renderer #submit-button button",
                "ytd-comment-simplebox-renderer #submit-button",
                "#submit-button",
                'ytd-button-renderer#submit-button a',
            ]
            for sel in submit_selectors:
                try:
                    btn = self.page.query_selector(sel)
                    if btn and btn.is_visible():
                        submit_button = btn
                        console.print(f"[dim]게시 버튼 발견: {sel}[/dim]")
                        break
                except Exception:
                    continue

            if not submit_button:
                # 최후 수단: Ctrl+Enter로 게시
                try:
                    console.print("[yellow]게시 버튼 못 찾음 — Ctrl+Enter로 게시 시도[/yellow]")
                    comment_input.click()
                    time.sleep(0.3)
                    self.page.keyboard.press("Control+Enter")
                except Exception:
                    self._last_error = "게시 버튼을 찾을 수 없음"
                    console.print(f"[red]{self._last_error}[/red]")
                    return None
            else:
                submit_button.click()
            console.print("[dim]댓글 게시 버튼 클릭됨 — 게시 확인 대기 중...[/dim]")

            # 댓글이 실제로 게시되었는지 검증 (최대 10초 대기)
            comment_verified = False
            for _verify_attempt in range(5):
                time.sleep(2)
                # 게시 성공 시: 입력란이 사라지거나 초기화됨
                try:
                    input_box = self.page.query_selector('#contenteditable-root') or self.page.query_selector('div[contenteditable="true"]')
                    if input_box:
                        input_text = input_box.inner_text().strip()
                        if not input_text or input_text != comment_text:
                            # 입력란이 비었거나 텍스트가 바뀜 → 게시됨
                            comment_verified = True
                            break
                    else:
                        comment_verified = True
                        break
                except Exception:
                    comment_verified = True
                    break

            if not comment_verified:
                self._last_error = "댓글 게시 확인 실패 — 게시 버튼 클릭 후 변화 없음"
                console.print(f"[red]{self._last_error}[/red]")
                return None

            console.print("[green]댓글 게시 확인됨[/green]")
            time.sleep(max(1, self.delay_after_comment - 10))

            # 댓글 URL 추출 (실제 lc= 파라미터가 있는 URL만 인정)
            comment_url = self._extract_comment_url(youtube_url, my_comment_text=comment_text)

            if comment_url:
                console.print(f"[green]댓글 URL 확인: {comment_url}[/green]")
            else:
                console.print("[yellow]댓글은 게시되었으나 URL 추출 실패 — 재시도[/yellow]")
                # 페이지 새로고침 후 재시도
                try:
                    self.page.reload()
                    time.sleep(3)
                    self.page.evaluate("window.scrollTo(0, 500)")
                    time.sleep(2)
                    comment_url = self._extract_comment_url(youtube_url, my_comment_text=comment_text)
                except Exception:
                    pass

                if not comment_url:
                    # 최종 폴백: 영상 URL 반환하되 로그에 경고
                    console.print("[yellow]댓글 URL 추출 최종 실패 — 영상 URL로 대체[/yellow]")
                    comment_url = self._build_fallback_url(youtube_url)

            return comment_url

        except PlaywrightTimeout:
            self._last_error = "댓글 섹션 로드 시간 초과 (15초)"
            console.print(f"[red]{self._last_error}[/red]")
            return None
        except Exception as e:
            self._last_error = str(e)[:100]
            console.print(f"[red]댓글 작성 실패: {self._last_error}[/red]")
            return None

    def post_reply(self, comment_url, reply_text):
        """
        기존 댓글에 대댓글을 작성합니다.
        comment_url: https://www.youtube.com/watch?v=VIDEO_ID&lc=COMMENT_ID
        reply_text: 대댓글 텍스트

        Returns:
            bool: 성공 여부
        """
        console.print(f"[blue]댓글 페이지 접속: {comment_url}[/blue]")

        try:
            self.page.goto(comment_url)
            time.sleep(3)

            # 쿠키 동의 팝업 처리
            try:
                accept_btn = self.page.query_selector(
                    'button[aria-label*="Accept"], '
                    'button[aria-label*="동의"], '
                    'tp-yt-paper-button:has-text("동의")'
                )
                if accept_btn:
                    accept_btn.click()
                    time.sleep(1)
            except Exception:
                pass

            # 댓글 섹션까지 스크롤
            self.page.evaluate("window.scrollTo(0, 500)")
            time.sleep(3)

            # lc= 파라미터가 있으면 해당 댓글이 하이라이트됨
            # 답글 버튼 찾기 - 하이라이트된 댓글의 답글 버튼
            reply_btn = None

            # 방법 1: 하이라이트된 댓글 스레드의 답글 버튼
            try:
                reply_btn = self.page.wait_for_selector(
                    'ytd-comment-thread-renderer:first-child #reply-button-end button, '
                    'ytd-comment-thread-renderer:first-child ytd-button-renderer#reply-button-end, '
                    'ytd-comment-thread-renderer:first-child [id="reply-button-end"] button',
                    timeout=10000,
                )
            except PlaywrightTimeout:
                pass

            # 방법 2: 첫 번째 댓글의 답글 버튼
            if not reply_btn:
                try:
                    reply_btn = self.page.wait_for_selector(
                        '#reply-button-end button, '
                        'ytd-button-renderer#reply-button-end',
                        timeout=5000,
                    )
                except PlaywrightTimeout:
                    console.print("[red]답글 버튼을 찾을 수 없습니다[/red]")
                    return False

            reply_btn.click()
            time.sleep(1)

            # 답글 입력란에 텍스트 입력
            reply_input = self.page.wait_for_selector(
                '#contenteditable-root, '
                'div[contenteditable="true"]',
                timeout=10000,
            )
            reply_input.click()
            self.page.keyboard.type(reply_text, delay=random.randint(30, 120))
            time.sleep(random.uniform(0.5, 2.0))

            # 답글 게시 버튼 클릭
            submit_btn = self.page.wait_for_selector(
                '#submit-button ytd-button-renderer, '
                '#reply-dialog #submit-button',
                timeout=5000,
            )
            submit_btn.click()

            console.print("[green]대댓글 작성 요청 전송됨[/green]")
            time.sleep(self.delay_after_comment)

            return True

        except PlaywrightTimeout:
            console.print("[red]대댓글 작성 시간 초과[/red]")
            return False
        except Exception as e:
            console.print(f"[red]대댓글 작성 실패: {e}[/red]")
            return False

    def _extract_comment_url(self, video_url, my_comment_text=None):
        """
        작성된 내 댓글의 URL을 추출합니다.
        my_comment_text가 주어지면 해당 텍스트를 포함하는 댓글만 매칭합니다.
        """
        try:
            self.page.evaluate("window.scrollTo(0, 500)")
            time.sleep(2)

            # 비디오 ID 추출
            vid_match = re.search(r"v=([a-zA-Z0-9_-]{11})", video_url)
            video_id = vid_match.group(1) if vid_match else ""

            # 댓글 스레드 전체를 가져와서 텍스트 매칭
            comment_threads = self.page.query_selector_all("ytd-comment-thread-renderer")

            for thread in comment_threads[:10]:
                try:
                    content_el = thread.query_selector("#content-text")
                    if not content_el:
                        continue
                    comment_content = content_el.inner_text().strip()
                except Exception:
                    continue

                # 내 댓글 텍스트와 매칭 (앞 15자 비교)
                if my_comment_text:
                    my_prefix = my_comment_text[:15].strip()
                    if my_prefix not in comment_content:
                        continue
                    console.print(f"[green]내 댓글 확인됨: {comment_content[:30]}...[/green]")

                # 매칭된 댓글의 URL 추출 — 여러 셀렉터 시도
                for selector in [
                    "a[href*='&lc=']",
                    "a.yt-simple-endpoint[href*='lc=']",
                    "#header-author a",
                    "a[href*='/watch']",
                ]:
                    link = thread.query_selector(selector)
                    if link:
                        href = link.get_attribute("href") or ""
                        if "&lc=" in href:
                            if href.startswith("/"):
                                return f"https://www.youtube.com{href}"
                            return href

                # lc= 링크를 못 찾아도 댓글은 확인됨 → JS로 comment ID 추출 시도
                try:
                    comment_id = self.page.evaluate("""(thread) => {
                        const el = thread.querySelector('#comment');
                        if (el && el.data && el.data.commentId) return el.data.commentId;
                        const actionMenu = thread.querySelector('#action-menu');
                        if (actionMenu) {
                            const parentComment = actionMenu.closest('ytd-comment-renderer');
                            if (parentComment && parentComment.data && parentComment.data.commentId)
                                return parentComment.data.commentId;
                        }
                        // data attribute에서 찾기
                        const renderer = thread.querySelector('ytd-comment-renderer');
                        if (renderer) {
                            const keys = Object.keys(renderer.__data || {});
                            for (const k of keys) {
                                if (k === 'commentId' || k === 'comment-id') return renderer.__data[k];
                            }
                        }
                        return null;
                    }""", thread)
                    if comment_id:
                        url = f"https://www.youtube.com/watch?v={video_id}&lc={comment_id}"
                        console.print(f"[green]댓글 ID 추출 (JS): {comment_id}[/green]")
                        return url
                except Exception:
                    pass

                # 내 댓글을 찾았지만 URL을 못 얻은 경우 — 페이지 URL에서 lc= 확인
                try:
                    current_url = self.page.url
                    if "&lc=" in current_url:
                        return current_url
                except Exception:
                    pass

                # 댓글 확인됨 + URL 미추출 → 내 댓글인 건 확인
                console.print("[yellow]내 댓글 확인됨, URL 추출 실패 — 영상 URL로 대체[/yellow]")
                return f"https://www.youtube.com/watch?v={video_id}&lc=MY_COMMENT_VERIFIED"

            if my_comment_text:
                console.print(f"[red]내 댓글을 찾을 수 없습니다: '{my_comment_text[:30]}...'[/red]")
            return None

        except Exception as e:
            console.print(f"[yellow]댓글 URL 추출 중 오류: {e}[/yellow]")
            return None

    def _build_fallback_url(self, video_url):
        """영상 URL을 기반으로 fallback URL을 생성합니다."""
        video_id_match = re.search(
            r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})", video_url
        )
        if video_id_match:
            video_id = video_id_match.group(1)
            return f"https://www.youtube.com/watch?v={video_id}"
        return video_url

    def get_top_comment_likes(self, count=5):
        """
        현재 페이지의 상위 댓글 좋아요 수를 스크래핑합니다.
        post_comment() 이후 페이지가 열린 상태에서 호출해야 합니다.

        Args:
            count: 스크래핑할 상위 댓글 수 (기본 5개)

        Returns:
            list[int]: 좋아요 수 리스트 (높은 순 정렬), 실패 시 빈 리스트
        """
        try:
            # 댓글 섹션이 로드되어 있는 상태에서 좋아요 수 추출
            likes_list = self.page.evaluate(f"""
                (() => {{
                    const comments = document.querySelectorAll(
                        'ytd-comment-thread-renderer #vote-count-middle'
                    );
                    const result = [];
                    for (let i = 0; i < Math.min(comments.length, {count}); i++) {{
                        const text = comments[i].innerText.trim();
                        result.push(text);
                    }}
                    return result;
                }})()
            """)

            parsed = []
            for text in likes_list:
                parsed.append(self._parse_like_count(text))
            parsed.sort(reverse=True)

            console.print(f"[blue]상위 댓글 좋아요: {parsed}[/blue]")
            return parsed

        except Exception as e:
            console.print(f"[yellow]좋아요 스크래핑 실패: {e}[/yellow]")
            return []

    def get_top_comments_with_text(self, count=5):
        """
        현재 페이지의 상위 댓글 좋아요 수 + 댓글 텍스트를 함께 스크래핑합니다.

        Returns:
            list[dict]: [{"text": "댓글내용...", "likes": 1300}, ...] (좋아요 높은 순)
            실패 시 빈 리스트
        """
        try:
            raw = self.page.evaluate(f"""
                (() => {{
                    const threads = document.querySelectorAll('ytd-comment-thread-renderer');
                    const result = [];
                    for (let i = 0; i < Math.min(threads.length, {count}); i++) {{
                        const likeEl = threads[i].querySelector('#vote-count-middle');
                        const textEl = threads[i].querySelector('#content-text');
                        result.push({{
                            likes_text: likeEl ? likeEl.innerText.trim() : '',
                            comment_text: textEl ? textEl.innerText.trim() : ''
                        }});
                    }}
                    return result;
                }})()
            """)

            comments = []
            for item in raw:
                likes = self._parse_like_count(item.get("likes_text", ""))
                text = item.get("comment_text", "")[:200]  # 최대 200자
                comments.append({"text": text, "likes": likes})

            comments.sort(key=lambda x: x["likes"], reverse=True)
            console.print(f"[blue]상위 댓글 (텍스트 포함): {len(comments)}건[/blue]")
            return comments

        except Exception as e:
            console.print(f"[yellow]상위 댓글 텍스트 스크래핑 실패: {e}[/yellow]")
            return []

    @staticmethod
    def _parse_like_count(text):
        """좋아요 텍스트를 숫자로 변환 (예: '1.2천' → 1200)"""
        if not text or text.strip() == "":
            return 0
        text = text.strip()
        try:
            if "천" in text:
                return int(float(text.replace("천", "").strip()) * 1000)
            elif "만" in text:
                return int(float(text.replace("만", "").strip()) * 10000)
            elif "K" in text.upper():
                return int(float(text.upper().replace("K", "").strip()) * 1000)
            elif "M" in text.upper():
                return int(float(text.upper().replace("M", "").strip()) * 1000000)
            else:
                return int(text.replace(",", ""))
        except (ValueError, AttributeError):
            return 0
