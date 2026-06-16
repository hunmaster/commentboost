"""사용자 데이터베이스 모델."""
import json
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))

def _now_kst():
    return datetime.now(KST).replace(tzinfo=None)
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    VALID_PLANS = ["Free", "Business", "Agency", "Enterprise"]

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    nickname = db.Column(db.String(50), nullable=True)
    license_key = db.Column(db.String(255), nullable=True)
    plan = db.Column(db.String(50), nullable=False, default="Free")
    token_balance = db.Column(db.Integer, nullable=False, default=0)
    like_credit_balance = db.Column(db.Integer, nullable=False, default=0)
    hardware_id = db.Column(db.String(64), nullable=True, index=True)
    is_active_user = db.Column(db.Boolean, default=True)
    agreed_terms = db.Column(db.Boolean, default=False)
    agreed_at = db.Column(db.DateTime, nullable=True)
    setup_completed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=_now_kst)
    last_login = db.Column(db.DateTime, nullable=True)
    auto_login_token = db.Column(db.String(64), nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        return self.is_active_user

    # 관계
    youtube_accounts = db.relationship("YouTubeAccount", backref="owner", lazy="dynamic")
    settings = db.relationship("UserSettings", backref="owner", uselist=False, lazy="joined")

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "nickname": self.nickname,
            "plan": self.plan or "Free",
            "token_balance": self.token_balance or 0,
            "like_credit_balance": self.like_credit_balance or 0,
            "license_key": (self.license_key or "")[:8] + "..." if self.license_key else None,
            "setup_completed": self.setup_completed,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
        }


class YouTubeAccount(db.Model):
    """유저별 유튜브 계정 저장 (영속성)."""
    __tablename__ = "youtube_accounts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    account_email = db.Column(db.String(255), nullable=False)
    account_password = db.Column(db.String(500), nullable=True)
    label = db.Column(db.String(100), nullable=True)
    account_type = db.Column(db.String(50), default="google")
    cookies_saved = db.Column(db.Boolean, default=False)
    channel_terminated = db.Column(db.Boolean, default=False)
    last_comment_at = db.Column(db.DateTime, nullable=True)       # 마지막 댓글 작성 시각
    daily_comment_limit = db.Column(db.Integer, default=10)       # 계정별 일일 댓글 한도 (기본 10)
    interest_keywords = db.Column(db.Text, nullable=True)         # 관심사 키워드 (JSON array)
    created_at = db.Column(db.DateTime, default=_now_kst)
    updated_at = db.Column(db.DateTime, default=_now_kst, onupdate=_now_kst)

    def get_interest_keywords(self):
        """관심사 키워드 리스트 반환 (없으면 빈 리스트)."""
        if not self.interest_keywords:
            return []
        try:
            return json.loads(self.interest_keywords)
        except Exception:
            return []

    def set_interest_keywords(self, keywords):
        """관심사 키워드 저장 (최대 15개)."""
        if not isinstance(keywords, list):
            keywords = []
        # 중복 제거 + 공백 필터 + 최대 15개
        clean = []
        seen = set()
        for kw in keywords:
            kw_s = str(kw).strip()
            if kw_s and kw_s not in seen:
                clean.append(kw_s)
                seen.add(kw_s)
            if len(clean) >= 15:
                break
        self.interest_keywords = json.dumps(clean, ensure_ascii=False)

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.account_email,
            "label": self.label or self.account_email.split("@")[0],
            "account_type": self.account_type,
            "cookies_saved": self.cookies_saved,
            "channel_terminated": self.channel_terminated or False,
            "last_comment_at": self.last_comment_at.isoformat() if self.last_comment_at else None,
            "daily_comment_limit": self.daily_comment_limit or 10,
            "interest_keywords": self.get_interest_keywords(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def to_account_dict(self):
        """자동화 실행용 딕셔너리 (비밀번호 포함)."""
        return {
            "id": self.id,
            "email": self.account_email,
            "password": self.account_password or "",
            "label": self.label or self.account_email.split("@")[0],
            "account_type": self.account_type or "sub",
            "channel_terminated": self.channel_terminated or False,
            "last_comment_at": self.last_comment_at.isoformat() if self.last_comment_at else None,
            "daily_comment_limit": self.daily_comment_limit or 10,
            "interest_keywords": self.get_interest_keywords(),
        }


class UserSettings(db.Model):
    """유저별 설정 영속 저장 (JSON)."""
    __tablename__ = "user_settings"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True, index=True)
    settings_json = db.Column(db.Text, default="{}")
    updated_at = db.Column(db.DateTime, default=_now_kst, onupdate=_now_kst)

    # 설정 기본값
    DEFAULTS = {
        "NOTION_API_TOKEN": "",
        "NOTION_DATABASE_ID": "",
        "MAX_COMMENTS_PER_DAY": "20",
        "COMMENT_INTERVAL_SEC": "180",
        "SAME_VIDEO_INTERVAL_MIN": "30",
        "SMM_LIKE_QUANTITY": "20",
        "SMM_LIKE_AUTO_MAX": "500",
        "HEADLESS": "false",
        "ADB_IP_CHANGE_ENABLED": "false",
        "ADB_PATH": "D:\\platform-tools\\adb.exe",
        "ADB_AIRPLANE_WAIT": "4",
        "ADB_AUTO_ETHERNET": "true",
        "ADB_ETHERNET_NAME": "이더넷",
        "LIKE_CREDIT_THRESHOLD": "5000",
        "LIKE_CREDIT_AUTO_ALERT": "true",
    }

    def get_settings(self):
        """설정 딕셔너리 반환 (기본값 병합)."""
        try:
            saved = json.loads(self.settings_json or "{}")
        except (json.JSONDecodeError, TypeError):
            saved = {}
        merged = dict(self.DEFAULTS)
        merged.update(saved)
        return merged

    def get(self, key, default=None):
        """개별 설정값 반환."""
        settings = self.get_settings()
        return settings.get(key, default or self.DEFAULTS.get(key, ""))

    def update_settings(self, updates):
        """설정 업데이트 (딕셔너리)."""
        try:
            current = json.loads(self.settings_json or "{}")
        except (json.JSONDecodeError, TypeError):
            current = {}
        current.update(updates)
        self.settings_json = json.dumps(current, ensure_ascii=False)

    def to_dict(self):
        return self.get_settings()


