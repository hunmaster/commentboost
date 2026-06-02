"""
댓글 안전 규칙 모듈

유튜브 바이럴 가이드라인 핵심 규칙:
1. 1일 1계정당 댓글 3~5개 이내
2. 같은 영상에 여러 계정 → 30분~1시간 간격
3. 동일/유사 문구 반복 금지 (AI 탐지)
4. 링크 삽입 자제 (99% 스팸 처리)
5. 신규/숙성 미완료 계정은 바이럴 투입 금지
"""

import re
import json
import os
import random
from datetime import datetime, timedelta
from rich.console import Console

console = Console()

# 기본 제한값
DEFAULT_MAX_COMMENTS_PER_DAY = 20  # 1일 1계정당 최대 댓글 수 (시간 간격을 두고 작업)
DEFAULT_SAME_VIDEO_INTERVAL_MIN = 10  # 같은 영상 다른 계정 간격 (분)
DEFAULT_COMMENT_INTERVAL_SEC = 180  # 연속 댓글 사이 최소 간격 (초) - 3분


class SafetyRules:
    """
    유튜브 스팸 탐지를 회피하기 위한 안전 규칙을 관리합니다.
    """

    def __init__(self):
        self.history_file = "config/comment_history.json"
        self.history = self._load_history()
        self.max_comments_per_day = int(
            os.getenv("MAX_COMMENTS_PER_DAY", DEFAULT_MAX_COMMENTS_PER_DAY)
        )
        self.same_video_interval = int(
            os.getenv("SAME_VIDEO_INTERVAL_MIN", DEFAULT_SAME_VIDEO_INTERVAL_MIN)
        )
        self.comment_interval = int(
            os.getenv("COMMENT_INTERVAL_SEC", DEFAULT_COMMENT_INTERVAL_SEC)
        )

    def _load_history(self):
        """댓글 히스토리를 로드합니다."""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, Exception):
                # 깨진 JSON 파일 → 백업 후 초기화
                try:
                    import shutil
                    shutil.copy2(self.history_file, self.history_file + ".broken")
                except Exception:
                    pass
        return {"accounts": {}, "videos": {}}

    def _save_history(self):
        """댓글 히스토리를 저장합니다."""
        try:
            os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
            # 임시 파일에 먼저 쓰고 rename (쓰기 중 크래시 방지)
            tmp = self.history_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.history, f, indent=2, ensure_ascii=False)
            if os.path.exists(self.history_file):
                os.replace(tmp, self.history_file)
            else:
                os.rename(tmp, self.history_file)
        except Exception:
            pass

    def check_all_rules(self, account_label, video_url, comment_text, skip_interval=False):
        """
        모든 안전 규칙을 검사합니다.

        Args:
            skip_interval: True이면 시간 간격 규칙을 건너뜁니다 (테스트 모드용)

        Returns:
            (bool, str): (통과 여부, 실패 시 사유)
        """
        # 규칙 1: 링크 포함 여부 검사
        passed, reason = self._check_no_links(comment_text)
        if not passed:
            return False, reason

        # 규칙 2: 1일 댓글 수 제한 (테스트 모드에서도 유지)
        passed, reason = self._check_daily_limit(account_label)
        if not passed:
            return False, reason

        # 규칙 3: 동일/유사 문구 검사 (테스트 모드에서는 건너뜀)
        # (같은 영상 간격 규칙은 중복 스캔으로 대체되어 제거됨)
        if not skip_interval:
            passed, reason = self._check_duplicate_text(comment_text)
            if not passed:
                return False, reason

        return True, "OK"

    def _check_no_links(self, comment_text):
        """
        규칙: 링크 삽입 자제
        - 유튜브 댓글에 URL 넣으면 99% 스팸 처리
        """
        url_pattern = r'https?://|www\.|\.com/|\.net/|\.org/|\.kr/|\.co/'
        if re.search(url_pattern, comment_text, re.IGNORECASE):
            return False, (
                "댓글에 링크가 포함되어 있습니다. "
                "유튜브 댓글에 URL을 넣으면 99% 스팸 처리됩니다."
            )
        return True, "OK"

    def _check_daily_limit(self, account_label):
        """
        규칙: 1일 1계정당 댓글 3~5개 이내
        - 과다 활동 = 스팸 탐지
        """
        today = datetime.now().strftime("%Y-%m-%d")
        account_history = self.history.get("accounts", {}).get(account_label, {})
        today_comments = account_history.get(today, [])

        if len(today_comments) >= self.max_comments_per_day:
            return False, (
                f"계정 '{account_label}'의 오늘 댓글 수가 "
                f"최대치({self.max_comments_per_day}개)에 도달했습니다. "
                f"내일 다시 시도하세요."
            )

        remaining = self.max_comments_per_day - len(today_comments)
        console.print(
            f"[blue]계정 '{account_label}' 오늘 댓글: "
            f"{len(today_comments)}/{self.max_comments_per_day} "
            f"(남은 횟수: {remaining})[/blue]"
        )
        return True, "OK"

    def _check_same_video_interval(self, video_url):
        """
        규칙: 같은 영상에 여러 계정 → 30분~1시간 간격
        - 시간 간격 없이 몰리면 조작으로 탐지
        """
        video_id = self._extract_video_id(video_url)
        if not video_id:
            return True, "OK"

        video_history = self.history.get("videos", {}).get(video_id, [])
        if not video_history:
            return True, "OK"

        # 마지막 댓글 시간 확인
        last_comment_time = datetime.fromisoformat(video_history[-1]["time"])
        elapsed = (datetime.now() - last_comment_time).total_seconds() / 60

        if elapsed < self.same_video_interval:
            wait_minutes = int(self.same_video_interval - elapsed)
            return False, (
                f"같은 영상에 최근 {int(elapsed)}분 전에 다른 계정으로 댓글을 달았습니다. "
                f"최소 {self.same_video_interval}분 간격이 필요합니다. "
                f"약 {wait_minutes}분 후에 다시 시도하세요."
            )

        return True, "OK"

    def _check_duplicate_text(self, comment_text):
        """
        규칙: 동일/유사 문구 반복 금지
        - Copy-paste 탐지됨
        - 매번 다른 문구 사용 필요
        """
        # 최근 50개 댓글과 비교
        all_recent_texts = []
        for account_data in self.history.get("accounts", {}).values():
            for day_comments in account_data.values():
                for comment in day_comments:
                    all_recent_texts.append(comment.get("text", ""))

        # 최근 50개만 비교
        recent_texts = all_recent_texts[-50:]

        for prev_text in recent_texts:
            similarity = self._calculate_similarity(comment_text, prev_text)
            if similarity > 0.8:  # 80% 이상 유사하면 경고
                return False, (
                    "이전에 작성한 댓글과 너무 유사합니다. "
                    "동일/유사 문구 반복 시 AI가 탐지하여 댓글이 자동 숨김 처리됩니다. "
                    "다른 문구를 사용해주세요."
                )

        return True, "OK"

    def _calculate_similarity(self, text1, text2):
        """두 텍스트의 유사도를 계산합니다 (0~1)."""
        if not text1 or not text2:
            return 0.0

        # 간단한 문자 기반 유사도 (Jaccard)
        set1 = set(text1.split())
        set2 = set(text2.split())

        if not set1 or not set2:
            return 0.0

        intersection = set1 & set2
        union = set1 | set2

        return len(intersection) / len(union)

    def record_comment(self, account_label, video_url, comment_text):
        """댓글 작성 이력을 기록합니다."""
        today = datetime.now().strftime("%Y-%m-%d")
        now = datetime.now().isoformat()
        video_id = self._extract_video_id(video_url)

        # 계정별 기록
        if "accounts" not in self.history:
            self.history["accounts"] = {}
        if account_label not in self.history["accounts"]:
            self.history["accounts"][account_label] = {}
        if today not in self.history["accounts"][account_label]:
            self.history["accounts"][account_label][today] = []

        self.history["accounts"][account_label][today].append({
            "time": now,
            "video_id": video_id,
            "text": comment_text[:100],  # 처음 100자만 저장
        })

        # 영상별 기록
        if video_id:
            if "videos" not in self.history:
                self.history["videos"] = {}
            if video_id not in self.history["videos"]:
                self.history["videos"][video_id] = []

            self.history["videos"][video_id].append({
                "time": now,
                "account": account_label,
            })

        self._save_history()
        self._cleanup_old_history()

    def _cleanup_old_history(self):
        """7일 이상 된 기록을 정리합니다."""
        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        # 계정별 기록 정리
        for account in self.history.get("accounts", {}).values():
            old_dates = [d for d in account if d < cutoff]
            for d in old_dates:
                del account[d]

        # 영상별 기록 정리
        cutoff_iso = (datetime.now() - timedelta(days=7)).isoformat()
        for video_id in list(self.history.get("videos", {}).keys()):
            self.history["videos"][video_id] = [
                c for c in self.history["videos"][video_id]
                if c.get("time", "") > cutoff_iso
            ]
            if not self.history["videos"][video_id]:
                del self.history["videos"][video_id]

        self._save_history()

    def _extract_video_id(self, url):
        """YouTube URL에서 video ID를 추출합니다."""
        match = re.search(r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})", url)
        return match.group(1) if match else None

    def get_human_delay(self, delay_type="comment"):
        """
        사람처럼 불규칙한 대기 시간을 계산합니다.

        사람의 행동 패턴을 모방:
        - 기본 간격 ±30~50% 랜덤 변동
        - 가끔 더 긴 휴식 (커피타임/화장실 시뮬레이션)
        - 타이핑 속도도 불규칙하게

        Args:
            delay_type: "comment" (연속 댓글 간격) 또는 "same_video" (같은 영상 간격)

        Returns:
            dict: {delay_sec, typing_delay_ms, description}
        """
        if delay_type == "comment":
            base = self.comment_interval
            # ±40% 범위에서 랜덤 (정규분포 근사)
            jitter = random.uniform(-0.4, 0.4)
            delay = base * (1 + jitter)

            # 5% 확률로 "긴 휴식" (1.5~2배) - 사람이 잠깐 다른 일 하는 패턴
            if random.random() < 0.05:
                delay *= random.uniform(1.5, 2.0)
                desc = f"긴 휴식 ({int(delay)}초)"
            else:
                desc = f"대기 {int(delay)}초"

            # 최소 30초는 보장
            delay = max(30, delay)

        else:
            delay = random.uniform(60, 180)
            desc = f"대기 {int(delay)}초"

        # 타이핑 속도도 랜덤 (30~120ms, 사람마다 다름)
        typing_delay = random.randint(30, 120)

        # 가끔 빠르게, 가끔 느리게 (사람의 피로도 반영)
        if random.random() < 0.15:
            typing_delay = random.randint(100, 200)  # 느린 타이핑

        return {
            "delay_sec": int(delay),
            "typing_delay_ms": typing_delay,
            "description": desc,
        }

    def get_account_status(self, account_label):
        """계정의 오늘 사용 현황을 반환합니다."""
        today = datetime.now().strftime("%Y-%m-%d")
        today_comments = (
            self.history
            .get("accounts", {})
            .get(account_label, {})
            .get(today, [])
        )
        return {
            "today_count": len(today_comments),
            "max_count": self.max_comments_per_day,
            "remaining": self.max_comments_per_day - len(today_comments),
        }

    def get_today_total_success(self):
        """모든 계정의 오늘 총 성공 댓글 수를 반환합니다."""
        today = datetime.now().strftime("%Y-%m-%d")
        total = 0
        for account_data in self.history.get("accounts", {}).values():
            today_comments = account_data.get(today, [])
            total += len(today_comments)
        return total
