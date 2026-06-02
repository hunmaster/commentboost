"""
댓글 트래킹 모듈

작성한 댓글이 실제로 유지되고 있는지 확인하고,
하이라이트(인기 댓글) 위치를 추적합니다.

트래킹 방식:
1. lc= 파라미터로 댓글 URL 직접 접속 → 해당 댓글이 하이라이트되면 생존 확인
2. 영상 페이지에서 댓글 위치(순서) 확인
3. 비로그인 시크릿 모드로 조회 (IP 부담 없음 - 일반 시청자 행동)

기능:
- 댓글 생존 확인 (삭제/숨김 감지)
- 하이라이트 위치 추적 (몇 번째 댓글인지)
- 댓글 위치 모니터링
- 트래킹 히스토리 저장
"""

import os
import sys
import glob
import json
import time
import re
import asyncio
import threading
from concurrent.futures import Future
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from rich.console import Console

console = Console()


def _find_chromium_executable():
    """Playwright Chromium 실행 파일 경로를 찾습니다. EXE/개발 환경 모두 동작."""
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


def _run_in_clean_thread(func, *args, **kwargs):
    """asyncio 루프가 없는 깨끗한 스레드에서 함수를 실행합니다.

    Playwright sync API는 실행 중인 asyncio 루프가 있으면 에러를 발생시킵니다.
    별도 스레드에서 실행하면 부모 스레드의 asyncio 루프와 격리됩니다.
    """
    # 먼저 현재 스레드에 실행 중인 루프가 있는지 확인
    try:
        running = asyncio._get_running_loop()
    except AttributeError:
        running = None

    if running is None:
        # 실행 중인 루프가 없으면 그냥 직접 실행
        return func(*args, **kwargs)

    # 실행 중인 루프가 있으면 별도 깨끗한 스레드에서 실행
    result_future = Future()

    def _worker():
        try:
            r = func(*args, **kwargs)
            result_future.set_result(r)
        except Exception as e:
            result_future.set_exception(e)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=600)  # 최대 10분 대기
    return result_future.result()


def _ensure_clean_event_loop():
    """현재 스레드의 asyncio 이벤트 루프를 깨끗한 상태로 교체합니다."""
    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
    except Exception:
        pass