class AccountType(db.Model):
    """유저별 계정 유형 (자유롭게 추가/삭제 가능)."""
    __tablename__ = "account_types"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(50), nullable=False)    # 표시 이름 (e.g. 찐계정, 해외계정)
    color = db.Column(db.String(20), default="#6c757d") # 배지 색상
    created_at = db.Column(db.DateTime, default=_now_kst)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
        }


class CommentTracking(db.Model):
    """유저별 댓글 트래킹 데이터 영속 저장."""
    __tablename__ = "comment_tracking"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    video_url = db.Column(db.String(500), nullable=False)
    video_title = db.Column(db.String(500), nullable=True)
    comment_text = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default="tracking")  # tracking, exposed, lost
    last_checked = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=_now_kst)

    def to_dict(self):
        return {
            "id": self.id,
            "video_url": self.video_url,
            "video_title": self.video_title,
            "comment_text": self.comment_text,
            "status": self.status,
            "last_checked": self.last_checked.isoformat() if self.last_checked else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class LikeOrder(db.Model):
    """좋아요 주문 이력 (SMM 주문 추적)."""
    __tablename__ = "like_orders"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    order_id = db.Column(db.String(50), nullable=False)  # SMM 주문 ID
    comment_url = db.Column(db.String(500), nullable=False)
    quantity = db.Column(db.Integer, default=0)
    tier = db.Column(db.String(50), default="standard")
    cost = db.Column(db.Integer, default=0)  # 원 단위
    status = db.Column(db.String(50), default="Pending")  # Pending, In progress, Completed, Partial, Canceled
    remains = db.Column(db.Integer, nullable=True)  # 남은 수량
    source = db.Column(db.String(50), default="manual")  # manual, auto, boost
    created_at = db.Column(db.DateTime, default=_now_kst)
    updated_at = db.Column(db.DateTime, default=_now_kst, onupdate=_now_kst)

    def to_dict(self):
        return {
            "id": self.id,
            "order_id": self.order_id,
            "comment_url": self.comment_url,
            "quantity": self.quantity,
            "tier": self.tier,
            "cost": self.cost,
            "status": self.status,
            "remains": self.remains,
            "source": self.source,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class AutomationLog(db.Model):
    """자동화 실행 로그 (댓글 작성 성공/실패 기록 - 영속 저장)."""
    __tablename__ = "automation_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    action = db.Column(db.String(50), nullable=False)  # comment_post, comment_fail, like_order, automation_start, automation_end
    account_label = db.Column(db.String(100), nullable=True)
    video_url = db.Column(db.String(500), nullable=True)
    video_title = db.Column(db.String(500), nullable=True)
    comment_text = db.Column(db.Text, nullable=True)
    comment_url = db.Column(db.String(500), nullable=True)
    detail = db.Column(db.String(500), nullable=True)
    level = db.Column(db.String(20), default="info")  # info, success, warning, error
    created_at = db.Column(db.DateTime, default=_now_kst)

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "action": self.action,
            "account_label": self.account_label,
            "video_url": self.video_url,
            "video_title": self.video_title,
            "comment_text": (self.comment_text or "")[:100],
            "comment_url": self.comment_url,
            "detail": self.detail,
            "level": self.level,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class CommentHistory(db.Model):
    """댓글 히스토리 (안전 규칙용 - 파일 대신 DB에 저장)."""
    __tablename__ = "comment_history"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    account_label = db.Column(db.String(100), nullable=False, index=True)
    video_id = db.Column(db.String(20), nullable=True, index=True)
    video_url = db.Column(db.String(500), nullable=True)
    comment_text = db.Column(db.String(200), nullable=True)  # 처음 100자
    created_at = db.Column(db.DateTime, default=_now_kst, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "account_label": self.account_label,
            "video_id": self.video_id,
            "comment_text": self.comment_text,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class UserActivityLog(db.Model):
    """유저 활동 로그 (가입, 로그인, 탈퇴 등)."""
    __tablename__ = "user_activity_log"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True, index=True)
    email = db.Column(db.String(255), nullable=True)
    action = db.Column(db.String(50), nullable=False)  # register, login, logout, deactivate
    detail = db.Column(db.String(500), nullable=True)
    ip_address = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=_now_kst)

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "email": self.email,
            "action": self.action,
            "detail": self.detail,
            "ip_address": self.ip_address,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

# ==========================================================
# YouTube 바이럴 통합 SaaS 모델 (Phase 1, 2, 3)
# ==========================================================

class Campaign(db.Model):
    """유튜브 1단계 수집 캠페인 관리"""
    __tablename__ = 'campaigns'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)  # 소유 유저 (유저별 격리)
    name = db.Column(db.String(100), nullable=False)
    keyword = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(50), default='대기중')  # 대기중/수집중/완료/실패
    created_at = db.Column(db.DateTime, default=_now_kst)

    videos = db.relationship('VideoTarget', backref='campaign', lazy=True, cascade="all, delete-orphan")

