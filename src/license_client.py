"""
라이선스 클라이언트 - 로컬 DB 중심 (v2)

서버 의존성 제거. 플랜/토큰/크레딧을 로컬 DB에서 직접 관리합니다.
플랜 검증 해시로 DB 조작을 방지합니다.
Lemon Squeezy 결제 → /api/payment/confirm → 로컬 DB 반영.

ADMIN_SECRET_KEY: 관리자 기능 (플랜 변경, 크레딧 지급)
"""

import os
import hashlib
import json
import threading
import platform
import base64

# ─── 암호화 헬퍼 (YouTube 계정 비밀번호 등) ───
try:
    from cryptography.fernet import Fernet
    _ENCRYPTION_AVAILABLE = True
except ImportError:
    _ENCRYPTION_AVAILABLE = False


def _get_fernet():
    """Get Fernet cipher using machine-specific key."""
    key_source = os.getenv("ENCRYPTION_KEY", platform.node() + "CommentBoost2026")
    key = base64.urlsafe_b64encode(hashlib.sha256(key_source.encode()).digest())
    return Fernet(key)


def encrypt_value(plaintext):
    """Encrypt a string value. Returns plaintext if cryptography not installed."""
    if not plaintext:
        return plaintext
    if not _ENCRYPTION_AVAILABLE:
        return plaintext
    try:
        return _get_fernet().encrypt(plaintext.encode()).decode()
    except Exception:
        return plaintext


def decrypt_value(ciphertext):
    """Decrypt a string value. Returns original if decryption fails (backward compat)."""
    if not ciphertext:
        return ciphertext
    if not _ENCRYPTION_AVAILABLE:
        return ciphertext
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except Exception:
        return ciphertext  # Return as-is if not encrypted (backward compatibility)

# ─── 플랜 해시 시크릿 (DB 조작 방지) ───
_PLAN_HASH_SECRET = os.environ.get("PLAN_HASH_SECRET", "CB_" + platform.node() + "_2026")

# 토큰 소모 기준
TOKEN_COSTS = {
    "comment_post": 10,      # 댓글 작성
    "comment_repost": 10,    # 리포스팅
    "exposure_check": 2,     # 노출 확인
    "rank_check": 5,         # 순위 체크
    "duplicate_scan": 3,     # 중복 스캔
    "notion_sync": 1,        # 노션 동기화
}

# 좋아요 대행 가격 (원/개) - 3개 품질 티어
LIKE_TIERS = {
    "basic": {
        "name": "베이직",
        "description": "일반 좋아요 (속도 느림, 이탈률 있음)",
        "price_per_unit": 10,
        "min_quantity": 10,
        "smm_service_env": "SMM_LIKE_SERVICE_ID_BASIC",
        "badge_color": "#888",
    },
    "standard": {
        "name": "스탠다드",
        "description": "고품질 좋아요 (빠른 처리, 안정적)",
        "price_per_unit": 15,
        "min_quantity": 10,
        "smm_service_env": "SMM_LIKE_SERVICE_ID_STANDARD",
        "badge_color": "#3b82f6",
        "recommended": True,
    },
    "premium": {
        "name": "프리미엄",
        "description": "최고품질 좋아요 (실제 활성 계정, 이탈 없음)",
        "price_per_unit": 20,
        "min_quantity": 10,
        "smm_service_env": "SMM_LIKE_SERVICE_ID_PREMIUM",
        "badge_color": "#f59e0b",
    },
}

# ─── 글로벌 SMM 설정 ───
# SMM API 키: 환경변수 우선, 없으면 내장값 사용 (사용자 PC에 .env 배포 불가하므로)
_SMM_GLOBAL_CONFIG = {
    "api_key": os.getenv("SMM_API_KEY", "d4c4a25beebe411241429ea75b92bf83"),
    "service_id": os.getenv("SMM_LIKE_SERVICE_ID", "4001"),
    "tier_service_ids": {
        "basic": os.getenv("SMM_LIKE_SERVICE_ID_BASIC", "4001"),
        "standard": os.getenv("SMM_LIKE_SERVICE_ID_STANDARD", "4001"),
        "premium": os.getenv("SMM_LIKE_SERVICE_ID_PREMIUM", "4001"),
    },
}