class CommentTracker:
    """댓글 생존 확인 및 하이라이트 위치 추적"""

    def __init__(self):
        self.history_file = "config/tracking_history.json"
        self.history = self._load_history()
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self._log_callback = None  # 외부 로그 콜백 (app.py add_log 연결용)
        self._progress_callback = None  # 트래킹 진행 상태 콜백
        self._stop_requested = False  # 트래킹 중지 플래그

    def set_log_callback(self, callback):
        """대시보드 실행로그에 연결할 콜백을 설정합니다."""
        self._log_callback = callback

    def set_progress_callback(self, callback):
        """트래킹 진행 상태 콜백을 설정합니다. callback(progress, total)"""
        self._progress_callback = callback

    def stop_tracking(self):
        """진행 중인 트래킹을 중지 요청합니다."""
        self._stop_requested = True

    def _log(self, message, level="info"):
        """콘솔 + 대시보드 실행로그에 동시 출력"""
        color_map = {"info": "blue", "warning": "yellow", "error": "red", "debug": "dim"}
        color = color_map.get(level, "white")
        console.print(f"[{color}]{message}[/{color}]")
        if self._log_callback:
            self._log_callback(f"[트래킹] {message}", level)

    def _load_history(self):
        if os.path.exists(self.history_file):
            with open(self.history_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"comments": {}}

    def _save_history(self):
        os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump(self.history, f, indent=2, ensure_ascii=False)

    def _start_browser(self):
        """트래킹용 브라우저 (비로그인, 시크릿 모드)"""
        if self.browser:
            return  # 이미 열려있으면 재사용

        # Flask/threading 환경에서 asyncio 루프 충돌 방지
        _ensure_clean_event_loop()
        self.playwright = sync_playwright().start()

        launch_args = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        }
        # EXE 환경에서 Chromium 경로 명시 (미설치 시 자동 탐색)
        chromium_path = _find_chromium_executable()
        if chromium_path and os.path.exists(chromium_path):
            launch_args["executable_path"] = chromium_path
            console.print(f"[dim]트래킹 Chromium: {chromium_path}[/dim]")
        else:
            console.print("[yellow]Chromium 경로 자동 탐색 실패 — Playwright 기본 경로 사용[/yellow]")

        self.browser = self.playwright.chromium.launch(**launch_args)
        self.context = self.browser.new_context(
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
        )
        self.context.set_default_timeout(20000)
        self.page = self.context.new_page()

    def _close_browser(self):
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

    def _dismiss_consent(self):
        """쿠키 동의 팝업 처리"""
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

    def register_comment(self, comment_url, video_url, account_label, comment_text, initial_status="active"):
        """
        작성된 댓글을 트래킹 대상으로 등록합니다.

        Args:
            comment_url: 댓글 URL (lc= 파라미터 포함)
            video_url: 영상 URL
            account_label: 작성 계정
            comment_text: 댓글 내용
            initial_status: 초기 상태 ("active" 또는 "pending_tracking")
        """
        comment_id = self._extract_comment_id(comment_url)
        if not comment_id:
            return False

        # 이미 등록된 댓글이면 스킵
        if comment_id in self.history["comments"]:
            existing = self.history["comments"][comment_id]
            if existing["status"] != "deleted":
                return True

        video_id = self._extract_video_id(video_url or comment_url)

        # video_url이 없으면 comment_url에서 추출
        if not video_url and video_id:
            video_url = f"https://www.youtube.com/watch?v={video_id}"

        self.history["comments"][comment_id] = {
            "comment_url": comment_url,
            "video_url": video_url or "",
            "video_id": video_id,
            "account_label": account_label or "수동등록",
            "comment_text": comment_text[:200] if comment_text else "",
            "registered_at": datetime.now().isoformat(),
            "checks": [],
            "status": initial_status,
            "best_position": None,
            "last_position": None,
        }

        self._save_history()
        status_label = "트래킹 예정" if initial_status == "pending_tracking" else "등록"
        console.print(f"[green]트래킹 {status_label}: {comment_id}[/green]")
        return True

    def check_comment(self, comment_id, reuse_browser=False):
        """
        개별 댓글의 생존 상태와 위치를 확인합니다.
        asyncio 루프 충돌 방지를 위해 필요 시 별도 스레드에서 실행됩니다.
        (reuse_browser=True일 때는 이미 안전한 스레드에서 호출되므로 직접 실행)
        """
        if reuse_browser:
            # check_all 내부에서 호출 - 이미 깨끗한 스레드
            return self._check_comment_impl(comment_id, reuse_browser)
        # 외부에서 직접 호출 - 깨끗한 스레드에서 실행
        return _run_in_clean_thread(self._check_comment_impl, comment_id, reuse_browser)

    def _check_comment_impl(self, comment_id, reuse_browser=False):
        """
        개별 댓글의 생존 상태와 위치를 확인합니다.

        방식: lc= URL로 직접 접속 → 해당 댓글이 페이지에 나타나는지 확인
              + 영상 댓글 목록에서 순서(위치) 확인

        Args:
            reuse_browser: True이면 브라우저를 닫지 않음 (check_all에서 사용)

        Returns:
            dict: {alive, position, is_highlighted, status}
        """
        comment_data = self.history["comments"].get(comment_id)
        if not comment_data:
            return {"error": "등록되지 않은 댓글"}

        comment_url = comment_data["comment_url"]
        video_url = comment_data["video_url"]
        comment_text = comment_data["comment_text"]

        try:
            self._start_browser()

            # ── 1단계: lc= URL로 직접 접속하여 댓글 생존 확인 ──
            self._log(f"1단계 시작: lc= URL 접속 → {comment_url[:80]}...", "debug")
            self.page.goto(comment_url, wait_until="domcontentloaded")
            time.sleep(3)
            self._dismiss_consent()

            # 댓글 섹션으로 스크롤
            self.page.evaluate("window.scrollTo(0, 500)")
            time.sleep(3)

            alive = False
            found_text = ""
            position = -1
            is_highlighted = False

            # lc= URL 페이지에서 댓글 생존 + 위치를 확인
            # (headless에서 일반 영상 URL은 댓글 렌더링 안 되므로 lc= 페이지 활용)

            highlighted_selectors = [
                "ytd-comment-thread-renderer.ytd-item-section-renderer #content-text",
                "#content-text.ytd-comment-renderer",
            ]

            for selector in highlighted_selectors:
                elements = self.page.query_selector_all(selector)
                self._log(f"1단계 셀렉터 '{selector}' → {len(elements)}개 요소 발견", "debug")
                if elements:
                    for idx_el, el in enumerate(elements):
                        el_text = el.inner_text().strip()
                        if not el_text:
                            continue

                        if not comment_text:
                            # comment_text 없는 경우: lc= URL의 첫 번째 댓글 = 우리 댓글
                            # YouTube는 lc= URL에서 해당 댓글을 페이지 최상단에 하이라이트
                            alive = True
                            found_text = el_text
                            comment_data["comment_text"] = el_text[:200]
                            self._log(f"1단계: comment_text 없음 → 첫 댓글 자동 인식: {el_text[:60]}", "info")
                            break
                        else:
                            is_our_comment = self._text_match(comment_text, el_text)
                            if is_our_comment:
                                alive = True
                                found_text = el_text
                                break

                    if alive:
                        self._log(f"1단계 결과: 텍스트 매칭 성공, found_text={found_text[:60]}", "info")
                    else:
                        sample = elements[0].inner_text().strip()[:60] if elements else "없음"
                        self._log(
                            f"1단계 결과: 텍스트 불일치 → 블라인드 판정\n"
                            f"    기대: {comment_text[:60]}...\n"
                            f"    페이지 첫댓글: {sample}...", "warning"
                        )
                    break

            # ── 2단계: lc= 페이지에서 댓글 위치 확인 ──
            # (headless에서 일반 영상 URL은 댓글이 로드되지 않으므로
            #  lc= 페이지에 이미 로드된 댓글 목록을 활용)

            if alive:
                self._log("2단계: lc= 페이지에서 댓글 위치 확인 시작", "debug")

                # 댓글을 더 로드하기 위해 스크롤
                prev_count = 0
                for scroll_i in range(10):
                    self.page.evaluate("window.scrollBy(0, 1500)")
                    time.sleep(1.5)
                    cur_count = self.page.evaluate(
                        "document.querySelectorAll('ytd-comment-thread-renderer #content-text').length"
                    )
                    if cur_count == prev_count and cur_count > 0:
                        break
                    prev_count = cur_count

                self._log(f"2단계 댓글 {prev_count}개 로드됨", "debug")

                # 모든 댓글에서 위치 찾기
                comment_elements = self.page.query_selector_all(
                    "ytd-comment-thread-renderer #content-text"
                )

                search_text = found_text or comment_text
                if search_text and comment_elements:
                    # 처음 5개 댓글 로그
                    for dbg_idx, dbg_el in enumerate(comment_elements[:5]):
                        dbg_text = dbg_el.inner_text().strip()[:60]
                        self._log(f"  댓글[{dbg_idx}]: {dbg_text}", "debug")

                    # lc= 페이지에서 첫 번째 댓글은 하이라이트(우리 댓글)
                    # 두 번째부터가 인기순 정렬된 일반 댓글
                    # → 일반 댓글 중에서 우리 댓글 위치를 찾음
                    found_in_regular = False
                    for idx, el in enumerate(comment_elements):
                        if idx == 0:
                            continue  # 하이라이트 댓글 건너뜀
                        el_text = el.inner_text().strip()
                        if self._text_match(search_text, el_text):
                            position = idx  # 인기순 목록에서의 위치 (1-based: idx=1→1위)
                            is_highlighted = position <= 3
                            found_in_regular = True
                            self._log(f"2단계 인기순 목록에서 발견: {position}위", "info")
                            break

                    if not found_in_regular:
                        # 인기순 목록에서 못 찾았지만 하이라이트로는 확인됨
                        # → 최소한 position=1로 설정 (페이지 최상단 노출)
                        position = 1
                        is_highlighted = True
                        self._log("2단계: 인기순 목록에서 미발견 → 하이라이트 위치(1위)로 설정", "debug")

                self._log(f"2단계 최종 결과: 위치={position}", "info")

            # ── 결과 기록 ──
            check_result = {
                "checked_at": datetime.now().isoformat(),
                "alive": alive,
                "position": position if alive else -1,
                "is_highlighted": is_highlighted,
            }

            comment_data["checks"].append(check_result)

            if alive:
                comment_data["status"] = "active"
                if position > 0:
                    comment_data["last_position"] = position
                    if comment_data["best_position"] is None or position < comment_data["best_position"]:
                        comment_data["best_position"] = position
                self._log(f"✓ 정상노출 확인 (위치:{position})", "info")
            else:
                comment_data["status"] = "hidden"
                self._log(f"✗ 숨김/블라인드 판정: {comment_text[:40]}...", "warning")

            self._save_history()

            return {
                "alive": alive,
                "position": position,
                "is_highlighted": is_highlighted,
                "status": comment_data["status"],
                "total_checks": len(comment_data["checks"]),
                "comment_text": comment_data["comment_text"][:50],
            }

        except Exception as e:
            console.print(f"[red]트래킹 오류: {e}[/red]")
            return {"error": str(e)}
        finally:
            if not reuse_browser:
                self._close_browser()

    def check_all(self):
        """
        등록된 모든 댓글의 상태를 확인합니다.
        asyncio 루프 충돌 방지를 위해 필요 시 별도 스레드에서 실행됩니다.
        """
        return _run_in_clean_thread(self._check_all_impl)

    def _check_all_impl(self):
        """
        등록된 모든 댓글의 상태를 확인합니다.
        같은 영상의 댓글은 한 번의 접속으로 묶어서 확인합니다.

        Returns:
            dict: {total, active, hidden, results: [...]}
        """
        active_comments = {
            cid: data for cid, data in self.history["comments"].items()
            if data["status"] not in ("deleted", "reposted")
        }

        total = len(active_comments)
        if total == 0:
            return {"total": 0, "active": 0, "hidden": 0, "results": []}

        console.print(f"[blue]총 {total}개 댓글 트래킹 시작...[/blue]")

        self._stop_requested = False
        if self._progress_callback:
            self._progress_callback(0, total)

        results = []
        try:
            self._start_browser()

            for idx, (comment_id, data) in enumerate(active_comments.items(), 1):
                if self._stop_requested:
                    self._log(f"사용자에 의해 중지됨 ({idx-1}/{total} 완료)", "warning")
                    break

                console.print(
                    f"[dim]확인 중 ({idx}/{total}): "
                    f"{data['account_label']} - {data['comment_text'][:30]}...[/dim]"
                )
                result = self.check_comment(comment_id, reuse_browser=True)
                result["comment_id"] = comment_id
                result["account_label"] = data["account_label"]
                result["comment_text"] = data["comment_text"][:50]
                result["video_id"] = data.get("video_id", "")
                results.append(result)

                if self._progress_callback:
                    self._progress_callback(idx, total)

                if idx < total:
                    time.sleep(3)

        except Exception as e:
            console.print(f"[red]전체 트래킹 오류: {e}[/red]")
        finally:
            self._close_browser()

        active_count = sum(1 for r in results if r.get("alive"))
        hidden_count = sum(1 for r in results if not r.get("alive") and "error" not in r)

        return {
            "total": total,
            "active": active_count,
            "hidden": hidden_count,
            "results": results,
        }

    def check_selected(self, comment_ids):
        """선택된 댓글만 트래킹합니다."""
        return _run_in_clean_thread(self._check_selected_impl, comment_ids)

    def _check_selected_impl(self, comment_ids):
        """선택된 댓글 ID 목록만 트래킹합니다."""
        targets = {
            cid: self.history["comments"][cid]
            for cid in comment_ids
            if cid in self.history["comments"]
            and self.history["comments"][cid]["status"] not in ("deleted", "reposted")
        }

        total = len(targets)
        if total == 0:
            return {"total": 0, "active": 0, "hidden": 0, "results": []}

        console.print(f"[blue]선택된 {total}개 댓글 트래킹 시작...[/blue]")

        self._stop_requested = False
        if self._progress_callback:
            self._progress_callback(0, total)

        results = []
        try:
            self._start_browser()

            for idx, (comment_id, data) in enumerate(targets.items(), 1):
                if self._stop_requested:
                    self._log(f"사용자에 의해 중지됨 ({idx-1}/{total} 완료)", "warning")
                    break

                console.print(
                    f"[dim]확인 중 ({idx}/{total}): "
                    f"{data['account_label']} - {data['comment_text'][:30]}...[/dim]"
                )
                result = self.check_comment(comment_id, reuse_browser=True)
                result["comment_id"] = comment_id
                result["account_label"] = data["account_label"]
                result["comment_text"] = data["comment_text"][:50]
                result["video_id"] = data.get("video_id", "")
                results.append(result)

                if self._progress_callback:
                    self._progress_callback(idx, total)

                if idx < total:
                    time.sleep(3)

        except Exception as e:
            console.print(f"[red]선택 트래킹 오류: {e}[/red]")
        finally:
            self._close_browser()

        active_count = sum(1 for r in results if r.get("alive"))
        hidden_count = sum(1 for r in results if not r.get("alive") and "error" not in r)

        return {
            "total": total,
            "active": active_count,
            "hidden": hidden_count,
            "results": results,
        }

    def get_summary(self):
        """등록된 댓글들의 현재 요약 정보를 반환합니다."""
        comments = self.history.get("comments", {})
        summary = []

        for cid, data in comments.items():
            if data.get("status") == "deleted":
                continue
            last_check = data["checks"][-1] if data["checks"] else None
            # video_id 없으면 comment_url에서 재추출
            video_id = data.get("video_id", "")
            if not video_id:
                video_id = self._extract_video_id(data.get("comment_url", "")) or ""
            summary.append({
                "comment_id": cid,
                "account_label": data["account_label"],
                "comment_text": data["comment_text"][:50],
                "video_id": video_id,
                "comment_url": data.get("comment_url", ""),
                "status": data["status"],
                "position": data.get("last_position"),
                "best_position": data.get("best_position"),
                "registered_at": data["registered_at"],
                "last_checked": last_check["checked_at"] if last_check else None,
                "total_checks": len(data["checks"]),
            })

        return summary

    def remove_comment(self, comment_id):
        """트래킹 대상에서 제거합니다."""
        if comment_id in self.history["comments"]:
            self.history["comments"][comment_id]["status"] = "deleted"
            self._save_history()
            return True
        return False

    def _extract_comment_id(self, comment_url):
        match = re.search(r"lc=([a-zA-Z0-9_-]+)", comment_url)
        return match.group(1) if match else None

    def _extract_video_id(self, url):
        match = re.search(r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})", url)
        return match.group(1) if match else None

    def _text_match(self, stored_text, page_text):
        """
        저장된 댓글 텍스트와 페이지에서 가져온 텍스트를 비교합니다.
        YouTube가 렌더링 시 변경하는 패턴(대괄호 제거, 구두점 변경 등)을
        고려하여 정규화 후 비교합니다.

        3단계 매칭:
        1) 원본 텍스트 처음 40자 포함 여부 (기존 방식)
        2) 정규화 후 처음 30자 포함 여부
        3) 정규화 후 핵심 키워드(한글만) 비교
        """
        if not stored_text or not page_text:
            return False

        # 1단계: 원본 텍스트 매칭 (기존 방식)
        match_len = min(40, len(stored_text))
        our_prefix = stored_text[:match_len].strip()
        if our_prefix and our_prefix in page_text:
            return True
        if page_text[:match_len].strip() in stored_text and len(page_text) > 10:
            return True

        # 2단계: 정규화 후 매칭
        norm_stored = self._normalize_text(stored_text)
        norm_page = self._normalize_text(page_text)

        norm_match_len = min(30, len(norm_stored))
        norm_prefix = norm_stored[:norm_match_len]
        if norm_prefix and norm_prefix in norm_page:
            return True
        if norm_page[:norm_match_len] in norm_stored and len(norm_page) > 10:
            return True

        # 3단계: 한글만 추출하여 비교 (숫자/구두점/특수문자 완전 무시)
        korean_stored = re.sub(r'[^\uAC00-\uD7A3]', '', stored_text)
        korean_page = re.sub(r'[^\uAC00-\uD7A3]', '', page_text)

        # 한글 문자열의 앞 20자로 포함 여부 확인 (양방향)
        kr_len = min(20, len(korean_stored))
        if kr_len >= 8:
            kr_prefix_s = korean_stored[:kr_len]
            kr_prefix_p = korean_page[:kr_len]
            if kr_prefix_s in korean_page or kr_prefix_p in korean_stored:
                return True

        return False

    @staticmethod
    def _normalize_text(text):
        """
        텍스트 매칭을 위한 정규화.
        YouTube가 렌더링 시 변경하는 패턴을 통일합니다:
        - 대괄호 제거: [00:24] → 00:24
        - 구두점 제거: 쉼표, 마침표, ... 등
        - 공백 통일: 연속 공백 → 단일 공백
        - 앞뒤 공백 제거
        """
        if not text:
            return ""
        t = text
        t = t.replace("[", "").replace("]", "")  # 대괄호 제거
        t = re.sub(r'[,.\!\?\;:…·~\-–—]', '', t)  # 구두점 제거
        t = re.sub(r'\s+', ' ', t)  # 연속 공백 통일
        return t.strip()