class VideoTarget(db.Model):
    """1단계 수집된 유튜브 영상 데이터"""
    __tablename__ = 'videos'
    
    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.id'), nullable=False)
    video_id = db.Column(db.String(50), nullable=False, index=True)  # 전역 unique 아님 — 유저/캠페인별 중복 허용
    title = db.Column(db.String(300), nullable=False)
    url = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, nullable=True)
    collected_at = db.Column(db.DateTime, default=_now_kst)
    
    comments = db.relationship('CommentTask', backref='video', lazy=True, cascade="all, delete-orphan")

class CommentTask(db.Model):
    """2단계 AI 생성 완료된 댓글 및 3단계 업로드 관리"""
    __tablename__ = 'comments'
    
    id = db.Column(db.Integer, primary_key=True)
    video_id = db.Column(db.Integer, db.ForeignKey('videos.id'), nullable=False)
    account_label = db.Column(db.String(100), nullable=True)  # 업로드할 계정
    prompt = db.Column(db.Text, nullable=True)               # AI 생성에 사용된 프롬프트
    generated_text = db.Column(db.Text, nullable=False)      # AI가 생성한 댓글 내용
    status = db.Column(db.String(50), default='대기')        # 대기/생성완료/업로드중/완료/실패
    result_url = db.Column(db.String(300), nullable=True)    # 3단계 업로드 후 결과 URL
    created_at = db.Column(db.DateTime, default=_now_kst)
    executed_at = db.Column(db.DateTime, nullable=True)