# 플랜별 기능 잠금
PLAN_FEATURES = {
    "free": {
        "comment_post": True,
        "exposure_check_manual": True,
        "notion_sync": True,
        "auto_repost": False,
        "rank_check": False,
        "duplicate_scan": False,
        "auto_exposure_schedule": False,
        "multi_account_parallel": False,
        "task_scheduling": False,
        "like_boost": False,
        "like_preview": False,
        "tracking_unlimited": False,
        "api_access": False,
        "youtube_collect": False,
    },
    "business": {
        "comment_post": True,
        "exposure_check_manual": True,
        "notion_sync": True,
        "auto_repost": True,
        "rank_check": True,
        "duplicate_scan": True,
        "auto_exposure_schedule": False,
        "multi_account_parallel": False,
        "task_scheduling": False,
        "like_boost": True,
        "like_preview": True,
        "tracking_unlimited": False,
        "api_access": False,
        "youtube_collect": False,
    },
    "agency": {
        "comment_post": True,
        "exposure_check_manual": True,
        "notion_sync": True,
        "auto_repost": True,
        "rank_check": True,
        "duplicate_scan": True,
        "auto_exposure_schedule": True,
        "multi_account_parallel": True,
        "task_scheduling": True,
        "like_boost": True,
        "like_preview": True,
        "tracking_unlimited": True,
        "api_access": False,
        "youtube_collect": True,
    },
    "enterprise": {
        "comment_post": True,
        "exposure_check_manual": True,
        "notion_sync": True,
        "auto_repost": True,
        "rank_check": True,
        "duplicate_scan": True,
        "auto_exposure_schedule": True,
        "multi_account_parallel": True,
        "task_scheduling": True,
        "like_boost": True,
        "like_preview": True,
        "tracking_unlimited": True,
        "api_access": True,
        "youtube_collect": True,
    },
}

# 플랜별 기본 토큰
PLAN_DEFAULT_TOKENS = {
    "free": 0, "business": 50000, "agency": 150000, "enterprise": 999999,
}

# 플랜별 최대 계정 수
PLAN_MAX_ACCOUNTS = {
    "free": 1, "business": 10, "agency": 30, "enterprise": 9999,
}

# 트래킹 무료 횟수 제한
TRACKING_FREE_LIMIT = 3
TRACKING_BUSINESS_LIMIT = 10

# 플랜별 일일 댓글 한도
PLAN_DAILY_LIMITS = {
    "free": 0,
    "business": 200,
    "agency": 500,
    "enterprise": 99999,
}


def compute_plan_hash(email, plan):
    """플랜 무결성 해시를 계산합니다. DB 조작 방지용."""
    raw = f"{email}:{plan}:{_PLAN_HASH_SECRET}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def verify_plan_hash(email, plan, stored_hash):
    """플랜 해시가 유효한지 검증합니다."""
    if not stored_hash:
        return False
    return compute_plan_hash(email, plan) == stored_hash


def is_owner_mode():
    """관리자(owner) 모드인지 확인. ADMIN_SECRET_KEY 기반."""
    admin_key = os.environ.get("ADMIN_SECRET_KEY", "")
    if not admin_key:
        return False
    # ADMIN_SECRET_KEY가 설정되어 있으면 owner 모드 가능
    # 실제 활성화는 앱에서 _admin_view_active로 토글
    return os.environ.get("LICENSE_MODE", "client").lower() == "owner"


class LicenseClient:
    """로컬 DB 중심 라이선스 관리. 유저별 토큰/크레딧은 DB에 영속 저장."""

    def __init__(self):
        self.license_info = {"plan": "free", "plan_name": "free", "max_accounts": 1}
        self.token_balance = 0
        self.like_credit_balance = 0
        self._balance_lock = threading.Lock()
        self._current_user_id = None  # 현재 로그인 유저 추적
        self._current_email = None   # 현재 로그인 유저 이메일 (원장 기록용)
        self.owner_mode = is_owner_mode()
        self.license_key = None  # 하위 호환성
        self.server_url = ""     # 하위 호환성
        self.hardware_id = ""    # 하위 호환성

        if self.owner_mode:
            self.license_info = {
                "plan": "Owner",
                "plan_name": "owner",
                "is_permanent": True,
                "max_accounts": 9999,
            }
            self.token_balance = 999999999
            self.like_credit_balance = 999999999

    def init_from_db_user(self, user):
        """Flask User 객체에서 플랜/토큰/크레딧을 로드합니다.
        로그인 시 호출. 이전 유저의 데이터를 먼저 저장합니다."""
        if self.owner_mode:
            return
        if not user:
            return

        # 이전 유저 데이터 저장 (유저 전환 시)
        if self._current_user_id and self._current_user_id != user.id:
            self._save_to_db()

        self._current_user_id = user.id
        self._current_email = getattr(user, 'email', None)

        plan = (user.plan or "Free").lower()
        plan_key = plan if plan in PLAN_FEATURES else "free"

        self.license_info = {
            "plan": user.plan or "Free",
            "plan_name": plan_key,
            "max_accounts": PLAN_MAX_ACCOUNTS.get(plan_key, 1),
            "is_permanent": plan_key == "enterprise",
        }

        # DB에서 유저별 토큰/크레딧 로드
        self.token_balance = user.token_balance or 0
        self.like_credit_balance = user.like_credit_balance or 0

        # 토큰이 0이면 플랜 기본값 부여 (최초 가입/업그레이드 시)
        if self.token_balance <= 0 and plan_key != "free":
            self.token_balance = PLAN_DEFAULT_TOKENS.get(plan_key, 0)
            user.token_balance = self.token_balance

    def _save_to_db(self):
        """현재 유저의 토큰/크레딧을 DB에 저장합니다."""
        if self.owner_mode or not self._current_user_id:
            return
        try:
            from src.models import User, db
            user = User.query.get(self._current_user_id)
            if user:
                user.token_balance = self.token_balance
                user.like_credit_balance = self.like_credit_balance
                db.session.commit()
        except Exception:
            pass

    def save_balances(self):
        """외부에서 명시적으로 잔액 저장 호출."""
        self._save_to_db()

    def is_active(self):
        """플랜이 활성 상태인지 (Free 이상)"""
        if self.owner_mode:
            return True
        if not self.license_info:
            return False
        plan = self.license_info.get("plan_name", "free")
        return plan != "free"

    def get_plan_name(self):
        """현재 플랜 이름"""
        if self.license_info:
            return self.license_info.get("plan", "Free")
        return "Free"

    def get_max_accounts(self):
        """최대 계정 수"""
        if self.license_info:
            return self.license_info.get("max_accounts", 1)
        return 1

    def can_use_feature(self, feature_name):
        """현재 플랜에서 해당 기능을 사용할 수 있는지 확인"""
        if self.owner_mode:
            return True
        if not self.license_info:
            return False
        plan_name = self.license_info.get("plan_name", "free").lower()
        features = PLAN_FEATURES.get(plan_name, PLAN_FEATURES["free"])
        return features.get(feature_name, False)

    def get_upgrade_message(self, feature_name):
        """기능 잠금 시 업그레이드 안내 메시지"""
        required_plan = "Business"
        for plan in ["business", "agency", "enterprise"]:
            if PLAN_FEATURES.get(plan, {}).get(feature_name):
                required_plan = plan.capitalize()
                break
        return f"이 기능은 {required_plan} 플랜부터 사용 가능합니다. 업그레이드해주세요."

    def use_tokens(self, action, description=None):
        """토큰 소모. 성공 시 잔액 반환, 실패 시 None. DB에 자동 저장 + 서버 원장 기록."""
        if self.owner_mode:
            return 999999999

        tokens = TOKEN_COSTS.get(action, 0)
        if tokens == 0:
            return self.token_balance

        with self._balance_lock:
            if self.token_balance < tokens:
                return None
            self.token_balance -= tokens
            self._save_to_db()

            # 서버 token_ledger에 차감 기록
            try:
                import os, requests as _req, uuid
                server_url = os.environ.get("CENTRAL_SERVER_URL", "https://commentboost-app.fly.dev")
                email = self._current_email or self.license_info.get("email", "")
                if email:
                    _req.post(f"{server_url}/api/tokens/transact", json={
                        "email": email,
                        "entry_type": "use",
                        "amount": -tokens,
                        "description": description or f"토큰 사용: {action}",
                        "idempotency_key": str(uuid.uuid4()),
                    }, timeout=5)
            except Exception:
                pass

            return self.token_balance

    def get_balance(self):
        """토큰 잔액"""
        if self.owner_mode:
            return 999999999
        return self.token_balance

    def check_daily_limit(self):
        """Check if user has reached daily comment limit. Returns (can_post, remaining, limit)."""
        if self.owner_mode:
            return True, 99999, 99999
        plan = self.license_info.get("plan", "free")
        limit = PLAN_DAILY_LIMITS.get(plan, 0)
        if limit == 0:
            return False, 0, 0
        # Daily count is managed by app.py via DB
        return True, limit, limit

    def get_like_credit_balance(self):
        """좋아요 크레딧 잔액 (원)"""
        if self.owner_mode:
            return 999999999
        return self.like_credit_balance

    def get_like_cost(self, quantity, tier="standard"):
        """좋아요 비용 계산 (원)"""
        if self.owner_mode:
            return 0
        tier_info = LIKE_TIERS.get(tier, LIKE_TIERS["standard"])
        return quantity * tier_info["price_per_unit"]

    def get_like_tiers(self):
        """좋아요 티어 목록"""
        return LIKE_TIERS

    def order_likes_via_server(self, comment_url, quantity=10, tier="standard", source="boost"):
        """좋아요 주문 — SMM 직접 주문 + 로컬 크레딧 차감."""
        cost = self.get_like_cost(quantity, tier)

        # 크레딧 차감 (owner 모드는 무료)
        if not self.owner_mode:
            with self._balance_lock:
                if self.like_credit_balance < cost:
                    return {"success": False, "error": f"크레딧 부족 (필요: {cost:,}원, 보유: {self.like_credit_balance:,}원)"}
                self.like_credit_balance -= cost
                self._save_to_db()

        # SMM 직접 주문 — 글로벌 SMM 설정 사용 (사용자 PC에 .env가 없어도 동작)
        try:
            from src.smm_client import SMMClient
            smm = SMMClient(
                api_key=_SMM_GLOBAL_CONFIG.get("api_key"),
                enabled=True if _SMM_GLOBAL_CONFIG.get("api_key") else None,
                service_id=_SMM_GLOBAL_CONFIG.get("service_id"),
                tier_service_ids=_SMM_GLOBAL_CONFIG.get("tier_service_ids"),
            )
            if not smm.enabled:
                if not self.owner_mode:
                    with self._balance_lock:
                        self.like_credit_balance += cost
                        self._save_to_db()
                return {"success": False, "error": "좋아요 서비스가 설정되지 않았습니다. 관리자에게 문의해주세요."}
            result = smm.order_likes(comment_url, quantity=quantity, tier=tier)
            if result.get("success"):
                # 서버 원장에 좋아요 주문 크레딧 차감 기록
                try:
                    import uuid as _uuid
                    import requests as _ledger_req
                    _server_url = os.environ.get("CENTRAL_SERVER_URL", "https://commentboost-app.fly.dev")
                    _ledger_req.post(f"{_server_url}/api/credits/transact", json={
                        "email": self._current_email or "",
                        "entry_type": "like_order",
                        "amount": -cost,
                        "description": f"좋아요 주문 {quantity}개 ({tier})",
                        "idempotency_key": str(_uuid.uuid4())
                    }, timeout=5)
                except Exception:
                    pass  # Local deduction already done
                return {
                    "success": True,
                    "order_id": result["order_id"],
                    "cost": cost,
                    "remaining_credits": self.like_credit_balance,
                }
            else:
                # 주문 실패 시 크레딧 복원
                if not self.owner_mode:
                    with self._balance_lock:
                        self.like_credit_balance += cost
                        self._save_to_db()
                return {"success": False, "error": result.get("error", "SMM 주문 실패")}
        except Exception as e:
            # 오류 시 크레딧 복원
            if not self.owner_mode:
                with self._balance_lock:
                    self.like_credit_balance += cost
                    self._save_to_db()
            return {"success": False, "error": f"주문 오류: {str(e)}"}

    def get_like_orders(self):
        """좋아요 주문 이력 (로컬 DB에서 조회 - app.py에서 처리)"""
        return {"orders": [], "purchases": [], "balance": self.like_credit_balance}

    def refresh_like_order_status(self, order_ids):
        """좋아요 주문 상태 업데이트 (SMM 직접 조회)"""
        if not order_ids:
            return {"updated": 0}
        try:
            from src.smm_client import SMMClient
            smm = SMMClient()
            result = smm.check_multiple_orders(order_ids)
            return {"updated": len(result), "statuses": result}
        except Exception:
            return {"updated": 0}

    # 하위 호환성 메서드 (서버 호출 없이 동작)
    def verify(self):
        return {"valid": self.is_active(), "message": "로컬 모드"}

    def auto_verify(self):
        return {"valid": self.is_active(), "message": "로컬 모드"}

    def activate(self, license_key):
        return {"valid": True, "message": "로컬 모드 (라이선스 키 불필요)"}

    def stop(self):
        pass


# 글로벌 인스턴스
license_client = LicenseClient()
