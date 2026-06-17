"""
댓글 부스터 - 유튜브 댓글 자동화 웹 대시보드 (Flask)

기능:
- 대시보드: 작업 현황, 계정 상태, SMM 잔액
- 작업 관리: 노션 DB 작업 목록 조회/실행
- 자동화 실행: 백그라운드에서 댓글 작업 실행
- SMM 관리: 서비스 조회, 주문 상태 확인
- 설정: 환경 변수 조회/수정
"""

import os
import sys

# Windows 한국어 환경 (cp949) 인코딩 문제 해결 — 가장 먼저 실행
# rich 라이브러리의 유니코드 박스 문자(╔, ╗ 등)가 cp949에서 인코딩 실패하는 문제 방지
if sys.platform == "win32":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    # stdout/stderr 가 None 일 수 있음 (pythonw / pyinstaller --noconsole)
    for _stream in (sys.stdout, sys.stderr):
        if _stream and hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

# 크래시 디버깅: 에러를 파일로 기록 (CMD 에 로그가 안 보일 때 확인용)
_startup_log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "startup_error.log")
try:
    import json
    import time
    import uuid
    import random
    import hashlib
    import platform
    import threading
    from collections import defaultdict, deque
    from datetime import datetime, timedelta, timezone, date
    KST = timezone(timedelta(hours=9))

    from flask import Flask, render_template, jsonify, request, redirect, url_for, send_file
    try:
        from flask_cors import CORS
    except ImportError:
        CORS = None  # 선택 의존성 — 없으면 CORS 미적용(로컬 단일 출처/PyWebView엔 불필요)
    from flask_login import LoginManager, login_user, logout_user, login_required, current_user
    from dotenv import load_dotenv

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    load_dotenv(override=True)

    from src.notion_client import NotionManager
    from src.fingerprint import FingerprintManager
    from src.safety_rules import SafetyRules
    from src.smm_client import SMMClient
    from src.adb_ip_changer import ADBIPChanger
    from src.comment_tracker import CommentTracker
    from src.license_client import license_client, is_owner_mode, LIKE_TIERS, PLAN_FEATURES
    from src.lemonsqueezy_client import LemonSqueezyClient
    from src.updater import (
        check_updates_async, get_update_status, check_for_updates,
        perform_update, get_update_progress, get_current_version,
    )
except Exception as _startup_err:
    import traceback as _tb
    with open(_startup_log, "w", encoding="utf-8") as _f:
        _f.write(f"=== 시작 에러 ({__import__('datetime').datetime.now()}) ===\n")
        _tb.print_exc(file=_f)
    # 터미널에도 출력 시도
    try:
        print(f"\n[에러] 앱 시작 실패! 상세 로그: {_startup_log}")
        _tb.print_exc()
    except Exception:
        pass
    sys.exit(1)

# Lemon Squeezy 클라이언트 초기화
try:
    ls_client = LemonSqueezyClient()
except Exception as _ls_init_err:
    print(f"[LemonSqueezy] 클라이언트 생성 오류: {_ls_init_err}")
    ls_client = None

# PyInstaller EXE에서는 __file__이 _internal/ 안을 가리키므로,
# template/static/data 등은 모두 sys.executable 기준으로 잡아야 함
if getattr(sys, 'frozen', False):
    _exe_root = os.path.dirname(sys.executable)
    # PyInstaller 5+ 는 datas를 _internal/ 에 번들
    _internal = os.path.join(_exe_root, "_internal")
    _tmpl_dir = (os.path.join(_internal, "templates")
                 if os.path.isdir(os.path.join(_internal, "templates"))
                 else os.path.join(_exe_root, "templates"))
    _static_dir = (os.path.join(_internal, "static")
                   if os.path.isdir(os.path.join(_internal, "static"))
                   else os.path.join(_exe_root, "static"))
    app = Flask(
        __name__,
        template_folder=_tmpl_dir,
        static_folder=_static_dir,
    )
else:
    app = Flask(__name__)
    _exe_root = None
    _internal = None

# PyWebView 로컬 + 외부 도메인 허용 (flask_cors 미설치 시 건너뜀)
if CORS:
    CORS(app)

app.secret_key = os.environ.get("SECRET_KEY") or hashlib.sha256(
    (platform.node() + os.path.abspath(__file__)).encode()
).hexdigest()

# ─── 데이터베이스 & 로그인 매니저 ───
# 데이터 경로: AppData > Fly.io 볼륨 > EXE 폴더 > 소스 폴더
_appdata_dir = os.path.join(os.environ.get("APPDATA", ""), "CommentBoost") if sys.platform == "win32" else ""
if _appdata_dir and (os.path.isdir(_appdata_dir) or getattr(sys, 'frozen', False)):
    _data_dir = os.path.join(_appdata_dir, "data")
elif os.path.isdir("/data"):
    _data_dir = "/data"
elif _exe_root:
    _data_dir = os.path.join(_exe_root, "data")
else:
    _data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(_data_dir, exist_ok=True)
# config 경로도 AppData 하위에 통합
_config_dir = os.path.join(os.path.dirname(_data_dir), "config") if _appdata_dir else (
    os.path.join(_exe_root, "config") if _exe_root else
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")
)
os.makedirs(_config_dir, exist_ok=True)
os.makedirs(os.path.join(_config_dir, "sessions"), exist_ok=True)
# ACCOUNTS_FILE 환경변수를 AppData 기반으로 설정
if "ACCOUNTS_FILE" not in os.environ:
    os.environ["ACCOUNTS_FILE"] = os.path.join(_config_dir, "accounts.json")
print(f"[DB] 데이터 경로: {_data_dir}")
print(f"[DB] 설정 경로: {_config_dir}")
print(f"[DB] users.db 존재: {os.path.exists(os.path.join(_data_dir, 'users.db'))}")

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(_data_dir, "users.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=7)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["REMEMBER_COOKIE_HTTPONLY"] = True
app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"

from src.models import db, User, UserSettings, YouTubeAccount, UserActivityLog, LikeOrder, AutomationLog, CommentHistory, Campaign, VideoTarget, CommentTask, AccountType
db.init_app(app)

from src.api.collect_api import collect_bp
from src.api.comment_api import comment_bp
app.register_blueprint(collect_bp)
app.register_blueprint(comment_bp)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "auth_page"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ─── 중앙 서버 동기화 ───
CENTRAL_SERVER_URL = "https://commentboost-app.fly.dev"


def _sync_to_server(email, action, detail="", **extra):
    """이벤트를 중앙 서버로 비동기 전송 (실패 시 1회 재시도, 앱 동작에 영향 없음)."""
    def _send():
        import requests as _req
        import time as _time
        payload = {"email": email, "action": action, "detail": detail}
        payload.update(extra)
        for attempt in range(2):
            try:
                _req.post(f"{CENTRAL_SERVER_URL}/api/sync/event", json=payload, timeout=10)
                return
            except Exception:
                if attempt == 0:
                    _time.sleep(3)  # 서버 cold start 대기 후 재시도
    threading.Thread(target=_send, daemon=True).start()


def _sync_balance_snapshot(email, event, token_balance=0, like_credit_balance=0):
    """잔액 스냅샷을 서버에 전송 (이상징후 감지용)."""
    def _send():
        try:
            import requests as _req
            _req.post(f"{CENTRAL_SERVER_URL}/api/sync/balance-snapshot", json={
                "email": email, "event": event,
                "token_balance": token_balance,
                "like_credit_balance": like_credit_balance,
            }, timeout=5)
        except Exception:
            pass
    threading.Thread(target=_send, daemon=True).start()


def _sync_like_order(email, order_data):
    """좋아요 주문 데이터를 서버에 동기화 (수익 대시보드용)."""
    def _send():
        try:
            import requests as _req
            _req.post(f"{CENTRAL_SERVER_URL}/api/sync/like-order", json={
                "email": email,
                "smm_order_id": str(order_data.get("order_id", "")),
                "comment_url": order_data.get("comment_url", ""),
                "quantity": order_data.get("quantity", 0),
                "cost": order_data.get("cost", 0),
                "tier": order_data.get("tier", "standard"),
                "source": order_data.get("source", "boost"),
            }, timeout=5)
        except Exception:
            pass
    threading.Thread(target=_send, daemon=True).start()


def _sync_user_full(user):
    """유저의 전체 상태를 서버에 동기화 (비밀번호 해시 포함)."""
    if not user:
        return
    from flask import session as flask_session
    _sync_to_server(
        user.email, "sync",
        nickname=user.nickname,
        plan=user.plan,
        hardware_id=user.hardware_id or "",
        setup_completed=user.setup_completed or False,
        session_id=flask_session.get("session_id", ""),
    )


def _safe_proxy(resp):
    """서버 프록시 응답을 안전하게 JSON으로 반환."""
    try:
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({"error": f"서버 응답 오류 ({resp.status_code})"}), resp.status_code
    except Exception:
        return jsonify({"error": "서버 응답 파싱 실패"}), 502


def _check_server_user_status(email):
    """서버에서 유저 상태를 조회합니다. 관리자가 변경한 내용 반영."""
    try:
        import requests as _req
        resp = _req.post(f"{CENTRAL_SERVER_URL}/api/sync/user-status", json={"email": email}, timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def _log_activity(user_id, email, action, detail=None):
    """활동 로그 기록 + 서버 동기화."""
    db.session.add(UserActivityLog(
        user_id=user_id, email=email, action=action,
        detail=detail,
        ip_address=request.remote_addr,
    ))
    db.session.commit()
    # 서버에도 전송
    _sync_to_server(email, action, detail=detail or "")


@login_manager.unauthorized_handler
def unauthorized():
    if request.path.startswith("/api/"):
        return jsonify({"error": "로그인이 필요합니다."}), 401
    return redirect(url_for("auth_page"))


@app.errorhandler(400)
def bad_request(e):
    return jsonify({"error": "잘못된 요청입니다."}), 400

@app.errorhandler(401)
def unauthorized_error(e):
    return jsonify({"error": "인증이 필요합니다."}), 401

@app.errorhandler(403)
def forbidden(e):
    return jsonify({"error": "접근 권한이 없습니다."}), 403

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "요청한 리소스를 찾을 수 없습니다."}), 404

@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"error": "허용되지 않는 메서드입니다."}), 405

@app.errorhandler(500)
def internal_error(e):
    import traceback
    tb = traceback.format_exc()
    try:
        import logging
        logging.getLogger("desktop").error(f"500 에러: {request.path}: {e}\n{tb}")
    except Exception:
        pass
    return jsonify({"error": "서버 내부 오류가 발생했습니다. 잠시 후 다시 시도해주세요."}), 500


@app.before_request
def add_request_id():
    request.request_id = str(uuid.uuid4())[:8]


@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Request-ID"] = getattr(request, 'request_id', '-')
    return response


with app.app_context():
    db.create_all()
    # 기존 DB에 새 컬럼/테이블 자동 추가 (마이그레이션)
    import sqlite3 as _sqlite3
    _db_path = os.path.join(_data_dir, "users.db")
    if os.path.exists(_db_path):
        _conn = _sqlite3.connect(_db_path)
        _cursor = _conn.cursor()
        _cursor.execute("PRAGMA table_info(users)")
        _existing_cols = {row[1] for row in _cursor.fetchall()}
        _new_cols = [
            ("plan", "VARCHAR(50) DEFAULT 'Free'"),
            ("agreed_terms", "BOOLEAN DEFAULT 0"),
            ("agreed_at", "DATETIME"),
            ("setup_completed", "BOOLEAN DEFAULT 0"),
            ("token_balance", "INTEGER DEFAULT 0"),
            ("like_credit_balance", "INTEGER DEFAULT 0"),
            ("hardware_id", "VARCHAR(64)"),
            ("auto_login_token", "VARCHAR(64)"),
        ]
        for col_name, col_def in _new_cols:
            if col_name not in _existing_cols:
                _cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")
        # youtube_accounts 컬럼 추가
        _cursor.execute("PRAGMA table_info(youtube_accounts)")
        _yt_cols = {row[1] for row in _cursor.fetchall()}
        if _yt_cols and "account_password" not in _yt_cols:
            _cursor.execute("ALTER TABLE youtube_accounts ADD COLUMN account_password VARCHAR(500)")
        if _yt_cols and "channel_terminated" not in _yt_cols:
            _cursor.execute("ALTER TABLE youtube_accounts ADD COLUMN channel_terminated BOOLEAN DEFAULT 0")
        if _yt_cols and "last_comment_at" not in _yt_cols:
            _cursor.execute("ALTER TABLE youtube_accounts ADD COLUMN last_comment_at DATETIME")
        if _yt_cols and "daily_comment_limit" not in _yt_cols:
            _cursor.execute("ALTER TABLE youtube_accounts ADD COLUMN daily_comment_limit INTEGER DEFAULT 10")
        if _yt_cols and "interest_keywords" not in _yt_cols:
            _cursor.execute("ALTER TABLE youtube_accounts ADD COLUMN interest_keywords TEXT")
        # campaigns: user_id 컬럼 추가 (유저별 캠페인 격리)
        _cursor.execute("PRAGMA table_info(campaigns)")
        _camp_cols = {row[1] for row in _cursor.fetchall()}
        if _camp_cols and "user_id" not in _camp_cols:
            _cursor.execute("ALTER TABLE campaigns ADD COLUMN user_id INTEGER")
        # comments: reply_text 컬럼 추가 (AI 대댓글 저장)
        _cursor.execute("PRAGMA table_info(comments)")
        _cmt_cols = {row[1] for row in _cursor.fetchall()}
        if _cmt_cols and "reply_text" not in _cmt_cols:
            _cursor.execute("ALTER TABLE comments ADD COLUMN reply_text TEXT")
        # videos: video_id 전역 UNIQUE 제약 제거 (유저/캠페인별 동일 영상 허용 → 테이블 재생성)
        _cursor.execute("PRAGMA index_list(videos)")
        _has_unique_videoid = False
        for _ix in _cursor.fetchall():
            if _ix[2]:  # unique == 1
                _cursor.execute(f"PRAGMA index_info('{_ix[1]}')")
                if [r[2] for r in _cursor.fetchall()] == ["video_id"]:
                    _has_unique_videoid = True
                    break
        if _has_unique_videoid:
            _cursor.executescript(
                """
                CREATE TABLE videos_new (
                    id INTEGER PRIMARY KEY,
                    campaign_id INTEGER NOT NULL,
                    video_id VARCHAR(50) NOT NULL,
                    title VARCHAR(300) NOT NULL,
                    url VARCHAR(300) NOT NULL,
                    description TEXT,
                    collected_at DATETIME
                );
                INSERT INTO videos_new (id, campaign_id, video_id, title, url, description, collected_at)
                    SELECT id, campaign_id, video_id, title, url, description, collected_at FROM videos;
                DROP TABLE videos;
                ALTER TABLE videos_new RENAME TO videos;
                CREATE INDEX ix_videos_video_id ON videos (video_id);
                """
            )
        _conn.commit()
        _conn.close()
    # 새 테이블도 생성 (youtube_accounts, comment_tracking, user_settings)
    db.create_all()

    # 기존 계정의 account_type 값 정규화 (sub→부계정, main/google→찐계정)
    try:
        _legacy_map = {"sub": "부계정", "main": "찐계정", "google": "찐계정"}
        for _acc in YouTubeAccount.query.all():
            if _acc.account_type in _legacy_map:
                _acc.account_type = _legacy_map[_acc.account_type]
        db.session.commit()
    except Exception:
        db.session.rollback()


def _auto_backup_db():
    """Daily auto backup of user database."""
    try:
        import shutil
        data_dir = os.path.join(os.environ.get("APPDATA", "."), "CommentBoost", "data")
        db_path = os.path.join(data_dir, "users.db")
        if not os.path.exists(db_path):
            return
        backup_dir = os.path.join(data_dir, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        backup_path = os.path.join(backup_dir, f"users_{today}.db")
        if not os.path.exists(backup_path):
            shutil.copy2(db_path, backup_path)
            print(f"[DB 백업] {backup_path}")
        # Keep only last 7 backups
        backups = sorted([f for f in os.listdir(backup_dir) if f.startswith("users_")])
        while len(backups) > 7:
            old = backups.pop(0)
            os.remove(os.path.join(backup_dir, old))
    except Exception:
        pass


_auto_backup_db()


def _auto_sync_like_orders():
    """앱 시작 시 로컬 좋아요 주문을 서버에 동기화 (서버에서 smm_order_id로 중복 방지)."""
    try:
        with app.app_context():
            orders = LikeOrder.query.all()
            if not orders:
                return
            import requests as _req

            synced = 0
            for o in orders:
                user = db.session.get(User, o.user_id)
                email = user.email if user else ""
                try:
                    _req.post(f"{CENTRAL_SERVER_URL}/api/sync/like-order", json={
                        "email": email,
                        "smm_order_id": str(o.order_id or ""),
                        "comment_url": o.comment_url or "",
                        "quantity": o.quantity or 0,
                        "cost": o.cost or 0,
                        "tier": o.tier or "standard",
                        "source": o.source or "auto",
                    }, timeout=5)
                    synced += 1
                except Exception:
                    pass
            if synced > 0:
                print(f"[주문 동기화] {synced}/{len(orders)}건 서버 전송 완료")
    except Exception:
        pass


threading.Thread(target=_auto_sync_like_orders, daemon=True).start()


# ── 크레딧 실시간 동기화 (30초 폴링) ──
_credit_sync_paused = False  # 자동화 실행 중 일시 중지


def _credit_sync_loop():
    """30초마다 서버 원장에서 크레딧 잔액 조회 + 즉시 반영."""
    global _credit_sync_paused
    import requests as _req
    time.sleep(10)  # 앱 시작 후 10초 대기

    while True:
        try:
            time.sleep(30)
            if _credit_sync_paused:
                continue

            with app.app_context():
                try:
                    user = current_user if current_user and current_user.is_authenticated else None
                except Exception:
                    user = None
                if not user:
                    continue

                server_url = os.environ.get("CENTRAL_SERVER_URL", "https://commentboost-app.fly.dev")
                # 서버 원장에서 좋아요 크레딧 잔액 조회
                try:
                    resp = _req.post(f"{server_url}/api/credits/balance",
                        json={"email": user.email}, timeout=10)
                    if resp.status_code == 200:
                        server_balance = resp.json().get("balance", 0)
                        local_balance = user.like_credit_balance or 0
                        if server_balance != local_balance:
                            user.like_credit_balance = server_balance
                            license_client.like_credit_balance = server_balance
                            db.session.commit()
                            add_log(f"크레딧 자동 동기화: {local_balance:,}→{server_balance:,}원", "info")
                except Exception:
                    pass
                # 토큰도 서버 원장에서 조회 (token_ledger = single source of truth)
                try:
                    resp = _req.post(f"{server_url}/api/tokens/balance",
                        json={"email": user.email}, timeout=10)
                    if resp.status_code == 200:
                        server_tokens = resp.json().get("balance", 0)
                        local_tokens = user.token_balance or 0
                        if server_tokens != local_tokens:
                            user.token_balance = server_tokens
                            license_client.token_balance = server_tokens
                            db.session.commit()
                            add_log(f"토큰 자동 동기화: {local_tokens:,}→{server_tokens:,}", "info")
                except Exception:
                    pass
        except Exception:
            pass


threading.Thread(target=_credit_sync_loop, daemon=True).start()

# TODO: 2FA/TOTP - 데스크톱 앱 특성상 복잡도가 높아 추후 별도 구현 예정

# 글로벌 상태
automation_state = {
    "running": False,
    "current_task": None,
    "progress": 0,
    "total": 0,
    "logs": [],
    "results": {"success": 0, "fail": 0, "skip": 0, "likes": 0, "duplicate": 0},
    "test_mode": False,
    "limit": 0,
    "full_auto": False,
    "credit_alert": None,  # 크레딧 부족 시 충전 유도 알림 (프론트엔드 감지용)
    "distribution_notice": None,  # 댓글계정 자동 분배 결과 알림 (프론트엔드 팝업용)
}

# ETA 추적용
eta_tracker = {
    "task_times": [],          # 최근 작업별 소요시간 (초)
    "current_task_start": 0,   # 현재 작업 시작 시각 (time.time())
    "started_at": 0,           # 전체 자동화 시작 시각
}
automation_lock = threading.Lock()

# 좋아요 승인 대기 리스트 (MAX 초과 건)
like_pending_list = []  # [{id, comment_url, page_id, qty, top_likes, video_url, video_title, account, created_at}]
like_pending_lock = threading.Lock()


def _check_credit_threshold():
    """좋아요 크레딧 임계값 체크 — 부족 시 automation_state에 알림 설정."""
    try:
        alert_enabled = _get_user_setting(1, "LIKE_CREDIT_AUTO_ALERT", "true").lower() == "true"
        if not alert_enabled:
            return
        threshold = int(_get_user_setting(1, "LIKE_CREDIT_THRESHOLD", "5000"))
        balance = license_client.get_like_credit_balance()
        if balance <= threshold and not license_client.owner_mode:
            automation_state["credit_alert"] = {
                "type": "low_credit",
                "balance": balance,
                "threshold": threshold,
                "message": f"좋아요 크레딧이 {balance:,}원 남았습니다. 충전이 필요합니다.",
            }
            add_log(
                f"💳 크레딧 잔액 부족! {balance:,}원 (임계값 {threshold:,}원) — 충전을 권장합니다.",
                "warning"
            )
        else:
            automation_state["credit_alert"] = None
    except Exception:
        pass


def _save_like_order(order_id, comment_url, quantity, source="auto", tier="standard", cost=0):
    """좋아요 주문을 DB에 저장하는 헬퍼 함수."""
    try:
        with app.app_context():
            # 백그라운드 스레드에서는 current_user가 없으므로 automation_state에서 가져옴
            try:
                user_id = current_user.id if current_user and current_user.is_authenticated else None
            except Exception:
                user_id = None
            if not user_id:
                user_id = automation_state.get("user_id") or 1
            order = LikeOrder(
                user_id=user_id,
                order_id=str(order_id),
                comment_url=comment_url,
                quantity=quantity,
                tier=tier,
                cost=cost,
                status="Pending",
                source=source,
            )
            db.session.add(order)
            db.session.commit()

            # 서버에 주문 동기화 (수익 대시보드용)
            try:
                user = db.session.get(User, user_id)
                user_email = user.email if user else ""
                _sync_like_order(user_email, {
                    "order_id": order_id, "comment_url": comment_url,
                    "quantity": quantity, "cost": cost, "tier": tier, "source": source,
                })
            except Exception:
                pass
    except Exception:
        pass

# 대댓글 자동화 상태
reply_state = {
    "running": False,
    "current_task": None,
    "progress": 0,
    "total": 0,
    "results": {"success": 0, "fail": 0, "skip": 0},
}
reply_lock = threading.Lock()

# 댓글 트래커
comment_tracker = CommentTracker()
tracking_state = {
    "running": False,
    "progress": 0,
    "total": 0,
    "started_at": None,
    "last_result": None,
}
tracking_lock = threading.Lock()

# 관리자 뷰 상태 (런타임 전역)
_admin_view_active = is_owner_mode()

# 플랜별 최대 계정 수 매핑
PLAN_MAX_ACCOUNTS = {
    "business": 10, "agency": 30, "enterprise": 9999,
}

# 플랜별 월 토큰 수 매핑
PLAN_TOKENS = {
    "business": 10000, "agency": 30000, "enterprise": 999999999,
}


def _estimate_credit_usage(pending_count=0):
    """대기 작업 수 기반으로 예상 크레딧 소모량을 계산합니다.
    사용자에게는 좋아요 크레딧(원) 기준만 노출. SMM 원가는 내부용."""
    like_qty = int(os.getenv("SMM_LIKE_QUANTITY", "20"))
    like_price_per_unit = 10  # 원/개 (베이직 — 전국 최저가)

    token_per_task = 11  # comment_post(10) + notion_sync(1)
    like_credit_per_task = like_qty * like_price_per_unit

    total_tokens_needed = pending_count * token_per_task
    total_like_credits_needed = pending_count * like_credit_per_task

    current_tokens = license_client.token_balance
    current_like_credits = license_client.get_like_credit_balance()

    token_shortage = max(0, total_tokens_needed - current_tokens)
    like_credit_shortage = max(0, total_like_credits_needed - current_like_credits)

    return {
        "pending_count": pending_count,
        "like_qty_per_task": like_qty,
        "like_price_per_unit": like_price_per_unit,
        "token_per_task": token_per_task,
        "like_credit_per_task": like_credit_per_task,
        "total_tokens_needed": total_tokens_needed,
        "total_like_credits_needed": total_like_credits_needed,
        "current_tokens": current_tokens,
        "current_like_credits": current_like_credits,
        "token_shortage": token_shortage,
        "like_credit_shortage": like_credit_shortage,
        "sufficient": token_shortage == 0 and like_credit_shortage == 0,
    }


def _sync_plan_from_db():
    """DB에 저장된 유저 플랜을 license_client에 동기화하고, 플랜 정보를 반환합니다."""
    plan_name = license_client.get_plan_name()
    token_balance = license_client.token_balance
    max_accounts = license_client.get_max_accounts()
    features = {}

    try:
        if current_user and current_user.is_authenticated:
            db_plan = current_user.plan
            if db_plan and db_plan != "Free":
                plan_key = db_plan.lower()
                if plan_key in PLAN_FEATURES:
                    plan_name = db_plan
                    max_accounts = PLAN_MAX_ACCOUNTS.get(plan_key, 3)
                    # 토큰: license_client에 잔액이 없으면 플랜 기본값 사용
                    if token_balance <= 0:
                        token_balance = PLAN_TOKENS.get(plan_key, 0)
                    # license_client 캐시 동기화
                    if license_client.license_info is None:
                        license_client.license_info = {}
                    license_client.license_info["plan"] = db_plan
                    license_client.license_info["plan_name"] = plan_key
                    license_client.license_info["max_accounts"] = max_accounts
                    license_client.license_info["is_permanent"] = (plan_key == "enterprise")
                    license_client.token_balance = token_balance
                    features = {feat: PLAN_FEATURES[plan_key].get(feat, False)
                                for feat in PLAN_FEATURES[plan_key]}
    except Exception:
        pass

    return plan_name, token_balance, max_accounts, features


# 트래킹 월간 사용 횟수 (Business/Agency 제한용)
tracking_usage = {"month": None, "count": 0}

def _get_tracking_usage_count():
    """이번 달 트래킹 사용 횟수 반환"""
    current_month = datetime.now().strftime("%Y-%m")
    if tracking_usage["month"] != current_month:
        tracking_usage["month"] = current_month
        tracking_usage["count"] = 0
    return tracking_usage["count"]

def _increment_tracking_usage():
    """트래킹 사용 횟수 증가"""
    current_month = datetime.now().strftime("%Y-%m")
    if tracking_usage["month"] != current_month:
        tracking_usage["month"] = current_month
        tracking_usage["count"] = 0
    tracking_usage["count"] += 1

def _check_tracking_allowed():
    """트래킹 사용 가능 여부 확인. 불가 시 에러 메시지 반환"""
    if license_client.owner_mode:
        return None  # Owner는 무제한
    if license_client.can_use_feature("tracking_unlimited"):
        return None  # Agency 이상은 무제한
    from src.license_client import TRACKING_FREE_LIMIT, TRACKING_BUSINESS_LIMIT
    plan_name = (license_client.get_plan_name() or "").lower()
    limit = TRACKING_BUSINESS_LIMIT if plan_name == "business" else TRACKING_FREE_LIMIT
    used = _get_tracking_usage_count()
    if used >= limit:
        if plan_name == "business":
            return (f"이번 달 트래킹 {TRACKING_BUSINESS_LIMIT}회를 모두 사용했습니다. "
                    f"Agency 플랜으로 업그레이드하면 무제한 이용 가능합니다.")
        return (f"이번 달 무료 트래킹 {TRACKING_FREE_LIMIT}회를 모두 사용했습니다. "
                f"Business 플랜은 월 {TRACKING_BUSINESS_LIMIT}회, Agency는 무제한입니다.")
    return None


# Free 플랜 체험 제한
FREE_TRIAL_LIMIT = 2  # Free 플랜 최대 실행 횟수

def _check_license_active():
    """플랜 확인. Free는 체험 2건까지만 허용."""
    if license_client.owner_mode:
        return None

    plan = (license_client.get_plan_name() or "Free")
    if plan == "Free":
        # Free 플랜: 체험 횟수 확인
        try:
            if current_user and current_user.is_authenticated:
                from src.models import AutomationLog
                total_runs = AutomationLog.query.filter_by(
                    user_id=current_user.id, action="comment_post"
                ).count()
                if total_runs >= FREE_TRIAL_LIMIT:
                    return (f"무료 체험 {FREE_TRIAL_LIMIT}건을 모두 사용했습니다. "
                            f"구독 플랜을 선택하면 무제한으로 이용할 수 있습니다.")
                remaining = FREE_TRIAL_LIMIT - total_runs
                print(f"[Free] 체험 사용: {total_runs}/{FREE_TRIAL_LIMIT} (남은: {remaining}건)")
        except Exception:
            pass
        return None

    return None


def _tracking_progress_callback(progress, total):
    """comment_tracker에서 호출되는 진행 상태 콜백"""
    tracking_state["progress"] = progress
    tracking_state["total"] = total
    if progress == 0:
        tracking_state["started_at"] = time.time()


comment_tracker.set_progress_callback(_tracking_progress_callback)

# 리포스팅 상태
repost_state = {
    "running": False,
    "current_task": None,
    "progress": 0,
    "total": 0,
    "results": {"success": 0, "fail": 0},
}
repost_lock = threading.Lock()

# 작업 목록 캐시 (매번 노션 API 전체 조회 방지) — 멀티 키 지원
# { "status:date": {"tasks": [...], "fetched_at": timestamp}, ... }
_task_cache = {}
_task_cache_ttl = 300  # 5분 (탭 전환 캐시용)
_task_cache_lock = threading.Lock()


def _filter_tasks_from_cache(all_tasks, status_filter):
    """'전체' 캐시 데이터에서 개별 탭용 데이터를 파생합니다 (상태만 필터링)."""
    checkbox_filters = {
        "댓글완료":      lambda t: t.get("comment_done") and not t.get("reply_done"),
        "대댓글완료":    lambda t: t.get("reply_done"),
        "좋아요작업완료": lambda t: t.get("like_done"),
    }

    if status_filter in checkbox_filters:
        return [t for t in all_tasks if checkbox_filters[status_filter](t)]
    else:
        return [t for t in all_tasks if t.get("status") == status_filter]


def _apply_date_filter(tasks, date_filter):
    """날짜 필터를 캐시된 데이터에 적용합니다."""
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))

    if date_filter.startswith("since:"):
        since_str = date_filter.split(":", 1)[1]
        since_dt = datetime.strptime(since_str, "%Y-%m-%d").replace(tzinfo=KST)
        return [t for t in tasks if t.get("last_edited") and
                datetime.fromisoformat(t["last_edited"].replace("Z", "+00:00")) >= since_dt]
    else:
        day_start = datetime.strptime(date_filter, "%Y-%m-%d").replace(tzinfo=KST)
        day_end = day_start + timedelta(days=1)
        return [t for t in tasks if t.get("last_edited") and
                day_start <= datetime.fromisoformat(t["last_edited"].replace("Z", "+00:00")) < day_end]


def _apply_sort(tasks, sort_param):
    """정렬을 적용합니다. sort_param: 'field:asc' 또는 'field:desc'"""
    if not sort_param or not tasks:
        return tasks

    parts = sort_param.split(":")
    field = parts[0] if parts else "last_edited"
    direction = parts[1] if len(parts) > 1 else "desc"
    reverse = (direction == "desc")

    def sort_key(t):
        val = t.get(field)
        if val is None:
            return ""
        return str(val)

    try:
        return sorted(tasks, key=sort_key, reverse=reverse)
    except Exception:
        return tasks


# 작업 목록 로딩 진행 상태 (UI 프로그레스 바용)
_loading_state = {
    "active": False,
    "loaded": 0,
    "message": "",
}
_loading_lock = threading.Lock()


def add_log(message, level="info"):
    """로그 메시지를 추가합니다."""
    automation_state["logs"].append({
        "time": datetime.now(KST).strftime("%H:%M:%S"),
        "message": message,
        "level": level,
    })
    # 최근 200개만 유지
    if len(automation_state["logs"]) > 200:
        automation_state["logs"] = automation_state["logs"][-200:]

    # Structured log to file
    try:
        import json as _json
        log_entry = {
            "timestamp": datetime.now(KST).isoformat(),
            "level": level,
            "message": message,
            "user": getattr(current_user, 'email', None) if current_user and hasattr(current_user, 'is_authenticated') and current_user.is_authenticated else None,
        }
        log_dir = os.path.join(os.environ.get("APPDATA", "."), "CommentBoost", "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, f"{datetime.now().strftime('%Y-%m-%d')}.jsonl")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(_json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def save_automation_log(action, user_id=None, account_label=None, video_url=None,
                        video_title=None, comment_text=None, comment_url=None,
                        detail=None, level="info"):
    """자동화 실행 로그를 DB에 영속 저장합니다."""
    try:
        with app.app_context():
            if user_id is None:
                user_id = current_user.id if (current_user and current_user.is_authenticated) else 1
            log = AutomationLog(
                user_id=user_id,
                action=action,
                account_label=account_label,
                video_url=video_url,
                video_title=video_title,
                comment_text=(comment_text or "")[:200] if comment_text else None,
                comment_url=comment_url,
                detail=(detail or "")[:500] if detail else None,
                level=level,
            )
            db.session.add(log)
            db.session.commit()
    except Exception:
        pass


def get_daily_comment_count(user_id):
    """Get today's comment count from DB."""
    today = date.today().isoformat()
    try:
        count = AutomationLog.query.filter(
            AutomationLog.user_id == user_id,
            AutomationLog.action == "comment_success",
            AutomationLog.created_at >= today
        ).count()
        return count
    except Exception:
        return 0

def check_daily_limit(user_id):
    """Check daily comment limit for user. Returns (can_post, used, limit)."""
    # owner_mode는 무제한
    if license_client.owner_mode:
        return True, 0, 99999

    # DB에서 직접 유저 플랜 조회 (license_info 동기화 지연 문제 방지)
    plan = "free"
    try:
        user = User.query.get(user_id)
        if user and user.plan:
            plan = user.plan.lower().strip()
    except Exception:
        # DB 조회 실패 시 license_info 폴백
        info = license_client.license_info
        if info and isinstance(info, dict):
            plan = (info.get("plan") or "free").lower().strip()

    from src.license_client import PLAN_DAILY_LIMITS
    limit = PLAN_DAILY_LIMITS.get(plan, 0)

    # enterprise는 무제한
    if plan == "enterprise" or limit >= 99999:
        return True, 0, 99999

    # free 플랜이라도 자동화는 허용 (토큰으로 제한됨)
    if limit == 0:
        return True, 0, 99999

    used = get_daily_comment_count(user_id)
    return used < limit, used, limit


def save_comment_history_db(user_id, account_label, video_url, video_id, comment_text):
    """댓글 히스토리를 DB에 저장합니다 (안전 규칙 + 관리자 통계용)."""
    try:
        with app.app_context():
            record = CommentHistory(
                user_id=user_id,
                account_label=account_label,
                video_id=video_id,
                video_url=video_url,
                comment_text=(comment_text or "")[:100],
            )
            db.session.add(record)
            db.session.commit()
    except Exception:
        pass


# 트래커에 대시보드 로그 콜백 연결
comment_tracker.set_log_callback(add_log)


def load_accounts(user_id=None):
    """유저별 계정 로드 (DB 우선 → 레거시 파일 폴백은 관리자만)."""
    # 유저 ID 결정
    if user_id is None:
        try:
            if current_user and current_user.is_authenticated:
                user_id = current_user.id
        except Exception:
            pass

    # DB에서 유저별 계정 조회
    if user_id:
        try:
            db_accounts = YouTubeAccount.query.filter_by(user_id=user_id).all()
            if db_accounts:
                return [acc.to_account_dict() for acc in db_accounts]
        except Exception:
            pass

    # 레거시 폴백 (관리자/Owner 모드에서만 — 일반 유저는 빈 리스트)
    if _admin_view_active or license_client.owner_mode:
        accounts_file = os.getenv("ACCOUNTS_FILE", "config/accounts.json")
        if os.path.exists(accounts_file):
            with open(accounts_file, "r", encoding="utf-8") as f:
                return json.load(f)
    return []


# ──────────────────────────── 페이지 라우트 ────────────────────────────

# ──────────────────────────── 인증 라우트 ────────────────────────────

@app.route("/auth")
def auth_page():
    """로그인/회원가입 페이지."""
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return render_template("auth.html")


def _get_hardware_id():
    """PC 고유 하드웨어 ID 생성 (MAC + CPU + 호스트명)."""
    try:
        import uuid as _uuid
        parts = [
            str(_uuid.getnode()),       # MAC 주소
            platform.node(),            # 호스트명
            platform.machine(),         # CPU 아키텍처
            platform.system(),          # OS
        ]
        return hashlib.sha256("-".join(parts).encode()).hexdigest()[:32]
    except Exception:
        return None


# Free 계정: 디바이스당 최대 생성 수
FREE_ACCOUNTS_PER_DEVICE = 1


# ─── 초대 코드 시스템 ───
# 고정 초대 코드 (코드에 내장 + .env 추가 가능)
_BUILTIN_CODES = {"BOOST2026", "COMMENT-VIP", "AGENCY-PRO"}
_env_codes = set(c.strip() for c in os.getenv("INVITE_CODES", "").split(",") if c.strip())
_FIXED_INVITE_CODES = _BUILTIN_CODES | _env_codes
# 1회용 초대 코드 (관리자가 생성, 사용 후 삭제)
_ONETIME_INVITE_CODES = {}  # {code: {"created_at": datetime, "expires_at": datetime}}
INVITE_REQUIRED = os.getenv("INVITE_REQUIRED", "true").lower() == "true"

# 유효한 코드인지 검증
def _is_valid_invite_code(code):
    if not code:
        return False
    # 고정 코드 체크
    if code in _FIXED_INVITE_CODES:
        return True
    # 1회용 코드 체크
    if code in _ONETIME_INVITE_CODES:
        info = _ONETIME_INVITE_CODES[code]
        if info.get("expires_at") and datetime.utcnow() > info["expires_at"]:
            del _ONETIME_INVITE_CODES[code]
            return False
        del _ONETIME_INVITE_CODES[code]  # 1회용이므로 삭제
        return True
    return False

INVITE_CODES = _FIXED_INVITE_CODES  # 하위 호환


@app.route("/api/auth/register", methods=["POST"])
def api_register():
    """회원가입 (초대 코드 필요)."""
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    nickname = (data.get("nickname") or "").strip()
    invite_code = (data.get("invite_code") or "").strip()

    if not email or "@" not in email:
        return jsonify({"error": "올바른 이메일을 입력해주세요."}), 400
    if len(password) < 6:
        return jsonify({"error": "비밀번호는 최소 6자 이상이어야 합니다."}), 400

    # 초대 코드 검증
    if INVITE_REQUIRED:
        if not invite_code:
            return jsonify({"error": "초대 코드를 입력해주세요. 관리자에게 문의하세요."}), 400
        if not _is_valid_invite_code(invite_code):
            return jsonify({"error": "유효하지 않은 초대 코드입니다."}), 403

    if User.query.filter_by(email=email).first():
        return jsonify({"error": "이미 가입된 이메일입니다."}), 409

    # 디바이스당 Free 계정 수 제한
    hw_id = _get_hardware_id()
    if hw_id:
        existing_free = User.query.filter_by(hardware_id=hw_id, plan="Free").count()
        if existing_free >= FREE_ACCOUNTS_PER_DEVICE:
            return jsonify({"error": "이 디바이스에서는 추가 계정을 생성할 수 없습니다. 기존 계정으로 로그인해주세요."}), 403

    user = User(email=email, nickname=nickname or email.split("@")[0])
    user.set_password(password)
    user.agreed_terms = True
    user.agreed_at = datetime.utcnow()
    user.hardware_id = hw_id

    # 신규 가입자는 항상 Free 플랜으로 시작 (결제 후 플랜 업그레이드)
    user.plan = "Free"

    db.session.add(user)
    db.session.flush()  # ID 생성

    # 활동 로그
    log = UserActivityLog(
        user_id=user.id, email=email, action="register",
        ip_address=request.remote_addr,
    )
    db.session.add(log)
    db.session.commit()

    login_user(user, remember=True)
    _sync_user_full(user)
    return jsonify({"success": True, "user": user.to_dict()})


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    """로그인 — 로컬 DB 우선, 없으면 서버에서 인증 후 로컬에 동기화."""
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    user = User.query.filter_by(email=email).first()

    if user and user.check_password(password):
        # 로컬 DB에서 인증 성공
        pass
    else:
        # 로컬 없음 또는 비밀번호 불일치 → 서버에서 인증 시도 (비밀번호 변경/초기화 대응)
        try:
            import requests as _req
            server_url = os.environ.get("CENTRAL_SERVER_URL", "https://commentboost-app.fly.dev")
            resp = _req.post(f"{server_url}/api/admin/auth-verify", json={
                "email": email, "password": password
            }, timeout=10)
            if resp.status_code == 200:
                srv_data = resp.json()
                if srv_data.get("success"):
                    if user:
                        # 로컬 계정 있음 + 서버 인증 성공 → 로컬 비밀번호 해시 갱신
                        user.set_password(password)
                        user.plan = srv_data.get("plan", user.plan)
                        user.token_balance = srv_data.get("token_balance", user.token_balance or 0)
                        user.like_credit_balance = srv_data.get("like_credit_balance", user.like_credit_balance or 0)
                        db.session.commit()
                        _log.info(f"서버 인증 성공 → 로컬 비밀번호 해시 갱신: {email}")
                    else:
                        # 로컬에 없음 → 새로 생성
                        user = User(
                            email=email,
                            nickname=srv_data.get("nickname", email.split("@")[0]),
                            plan=srv_data.get("plan", "Free"),
                        )
                        user.set_password(password)
                        user.agreed_terms = True
                        user.agreed_at = datetime.utcnow()
                        user.hardware_id = _get_hardware_id()
                        user.token_balance = srv_data.get("token_balance", 0)
                        user.like_credit_balance = srv_data.get("like_credit_balance", 0)
                        user.setup_completed = srv_data.get("setup_completed", False)
                        db.session.add(user)
                        db.session.commit()
                        _log.info(f"서버 인증 성공 → 로컬 유저 생성: {email} (plan={user.plan})")
                else:
                    return jsonify({"error": "이메일 또는 비밀번호가 올바르지 않습니다."}), 401
            else:
                return jsonify({"error": "이메일 또는 비밀번호가 올바르지 않습니다."}), 401
        except Exception as e:
            _log.warning(f"서버 인증 시도 실패 (네트워크 오류): {e}")
            return jsonify({"error": "이메일 또는 비밀번호가 올바르지 않습니다."}), 401

    user.last_login = datetime.utcnow()

    # 서버에서 관리자가 변경한 플랜/크레딧 동기화
    import requests as _req
    server_url = os.environ.get("CENTRAL_SERVER_URL", "https://commentboost-app.fly.dev")
    try:
        server_status = _check_server_user_status(email)
        if server_status and server_status.get("found"):
            # 서버에서 비활성화된 유저 차단
            if not server_status.get("is_active", True):
                return jsonify({"error": "계정이 비활성화되었습니다. 관리자에게 문의해주세요."}), 403
            # 서버 플랜이 로컬보다 높으면 동기화
            server_plan = server_status.get("plan", "")
            if server_plan and server_plan != "Free" and server_plan != user.plan:
                user.plan = server_plan
            # 서버 원장에서 좋아요 크레딧 잔액 조회 (ledger = single source of truth)
            try:
                credit_resp = _req.post(f"{server_url}/api/credits/balance",
                    json={"email": email}, timeout=10)
                if credit_resp.status_code == 200:
                    server_balance = credit_resp.json().get("balance", 0)
                    old_credits = user.like_credit_balance or 0
                    user.like_credit_balance = server_balance
                    license_client.like_credit_balance = server_balance
                    db.session.commit()
                    if old_credits != server_balance:
                        add_log(f"크레딧 동기화 완료: {old_credits:,}→{server_balance:,}원 (서버 원장 기준)", "info")
                    else:
                        add_log(f"크레딧 확인: {server_balance:,}원", "info")
                else:
                    add_log(f"크레딧 동기화 실패: 서버 응답 {credit_resp.status_code}", "warning")
            except Exception as e:
                add_log(f"크레딧 동기화 오류: {str(e)[:50]}", "warning")
            # 토큰도 서버 원장에서 조회 (token_ledger = single source of truth)
            try:
                token_resp = _req.post(f"{server_url}/api/tokens/balance",
                    json={"email": email}, timeout=10)
                if token_resp.status_code == 200:
                    server_tokens = token_resp.json().get("balance", 0)
                    old_tokens = user.token_balance or 0
                    user.token_balance = server_tokens
                    license_client.token_balance = server_tokens
                    db.session.commit()
                    if old_tokens != server_tokens:
                        add_log(f"토큰 동기화 완료: {old_tokens:,}→{server_tokens:,} (서버 원장 기준)", "info")
                    else:
                        add_log(f"토큰 확인: {server_tokens:,}", "info")
                else:
                    add_log(f"토큰 동기화 실패: 서버 응답 {token_resp.status_code}", "warning")
            except Exception as e:
                add_log(f"토큰 동기화 오류: {str(e)[:50]}", "warning")
    except Exception:
        pass

    # 로컬 DB에서 유저 플랜을 license_client에 동기화
    try:
        license_client.init_from_db_user(user)
    except Exception:
        pass

    db.session.commit()
    _log_activity(user.id, email, "login")

    login_user(user, remember=True)

    # 단일 세션 관리: 고유 session_id 생성 → 서버에 등록
    from flask import session as flask_session
    flask_session["session_id"] = str(uuid.uuid4())
    flask_session["login_ts"] = datetime.utcnow().timestamp()

    _sync_user_full(user)
    _sync_balance_snapshot(email, "login", user.token_balance or 0, user.like_credit_balance or 0)
    return jsonify({"success": True, "user": user.to_dict()})


@app.route("/api/auth/reset-password", methods=["POST"])
def api_reset_password():
    """비밀번호 재설정 (관리자 키 인증 필수)."""
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    new_password = data.get("new_password") or ""
    admin_key = (data.get("admin_key") or "").strip()

    # 관리자 키 인증 필수
    expected_key = os.environ.get("ADMIN_SECRET_KEY")
    if not expected_key or admin_key != expected_key:
        return jsonify({"error": "관리자 인증이 필요합니다."}), 403

    if not email or "@" not in email:
        return jsonify({"error": "올바른 이메일을 입력해주세요."}), 400
    if len(new_password) < 6:
        return jsonify({"error": "비밀번호는 최소 6자 이상이어야 합니다."}), 400

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({"error": "가입되지 않은 이메일입니다."}), 404

    user.set_password(new_password)
    _log_activity(user.id, email, "reset_password")

    return jsonify({"success": True, "message": "비밀번호가 재설정되었습니다."})


@app.route("/api/auth/logout", methods=["POST"])
@login_required
def api_logout():
    """로그아웃."""
    # 로그아웃 전 잔액 스냅샷 서버 전송 (이상징후 비교 기준)
    _sync_balance_snapshot(
        current_user.email, "logout",
        license_client.token_balance, license_client.like_credit_balance
    )
    license_client._save_to_db()  # 잔액을 DB에 확실히 저장
    _log_activity(current_user.id, current_user.email, "logout")
    logout_user()
    return jsonify({"success": True})


@app.route("/api/auth/save-session", methods=["POST"])
@login_required
def api_save_session():
    """자동로그인을 위한 세션 토큰 저장."""
    token = str(uuid.uuid4())
    user = current_user
    user.auto_login_token = token
    db.session.commit()
    return jsonify({"success": True, "token": token, "email": user.email})


@app.route("/api/auth/change-password", methods=["POST"])
@login_required
def api_change_password():
    """로컬 비밀번호 변경 (로그인 상태 + 현재 비밀번호 확인)."""
    data = request.get_json() or {}
    current_pw = data.get("current_password", "")
    new_pw = data.get("new_password", "")

    if not current_pw or not new_pw:
        return jsonify({"success": False, "error": "모든 필드를 입력해주세요."}), 400
    if len(new_pw) < 6:
        return jsonify({"success": False, "error": "새 비밀번호는 6자 이상이어야 합니다."}), 400
    if not check_password_hash(current_user.password_hash, current_pw):
        return jsonify({"success": False, "error": "현재 비밀번호가 올바르지 않습니다."}), 403

    current_user.password_hash = generate_password_hash(new_pw)
    db.session.commit()

    # 서버에도 동기화
    try:
        import requests as _req
        server_url = os.environ.get("CENTRAL_SERVER_URL", "https://commentboost-app.fly.dev")
        _req.post(f"{server_url}/api/sync/update-password",
            json={"email": current_user.email, "password_hash": current_user.password_hash},
            timeout=5)
    except Exception:
        pass

    return jsonify({"success": True})


@app.route("/api/auth/auto-login", methods=["POST"])
def api_auto_login():
    """저장된 토큰으로 자동 로그인."""
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    token = data.get("token", "")
    if not email or not token:
        return jsonify({"success": False, "error": "인증 정보 없음"}), 400

    user = User.query.filter_by(email=email).first()
    if not user or user.auto_login_token != token:
        return jsonify({"success": False, "error": "세션 만료"}), 401
    if not user.is_active:
        return jsonify({"success": False, "error": "비활성화된 계정"}), 403

    user.last_login = datetime.utcnow()

    # 서버에서 관리자가 변경한 플랜/크레딧 동기화
    try:
        server_status = _check_server_user_status(email)
        if server_status and server_status.get("found"):
            if not server_status.get("is_active", True):
                return jsonify({"success": False, "error": "계정이 비활성화되었습니다."}), 403
            server_plan = server_status.get("plan", "")
            if server_plan and server_plan != "Free" and server_plan != user.plan:
                user.plan = server_plan
            # 서버 원장에서 좋아요 크레딧 잔액 조회 (ledger = single source of truth)
            try:
                import requests as _req
                server_url = os.environ.get("CENTRAL_SERVER_URL", "https://commentboost-app.fly.dev")
                credit_resp = _req.post(f"{server_url}/api/credits/balance",
                    json={"email": email}, timeout=10)
                if credit_resp.status_code == 200:
                    server_balance = credit_resp.json().get("balance", 0)
                    user.like_credit_balance = server_balance
                    license_client.like_credit_balance = server_balance
                    db.session.commit()
            except Exception:
                pass
            # 토큰도 서버 원장에서 조회 (token_ledger = single source of truth)
            try:
                import requests as _req
                server_url = os.environ.get("CENTRAL_SERVER_URL", "https://commentboost-app.fly.dev")
                token_resp = _req.post(f"{server_url}/api/tokens/balance",
                    json={"email": email}, timeout=10)
                if token_resp.status_code == 200:
                    server_tokens = token_resp.json().get("balance", 0)
                    user.token_balance = server_tokens
                    license_client.token_balance = server_tokens
                    db.session.commit()
            except Exception:
                pass
    except Exception:
        pass

    try:
        license_client.init_from_db_user(user)
    except Exception:
        pass

    db.session.commit()
    _log_activity(user.id, email, "login")

    login_user(user, remember=True)

    from flask import session as flask_session
    flask_session["session_id"] = str(uuid.uuid4())
    flask_session["login_ts"] = datetime.utcnow().timestamp()

    _sync_user_full(user)
    _sync_balance_snapshot(email, "login", user.token_balance or 0, user.like_credit_balance or 0)
    return jsonify({"success": True, "nickname": user.nickname, "plan": user.plan})


@app.route("/api/auth/session-check")
@login_required
def api_session_check():
    """세션 유효성 확인 — 다른 기기에서 로그인 감지."""
    # 관리자/오너 모드에서는 단일 세션 강제 해제 (멀티 PC 관리 허용)
    if _admin_view_active or license_client.owner_mode:
        return jsonify({"valid": True})
    from flask import session as flask_session
    local_sid = flask_session.get("session_id", "")
    if not local_sid:
        return jsonify({"valid": True})  # 구버전 호환
    # 로그인 직후 60초간 세션 체크 유예 (cold start sync 지연 대응)
    login_ts = flask_session.get("login_ts", 0)
    if (datetime.utcnow().timestamp() - login_ts) < 60:
        return jsonify({"valid": True})
    try:
        server_status = _check_server_user_status(current_user.email)
        if server_status and server_status.get("found"):
            server_sid = server_status.get("session_id", "")
            if server_sid and server_sid != local_sid:
                logout_user()
                return jsonify({"valid": False, "reason": "다른 기기에서 로그인되어 로그아웃됩니다."})
    except Exception:
        pass
    return jsonify({"valid": True})


@app.route("/api/auth/me")
@login_required
def api_auth_me():
    """현재 로그인한 사용자 정보."""
    return jsonify({"user": current_user.to_dict()})


@app.route("/api/auth/bind-license", methods=["POST"])
@login_required
def api_bind_license():
    """라이선스 키를 현재 유저 계정에 바인딩."""
    data = request.get_json() or {}
    key = (data.get("license_key") or "").strip()
    if not key:
        return jsonify({"error": "라이선스 키를 입력해주세요."}), 400

    current_user.license_key = key
    db.session.commit()
    return jsonify({"success": True, "user": current_user.to_dict()})


# ──────────────────────────── 메인 페이지 ────────────────────────────

@app.route("/")
@login_required
def dashboard():
    """메인 대시보드."""
    return render_template(
        "dashboard.html",
        is_owner=_admin_view_active,
        setup_completed=current_user.setup_completed,
        plan=current_user.plan or "Free",
        notion_template_url=os.environ.get("NOTION_TEMPLATE_URL", "https://quirky-watch-e9e.notion.site/31e35cfd83d380a08acde2a493d047f7?source=copy_link"),
    )


# ──────────────────────────── API 라우트 ────────────────────────────

@app.route("/api/setup/complete", methods=["POST"])
@login_required
def api_setup_complete():
    """초기 설정 완료 마킹."""
    current_user.setup_completed = True
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/loading-status")
def api_loading_status():
    """작업 목록 로딩 진행 상태를 반환합니다."""
    with _loading_lock:
        return jsonify({
            "active": _loading_state["active"],
            "loaded": _loading_state["loaded"],
            "message": _loading_state["message"],
        })


@app.route("/api/status")
def api_status():
    """현재 자동화 상태를 반환합니다 (ETA 포함)."""
    state = dict(automation_state)

    # ETA 계산
    eta = {"avg_sec": 0, "remaining_sec": 0, "eta_time": None, "elapsed_sec": 0}
    if state["running"] and state["total"] > 0:
        elapsed_total = time.time() - eta_tracker["started_at"] if eta_tracker["started_at"] else 0
        eta["elapsed_sec"] = int(elapsed_total)

        task_times = eta_tracker["task_times"]
        if task_times:
            avg = sum(task_times) / len(task_times)
            eta["avg_sec"] = round(avg, 1)
            remaining_tasks = state["total"] - state["progress"]
            eta["remaining_sec"] = int(avg * remaining_tasks)
            finish_time = datetime.now() + timedelta(seconds=eta["remaining_sec"])
            eta["eta_time"] = finish_time.strftime("%H:%M")
        elif state["progress"] > 0 and elapsed_total > 0:
            # task_times가 아직 없지만 progress가 있으면 전체 경과로 추정
            avg = elapsed_total / state["progress"]
            eta["avg_sec"] = round(avg, 1)
            remaining_tasks = state["total"] - state["progress"]
            eta["remaining_sec"] = int(avg * remaining_tasks)
            finish_time = datetime.now() + timedelta(seconds=eta["remaining_sec"])
            eta["eta_time"] = finish_time.strftime("%H:%M")

    state["eta"] = eta
    state["stopping"] = automation_state.get("stopping", False)
    return jsonify(state)


@app.route("/api/daily-usage")
@login_required
def api_daily_usage():
    info = license_client.license_info
    plan = ((info.get("plan") if info and isinstance(info, dict) else None) or "free").lower().strip()
    from src.license_client import PLAN_DAILY_LIMITS
    limit = PLAN_DAILY_LIMITS.get(plan, 0)
    if license_client.owner_mode or plan == "enterprise":
        limit = 99999
    used = get_daily_comment_count(current_user.id)
    return jsonify({"used": used, "limit": limit, "plan": plan})


@app.route("/api/refresh-credit", methods=["POST"])
@login_required
def api_refresh_credit():
    """서버 원장에서 최신 크레딧 잔액을 조회하여 로컬에 반영."""
    try:
        import requests as _req
        server_url = os.environ.get("CENTRAL_SERVER_URL", "https://commentboost-app.fly.dev")
        resp = _req.post(f"{server_url}/api/credits/balance",
                        json={"email": current_user.email}, timeout=5)
        if resp.status_code == 200:
            balance = resp.json().get("balance", 0)
            current_user.like_credit_balance = balance
            db.session.commit()
            license_client.like_credit_balance = balance
            return jsonify({"success": True, "balance": balance})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "balance": current_user.like_credit_balance or 0})
    return jsonify({"success": False, "balance": current_user.like_credit_balance or 0})


@app.route("/api/dashboard")
def api_dashboard():
    """대시보드 데이터를 반환합니다."""
    safety_rules = SafetyRules()
    user_id = None
    try:
        if current_user and current_user.is_authenticated:
            user_id = current_user.id
    except Exception:
        pass
    accounts = load_accounts(user_id=user_id)
    smm = SMMClient()

    # 계정별 현황 (쿠키 상태 포함)
    account_stats = []
    import re as _re_dash
    for acc in accounts:
        label = acc.get("label", acc.get("email", "unknown"))
        status = safety_rules.get_account_status(label)
        # 쿠키 존재 여부 확인
        safe_label = _re_dash.sub(r'[^\w\-]', '_', label)
        cookie_dir = os.path.join(_config_dir, "sessions", str(user_id)) if user_id else os.path.join(_config_dir, "sessions")
        cookie_exists = os.path.exists(os.path.join(cookie_dir, f"{safe_label}.json"))
        account_stats.append({
            "label": label,
            "email": acc.get("email", ""),
            "type": acc.get("account_type", "unknown"),
            "today_count": status["today_count"],
            "max_count": status["max_count"],
            "remaining": status["remaining"],
            "has_cookies": cookie_exists,
        })

    # SMM 잔액
    smm_balance = None
    if smm.enabled:
        try:
            smm_balance = smm.get_balance()
        except Exception:
            smm_balance = None

    # 상태별 카운트 (전체 DB 1회 조회)
    status_counts = {}
    try:
        notion = _create_notion_manager()
        status_counts = notion.count_all_statuses()
    except Exception:
        pass

    # 오늘 총 성공 댓글 수 (DB 우선, 파일 폴백)
    try:
        from sqlalchemy import func as sa_func
        today_start = datetime.strptime(datetime.now().strftime("%Y-%m-%d"), "%Y-%m-%d")
        today_success = db.session.query(sa_func.count(CommentHistory.id)).filter(
            CommentHistory.created_at >= today_start
        ).scalar() or 0
        if today_success == 0:
            today_success = safety_rules.get_today_total_success()
    except Exception:
        today_success = safety_rules.get_today_total_success()

    pending_count = status_counts.get("댓글작업전", 0)
    result = {
        "accounts": account_stats,
        "account_count": len(accounts),
        "pending_count": pending_count,
        "reply_done_count": status_counts.get("댓글완료", 0),
        "like_pending_count": status_counts.get("대댓글완료", 0),
        "today_success": today_success,
        "status_counts": status_counts,
        "like_credit_balance": license_client.get_like_credit_balance(),
        "like_price_per_unit": 10,  # 전국 최저가 (원/개)
        "like_tiers": {
            "basic": {"name": "베이직", "price": 10, "label": "전국 최저가"},
            "standard": {"name": "스탠다드", "price": 15},
            "premium": {"name": "프리미엄", "price": 20},
        },
        "credit_estimate": _estimate_credit_usage(pending_count),
        "settings": {
            "max_comments_per_day": int(os.getenv("MAX_COMMENTS_PER_DAY", "20")),
            "comment_interval_sec": int(os.getenv("COMMENT_INTERVAL_SEC", "180")),
            "same_video_interval_min": int(os.getenv("SAME_VIDEO_INTERVAL_MIN", "30")),
            "smm_like_quantity": int(os.getenv("SMM_LIKE_QUANTITY", "20")),
        },
    }
    # SMM 원가 정보는 관리자에게만 노출
    if _admin_view_active or license_client.owner_mode:
        result["smm_enabled"] = smm.enabled
        result["smm_balance"] = smm_balance

    return jsonify(result)


@app.route("/api/credit/estimate")
@login_required
def api_credit_estimate():
    """크레딧 소모 예측 정보를 반환합니다."""
    try:
        notion = _create_notion_manager()
        counts = notion.count_all_statuses()
        pending = counts.get("댓글작업전", 0)
        estimate = _estimate_credit_usage(pending)
        return jsonify(estimate)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/tasks")
def api_tasks():
    """노션 DB에서 작업 목록을 가져옵니다.
    ?status=&date=YYYY-MM-DD&page=1&search=&sort=last_edited:desc&refresh=1 파라미터 지원.
    캐시: 상태별 1회 로드 → 날짜/검색/정렬은 캐시에서 즉시 처리 (노션 재호출 없음)
    """
    try:
        status_filter = request.args.get("status", "댓글작업전")
        date_filter = request.args.get("date", None)
        # 전체 리스트는 날짜 필터 무시
        if status_filter == "전체":
            date_filter = None
        search_query = request.args.get("search", "").strip()
        sort_param = request.args.get("sort", "last_edited:desc")  # 기본: 최근 작업순
        page = int(request.args.get("page", 1))
        force_refresh = request.args.get("refresh", "0") == "1"
        max_results = int(request.args.get("max_results", 0))  # 0=전체, 양수=제한
        page_size = 100

        # 캐시 키: 상태만 사용 (날짜/검색/정렬은 캐시 후 필터링)
        cache_key = status_filter

        # 캐시 확인 (유효한 캐시가 있으면 노션 API 호출 생략)
        tasks = None
        cached_ago = 0
        from_cache = False
        with _task_cache_lock:
            entry = _task_cache.get(cache_key)
            if (not force_refresh
                    and entry
                    and entry["tasks"] is not None
                    and (time.time() - entry["fetched_at"]) < _task_cache_ttl):
                tasks = list(entry["tasks"])  # 원본 보호용 복사
                cached_ago = int(time.time() - entry["fetched_at"])
                from_cache = True

            # "전체" 캐시에서 개별 탭 데이터 파생 (불필요한 재수집 방지)
            if tasks is None and not force_refresh and status_filter != "전체":
                all_entry = _task_cache.get("전체")
                if (all_entry and all_entry["tasks"] is not None
                        and (time.time() - all_entry["fetched_at"]) < _task_cache_ttl):
                    all_tasks = all_entry["tasks"]
                    tasks = _filter_tasks_from_cache(all_tasks, status_filter)
                    cached_ago = int(time.time() - all_entry["fetched_at"])
                    from_cache = True

        # 캐시 미스 → 노션에서 가져오기
        if tasks is None:
            def on_progress(loaded, message=""):
                with _loading_lock:
                    _loading_state["active"] = True
                    _loading_state["loaded"] = loaded
                    _loading_state["message"] = message

            with _loading_lock:
                _loading_state["active"] = True
                _loading_state["loaded"] = 0
                _loading_state["message"] = "노션 데이터 불러오는 중..."

            try:
                notion = _create_notion_manager()
                if status_filter == "전체":
                    tasks = notion.get_all_tasks(progress_callback=on_progress)
                else:
                    # 날짜 없이 전체 상태 로드 (캐시 후 날짜는 필터링)
                    tasks = notion.get_tasks_by_status(status_filter, date_filter=None, progress_callback=on_progress, max_results=max_results)

                    # 상태 필터 결과가 0건이면 → 전체 로드 후 실제 상태값 감지
                    if not tasks and status_filter == "댓글작업전":
                        on_progress(0, "상태값 자동 감지 중...")
                        all_tasks = notion.get_all_tasks(progress_callback=on_progress)
                        if all_tasks:
                            # 실제 상태값 수집
                            found_statuses = {}
                            for t in all_tasks:
                                sv = t.get("status", "")
                                if sv:
                                    found_statuses[sv] = found_statuses.get(sv, 0) + 1
                            # 전체 캐시에 저장
                            now_t = time.time()
                            with _task_cache_lock:
                                _task_cache["전체"] = {"tasks": all_tasks, "fetched_at": now_t}
                            # 댓글 완료 체크박스가 False인 작업 = 댓글 미작업
                            tasks = [t for t in all_tasks if not t.get("comment_done", False)]
                            add_log(f"[상태 자동감지] 실제 상태값: {found_statuses}")
                            add_log(f"[상태 자동감지] 댓글 미완료 작업: {len(tasks)}건 (전체 {len(all_tasks)}건)")
            except ValueError as ve:
                # 토큰/DB ID 미설정
                add_log(f"[노션] 설정 오류: {ve}")
                return jsonify({
                    "error": f"노션 설정을 확인해주세요: {ve}",
                    "tasks": [], "count": 0, "total_count": 0,
                    "page": 1, "total_pages": 1,
                }), 400
            except Exception as notion_err:
                add_log(f"[노션] 데이터 로딩 실패: {notion_err}")
                return jsonify({
                    "error": f"노션 데이터 로딩 실패: {notion_err}",
                    "tasks": [], "count": 0, "total_count": 0,
                    "page": 1, "total_pages": 1,
                }), 500
            finally:
                with _loading_lock:
                    _loading_state["active"] = False
                    _loading_state["loaded"] = len(tasks) if tasks else 0
                    _loading_state["message"] = ""

            # 캐시 저장 (상태별, 날짜 무관)
            now = time.time()
            with _task_cache_lock:
                _task_cache[cache_key] = {"tasks": tasks, "fetched_at": now}
                # "전체" 로드 시 개별 탭 캐시도 자동 생성
                if status_filter == "전체" and tasks:
                    for st in ["댓글작업전", "댓글완료", "대댓글완료", "좋아요작업완료", "중복", "에러"]:
                        _task_cache[st] = {
                            "tasks": _filter_tasks_from_cache(tasks, st),
                            "fetched_at": now,
                        }
                # 오래된 캐시 정리 (12개 초과 시 가장 오래된 것 제거)
                if len(_task_cache) > 12:
                    oldest_key = min(_task_cache, key=lambda k: _task_cache[k]["fetched_at"])
                    del _task_cache[oldest_key]
            cached_ago = 0
            tasks = list(tasks)  # 복사

        # 날짜 필터 적용 (캐시된 데이터에서 즉시 필터링)
        if date_filter and tasks:
            tasks = _apply_date_filter(tasks, date_filter)

        # 검색 필터 적용
        if search_query:
            q = search_query.lower()
            tasks = [t for t in tasks if
                     q in (t.get("youtube_url") or "").lower() or
                     q in (t.get("comment_text") or "").lower() or
                     q in (t.get("video_title") or "").lower() or
                     q in (t.get("account") or "").lower() or
                     q in (t.get("brand") or "").lower()]

        # 정렬 적용
        tasks = _apply_sort(tasks, sort_param)

        # 페이지네이션
        total_count = len(tasks)
        total_pages = max(1, (total_count + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paged_tasks = tasks[start_idx:end_idx]

        # max_results 제한으로 잘린 경우 표시
        truncated = max_results > 0 and total_count >= max_results

        return jsonify({
            "tasks": paged_tasks,
            "count": len(paged_tasks),
            "total_count": total_count,
            "page": page,
            "total_pages": total_pages,
            "status": status_filter,
            "date": date_filter,
            "search": search_query,
            "sort": sort_param,
            "from_cache": from_cache,
            "cached_ago": cached_ago,
            "truncated": truncated,
            "max_results": max_results if truncated else 0,
        })
    except Exception as e:
        return jsonify({"error": str(e), "tasks": [], "count": 0}), 500


@app.route("/api/tasks/counts")
def api_task_counts():
    """각 탭별 작업 개수를 반환합니다 (캐시 기반, "전체" 캐시에서 파생)."""
    try:
        statuses = ["댓글작업전", "댓글완료", "대댓글완료", "좋아요작업완료", "중복", "에러"]
        counts = {}

        with _task_cache_lock:
            # "전체" 캐시가 있으면 거기서 파생
            all_entry = _task_cache.get("전체")
            if all_entry and all_entry["tasks"] and (time.time() - all_entry["fetched_at"]) < _task_cache_ttl:
                all_tasks = all_entry["tasks"]
                counts["전체"] = len(all_tasks)
                for st in statuses:
                    counts[st] = len(_filter_tasks_from_cache(all_tasks, st))
            else:
                # 개별 캐시에서 수집
                for st in statuses:
                    entry = _task_cache.get(st)
                    if entry and entry["tasks"] and (time.time() - entry["fetched_at"]) < _task_cache_ttl:
                        counts[st] = len(entry["tasks"])

        return jsonify({"counts": counts})
    except Exception as e:
        return jsonify({"counts": {}, "error": str(e)})


@app.route("/api/notion/debug")
@login_required
def api_notion_debug():
    """노션 DB 구조를 확인합니다 (디버깅용)."""
    try:
        from notion_client import Client
        token, db_id = _ensure_notion_env()
        token = token or ""
        db_id = db_id or ""

        if not token or not db_id:
            return jsonify({"error": "NOTION_API_TOKEN 또는 NOTION_DATABASE_ID 미설정"}), 400

        client = Client(auth=token)

        # DB 메타데이터 조회 (컬럼 구조)
        db_info = client.databases.retrieve(database_id=db_id)
        properties = {}
        for name, prop in db_info.get("properties", {}).items():
            properties[name] = {
                "type": prop.get("type", "unknown"),
                "id": prop.get("id", ""),
            }

        # 댓글작업전 상태의 샘플 3개 조회 (유저 설정 컬럼명 우선)
        col_status = _get_user_setting(current_user.id, "NOTION_COLUMN_STATUS") or os.getenv("NOTION_COLUMN_STATUS", "상태")
        try:
            response = client.databases.query(
                database_id=db_id,
                page_size=3,
                filter={"property": col_status, "select": {"equals": "댓글작업전"}},
            )
        except Exception:
            try:
                response = client.databases.query(
                    database_id=db_id,
                    page_size=3,
                    filter={"property": col_status, "status": {"equals": "댓글작업전"}},
                )
            except Exception:
                response = client.databases.query(database_id=db_id, page_size=3)
        sample_pages = []
        for page in response.get("results", []):
            page_data = {"id": page["id"]}
            for name, prop in page.get("properties", {}).items():
                prop_type = prop.get("type", "")
                if prop_type == "title":
                    titles = prop.get("title", [])
                    page_data[name] = "".join(t.get("plain_text", "") for t in titles)
                elif prop_type == "rich_text":
                    texts = prop.get("rich_text", [])
                    page_data[name] = "".join(t.get("plain_text", "") for t in texts)
                elif prop_type == "url":
                    page_data[name] = prop.get("url", "")
                elif prop_type == "status":
                    status = prop.get("status")
                    page_data[name] = status.get("name", "") if status else ""
                elif prop_type == "select":
                    select = prop.get("select")
                    page_data[name] = select.get("name", "") if select else ""
                else:
                    page_data[name] = f"[{prop_type}]"
            sample_pages.append(page_data)

        # 현재 .env 파일에서 직접 컬럼명 읽기 (캐시된 환경변수 무시)
        env_columns = {}
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    env_columns[key.strip()] = val.strip()

        expected = {
            "영상 링크": _get_user_setting(current_user.id, "NOTION_COLUMN_YOUTUBE_URL") or env_columns.get("NOTION_COLUMN_YOUTUBE_URL", "영상 링크"),
            "댓글 원고": _get_user_setting(current_user.id, "NOTION_COLUMN_COMMENT_TEXT") or env_columns.get("NOTION_COLUMN_COMMENT_TEXT", "댓글 원고"),
            "상태": _get_user_setting(current_user.id, "NOTION_COLUMN_STATUS") or env_columns.get("NOTION_COLUMN_STATUS", "상태"),
            "댓글 계정": _get_user_setting(current_user.id, "NOTION_COLUMN_ACCOUNT") or env_columns.get("NOTION_COLUMN_ACCOUNT", "댓글 계정"),
            "댓글 url": _get_user_setting(current_user.id, "NOTION_COLUMN_COMMENT_RESULT_URL") or env_columns.get("NOTION_COLUMN_COMMENT_RESULT_URL", "댓글 url"),
        }

        # 매칭 확인
        actual_names = set(properties.keys())
        matching = {}
        for label, col_name in expected.items():
            matching[label] = {
                "expected": col_name,
                "found": col_name in actual_names,
            }

        return jsonify({
            "db_title": db_info.get("title", [{}])[0].get("plain_text", "Untitled") if db_info.get("title") else "Untitled",
            "properties": properties,
            "expected_columns": matching,
            "total_pages": len(response.get("results", [])),
            "sample_pages": sample_pages,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/run", methods=["POST"])
def api_run():
    """자동화를 백그라운드에서 시작합니다. limit 파라미터로 테스트 실행 가능."""
    # 라이선스 인증 확인
    lic_err = _check_license_active()
    if lic_err:
        return jsonify({"error": lic_err}), 403

    with automation_lock:
        if automation_state["running"]:
            return jsonify({"error": "이미 실행 중입니다."}), 409

        automation_state["running"] = True
        automation_state["progress"] = 0
        automation_state["logs"] = []
        automation_state["distribution_notice"] = None  # 이전 실행의 분배 알림 초기화
        global _credit_sync_paused
        _credit_sync_paused = True  # 자동화 중 크레딧 폴링 일시 중지
        automation_state["results"] = {"success": 0, "fail": 0, "skip": 0, "likes": 0, "duplicate": 0}

        # ETA 초기화
        eta_tracker["task_times"] = []
        eta_tracker["current_task_start"] = 0
        eta_tracker["started_at"] = time.time()

    data = request.get_json(silent=True) or {}
    limit = data.get("limit", 0)  # 0 = 전체, 1~N = 테스트 건수
    selected_ids = data.get("selected_ids", [])  # 선택된 page_id 목록
    full_auto = data.get("full_auto", False)  # Full Auto 모드
    test_mode = limit > 0

    automation_state["test_mode"] = test_mode
    automation_state["limit"] = limit
    automation_state["full_auto"] = full_auto
    automation_state["user_id"] = current_user.id  # 스레드에서 사용

    if full_auto:
        thread = threading.Thread(
            target=_run_full_auto, daemon=True
        )
    else:
        thread = threading.Thread(
            target=_run_automation, args=(limit, selected_ids), daemon=True
        )
    thread.start()

    if full_auto:
        mode_text = "Full Auto (무한 반복)"
    elif selected_ids:
        mode_text = f"선택 실행 ({len(selected_ids)}건)"
    elif test_mode:
        mode_text = f"테스트 모드 ({limit}건)"
    else:
        mode_text = "전체 실행"
    return jsonify({"message": f"자동화가 시작되었습니다. [{mode_text}]"})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    """자동화를 안전하게 중지합니다.
    현재 작업이 끝난 후 종료되며, 비행기모드 ON 상태면 OFF 후 종료합니다.
    """
    automation_state["running"] = False
    automation_state["stopping"] = True
    add_log("중지 요청됨 - 현재 작업 완료 후 안전하게 종료합니다...", "warning")

    # 비행기모드가 ON 상태일 수 있으므로 안전하게 OFF
    try:
        adb_changer = ADBIPChanger()
        if adb_changer.enabled:
            add_log("비행기모드 안전 해제 중...", "info")
            adb_changer.force_airplane_off()
            add_log("비행기모드 OFF 확인 완료", "success")
    except Exception as e:
        add_log(f"비행기모드 해제 중 오류 (수동 확인 필요): {e}", "warning")

    return jsonify({
        "message": "중지 요청이 전송되었습니다. 현재 작업 완료 후 안전하게 종료됩니다.",
        "safe_stop": True,
    })


@app.route("/api/reply/run", methods=["POST"])
def api_reply_run():
    """대댓글 자동화를 백그라운드에서 시작합니다."""
    lic_err = _check_license_active()
    if lic_err:
        return jsonify({"error": lic_err}), 403

    with reply_lock:
        if reply_state["running"]:
            return jsonify({"error": "대댓글 자동화가 이미 실행 중입니다."}), 409
        if automation_state["running"]:
            return jsonify({"error": "댓글 자동화가 실행 중입니다. 완료 후 시도하세요."}), 409

        reply_state["running"] = True
        reply_state["progress"] = 0
        reply_state["results"] = {"success": 0, "fail": 0, "skip": 0}

    data = request.get_json(silent=True) or {}
    limit = data.get("limit", 0)
    reply_state["user_id"] = current_user.id  # 스레드에서 사용

    thread = threading.Thread(target=_run_reply_automation, args=(limit,), daemon=True)
    thread.start()

    mode = f"테스트 ({limit}건)" if limit > 0 else "전체 실행"
    return jsonify({"message": f"대댓글 자동화가 시작되었습니다. [{mode}]"})


@app.route("/api/reply/stop", methods=["POST"])
def api_reply_stop():
    """대댓글 자동화를 중지합니다."""
    reply_state["running"] = False
    add_log("[대댓글] 사용자가 대댓글 자동화를 중지했습니다.", "warning")
    return jsonify({"message": "대댓글 자동화 중지 요청이 전송되었습니다."})


@app.route("/api/reply/status")
def api_reply_status():
    """대댓글 자동화 상태를 반환합니다."""
    return jsonify({
        "running": reply_state["running"],
        "progress": reply_state["progress"],
        "total": reply_state["total"],
        "current_task": reply_state["current_task"],
        "results": reply_state["results"],
    })


@app.route("/api/reply/preview")
def api_reply_preview():
    """대댓글 대기 작업 목록을 미리보기합니다."""
    try:
        notion = _create_notion_manager()
        tasks = notion.get_reply_pending_tasks()
        items = []
        for t in tasks:
            items.append({
                "page_id": t.get("page_id", ""),
                "video_title": t.get("video_title", ""),
                "youtube_url": t.get("youtube_url", ""),
                "account": t.get("account", ""),
                "comment_preview": (t.get("comment_text", "")[:30] + "...") if len(t.get("comment_text", "")) > 30 else t.get("comment_text", ""),
                "reply_preview": (t.get("reply_text", "")[:30] + "...") if len(t.get("reply_text", "")) > 30 else t.get("reply_text", ""),
                "result_url": t.get("result_url", ""),
            })
        return jsonify({"tasks": items, "count": len(items)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/duplicate-scan", methods=["POST"])
def api_duplicate_scan():
    """사전 중복 스캔: 대기 작업의 영상 링크를 전체 DB 완료 목록과 비교합니다."""
    if not license_client.can_use_feature("duplicate_scan") and not license_client.owner_mode:
        return jsonify({"error": "중복 스캔은 Business 플랜부터 사용 가능합니다.", "upgrade_required": True}), 403

    try:
        notion = _create_notion_manager()
        tasks = notion.get_pending_tasks()
        if not tasks:
            return jsonify({"duplicates": [], "clean_count": 0, "message": "대기 작업이 없습니다."})

        clean_tasks, duplicate_tasks = notion.check_duplicates(tasks)

        # 중복 목록 상세 정보
        dup_details = []
        for t in duplicate_tasks:
            dup_details.append({
                "page_id": t.get("page_id", ""),
                "youtube_url": t.get("youtube_url", ""),
                "video_title": t.get("video_title", ""),
                "account": t.get("account", ""),
                "comment_preview": (t.get("comment_text", "")[:40] + "...") if len(t.get("comment_text", "")) > 40 else t.get("comment_text", ""),
            })

        return jsonify({
            "duplicates": dup_details,
            "duplicate_count": len(duplicate_tasks),
            "clean_count": len(clean_tasks),
            "total_count": len(tasks),
            "message": f"스캔 완료: 전체 {len(tasks)}건 중 중복 {len(duplicate_tasks)}건 발견"
                       + (f" → '중복' 상태로 변경됨" if duplicate_tasks else " (중복 없음)"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/smm/services")
def api_smm_services():
    """SMM 서비스 목록을 조회합니다."""
    try:
        smm = SMMClient()
        if not smm.enabled:
            return jsonify({"error": "SMM이 비활성 상태입니다.", "services": []}), 400

        services = smm.get_services()
        # YouTube 관련 서비스 필터링
        youtube_services = [
            s for s in services
            if "youtube" in s.get("name", "").lower()
            or "youtube" in s.get("category", "").lower()
        ]
        return jsonify({
            "services": youtube_services,
            "all_count": len(services),
            "youtube_count": len(youtube_services),
        })
    except Exception as e:
        return jsonify({"error": str(e), "services": []}), 500


@app.route("/api/smm/balance")
def api_smm_balance():
    """SMM 잔액을 조회합니다."""
    try:
        smm = SMMClient()
        balance = smm.get_balance()
        return jsonify({"balance": balance})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _get_or_create_user_settings(user_id):
    """유저 설정 객체를 반환합니다. 없으면 생성합니다."""
    us = UserSettings.query.filter_by(user_id=user_id).first()
    if not us:
        us = UserSettings(user_id=user_id, settings_json="{}")
        db.session.add(us)
        db.session.commit()
    return us


def _get_user_setting(user_id, key, default=None):
    """유저 개별 설정값을 반환합니다. 유저 설정 → .env 기본값 순서로 폴백."""
    try:
        us = UserSettings.query.filter_by(user_id=user_id).first()
        if us:
            settings = us.get_settings()
            val = settings.get(key)
            if val:
                return val
    except Exception:
        # app context 밖에서 호출된 경우 .env 폴백
        pass
    # 폴백: .env 또는 기본값
    return os.getenv(key, default or UserSettings.DEFAULTS.get(key, ""))


def _ensure_notion_env(user_id=None):
    """유저 DB 설정의 Notion 토큰/DB ID를 os.environ에 반영하고, (token, db_id)를 반환합니다.
    NotionManager(token, database_id)로 직접 전달하여 .env 덮어쓰기 방지."""
    if user_id is None:
        try:
            if current_user and current_user.is_authenticated:
                user_id = current_user.id
            else:
                user_id = 1
        except Exception:
            user_id = 1

    token = _get_user_setting(user_id, "NOTION_API_TOKEN")
    db_id = _get_user_setting(user_id, "NOTION_DATABASE_ID")
    if token:
        os.environ["NOTION_API_TOKEN"] = token
    if db_id:
        os.environ["NOTION_DATABASE_ID"] = db_id

    # 컬럼명 설정도 반영
    for col_key in ["NOTION_COLUMN_YOUTUBE_URL", "NOTION_COLUMN_COMMENT_TEXT",
                     "NOTION_COLUMN_COMMENT_RESULT_URL", "NOTION_COLUMN_STATUS",
                     "NOTION_COLUMN_ACCOUNT"]:
        val = _get_user_setting(user_id, col_key)
        if val:
            os.environ[col_key] = val

    return token, db_id


def _create_notion_manager(user_id=None):
    """유저 설정에서 노션 토큰/DB ID를 읽어 NotionManager를 생성합니다.
    .env 파일의 오래된 토큰이 유저 설정을 덮어쓰는 문제를 방지합니다."""
    token, db_id = _ensure_notion_env(user_id)
    return NotionManager(token=token, database_id=db_id)


MASKED_PLACEHOLDER = "__MASKED__"


def _mask_key(value, visible=4):
    """API 키를 마스킹합니다. 앞 4자만 보여줌. 마스킹 접두사 추가."""
    if not value or len(value) <= visible:
        return value
    return MASKED_PLACEHOLDER + value[:visible] + "*" * min(len(value) - visible, 20)


def _is_masked_value(value):
    """마스킹된 값인지 정확하게 판별합니다 (단순 * 포함 여부 대신 접두사 체크)."""
    if not value or not isinstance(value, str):
        return False
    return value.startswith(MASKED_PLACEHOLDER)


# ──────────────────────────── 좋아요 관리자 컨펌 API ────────────────────────────

@app.route("/api/likes/pending")
def api_likes_pending():
    """좋아요 승인 대기 리스트를 조회합니다."""
    with like_pending_lock:
        return jsonify({"items": list(like_pending_list)})


@app.route("/api/likes/approve", methods=["POST"])
def api_likes_approve():
    """개별 또는 일괄 좋아요 주문을 승인합니다."""
    data = request.get_json() or {}
    item_id = data.get("id")         # 개별 승인
    approve_all = data.get("all")    # 일괄 승인
    custom_qty = data.get("custom_qty")  # 사용자 지정 수량

    results = []
    with like_pending_lock:
        targets = list(like_pending_list) if approve_all else [
            item for item in like_pending_list if item["id"] == item_id
        ]

        for item in targets:
            qty = custom_qty if (custom_qty and not approve_all) else item["qty"]
            order = license_client.order_likes_via_server(item["comment_url"], quantity=qty, source="approval")
            if order.get("success"):
                add_log(
                    f"[수동 승인] 좋아요 {item['qty']}개 주문 완료 | "
                    f"주문ID: {order.get('order_id')} | {item['video_title'][:30]}",
                    "success"
                )
                _save_like_order(order.get("order_id"), item["comment_url"], qty, source="approval")
                # 좋아요 체크박스 업데이트
                try:
                    notion = _create_notion_manager()
                    notion.update_like_checkbox(item["page_id"])
                except Exception:
                    pass
                results.append({"id": item["id"], "success": True, "order_id": order.get("order_id")})
            else:
                add_log(f"[수동 승인 실패] {item['video_title'][:30]} | {order.get('error')}", "error")
                results.append({"id": item["id"], "success": False, "error": order.get("error")})

        # 성공한 항목 제거
        success_ids = {r["id"] for r in results if r["success"]}
        like_pending_list[:] = [item for item in like_pending_list if item["id"] not in success_ids]

    return jsonify({"success": True, "results": results})


@app.route("/api/likes/dismiss", methods=["POST"])
def api_likes_dismiss():
    """대기 항목을 무시합니다 (기본 수량으로 이미 처리됨)."""
    data = request.get_json() or {}
    item_id = data.get("id")
    dismiss_all = data.get("all")

    with like_pending_lock:
        if dismiss_all:
            count = len(like_pending_list)
            like_pending_list.clear()
            add_log(f"[무시] 좋아요 대기 {count}건 모두 제거", "info")
        elif item_id:
            like_pending_list[:] = [item for item in like_pending_list if item["id"] != item_id]
            add_log(f"[무시] 좋아요 대기 항목 제거", "info")

    return jsonify({"success": True})


def _calculate_dynamic_likes(top_likes, default_qty):
    """
    상위 댓글 좋아요 수를 기반으로 동적 좋아요 수량을 계산합니다.

    전략: 1등 댓글 좋아요 + 10~20개 (랜덤)
    - 1등보다 살짝 높게 설정하여 상위 노출
    - MAX(500) 초과 시 → 대기 리스트로 이동 (호출측에서 처리)
    - 최소: 기본 수량 보장
    """
    if not top_likes:
        return default_qty

    nonzero = [n for n in top_likes if n > 0]
    if not nonzero:
        return default_qty

    top1 = max(nonzero)  # 1등 댓글 좋아요 수
    extra = random.randint(10, 20)
    target = top1 + extra
    return max(target, default_qty)


@app.route("/api/settings", methods=["GET"])
@login_required
def api_get_settings():
    """현재 설정값을 반환합니다 (유저별 설정 우선, .env 폴백)."""
    us = _get_or_create_user_settings(current_user.id)
    s = us.get_settings()
    return jsonify({
        "NOTION_API_TOKEN": _mask_key(s.get("NOTION_API_TOKEN", "")),
        "NOTION_DATABASE_ID": s.get("NOTION_DATABASE_ID", ""),
        "SMM_API_KEY": _mask_key(os.getenv("SMM_API_KEY", "")),
        "SMM_ENABLED": os.getenv("SMM_ENABLED", "false"),
        "SMM_LIKE_SERVICE_ID": os.getenv("SMM_LIKE_SERVICE_ID", "4001"),
        "SMM_LIKE_QUANTITY": s.get("SMM_LIKE_QUANTITY", "20"),
        "SMM_LIKE_AUTO_MAX": s.get("SMM_LIKE_AUTO_MAX", "500"),
        "LIKE_AUTO_TIER": s.get("LIKE_AUTO_TIER", "standard"),
        "MAX_COMMENTS_PER_DAY": s.get("MAX_COMMENTS_PER_DAY", "20"),
        "COMMENT_INTERVAL_SEC": s.get("COMMENT_INTERVAL_SEC", "180"),
        "SAME_VIDEO_INTERVAL_MIN": s.get("SAME_VIDEO_INTERVAL_MIN", "30"),
        "HEADLESS": s.get("HEADLESS", "false"),
        "PRE_WATCH_COMMENT": s.get("PRE_WATCH_COMMENT", "false"),
        "ADB_IP_CHANGE_ENABLED": s.get("ADB_IP_CHANGE_ENABLED", "false"),
        "ADB_PATH": s.get("ADB_PATH", r"D:\platform-tools\adb.exe"),
        "ADB_AIRPLANE_WAIT": s.get("ADB_AIRPLANE_WAIT", "4"),
        "ADB_AUTO_ETHERNET": s.get("ADB_AUTO_ETHERNET", "true"),
        "ADB_ETHERNET_NAME": s.get("ADB_ETHERNET_NAME", "이더넷"),
        "LIKE_CREDIT_THRESHOLD": s.get("LIKE_CREDIT_THRESHOLD", "5000"),
        "LIKE_CREDIT_AUTO_ALERT": s.get("LIKE_CREDIT_AUTO_ALERT", "true"),
    })


@app.route("/api/settings", methods=["POST"])
@login_required
def api_save_settings():
    """설정을 유저별 DB에 저장합니다. Owner용 SMM 설정만 .env에도 저장."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "데이터가 없습니다."}), 400

        # 유저별 저장 가능한 키 (DB에 저장)
        user_keys = {
            "NOTION_API_TOKEN", "NOTION_DATABASE_ID",
            "SMM_LIKE_QUANTITY", "SMM_LIKE_AUTO_MAX", "LIKE_AUTO_TIER",
            "MAX_COMMENTS_PER_DAY", "COMMENT_INTERVAL_SEC", "SAME_VIDEO_INTERVAL_MIN",
            "HEADLESS", "PRE_WATCH_COMMENT",
            "ADB_IP_CHANGE_ENABLED", "ADB_PATH", "ADB_AIRPLANE_WAIT",
            "ADB_AUTO_ETHERNET", "ADB_ETHERNET_NAME",
            "LIKE_CREDIT_THRESHOLD", "LIKE_CREDIT_AUTO_ALERT",
        }
        # Owner 전용 글로벌 설정 (.env에도 저장)
        owner_keys = {"SMM_API_KEY", "SMM_ENABLED", "SMM_LIKE_SERVICE_ID"}

        # 유저 설정 업데이트
        us = _get_or_create_user_settings(current_user.id)
        user_updates = {}
        for key, value in data.items():
            if key not in user_keys:
                continue
            if _is_masked_value(str(value)):
                continue  # 마스킹된 값은 저장하지 않음 (기존 값 유지)
            user_updates[key] = str(value)

        if user_updates:
            us.update_settings(user_updates)
            db.session.commit()
            # os.environ도 갱신 (자동화 엔진 호환)
            for k, v in user_updates.items():
                os.environ[k] = v

        # Owner 전용 글로벌 설정은 .env에도 저장
        if _admin_view_active:
            env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
            for key, value in data.items():
                if key not in owner_keys:
                    continue
                if _is_masked_value(str(value)):
                    continue  # 마스킹된 값은 저장하지 않음 (기존 값 유지)
                _update_env_var(env_path, key, str(value))

        # Notion 관련 설정이 바뀌었으면 setup_completed 체크
        if "NOTION_API_TOKEN" in user_updates and user_updates["NOTION_API_TOKEN"]:
            if not current_user.setup_completed:
                current_user.setup_completed = True
                db.session.commit()

        return jsonify({"message": "설정이 저장되었습니다."})
    except Exception as e:
        return jsonify({"error": f"설정 저장 중 오류: {str(e)}"}), 500


@app.route("/api/server/users")
@login_required
def api_server_users():
    """중앙 서버의 유저 목록을 프록시합니다."""
    if not _admin_view_active:
        return jsonify({"error": "관리자 권한 필요"}), 403
    try:
        import requests as _req
        resp = _req.get(f"{CENTRAL_SERVER_URL}/api/admin/users",
                       headers={"X-Admin-Key": os.environ.get("ADMIN_SECRET_KEY", "")}, timeout=30)
        return _safe_proxy(resp)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/server/activity-log")
@login_required
def api_server_activity_log():
    """중앙 서버의 활동 로그를 프록시합니다."""
    if not _admin_view_active:
        return jsonify({"error": "관리자 권한 필요"}), 403
    try:
        import requests as _req
        resp = _req.get(f"{CENTRAL_SERVER_URL}/api/admin/activity-log",
                       headers={"X-Admin-Key": os.environ.get("ADMIN_SECRET_KEY", "")}, timeout=30)
        return _safe_proxy(resp)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/server/update-user", methods=["POST"])
@login_required
def api_server_update_user():
    """중앙 서버의 유저 정보를 변경합니다."""
    if not _admin_view_active:
        return jsonify({"error": "관리자 권한 필요"}), 403
    try:
        import requests as _req
        resp = _req.post(f"{CENTRAL_SERVER_URL}/api/admin/update-user",
                        json=request.get_json(),
                        headers={"X-Admin-Key": os.environ.get("ADMIN_SECRET_KEY", "")}, timeout=30)
        return _safe_proxy(resp)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/invite-codes", methods=["GET"])
@login_required
def api_admin_invite_codes():
    """초대 코드 목록 조회."""
    if not _admin_view_active:
        return jsonify({"error": "관리자 권한 필요"}), 403
    return jsonify({
        "fixed_codes": list(_FIXED_INVITE_CODES),
        "onetime_codes": [{"code": k, **v} for k, v in _ONETIME_INVITE_CODES.items()],
        "invite_required": INVITE_REQUIRED,
    })


@app.route("/api/admin/generate-invite", methods=["POST"])
@login_required
def api_admin_generate_invite():
    """1회용 초대 코드 생성."""
    if not _admin_view_active:
        return jsonify({"error": "관리자 권한 필요"}), 403
    data = request.get_json() or {}
    hours = int(data.get("hours", 24))  # 유효 시간 (기본 24시간)

    code = f"CB-{uuid.uuid4().hex[:8].upper()}"
    _ONETIME_INVITE_CODES[code] = {
        "created_at": datetime.utcnow().isoformat(),
        "expires_at": (datetime.utcnow() + timedelta(hours=hours)),
    }
    return jsonify({
        "code": code,
        "expires_in_hours": hours,
        "message": f"초대 코드 생성: {code} ({hours}시간 유효)",
    })


@app.route("/api/server/alerts")
@login_required
def api_server_alerts():
    """중앙 서버의 이상징후 알림을 프록시합니다."""
    if not _admin_view_active:
        return jsonify({"error": "관리자 권한 필요"}), 403
    try:
        import requests as _req
        resp = _req.get(f"{CENTRAL_SERVER_URL}/api/admin/alerts",
                       headers={"X-Admin-Key": os.environ.get("ADMIN_SECRET_KEY", "")}, timeout=30)
        return _safe_proxy(resp)
    except Exception as e:
        return jsonify({"alerts": [], "error": str(e)})


@app.route("/api/server/resolve-alert", methods=["POST"])
@login_required
def api_server_resolve_alert():
    """중앙 서버의 알림을 해결 처리합니다."""
    if not _admin_view_active:
        return jsonify({"error": "관리자 권한 필요"}), 403
    try:
        import requests as _req
        resp = _req.post(f"{CENTRAL_SERVER_URL}/api/admin/resolve-alert",
                        json=request.get_json(),
                        headers={"X-Admin-Key": os.environ.get("ADMIN_SECRET_KEY", "")}, timeout=30)
        return _safe_proxy(resp)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/server/balance-history")
@login_required
def api_server_balance_history():
    """중앙 서버의 유저별 잔액 이력을 프록시합니다."""
    if not _admin_view_active:
        return jsonify({"error": "관리자 권한 필요"}), 403
    try:
        import requests as _req
        email = request.args.get("email", "")
        resp = _req.get(f"{CENTRAL_SERVER_URL}/api/admin/balance-history",
                       headers={"X-Admin-Key": os.environ.get("ADMIN_SECRET_KEY", "")},
                       params={"email": email}, timeout=10)
        return _safe_proxy(resp)
    except Exception as e:
        return jsonify({"snapshots": [], "error": str(e)})


@app.route("/api/server/credit-history", methods=["POST"])
@login_required
def api_server_credit_history():
    """중앙 서버의 원장 이력을 프록시합니다."""
    if not _admin_view_active:
        return jsonify({"error": "관리자 권한 필요"}), 403
    try:
        import requests as _req
        data = request.get_json() or {}
        email = data.get("email", "")
        resp = _req.post(f"{CENTRAL_SERVER_URL}/api/credits/history",
                        json={"email": email, "limit": int(data.get("limit", 1000))}, timeout=10)
        return _safe_proxy(resp)
    except Exception as e:
        return jsonify({"history": [], "error": str(e)})


@app.route("/api/server/token-history", methods=["POST"])
@login_required
def api_server_token_history():
    """중앙 서버의 토큰 원장 이력을 프록시합니다."""
    if not _admin_view_active:
        return jsonify({"error": "관리자 권한 필요"}), 403
    try:
        import requests as _req
        data = request.get_json() or {}
        email = data.get("email", "")
        resp = _req.post(f"{CENTRAL_SERVER_URL}/api/tokens/history",
                        json={"email": email, "limit": int(data.get("limit", 1000))}, timeout=10)
        return _safe_proxy(resp)
    except Exception as e:
        return jsonify({"history": [], "error": str(e)})


@app.route("/api/server/credit-balance", methods=["POST"])
@login_required
def api_server_credit_balance():
    """중앙 서버의 크레딧 원장 잔액을 프록시합니다."""
    if not _admin_view_active:
        return jsonify({"error": "관리자 권한 필요"}), 403
    try:
        import requests as _req
        data = request.get_json() or {}
        resp = _req.post(f"{CENTRAL_SERVER_URL}/api/credits/balance",
                        json={"email": data.get("email", "")}, timeout=10)
        return _safe_proxy(resp)
    except Exception as e:
        return jsonify({"balance": 0, "error": str(e)})


@app.route("/api/server/token-balance", methods=["POST"])
@login_required
def api_server_token_balance():
    """중앙 서버의 토큰 원장 잔액을 프록시합니다."""
    if not _admin_view_active:
        return jsonify({"error": "관리자 권한 필요"}), 403
    try:
        import requests as _req
        data = request.get_json() or {}
        resp = _req.post(f"{CENTRAL_SERVER_URL}/api/tokens/balance",
                        json={"email": data.get("email", "")}, timeout=10)
        return _safe_proxy(resp)
    except Exception as e:
        return jsonify({"balance": 0, "error": str(e)})


@app.route("/api/server/delete-user", methods=["POST"])
@login_required
def api_server_delete_user():
    """중앙 서버에서 유저를 완전 삭제합니다."""
    if not _admin_view_active:
        return jsonify({"error": "관리자 권한 필요"}), 403
    try:
        import requests as _req
        resp = _req.post(f"{CENTRAL_SERVER_URL}/api/admin/delete-user",
                        json=request.get_json(),
                        headers={"X-Admin-Key": os.environ.get("ADMIN_SECRET_KEY", "")}, timeout=30)
        return _safe_proxy(resp)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/check-requirements")
@login_required
def api_check_requirements():
    """필수 프로그램 설치 상태를 확인합니다."""
    results = {}

    # 1. Playwright Chromium
    import glob
    chromium_paths = []
    for base in [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright"),
        os.path.join(os.path.expanduser("~"), "AppData", "Local", "ms-playwright"),
    ]:
        pattern = os.path.join(base, "chromium-*", "chrome-win", "chrome.exe")
        chromium_paths.extend(glob.glob(pattern))

    if chromium_paths:
        results["chromium"] = {"ok": True, "message": "Chromium 설치됨"}
    else:
        results["chromium"] = {"ok": False, "message": "Chromium 미설치 — 자동 설치가 필요합니다"}

    return jsonify(results)


@app.route("/api/install-chromium", methods=["POST"])
@login_required
def api_install_chromium():
    """Playwright Chromium을 자동 설치합니다."""
    from src.youtube_bot import YouTubeBot
    ready, msg = YouTubeBot.check_chromium_ready()
    if ready:
        return jsonify({"success": True, "message": msg})
    else:
        return jsonify({"success": False, "error": msg}), 500


@app.route("/api/check-connections", methods=["GET"])
@login_required
def api_check_connections():
    """모든 API 연동 상태를 확인합니다."""
    results = {}

    # 1. .env 파일 존재 여부 (여러 경로 확인) — 유저 DB 설정이 있으면 .env 없어도 OK
    env_candidates = [
        os.path.join(os.path.dirname(__file__), ".env"),
        os.path.join(os.getcwd(), ".env"),
    ]
    env_exists = any(os.path.exists(p) for p in env_candidates)
    # 유저 DB에 핵심 설정이 있으면 .env 없어도 정상
    has_user_settings = bool(
        _get_user_setting(current_user.id, "NOTION_API_TOKEN")
        and _get_user_setting(current_user.id, "NOTION_DATABASE_ID")
    )
    if env_exists:
        results["env_file"] = {"ok": True, "message": ".env 파일 존재"}
    elif has_user_settings:
        results["env_file"] = {"ok": True, "message": ".env 파일 없음 (유저 DB 설정 사용 중)"}
    else:
        results["env_file"] = {"ok": False, "message": ".env 파일이 없습니다. .env.example을 복사하거나 설정 페이지에서 API 키를 입력하세요."}

    # 2. Notion API 연결 테스트 (유저별 DB 설정 우선)
    notion_token = _get_user_setting(current_user.id, "NOTION_API_TOKEN") or os.getenv("NOTION_API_TOKEN", "")
    notion_db_id = _get_user_setting(current_user.id, "NOTION_DATABASE_ID") or os.getenv("NOTION_DATABASE_ID", "")
    if not notion_token or not notion_db_id:
        results["notion"] = {"ok": False, "message": "NOTION_API_TOKEN 또는 NOTION_DATABASE_ID 미설정"}
    else:
        try:
            # DB 스키마 조회만으로 연결 확인 (전체 작업 조회는 너무 오래 걸림)
            from notion_client import Client as NotionRawClient
            nc = NotionRawClient(auth=notion_token)
            db_info = nc.databases.retrieve(database_id=notion_db_id)
            db_title = ""
            for t in db_info.get("title", []):
                db_title += t.get("plain_text", "")
            prop_count = len(db_info.get("properties", {}))
            results["notion"] = {"ok": True, "message": f"연결 성공! DB: {db_title or '(제목없음)'} ({prop_count}개 컬럼)"}
        except Exception as e:
            err_msg = str(e)
            if "Could not find" in err_msg or "404" in err_msg or "not_found" in err_msg:
                results["notion"] = {"ok": False, "message": "데이터베이스를 찾을 수 없습니다. 노션에서 해당 DB 페이지 ⋯ → 연결 → 생성한 Integration을 추가해주세요."}
            elif "403" in err_msg or "Forbidden" in err_msg:
                results["notion"] = {"ok": False, "message": "DB 접근 권한 없음 (노션 DB 페이지 ⋯ → 연결 → Integration 추가 필요)"}
            elif "401" in err_msg or "Unauthorized" in err_msg:
                results["notion"] = {"ok": False, "message": "API 토큰이 유효하지 않습니다. 토큰을 다시 확인해주세요."}
            elif "Invalid" in err_msg and "id" in err_msg.lower():
                results["notion"] = {"ok": False, "message": "데이터베이스 ID 형식이 올바르지 않습니다. 32자리 영숫자를 입력해주세요."}
            else:
                results["notion"] = {"ok": False, "message": f"연결 실패: {err_msg[:200]}"}

    # 3. SMM Kings API 연결 테스트 (관리자 전용 — 일반 유저에겐 숨김)
    if _admin_view_active or license_client.owner_mode:
        smm_enabled = os.getenv("SMM_ENABLED", "false").lower() == "true"
        smm_api_key = os.getenv("SMM_API_KEY", "")
        if not smm_enabled:
            results["smm"] = {"ok": None, "message": "SMM 비활성 (SMM_ENABLED=false)"}
        elif not smm_api_key:
            results["smm"] = {"ok": False, "message": "SMM_API_KEY 미설정"}
        else:
            try:
                smm = SMMClient()
                balance = smm.get_balance()
                if balance is not None:
                    service_id = os.getenv("SMM_LIKE_SERVICE_ID", "")
                    msg = f"연결 성공! 잔액: ${balance:.2f}"
                    if not service_id:
                        msg += " (SMM_LIKE_SERVICE_ID 미설정 - 서비스 조회 필요)"
                    results["smm"] = {"ok": True, "message": msg}
                else:
                    results["smm"] = {"ok": False, "message": "API 응답 없음 (API 키 확인 필요)"}
            except Exception as e:
                results["smm"] = {"ok": False, "message": f"연결 실패: {str(e)}"}

    # 4. 계정 파일 확인
    accounts = load_accounts()
    if accounts:
        results["accounts"] = {"ok": True, "message": f"계정 {len(accounts)}개 로드됨"}
    else:
        results["accounts"] = {"ok": False, "message": "등록된 계정이 없습니다"}

    # 5. ADB IP 변경 상태
    adb_enabled = os.getenv("ADB_IP_CHANGE_ENABLED", "false").lower() == "true"
    if not adb_enabled:
        results["adb"] = {"ok": None, "message": "ADB IP 변경 비활성 (ADB_IP_CHANGE_ENABLED=false)"}
    else:
        try:
            adb_changer = ADBIPChanger()
            connected, info = adb_changer.check_device()
            if connected:
                ip = adb_changer.get_current_ip()
                msg = f"디바이스 연결됨: {info}"
                if ip:
                    msg += f" (IP: {ip})"
                results["adb"] = {"ok": True, "message": msg}
            else:
                results["adb"] = {"ok": False, "message": info}
        except Exception as e:
            results["adb"] = {"ok": False, "message": f"ADB 오류: {str(e)}"}

    return jsonify(results)


@app.route("/api/adb/install-bat")
@login_required
def api_adb_install_bat():
    """ADB 자동 설치 bat 파일을 다운로드합니다."""
    bat_path = os.path.join(os.path.dirname(__file__), "install_adb.bat")
    return send_file(bat_path, as_attachment=True, download_name="install_adb.bat")


# ADB 설치 상태 관리
_adb_install_state = {
    "active": False,
    "step": "",      # downloading, extracting, verifying, done, error
    "message": "",
    "progress": 0,   # 0~100
    "adb_path": "",
}


@app.route("/api/adb/run-install", methods=["POST"])
@login_required
def api_adb_run_install():
    """ADB Platform Tools를 백그라운드에서 설치합니다 (CMD 창 없이)."""
    import platform

    if platform.system() != "Windows":
        return jsonify({"error": "ADB 자동 설치는 Windows에서만 지원됩니다."}), 400

    if _adb_install_state["active"]:
        return jsonify({"error": "이미 설치가 진행 중입니다."}), 409

    data = request.get_json() or {}
    force = data.get("force", False)

    # 설치 경로 결정
    if os.path.exists("D:\\"):
        install_dir = "D:\\platform-tools"
    else:
        install_dir = "C:\\platform-tools"
    adb_path = os.path.join(install_dir, "adb.exe")

    # 이미 설치되어 있으면 확인
    if os.path.exists(adb_path) and not force:
        return jsonify({
            "already_installed": True,
            "adb_path": adb_path,
            "message": f"ADB가 이미 설치되어 있습니다: {adb_path}",
        })

    def _do_install():
        import urllib.request
        import zipfile
        import shutil
        import tempfile

        _adb_install_state["active"] = True
        _adb_install_state["adb_path"] = adb_path

        try:
            download_url = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
            zip_path = os.path.join(tempfile.gettempdir(), "platform-tools.zip")

            # 1단계: 다운로드
            _adb_install_state["step"] = "downloading"
            _adb_install_state["message"] = "Android Platform Tools 다운로드 중..."
            _adb_install_state["progress"] = 10
            print(f"[ADB설치] 다운로드 시작: {download_url}")

            urllib.request.urlretrieve(download_url, zip_path)

            # 파일 크기 확인
            file_size = os.path.getsize(zip_path)
            if file_size < 1000000:
                raise RuntimeError(f"다운로드 파일이 너무 작습니다 ({file_size} bytes)")

            _adb_install_state["progress"] = 50
            _adb_install_state["message"] = f"다운로드 완료 ({file_size // 1024 // 1024}MB)"
            print(f"[ADB설치] 다운로드 완료: {file_size} bytes")

            # 2단계: 압축 해제
            _adb_install_state["step"] = "extracting"
            _adb_install_state["message"] = f"압축 해제 중... ({install_dir})"
            _adb_install_state["progress"] = 60

            # 기존 폴더 삭제
            if os.path.exists(install_dir):
                shutil.rmtree(install_dir, ignore_errors=True)

            extract_dir = os.path.join(tempfile.gettempdir(), "adb_extract")
            if os.path.exists(extract_dir):
                shutil.rmtree(extract_dir, ignore_errors=True)

            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)

            # platform-tools 폴더 이동
            extracted = os.path.join(extract_dir, "platform-tools")
            if os.path.exists(extracted):
                shutil.move(extracted, install_dir)
            else:
                shutil.move(extract_dir, install_dir)

            _adb_install_state["progress"] = 85

            # 임시 파일 정리
            try:
                os.remove(zip_path)
                shutil.rmtree(extract_dir, ignore_errors=True)
            except Exception:
                pass

            # 3단계: 설치 확인
            _adb_install_state["step"] = "verifying"
            _adb_install_state["message"] = "설치 확인 중..."
            _adb_install_state["progress"] = 90

            if not os.path.exists(adb_path):
                raise RuntimeError("설치 후 adb.exe를 찾을 수 없습니다")

            _adb_install_state["step"] = "done"
            _adb_install_state["message"] = f"설치 완료! ADB 경로: {adb_path}"
            _adb_install_state["progress"] = 100
            print(f"[ADB설치] 설치 완료: {adb_path}")

        except Exception as e:
            print(f"[ADB설치] 오류: {e}")
            _adb_install_state["step"] = "error"
            _adb_install_state["message"] = f"설치 실패: {str(e)}"
            _adb_install_state["progress"] = 0
        finally:
            _adb_install_state["active"] = False

    thread = threading.Thread(target=_do_install, daemon=True)
    thread.start()

    return jsonify({
        "message": "ADB 설치가 백그라운드에서 시작되었습니다.",
        "adb_path": adb_path,
    })


@app.route("/api/adb/install-status")
@login_required
def api_adb_install_status():
    """ADB 설치 진행 상태를 반환합니다."""
    return jsonify(_adb_install_state)


@app.route("/api/adb/device-status")
@login_required
def api_adb_device_status():
    """ADB 연결 상태 및 디바이스 확인 (IP 분리 체크용)."""
    changer = ADBIPChanger()
    connected, info = changer.check_device()
    enabled = changer.enabled
    return jsonify({
        "connected": connected,
        "info": info,
        "adb_enabled": enabled,
        "ok": connected and enabled,
    })


@app.route("/api/adb/test", methods=["POST"])
def api_adb_test():
    """ADB 연결을 단계별로 테스트합니다 (활성 여부 무관)."""
    steps = []

    # UI에서 전달된 ADB 경로 사용 (저장 전이라도 테스트 가능)
    data = request.get_json() or {}
    adb_path = data.get("adb_path", os.getenv("ADB_PATH", "adb"))

    changer = ADBIPChanger()
    changer.adb_path = adb_path
    changer.enabled = True  # 테스트는 항상 활성 상태로

    # 1단계: ADB 실행 가능 여부
    import subprocess
    _si = None
    _cf = 0
    if sys.platform == "win32":
        _si = subprocess.STARTUPINFO()
        _si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        _si.wShowWindow = 0
        _cf = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            [adb_path, "version"], capture_output=True, text=True, timeout=5,
            creationflags=_cf, startupinfo=_si
        )
        if result.returncode == 0:
            version = result.stdout.strip().split("\n")[0]
            steps.append({"step": "ADB 실행 확인", "ok": True, "message": version})
        else:
            steps.append({"step": "ADB 실행 확인", "ok": False, "message": "ADB 실행 실패"})
            return jsonify({"steps": steps})
    except FileNotFoundError:
        hint = "ADB 경로를 전체 경로로 입력하세요 (예: D:\\platform-tools\\adb.exe)"
        steps.append({"step": "ADB 실행 확인", "ok": False, "message": f"ADB를 찾을 수 없습니다: {adb_path} → {hint}"})
        return jsonify({"steps": steps})
    except Exception as e:
        steps.append({"step": "ADB 실행 확인", "ok": False, "message": str(e)})
        return jsonify({"steps": steps})

    # 2단계: 디바이스 연결 확인 (ADB 서버 자동 시작 포함)
    # ADB 서버가 꺼져있으면 먼저 시작
    subprocess.run([adb_path, "start-server"], capture_output=True, timeout=10, creationflags=_cf, startupinfo=_si)

    connected, info = changer.check_device()

    # 실패 시 ADB 서버 재시작 후 재시도
    if not connected:
        subprocess.run([adb_path, "kill-server"], capture_output=True, timeout=5, creationflags=_cf, startupinfo=_si)
        time.sleep(1)
        subprocess.run([adb_path, "start-server"], capture_output=True, timeout=10, creationflags=_cf, startupinfo=_si)
        time.sleep(2)
        connected, info = changer.check_device()

    if connected:
        steps.append({"step": "디바이스 연결", "ok": True, "message": f"연결됨: {info}"})
    else:
        hint = "연결 안 됨 → ① 폰 설정 > 개발자 옵션 > USB 디버깅 ON ② USB 연결 후 폰에서 'USB 디버깅 허용' 팝업 승인 ③ USB 모드를 '파일 전송(MTP)'으로 변경"
        steps.append({"step": "디바이스 연결", "ok": False, "message": hint})
        return jsonify({"steps": steps})

    # 3단계: 현재 IP 확인
    ip = changer.get_current_ip()
    if ip:
        steps.append({"step": "현재 IP 확인", "ok": True, "message": f"IP: {ip}"})
    else:
        steps.append({"step": "현재 IP 확인", "ok": None, "message": "IP 확인 불가 (비행기모드 토글은 가능)"})

    # 4단계: 비행기모드 제어 확인 (cmd connectivity 방식)
    output, code = changer._run_adb("shell", "cmd connectivity airplane-mode")
    if code == 0:
        mode = output.strip()
        steps.append({"step": "비행기모드 제어", "ok": True, "message": f"cmd connectivity 지원됨 (현재: {mode})"})
    else:
        # fallback: settings 방식 확인
        output2, code2 = changer._run_adb("shell", "settings get global airplane_mode_on")
        if code2 == 0:
            steps.append({"step": "비행기모드 제어", "ok": True, "message": f"settings 방식 사용 가능"})
        else:
            steps.append({"step": "비행기모드 제어", "ok": False, "message": "비행기모드 제어 접근 실패"})

    # 5단계: 실제 IP 변경 테스트 (단계별)
    if data.get("test_ip_change"):
        # 유선 인터넷 먼저 비활성화 (이게 안 되면 IP 변경 무의미)
        changer.auto_ethernet = True
        eth_ok, eth_msg = changer.disable_ethernet()
        if eth_ok:
            steps.append({"step": "유선 인터넷 비활성화", "ok": True, "message": eth_msg})
        else:
            steps.append({"step": "유선 인터넷 비활성화", "ok": False,
                          "message": f"{eth_msg} — 유선이 켜져있으면 IP 변경이 불가합니다. 프로그램을 관리자 권한으로 실행하세요."})

        time.sleep(2)
        old_ip = changer.get_current_ip()
        steps.append({"step": "현재 IP 기록", "ok": True, "message": f"변경 전 IP: {old_ip or '확인불가'}"})

        # 5-1: 비행기모드 ON 시도
        airplane_before = changer._check_airplane_status()
        steps.append({"step": "비행기모드 상태 (전)", "ok": True,
                       "message": f"{'ON' if airplane_before else 'OFF' if airplane_before is not None else '확인불가'}"})

        # cmd connectivity 방식
        out1, code1 = changer._run_adb("shell", "cmd connectivity airplane-mode enable")
        time.sleep(1)
        airplane_after_cmd = changer._check_airplane_status()

        if airplane_after_cmd:
            steps.append({"step": "비행기모드 ON (cmd connectivity)", "ok": True, "message": "성공 — 비행기모드 ON 확인됨"})
        else:
            steps.append({"step": "비행기모드 ON (cmd connectivity)", "ok": False, "message": f"실패 (상태: {airplane_after_cmd})"})
            # settings put 방식 시도
            changer._run_adb("shell", "settings", "put", "global", "airplane_mode_on", "1")
            changer._run_adb("shell", "am", "broadcast", "-a", "android.intent.action.AIRPLANE_MODE", "--ez", "state", "true")
            time.sleep(1)
            airplane_after_settings = changer._check_airplane_status()
            if airplane_after_settings:
                steps.append({"step": "비행기모드 ON (settings put)", "ok": True, "message": "성공 — 비행기모드 ON 확인됨"})
            else:
                steps.append({"step": "비행기모드 ON (settings put)", "ok": False, "message": "실패 — 비행기모드가 켜지지 않음"})

        # 5-2: 대기
        wait_sec = changer.airplane_wait
        time.sleep(wait_sec)
        steps.append({"step": f"LTE 재할당 대기 ({wait_sec}초)", "ok": True, "message": "대기 완료"})

        # 5-3: 비행기모드 OFF
        changer._run_adb("shell", "cmd connectivity airplane-mode disable")
        time.sleep(1)
        airplane_off = changer._check_airplane_status()
        if not airplane_off and airplane_off is not None:
            steps.append({"step": "비행기모드 OFF", "ok": True, "message": "성공 — 비행기모드 OFF 확인됨"})
        else:
            changer._run_adb("shell", "settings", "put", "global", "airplane_mode_on", "0")
            changer._run_adb("shell", "am", "broadcast", "-a", "android.intent.action.AIRPLANE_MODE", "--ez", "state", "false")
            steps.append({"step": "비행기모드 OFF (settings put)", "ok": True, "message": "OFF 명령 전송됨"})

        # 5-4: 네트워크 복구 대기
        new_ip = changer._wait_for_network(max_wait=15)
        steps.append({"step": "네트워크 복구", "ok": bool(new_ip), "message": f"IP: {new_ip}" if new_ip else "네트워크 복구 실패"})

        # 5-5: IP 비교
        if old_ip and new_ip and old_ip != new_ip:
            steps.append({"step": "IP 변경 결과", "ok": True, "message": f"성공! {old_ip} → {new_ip}"})
        elif old_ip and new_ip and old_ip == new_ip:
            steps.append({"step": "IP 변경 결과", "ok": False,
                          "message": f"IP 미변경 ({new_ip}). 유선 인터넷이 연결되어 있으면 수동으로 끄고 재시도하세요."})
        elif new_ip:
            steps.append({"step": "IP 변경 결과", "ok": None, "message": f"이전 IP 확인 불가. 현재 IP: {new_ip}"})
        else:
            steps.append({"step": "IP 변경 결과", "ok": False, "message": "네트워크 복구 실패"})

        # 유선 인터넷 복원
        changer.enable_ethernet()
        steps.append({"step": "유선 인터넷 복원", "ok": True, "message": "유선 복원 완료"})

    return jsonify({"steps": steps})


@app.route("/api/accounts", methods=["GET"])
@login_required
def api_get_accounts():
    """계정 목록을 반환합니다 (유저별 DB만 — 다른 유저의 계정은 절대 노출 안 됨)."""
    db_accounts = YouTubeAccount.query.filter_by(user_id=current_user.id).all()
    safe_accounts = [acc.to_dict() for acc in db_accounts]
    return jsonify({"accounts": safe_accounts})


# ── 계정 유형 관리 ──────────────────────────────────────────────

_DEFAULT_ACCOUNT_TYPES = [
    {"name": "찐계정",  "color": "#6495ed"},
    {"name": "부계정",  "color": "#f39c12"},
    {"name": "해외계정", "color": "#2ed573"},
]

def _ensure_default_account_types(user_id):
    """유저에게 기본 유형이 없으면 생성."""
    if AccountType.query.filter_by(user_id=user_id).count() == 0:
        for t in _DEFAULT_ACCOUNT_TYPES:
            db.session.add(AccountType(user_id=user_id, name=t["name"], color=t["color"]))
        db.session.commit()


@app.route("/api/account-types", methods=["GET"])
@login_required
def api_get_account_types():
    """유저별 계정 유형 목록 조회."""
    _ensure_default_account_types(current_user.id)
    types = AccountType.query.filter_by(user_id=current_user.id).order_by(AccountType.id).all()
    return jsonify({"types": [t.to_dict() for t in types]})


@app.route("/api/account-types", methods=["POST"])
@login_required
def api_add_account_type():
    """계정 유형 추가."""
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    color = data.get("color", "#6c757d")
    if not name:
        return jsonify({"error": "유형 이름을 입력하세요."}), 400
    if len(name) > 20:
        return jsonify({"error": "유형 이름은 20자 이내로 입력하세요."}), 400
    if AccountType.query.filter_by(user_id=current_user.id, name=name).first():
        return jsonify({"error": f"'{name}' 유형이 이미 존재합니다."}), 409
    t = AccountType(user_id=current_user.id, name=name, color=color)
    db.session.add(t)
    db.session.commit()
    return jsonify({"message": f"'{name}' 유형이 추가되었습니다.", "type": t.to_dict()})


@app.route("/api/account-types/<int:type_id>", methods=["DELETE"])
@login_required
def api_delete_account_type(type_id):
    """계정 유형 삭제. 해당 유형을 사용 중인 계정은 '기타'로 변경."""
    t = AccountType.query.filter_by(id=type_id, user_id=current_user.id).first()
    if not t:
        return jsonify({"error": "유형을 찾을 수 없습니다."}), 404
    type_name = t.name
    # 해당 유형 사용 계정 → '기타'로 변경
    YouTubeAccount.query.filter_by(user_id=current_user.id, account_type=type_name).update({"account_type": "기타"})
    db.session.delete(t)
    db.session.commit()
    return jsonify({"message": f"'{type_name}' 유형이 삭제되었습니다."})


@app.route("/api/accounts/<int:account_id>/type", methods=["PATCH"])
@login_required
def api_update_account_type(account_id):
    """계정 유형 변경."""
    data = request.get_json() or {}
    new_type = (data.get("account_type") or "").strip()
    if not new_type:
        return jsonify({"error": "유형을 선택하세요."}), 400
    acc = YouTubeAccount.query.filter_by(id=account_id, user_id=current_user.id).first()
    if not acc:
        return jsonify({"error": "계정을 찾을 수 없습니다."}), 404
    acc.account_type = new_type
    db.session.commit()
    return jsonify({"message": "유형이 변경되었습니다.", "account_type": new_type})


@app.route("/api/accounts", methods=["POST"])
@login_required
def api_add_account():
    """계정을 추가합니다 (유저별 DB)."""
    try:
        data = request.get_json()
        if not data or not data.get("email") or not data.get("password"):
            return jsonify({"error": "이메일과 비밀번호는 필수입니다."}), 400

        email = data["email"]

        # 플랜별 최대 계정 수 제한
        existing_count = YouTubeAccount.query.filter_by(user_id=current_user.id).count()
        plan = getattr(license_client, 'license_info', {}) or {}
        plan_key = plan.get('plan_name', 'free')
        max_accounts = PLAN_MAX_ACCOUNTS.get(plan_key, 1)
        if existing_count >= max_accounts:
            return jsonify({"error": f"현재 플랜({plan_key})의 최대 계정 수({max_accounts}개)에 도달했습니다."}), 403

        # DB에서 중복 확인
        existing = YouTubeAccount.query.filter_by(
            user_id=current_user.id, account_email=email
        ).first()
        if existing:
            return jsonify({"error": "이미 등록된 이메일입니다."}), 409

        new_acc = YouTubeAccount(
            user_id=current_user.id,
            account_email=email,
            account_password=data["password"],
            account_type=data.get("account_type", "부계정"),
            label=data.get("label", email.split("@")[0]),
        )
        db.session.add(new_acc)
        db.session.commit()

        # 레거시 파일에도 동기화 (자동화 엔진 호환)
        _sync_accounts_to_file(current_user.id)

        total = YouTubeAccount.query.filter_by(user_id=current_user.id).count()

        # 활동 로그에 기록
        log = UserActivityLog(
            user_id=current_user.id,
            email=current_user.email,
            action="account_add",
            detail=f"계정 추가: {email} (총 {total}개)",
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr),
        )
        db.session.add(log)
        db.session.commit()

        return jsonify({"message": "계정이 추가되었습니다.", "account": new_acc.to_dict(), "total": total})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"계정 추가 중 오류: {str(e)}"}), 500


@app.route("/api/accounts/<email>", methods=["DELETE"])
@login_required
def api_delete_account(email):
    """계정을 삭제합니다 (유저별 DB)."""
    try:
        acc = YouTubeAccount.query.filter_by(
            user_id=current_user.id, account_email=email
        ).first()
        if not acc:
            return jsonify({"error": "해당 계정을 찾을 수 없습니다."}), 404

        db.session.delete(acc)
        db.session.commit()

        # 레거시 파일에도 동기화
        _sync_accounts_to_file(current_user.id)

        remaining = YouTubeAccount.query.filter_by(user_id=current_user.id).count()

        # 활동 로그에 기록
        log = UserActivityLog(
            user_id=current_user.id,
            email=current_user.email,
            action="account_delete",
            detail=f"계정 삭제: {email} (남은 {remaining}개)",
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr),
        )
        db.session.add(log)
        db.session.commit()

        return jsonify({"message": f"계정 {email}이 삭제되었습니다.", "remaining": remaining})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"계정 삭제 중 오류: {str(e)}"}), 500


@app.route("/api/accounts/<int:account_id>/keywords", methods=["GET", "POST"])
@login_required
def api_account_keywords(account_id):
    """계정별 관심사 키워드 조회/저장."""
    acc = YouTubeAccount.query.filter_by(id=account_id, user_id=current_user.id).first()
    if not acc:
        return jsonify({"error": "계정을 찾을 수 없습니다."}), 404
    if request.method == "GET":
        return jsonify({"keywords": acc.get_interest_keywords()})
    # POST — 극한 방어 모드: Content-Type 무관하게 모든 방법으로 JSON 파싱 시도
    import json as _json_kw
    data = None
    # 방법 1: Flask get_json (force=True, silent=True)
    try:
        data = request.get_json(force=True, silent=True)
    except Exception:
        data = None
    # 방법 2: 직접 raw body 파싱
    if data is None:
        try:
            raw = request.get_data(as_text=True) or ""
            if raw.strip():
                data = _json_kw.loads(raw)
        except Exception:
            data = None
    # 방법 3: form / query string 파싱
    if data is None:
        try:
            if request.form:
                kw_form = request.form.get("keywords")
                if kw_form:
                    try:
                        data = {"keywords": _json_kw.loads(kw_form)}
                    except Exception:
                        data = {"keywords": [k.strip() for k in kw_form.split(",") if k.strip()]}
        except Exception:
            pass
    if not isinstance(data, dict):
        data = {}
    keywords = data.get("keywords", [])
    if not isinstance(keywords, list):
        return jsonify({"error": "keywords는 배열이어야 합니다.", "got": type(keywords).__name__}), 400
    try:
        acc.set_interest_keywords(keywords)
        db.session.commit()
    except Exception as _e:
        db.session.rollback()
        return jsonify({"error": f"DB 저장 실패: {_e}"}), 500
    return jsonify({"keywords": acc.get_interest_keywords()})


@app.route("/api/accounts/bulk-keywords", methods=["POST"])
@login_required
def api_bulk_keywords():
    """여러 계정에 동일 키워드 일괄 적용."""
    data = request.get_json(force=True, silent=True) or {}
    account_ids = data.get("account_ids", [])
    keywords = data.get("keywords", [])
    if not account_ids or not isinstance(keywords, list):
        return jsonify({"error": "account_ids와 keywords(배열)가 필요합니다."}), 400
    updated = 0
    for aid in account_ids:
        acc = YouTubeAccount.query.filter_by(id=aid, user_id=current_user.id).first()
        if acc:
            acc.set_interest_keywords(keywords)
            updated += 1
    db.session.commit()
    return jsonify({"updated": updated})


@app.route("/api/keywords/suggest", methods=["GET"])
@login_required
def api_keywords_suggest():
    """YouTube Suggest API 기반 연관 키워드 추천 (무료 공개 엔드포인트)."""
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"suggestions": []})
    try:
        import requests as _rq
        r = _rq.get(
            "https://suggestqueries.google.com/complete/search",
            params={"client": "youtube", "ds": "yt", "q": q, "hl": "ko", "gl": "kr"},
            timeout=3,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        # 응답이 JSON 배열 형태: ["query", ["sugg1","sugg2",...], ...]
        import json as _json
        text = r.text.strip()
        # 응답이 JSONP 형식: window.google.ac.h([...])
        # 괄호 안의 JSON 배열만 추출
        if "(" in text and ")" in text:
            text = text[text.find("(") + 1:text.rfind(")")]
        data = _json.loads(text)
        suggestions = []
        if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], list):
            for item in data[1]:
                if isinstance(item, list) and item:
                    suggestions.append(str(item[0]))
                elif isinstance(item, str):
                    suggestions.append(item)
        return jsonify({"suggestions": suggestions[:10]})
    except Exception as e:
        return jsonify({"suggestions": [], "error": str(e)})


@app.route("/api/accounts/test-login", methods=["POST"])
@login_required
def api_test_login():
    """계정의 YouTube 로그인을 테스트합니다."""
    import time as _time
    from src.youtube_bot import YouTubeBot

    data = request.get_json()
    if not data or not data.get("email"):
        return jsonify({"error": "이메일이 필요합니다."}), 400

    email = data["email"]
    account = _find_account(current_user.id, email)

    if not account:
        return jsonify({"error": "해당 계정을 찾을 수 없습니다."}), 404

    start_time = _time.time()
    bot = YouTubeBot(user_id=current_user.id if current_user.is_authenticated else None)
    bot.headless = True

    try:
        bot.start_browser()
        login_ok = bot.login_youtube(account["email"], account["password"])
        elapsed = round(_time.time() - start_time, 1)

        if login_ok:
            return jsonify({"success": True, "message": f"로그인 성공 ({elapsed}초)", "elapsed": elapsed})
        else:
            return jsonify({"success": False, "message": f"로그인 실패 ({elapsed}초)", "elapsed": elapsed})
    except Exception as e:
        elapsed = round(_time.time() - start_time, 1)
        return jsonify({"success": False, "message": f"오류: {str(e)} ({elapsed}초)", "elapsed": elapsed})
    finally:
        bot.close_browser()


# 수동 로그인 상태 관리
manual_login_state = {
    "active": False,
    "email": None,
    "status": "idle",  # idle, waiting, success, failed
    "message": "",
}
manual_login_bot = None


@app.route("/api/accounts/manual-login", methods=["POST"])
@login_required
def api_manual_login():
    """수동 로그인 - 브라우저를 열어 사용자가 직접 로그인합니다."""
    global manual_login_bot

    # Playwright 사용 가능 여부 먼저 확인
    try:
        from src.youtube_bot import YouTubeBot
    except ImportError as e:
        return jsonify({"error": f"브라우저 모듈 로드 실패: {str(e)}. playwright install 실행 필요"}), 500

    data = request.get_json()
    if not data or not data.get("email"):
        return jsonify({"error": "이메일이 필요합니다."}), 400

    # 이전 세션이 남아있으면 강제 정리
    if manual_login_state["active"]:
        print("[manual_login] 이전 세션 강제 정리")
        try:
            if manual_login_bot:
                manual_login_bot.close_browser()
        except Exception:
            pass
        manual_login_bot = None
        manual_login_state["active"] = False
        manual_login_state["status"] = "idle"

    email = data["email"]
    account = _find_account(current_user.id, email)

    if not account:
        return jsonify({"error": "해당 계정을 찾을 수 없습니다."}), 404

    manual_login_state["active"] = True
    manual_login_state["email"] = email
    manual_login_state["user_id"] = current_user.id
    manual_login_state["status"] = "waiting"
    manual_login_state["message"] = "브라우저 시작 중..."

    _login_user_id = current_user.id  # 스레드 진입 전에 캡처

    def _do_manual_login():
        global manual_login_bot
        with app.app_context():
            try:
                label = account.get("label", email.split("@")[0])
                bot = YouTubeBot(account_label=label, user_id=_login_user_id)
                bot.headless = False  # 화면 보이기 필수
                manual_login_bot = bot

                manual_login_state["message"] = "브라우저를 여는 중..."
                print(f"[manual_login] start_browser() 호출 시작...")

                try:
                    bot.start_browser()
                except Exception as browser_err:
                    err_msg = str(browser_err)
                    print(f"[manual_login] start_browser() 실패: {err_msg}")
                    if "executable" in err_msg.lower() or "browser" in err_msg.lower() or "chromium" in err_msg.lower():
                        manual_login_state["status"] = "failed"
                        manual_login_state["message"] = f"Chromium 설치 중... 잠시만 기다려주세요."
                    else:
                        manual_login_state["status"] = "failed"
                        manual_login_state["message"] = f"브라우저 실행 실패: {err_msg}"
                    return

                print(f"[manual_login] start_browser() 성공, page={bot.page is not None}")

                if not bot.page:
                    manual_login_state["status"] = "failed"
                    manual_login_state["message"] = "브라우저 페이지 생성 실패"
                    return

                # 브라우저 창을 최상위로 올리기 (PyWebView 뒤에 숨는 문제 방지)
                try:
                    bot.page.bring_to_front()
                except Exception:
                    pass

                manual_login_state["message"] = "브라우저가 열렸습니다. 로그인을 완료해주세요."
                print(f"[manual_login] 브라우저 시작됨, email={email}")
                password = account.get("password", "")
                login_ok = bot.manual_login(email=email, password=password, timeout=300)
                _yt_result = getattr(bot, 'yt_login_result', 'success')
                print(f"[manual_login] 결과: {login_ok}, yt_login_result={_yt_result}")

                if login_ok and _yt_result == "yt_banned":
                    manual_login_state["status"] = "terminated"
                    manual_login_state["message"] = "유튜브 로그인 버튼 감지 — 채널이 정지된 계정입니다."
                    try:
                        _acc_db = YouTubeAccount.query.filter_by(
                            user_id=manual_login_state.get("user_id", 0),
                            account_email=email
                        ).first()
                        if _acc_db:
                            _acc_db.channel_terminated = True
                            db.session.commit()
                        add_log(f"[계정정지] {email} — 자동로그인 중 유튜브 로그인 버튼 감지 → channel_terminated 설정", "error")
                    except Exception as _e:
                        print(f"[manual_login] channel_terminated DB 저장 오류: {_e}")
                elif login_ok:
                    manual_login_state["status"] = "success"
                    manual_login_state["message"] = "로그인 성공! 쿠키가 저장되었습니다."
                else:
                    manual_login_state["status"] = "failed"
                    manual_login_state["message"] = "로그인 시간 초과 또는 실패"
            except Exception as e:
                import traceback
                print(f"[manual_login] 오류: {e}")
                traceback.print_exc()
                manual_login_state["status"] = "failed"
                manual_login_state["message"] = f"오류: {str(e)}"
            finally:
                try:
                    if manual_login_bot:
                        manual_login_bot.close_browser()
                        manual_login_bot = None
                except Exception:
                    pass
                manual_login_state["active"] = False
                print(f"[manual_login] 완료, status={manual_login_state['status']}")

    thread = threading.Thread(target=_do_manual_login, daemon=True)
    thread.start()

    return jsonify({"message": "브라우저가 열렸습니다. 로그인을 완료해주세요."})


@app.route("/api/accounts/manual-login/status")
def api_manual_login_status():
    """수동 로그인 진행 상태를 반환합니다."""
    return jsonify(manual_login_state)


@app.route("/api/accounts/manual-login/confirm", methods=["POST"])
def api_manual_login_confirm():
    """사용자가 로그인 완료를 수동으로 확인합니다. 쿠키를 저장하고 브라우저를 닫습니다."""
    global manual_login_bot

    if not manual_login_bot:
        return jsonify({"error": "진행 중인 수동 로그인이 없습니다."}), 400

    try:
        import time as _time
        bot = manual_login_bot
        email = manual_login_state.get("email", "")

        # ── 1. YouTube로 이동하여 로그인 상태 확인 ──
        _AVATAR = "button#avatar-btn, ytd-topbar-menu-button-renderer img.yt-img-shadow"
        yt_logged_in = False
        account_suspended = False
        channel_terminated = False

        try:
            bot.page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=15000)
            # 최대 8초 아바타 대기
            for _ in range(8):
                if bot.page.query_selector(_AVATAR):
                    yt_logged_in = True
                    break
                _time.sleep(1)
        except Exception:
            pass

        # ── 2. 로그인 안 된 경우: sign-in 버튼 감지 + Google 정지 확인 ──
        yt_signin_visible = False
        if not yt_logged_in:
            # 2-a. YouTube에서 'Sign in' 버튼이 명확히 보이는지 확인
            #     (구글 OK 상태에서 YouTube만 로그인 안 됨 = YouTube 채널 정지)
            try:
                _SIGNIN_SELS = [
                    'a[href*="accounts.google.com"]',
                    'a[href*="ServiceLogin"]',
                    '#buttons ytd-button-renderer a',
                    'ytd-masthead #buttons a',
                ]
                for _sel in _SIGNIN_SELS:
                    _el = bot.page.query_selector(_sel)
                    if _el:
                        try:
                            _txt = (_el.inner_text() or "").strip()
                            if '로그인' in _txt or 'sign in' in _txt.lower():
                                yt_signin_visible = True
                                break
                        except Exception:
                            pass
            except Exception:
                pass

            # 2-b. Google 계정 정지 여부 확인
            _SUSPENDED_TEXTS = [
                "계정이 정지", "account has been suspended",
                "이 계정은 정지", "suspended your account",
                "비활성화되었습니다", "disabled",
            ]
            try:
                bot.page.goto("https://myaccount.google.com", wait_until="domcontentloaded", timeout=15000)
                _time.sleep(2)
                body = bot.page.inner_text("body") or ""
                if any(t in body for t in _SUSPENDED_TEXTS):
                    account_suspended = True
            except Exception:
                pass

            # 2-c. YouTube sign-in 버튼 노출 + Google 정지 아님
            #     → YouTube 채널이 정지된 것으로 간주 → channel_terminated
            if yt_signin_visible and not account_suspended:
                channel_terminated = True
                if email:
                    try:
                        _acc_db = YouTubeAccount.query.filter_by(
                            user_id=manual_login_state.get("user_id", 0),
                            account_email=email
                        ).first()
                        if _acc_db:
                            _acc_db.channel_terminated = True
                            db.session.commit()
                        add_log(f"[계정정지] {email} — 유튜브 로그인 버튼 감지 → channel_terminated 설정", "error")
                    except Exception:
                        pass

        # ── 3. 로그인 성공한 경우: YouTube 채널 삭제 여부 확인 ──
        if yt_logged_in:
            channel_terminated = bot.is_channel_terminated()
            # channel_terminated면 DB 플래그 저장
            if channel_terminated and email:
                try:
                    _acc_db = YouTubeAccount.query.filter_by(
                        user_id=manual_login_state.get("user_id", 0),
                        account_email=email
                    ).first()
                    if _acc_db:
                        _acc_db.channel_terminated = True
                        db.session.commit()
                except Exception:
                    pass

        # ── 4. 쿠키 저장 (로그인된 경우만) ──
        if yt_logged_in:
            bot.save_cookies()

        # ── 5. 브라우저 종료 ──
        try:
            bot.close_browser()
        except Exception:
            pass
        manual_login_bot = None
        manual_login_state["active"] = False

        # ── 6. 결과 반환 ──
        if account_suspended:
            manual_login_state["status"] = "suspended"
            manual_login_state["message"] = "Google 계정이 정지(suspended)된 상태입니다."
            return jsonify({
                "status": "suspended",
                "message": "이 Google 계정은 정지되었습니다. 더 이상 사용이 불가능합니다.",
                "action": "계정 목록에서 삭제 후 새 계정으로 교체하세요.",
            }), 200

        if not yt_logged_in:
            manual_login_state["status"] = "not_logged_in"
            manual_login_state["message"] = "YouTube 로그인 상태가 확인되지 않습니다."
            _msg = ("유튜브에서 로그인 버튼이 감지되었습니다. 계정이 정지되었을 가능성이 높습니다."
                    if yt_signin_visible
                    else "YouTube 로그인이 확인되지 않았습니다. 다시 수동 로그인을 시도해 주세요.")
            _action = ("계정이 유튜브에서 정지된 경우 새 계정으로 교체가 필요합니다."
                       if yt_signin_visible
                       else "브라우저에서 Google 계정으로 직접 로그인 후 [로그인 완료] 버튼을 누르세요.")
            return jsonify({
                "status": "not_logged_in",
                "message": _msg,
                "action": _action,
                "yt_signin_visible": yt_signin_visible,
            }), 200

        if channel_terminated:
            manual_login_state["status"] = "terminated"
            manual_login_state["message"] = "YouTube 채널이 삭제된 계정입니다."
            return jsonify({
                "status": "terminated",
                "message": "Google 계정 로그인은 성공했지만, YouTube 채널이 정책 위반으로 삭제되었습니다.",
                "action": "이의신청을 통해 채널 복구를 시도하거나, 새 계정으로 교체하세요.",
            }), 200

        manual_login_state["status"] = "success"
        manual_login_state["message"] = "로그인 성공! 쿠키가 저장되었습니다."
        return jsonify({"status": "success", "message": "로그인 확인 완료! 쿠키가 저장되었습니다."})

    except Exception as e:
        return jsonify({"error": f"확인 중 오류: {str(e)}"}), 500


@app.route("/api/accounts/login-status/<email>")
@login_required
def api_login_status(email):
    """계정의 저장된 쿠키 상태를 확인합니다."""
    import re as _re

    account = _find_account(current_user.id, email)
    if not account:
        return jsonify({"has_cookies": False, "message": "계정 없음"})

    label = account.get("label", email.split("@")[0])
    safe_label = _re.sub(r'[^\w\-]', '_', label)
    # 유저별 쿠키 격리: user_id를 경로에 포함
    cookie_dir = os.path.join(_config_dir, "sessions", str(current_user.id))
    os.makedirs(cookie_dir, exist_ok=True)
    cookie_path = os.path.join(cookie_dir, f"{safe_label}.json")

    if os.path.exists(cookie_path):
        mtime = os.path.getmtime(cookie_path)
        from datetime import datetime
        saved_time = datetime.fromtimestamp(mtime).strftime("%m/%d %H:%M")
        return jsonify({"has_cookies": True, "message": f"쿠키 저장됨 ({saved_time})"})

    return jsonify({"has_cookies": False, "message": "쿠키 없음"})


def _save_accounts(accounts):
    """계정 목록을 파일에 저장합니다."""
    accounts_file = os.getenv("ACCOUNTS_FILE", "config/accounts.json")
    os.makedirs(os.path.dirname(accounts_file), exist_ok=True)
    with open(accounts_file, "w", encoding="utf-8") as f:
        json.dump(accounts, f, indent=2, ensure_ascii=False)


def _find_account(user_id, email):
    """유저의 계정을 DB에서 찾고, 없으면 레거시 파일에서 찾습니다."""
    db_acc = YouTubeAccount.query.filter_by(user_id=user_id, account_email=email).first()
    if db_acc:
        return db_acc.to_account_dict()
    return None


def _sync_accounts_to_file(user_id):
    """유저 DB 계정을 레거시 파일로 동기화 (자동화 엔진 호환)."""
    db_accounts = YouTubeAccount.query.filter_by(user_id=user_id).all()
    accounts = [acc.to_account_dict() for acc in db_accounts]
    _save_accounts(accounts)


# ──────────────────────────── 스마트 스케줄러 ────────────────────────────

def _distribute_unassigned_accounts(tasks, accounts):
    """
    노션 '댓글 계정'이 비어있는(미지정) 작업에 계정을 라운드로빈으로 균등 분배합니다.

    - 이미 계정이 지정된 작업은 그대로 둡니다(노션 입력 존중).
    - 미지정 작업만 사용 가능한 계정에 순서대로 골고루 배정합니다.
    - 기존 분포를 고려해, 현재 가장 적게 배정된 계정부터 채워 전체 균형을 맞춥니다.

    Returns:
        (tasks, assigned_count) — tasks는 account 필드가 채워진 동일 리스트
    """
    if not accounts:
        return tasks, 0

    # 사용 가능한 계정 라벨 목록 (label 우선, 없으면 email)
    account_labels = [
        acc.get("label") or acc.get("email", "") for acc in accounts
    ]
    account_labels = [lbl for lbl in account_labels if lbl]
    if not account_labels:
        return tasks, 0

    def _is_assigned(task):
        """노션에 지정된 계정이 실제 계정 목록과 매칭되는지 확인."""
        label = (task.get("account") or "").strip()
        if not label:
            return False
        for acc in accounts:
            if acc.get("label") == label or acc.get("email", "").startswith(label):
                return True
        # 지정은 되어 있으나 매칭 실패 → 미지정으로 보지 않고 기존 경고 흐름에 맡김
        return True

    # 이미 배정된 작업 수를 계정별로 집계 (균형 분배 시작점)
    load = {lbl: 0 for lbl in account_labels}
    for task in tasks:
        label = (task.get("account") or "").strip()
        for acc in accounts:
            if acc.get("label") == label or (label and acc.get("email", "").startswith(label)):
                key = acc.get("label") or acc.get("email", "")
                if key in load:
                    load[key] += 1
                break

    assigned_count = 0
    for task in tasks:
        if _is_assigned(task):
            continue
        # 현재 부하가 가장 적은 계정 선택 (동률이면 목록 순서 우선)
        target = min(account_labels, key=lambda lbl: load[lbl])
        task["account"] = target
        load[target] += 1
        assigned_count += 1

    return tasks, assigned_count


def _schedule_tasks(tasks):
    """
    계정별 라운드로빈으로 태스크를 인터리빙하여 같은 계정 연속을 최소화합니다.

    예: A,A,A,B,B,C → A,B,C,A,B,A (계정 전환만으로 딜레이 없이 진행)
    """
    if len(tasks) <= 1:
        return tasks

    # 계정별 그룹핑 (순서 유지)
    account_queues = defaultdict(deque)
    account_order = []  # 등장 순서 보존
    for task in tasks:
        label = task.get("account", "unknown")
        if label not in account_queues:
            account_order.append(label)
        account_queues[label].append(task)

    # 라운드로빈 인터리빙
    scheduled = []
    remaining = sum(len(q) for q in account_queues.values())
    robin_idx = 0

    while remaining > 0:
        # 빈 큐가 아닌 다음 계정 찾기
        attempts = 0
        while attempts < len(account_order):
            label = account_order[robin_idx % len(account_order)]
            robin_idx += 1
            if account_queues[label]:
                scheduled.append(account_queues[label].popleft())
                remaining -= 1
                break
            attempts += 1
        else:
            # 모든 큐가 비었으면 종료 (안전장치)
            break

    return scheduled


# ──────────────────────────── 자동화 실행 ────────────────────────────

def _ensure_network(adb_changer, timeout=15):
    """네트워크 연결을 확인하고, 끊어져 있으면 비행기모드 OFF 후 복구 대기합니다."""
    if not adb_changer or not adb_changer.enabled:
        return True
    ip = adb_changer.get_current_ip()
    if ip:
        return True
    add_log("네트워크 끊김 감지 - 비행기모드 OFF 및 복구 대기 중...", "warning")
    adb_changer.force_airplane_off()
    ip = adb_changer._wait_for_network(max_wait=timeout)
    if ip:
        add_log(f"네트워크 복구 완료: {ip}", "success")
        return True
    add_log("네트워크 복구 실패 - 계속 진행합니다", "warning")
    return False


def _run_automation(limit=0, selected_ids=None):
    """백그라운드에서 자동화를 실행합니다. limit>0이면 해당 건수만 테스트 실행."""
    global _credit_sync_paused
    try:
        with app.app_context():
            _run_automation_inner(limit, selected_ids)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        add_log(f"자동화 스레드 오류: {str(e)}", "error")
        for line in tb.split('\n'):
            if 'File' in line or 'Error' in line:
                add_log(f"  {line.strip()}", "error")
        automation_state["running"] = False
    finally:
        _credit_sync_paused = False  # 자동화 완료 → 크레딧 폴링 재개


# ──────────────────────────── 로컬 댓글 게시 (영상수집 파이프라인, Notion 비경유) ────────────────────────────

def _run_local_posting(user_id, comment_ids=None):
    """백그라운드: 로컬 CommentTask(생성완료)를 YouTube에 게시."""
    try:
        with app.app_context():
            _run_local_posting_inner(user_id, comment_ids or [])
    except Exception as e:
        add_log(f"댓글 게시 스레드 오류: {str(e)}", "error")
    finally:
        automation_state["running"] = False


def _run_local_posting_inner(user_id, comment_ids):
    from src.youtube_bot import YouTubeBot

    accounts = load_accounts(user_id=user_id)
    if not accounts:
        add_log("게시할 계정이 없습니다. 계정을 먼저 등록·수동 로그인하세요.", "error")
        return
    acc_by_label = {a.get("label"): a for a in accounts}

    q = (db.session.query(CommentTask, VideoTarget)
         .join(VideoTarget, CommentTask.video_id == VideoTarget.id)
         .join(Campaign, VideoTarget.campaign_id == Campaign.id)
         .filter(Campaign.user_id == user_id, CommentTask.status == '생성완료'))
    if comment_ids:
        q = q.filter(CommentTask.id.in_(comment_ids))
    rows = q.order_by(CommentTask.created_at.asc()).all()

    automation_state["total"] = len(rows)
    automation_state["progress"] = 0
    if not rows:
        add_log("게시할 댓글(생성완료 상태)이 없습니다.", "warning")
        return

    add_log(f"댓글 게시 시작 — 대상 {len(rows)}건", "info")
    fingerprint_manager = FingerprintManager()
    success = fail = 0
    for i, (task, video) in enumerate(rows):
        automation_state["progress"] = i + 1
        account = acc_by_label.get(task.account_label) or accounts[0]
        label = account.get("label") or account.get("email")
        automation_state["current_task"] = f"[{i+1}/{len(rows)}] {label} → {video.title[:30]}"
        task.status = '업로드중'
        db.session.commit()

        bot = None
        try:
            bot = YouTubeBot(fingerprint_manager=fingerprint_manager, account_label=label, user_id=user_id)
            bot.start_browser()
            if not bot.login_youtube(account.get("email"), account.get("password")):
                add_log(f"[게시 실패] {label} 로그인 실패 — 수동 로그인이 필요합니다", "error")
                task.status = '실패'
                db.session.commit()
                fail += 1
                continue
            comment_url = bot.post_comment(video.url, task.generated_text)
            if not comment_url:
                add_log(f"[게시 실패] 댓글 작성 실패: {video.title[:30]}", "error")
                task.status = '실패'
                db.session.commit()
                fail += 1
                continue
            # 대댓글: 댓글 URL이 lc= 로 검증된 경우에만
            if task.reply_text and "&lc=" in comment_url:
                try:
                    bot.post_reply(comment_url, task.reply_text)
                except Exception as _re:
                    add_log(f"[대댓글 실패] {video.title[:30]}: {_re}", "warning")
            task.status = '완료'
            task.result_url = comment_url
            task.executed_at = datetime.now()
            db.session.commit()
            success += 1
            add_log(f"[게시 완료] {video.title[:30]} → {comment_url[:60]}", "success")
        except Exception as e:
            db.session.rollback()
            try:
                task.status = '실패'
                db.session.commit()
            except Exception:
                db.session.rollback()
            fail += 1
            add_log(f"[게시 오류] {video.title[:30]}: {str(e)}", "error")
        finally:
            if bot:
                try:
                    bot.close_browser()
                except Exception:
                    pass

    add_log(f"댓글 게시 완료 — 성공 {success}건 / 실패 {fail}건", "success")


@app.route("/api/youtube/post-comments", methods=["POST"])
@login_required
def api_post_local_comments():
    """생성된 댓글(로컬 CommentTask 생성완료)을 YouTube에 게시 (Agency 전용)."""
    if not license_client.can_use_feature("youtube_collect"):
        return jsonify({"error": "영상 수집/게시는 Agency 플랜부터 사용 가능합니다."}), 403
    data = request.get_json(silent=True) or {}
    comment_ids = data.get("comment_ids", [])
    with automation_lock:
        if automation_state["running"]:
            return jsonify({"error": "이미 다른 작업이 실행 중입니다. 완료 후 다시 시도하세요."}), 409
        automation_state["running"] = True
        automation_state["logs"] = []
        automation_state["progress"] = 0
        automation_state["current_task"] = "댓글 게시 준비 중..."
    uid = current_user.id
    automation_state["user_id"] = uid
    threading.Thread(target=_run_local_posting, args=(uid, comment_ids), daemon=True).start()
    return jsonify({"success": True, "message": "댓글 게시를 시작했습니다. 실행 로그 탭에서 진행 상황을 확인하세요."})


def _run_automation_inner(limit=0, selected_ids=None):
    import time
    from src.youtube_bot import YouTubeBot

    selected_ids = selected_ids or []
    test_mode = limit > 0

    try:
        if selected_ids:
            mode_label = f"[선택 실행 {len(selected_ids)}건]"
        elif test_mode:
            mode_label = f"[테스트 {limit}건]"
        else:
            mode_label = "[전체 실행]"
        add_log(f"자동화 시작 {mode_label}", "info")
        save_automation_log("automation_start", detail=mode_label, level="info")

        # ── Chromium 사전 체크 (브라우저 실행 전에 미리 확인) ──
        add_log("Chromium 브라우저 확인 중...", "info")
        chromium_ready, chromium_msg = YouTubeBot.check_chromium_ready()
        if not chromium_ready:
            add_log(f"Chromium 브라우저 오류: {chromium_msg}", "error")
            add_log("해결 방법: CMD(명령 프롬프트)에서 'playwright install chromium' 실행 후 재시도", "error")
            automation_state["running"] = False
            return
        add_log(f"Chromium 준비 완료", "success")

        # ── 사용자 설정을 환경변수에 반영 (DB 값이 .env보다 우선) ──
        auto_user_id = automation_state.get("user_id")
        _user_settings_keys = ["HEADLESS", "MAX_COMMENTS_PER_DAY", "COMMENT_INTERVAL_SEC",
                               "SAME_VIDEO_INTERVAL_MIN", "SMM_LIKE_QUANTITY", "SMM_LIKE_AUTO_MAX",
                               "ADB_IP_CHANGE_ENABLED", "ADB_PATH", "ADB_AIRPLANE_WAIT",
                               "ADB_AUTO_ETHERNET", "ADB_ETHERNET_NAME", "PRE_WATCH_COMMENT"]
        # HEADLESS 기본값: false (사용자가 설정을 저장하지 않았어도 화면 보이기가 기본)
        _user_settings_defaults = {"HEADLESS": "false", "ADB_IP_CHANGE_ENABLED": "false"}
        for _sk in _user_settings_keys:
            _sv = _get_user_setting(auto_user_id or 1, _sk)
            if _sv is not None:
                os.environ[_sk] = str(_sv)
            elif _sk in _user_settings_defaults:
                os.environ[_sk] = _user_settings_defaults[_sk]

        headless_mode = os.getenv("HEADLESS", "false").lower() == "true"
        add_log(f"브라우저 모드: {'백그라운드 (화면 안 보임)' if headless_mode else '화면 보이기'}", "info")

        notion = _create_notion_manager()
        # '원고 X' 기능 비활성화 (사용자 불편 — 추후 테스트 후 재활성화)
        # try:
        #     notion.check_and_restore_script_tasks()
        # except Exception:
        #     pass
        fingerprint_manager = FingerprintManager()
        safety_rules = SafetyRules()
        smm_client = SMMClient()
        accounts = load_accounts(user_id=auto_user_id)

        # ── 크레딧 사전 체크 ──
        try:
            task_count = limit if test_mode else (len(selected_ids) if selected_ids else 0)
            if task_count == 0:
                counts = notion.count_all_statuses()
                task_count = counts.get("댓글작업전", 0)
            estimate = _estimate_credit_usage(task_count)
            if not estimate["sufficient"]:
                if estimate["token_shortage"] > 0:
                    add_log(
                        f"⚠ 토큰 부족 예상: 현재 {estimate['current_tokens']:,} / "
                        f"필요 {estimate['total_tokens_needed']:,} "
                        f"(부족분 {estimate['token_shortage']:,})",
                        "warning"
                    )
                if estimate["like_credit_shortage"] > 0:
                    add_log(
                        f"⚠ 좋아요 크레딧 부족 예상: 현재 {estimate['current_like_credits']:,}원 / "
                        f"필요 {estimate['total_like_credits_needed']:,}원 "
                        f"(부족분 {estimate['like_credit_shortage']:,}원)",
                        "warning"
                    )
                add_log(
                    f"💡 크레딧 충전을 권장합니다! "
                    f"(대기 {task_count}건 × 좋아요 {estimate['like_qty_per_task']}개/건 × 10원 = "
                    f"{estimate['total_like_credits_needed']:,}원)",
                    "warning"
                )
        except Exception:
            pass

        # ADB IP 변경 활성 시 유선 인터넷 비활성화 (USB 테더링으로 전환)
        # Full Auto 모드에서는 _run_full_auto에서 관리하므로 건너뜀
        adb_changer = ADBIPChanger()
        _ethernet_disabled = False
        if adb_changer.enabled and adb_changer.auto_ethernet and not automation_state.get("full_auto"):
            automation_state["current_task"] = "[준비] 유선 인터넷 비활성화 중..."
            add_log("유선 인터넷 비활성화 → USB 테더링으로 전환 중...", "info")
            ok, msg = adb_changer.disable_ethernet()
            if ok:
                _ethernet_disabled = True
                add_log(f"유선 비활성화 완료: {msg}", "success")
            else:
                add_log(f"유선 비활성화 실패: {msg}", "warning")
                add_log("주의: 유선 인터넷이 켜진 상태에서는 IP 변경이 작동하지 않습니다!", "warning")
                add_log("해결: 프로그램을 관리자 권한으로 실행하거나, 수동으로 유선 인터넷을 끄세요.", "info")

        if not accounts:
            add_log(f"YouTube 계정이 없습니다. 대시보드 > 계정 관리에서 계정을 등록해주세요. (user_id: {auto_user_id})", "error")
            automation_state["running"] = False
            return

        # 쿠키(수동 로그인) 상태 체크 — 로그인 안 된 계정 경고
        _no_cookie_accounts = []
        for acc in accounts:
            label = acc.get("label", acc.get("email", ""))
            import re as _re_cookie
            safe_label = _re_cookie.sub(r'[^\w\-]', '_', label)
            # 실제 쿠키 저장 경로와 동일하게 체크
            cookie_path = os.path.join(_config_dir, "sessions", str(auto_user_id), f"{safe_label}.json")
            cookie_path2 = os.path.join(_config_dir, "sessions", f"{safe_label}.json")
            cookie_path3 = os.path.join(_config_dir, "sessions", str(auto_user_id), f"{label}.json")
            if not any(os.path.exists(p) for p in [cookie_path, cookie_path2, cookie_path3]):
                _no_cookie_accounts.append(label)
        if _no_cookie_accounts:
            add_log(f"수동 로그인 미완료 계정: {', '.join(_no_cookie_accounts)} — 해당 계정은 자동 스킵됩니다.", "warning")
        add_log(f"계정 {len(accounts)}개 로드됨", "info")

        automation_state["current_task"] = "[준비] 노션 DB에서 대기 작업 로딩 중..."
        add_log("노션 DB에서 대기 작업을 불러오는 중... (작업량에 따라 1~2분 소요될 수 있습니다)", "info")
        tasks = notion.get_pending_tasks()
        if not tasks:
            add_log("대기 중인 작업이 없습니다.", "warning")
            automation_state["running"] = False
            return

        # 중복 영상 체크 (테스트/선택 실행 시 스킵하여 빠르게 진행)
        automation_state["total"] = len(tasks)
        if not test_mode and not selected_ids:
            automation_state["current_task"] = "[준비] 중복 영상 체크 중..."
            add_log(f"대기 작업 {len(tasks)}건 로드 완료 → 중복 영상 체크 중...", "info")
            tasks, duplicate_tasks = notion.check_duplicates(tasks)
            if duplicate_tasks:
                dup_count = len(duplicate_tasks)
                add_log(f"중복 영상 {dup_count}건 발견 → '중복' 상태로 변경됨", "warning")
                automation_state["results"]["duplicate"] = dup_count
            if not tasks:
                add_log("중복 제외 후 대기 작업이 없습니다.", "warning")
                automation_state["running"] = False
                automation_state["current_task"] = None
                return
        else:
            add_log(f"대기 작업 {len(tasks)}건 로드 (테스트/선택 실행 → 중복 체크 스킵)", "info")

        # 선택 실행: 선택된 page_id만 필터링
        if selected_ids:
            selected_set = set(selected_ids)
            tasks = [t for t in tasks if t.get("page_id") in selected_set]
            if not tasks:
                add_log("선택된 작업을 찾을 수 없습니다.", "warning")
                automation_state["running"] = False
                return
            add_log(f"선택된 {len(tasks)}건 작업 실행", "info")
        # 테스트 모드: 지정 건수만 처리
        elif test_mode and len(tasks) > limit:
            add_log(f"테스트 모드: 전체 {len(tasks)}건 중 {limit}건만 실행", "warning")
            tasks = tasks[:limit]

        # ── 댓글계정 미지정 작업 자동 분배 (라운드로빈 균등 분배) ──
        tasks, _assigned_n = _distribute_unassigned_accounts(tasks, accounts)
        if _assigned_n:
            add_log(
                f"댓글계정 미지정 {_assigned_n}건 → {len(accounts)}개 계정에 라운드로빈 균등 분배",
                "info",
            )

        # ── 스마트 스케줄링: 계정 인터리빙으로 대기시간 최소화 ──
        tasks = _schedule_tasks(tasks)
        # 계정 분포 로그
        acct_counts = defaultdict(int)
        for t in tasks:
            acct_counts[t.get("account", "?")] += 1
        acct_summary = ", ".join(f"{k}:{v}건" for k, v in acct_counts.items())
        add_log(f"스케줄링 완료: {acct_summary} (계정 인터리빙 적용)", "info")

        # ── 분배 결과를 프론트엔드 팝업용 상태에 기록 (미지정 자동 분배가 있었을 때만) ──
        if _assigned_n:
            automation_state["distribution_notice"] = {
                "id": int(eta_tracker.get("started_at") or 0),  # 실행마다 고유 → 팝업 1회만
                "assigned": _assigned_n,
                "account_count": len(accounts),
                "breakdown": dict(acct_counts),
                "message": (
                    f"댓글계정이 비어있던 {_assigned_n}건을 "
                    f"등록된 계정 {len(accounts)}개에 자동 균등 분배했습니다."
                ),
            }

        automation_state["total"] = len(tasks)
        add_log(f"총 {len(tasks)}개 작업 {'(테스트)' if test_mode else ''} 발견", "info")

        delay_ip_change = int(os.getenv("DELAY_AFTER_IP_CHANGE", "3"))
        comment_interval = int(os.getenv("COMMENT_INTERVAL_SEC", "180"))
        prev_account_label = None
        prev_task_success = True  # 실패/스킵 시 대기 스킵용

        for i, task in enumerate(tasks):
            if not automation_state["running"]:
                add_log("사용자에 의해 중지됨", "warning")
                break

            automation_state["progress"] = i + 1
            eta_tracker["current_task_start"] = time.time()
            task_url_short = task.get("youtube_url", "")[:50]
            automation_state["current_task"] = task_url_short

            # 계정 결정 (노션 "댓글 계정" 컬럼 기준, 비어있으면 첫 번째 계정 자동 배정)
            account = None
            account_label = task.get("account", "")
            for acc in accounts:
                if acc.get("label") == account_label or acc.get("email", "").startswith(account_label):
                    account = acc
                    break
            if not account:
                account = accounts[0]
                if account_label:
                    add_log(f"계정 '{account_label}'을 찾을 수 없어 기본 계정 사용: {account.get('label', account.get('email'))}", "warning")
                elif i == 0:
                    add_log(f"노션 '댓글 계정' 미지정 → 기본 계정 자동 배정: {account.get('label', account.get('email'))}", "info")

            current_label = account.get("label", account.get("email", "unknown"))

            # ── 대기 시간 (스마트 스케줄링) ──
            if prev_account_label and prev_account_label != current_label:
                # 다른 계정 → IP 변경 필수 (1IP=1계정 원칙)
                adb_changer = ADBIPChanger()
                if adb_changer.enabled:
                    add_log(f"계정 전환: {prev_account_label} → {current_label} | IP 변경 시도 중...", "info")
                    automation_state["current_task"] = f"[IP 변경] 비행기모드 토글 중..."
                    success, msg = adb_changer.toggle_airplane_mode()
                    if success:
                        add_log(f"IP 변경 완료: {msg}", "success")
                    else:
                        # IP 변경 실패 → 계정 전환하지 않고 이전 계정으로 계속 진행
                        add_log(f"IP 변경 실패: {msg}", "warning")
                        add_log(f"1IP=1계정 원칙: 이전 계정({prev_account_label})으로 계속 진행합니다.", "warning")
                        # 이전 계정으로 되돌림
                        for acc in accounts:
                            if acc.get("label") == prev_account_label or acc.get("email", "").startswith(prev_account_label):
                                account = acc
                                current_label = prev_account_label
                                break
                else:
                    add_log(f"계정 전환: {prev_account_label} → {current_label} (IP 변경 {delay_ip_change}초 대기)", "info")
                    time.sleep(delay_ip_change)
            elif prev_account_label == current_label and i > 0:
                # 같은 계정 연속 - 이전 작업이 실패/스킵이면 대기 불필요
                if not prev_task_success:
                    add_log("같은 계정 연속이지만 이전 작업 실패/스킵 → 대기 없이 진행", "info")
                else:
                    human_delay = safety_rules.get_human_delay("comment")
                    actual_delay = human_delay["delay_sec"]
                    add_log(
                        f"같은 계정 연속 - 🧑 {human_delay['description']} "
                        f"(타이핑 {human_delay['typing_delay_ms']}ms)",
                        "info"
                    )
                    time.sleep(actual_delay)

            # 안전 규칙 검사 (테스트/선택 실행 모드에서는 시간 간격 규칙 건너뜀)
            skip = test_mode or bool(selected_ids)
            passed, reason = safety_rules.check_all_rules(
                current_label, task["youtube_url"], task["comment_text"],
                skip_interval=skip,
            )
            if not passed:
                add_log(f"[건너뜀] {reason}", "warning")
                automation_state["results"]["skip"] += 1
                prev_task_success = False
                prev_account_label = current_label
                continue

            # 일일 댓글 한도 체크
            can_post, used, limit = check_daily_limit(auto_user_id)
            if not can_post:
                add_log(f"일일 댓글 한도 도달 ({used}/{limit}건) — 자동화를 중지합니다.", "warning")
                break

            # 브라우저 실행 및 댓글 작성
            bot = YouTubeBot(
                fingerprint_manager=fingerprint_manager,
                account_label=current_label,
                user_id=automation_state.get('user_id'),
            )
            try:
                automation_state["current_task"] = f"[브라우저 시작] {task_url_short}"
                add_log(f"작업 {i+1}/{len(tasks)}: {task['youtube_url'][:50]}...", "info")
                bot.start_browser()

                automation_state["current_task"] = f"[로그인 중] {current_label}"

                # 채널 삭제(terminated) 상태 선-확인: DB 플래그가 있으면 즉시 스킵
                if account.get("channel_terminated"):
                    add_log(f"[채널 삭제] {current_label} — YouTube 채널이 삭제된 계정입니다. 작업을 건너뜁니다.", "error")
                    automation_state["results"]["skip"] += 1
                    prev_task_success = False
                    continue

                # 계정별 일일 댓글 한도 체크
                _acc_db_limit = YouTubeAccount.query.filter_by(
                    user_id=auto_user_id, account_email=account["email"]
                ).first()
                if _acc_db_limit:
                    _daily_limit = _acc_db_limit.daily_comment_limit or 10
                    _today_str = date.today().isoformat()
                    _acc_today_count = CommentHistory.query.filter(
                        CommentHistory.user_id == auto_user_id,
                        CommentHistory.account_label == current_label,
                        CommentHistory.created_at >= _today_str
                    ).count()
                    if _acc_today_count >= _daily_limit:
                        add_log(f"[계정 한도] {current_label} — 오늘 {_acc_today_count}/{_daily_limit}개 완료, 다음 계정으로 넘어갑니다.", "warning")
                        automation_state["results"]["skip"] += 1
                        prev_task_success = False
                        continue

                login_ok = bot.login_youtube(account["email"], account["password"])
                if not login_ok:
                    add_log(f"로그인 실패: {current_label} — 수동 로그인이 필요합니다. 이 계정의 작업을 건너뜁니다.", "error")
                    add_log(f"계정 관리에서 '{current_label}' 계정의 [수동 로그인] 버튼을 눌러 로그인을 완료해주세요.", "warning")
                    automation_state["results"]["fail"] += 1
                    prev_task_success = False
                    continue

                # 로그인 성공 후 채널 삭제 여부 자동 감지
                automation_state["current_task"] = f"[채널 상태 확인] {current_label}"
                if bot.is_channel_terminated():
                    add_log(f"[채널 삭제 감지] {current_label} — YouTube 채널이 정책 위반으로 삭제되었습니다. 계정을 비활성화하고 작업을 건너뜁니다.", "error")
                    # DB 플래그 저장 → 이후 라운드에서 즉시 스킵
                    try:
                        _acc_db = YouTubeAccount.query.filter_by(
                            user_id=automation_state.get("user_id"),
                            account_email=account["email"]
                        ).first()
                        if _acc_db:
                            _acc_db.channel_terminated = True
                            db.session.commit()
                    except Exception:
                        pass
                    automation_state["results"]["skip"] += 1
                    prev_task_success = False
                    continue

                automation_state["current_task"] = f"[댓글 작성 중] {task_url_short}"
                _pre_watch = os.getenv("PRE_WATCH_COMMENT", "false").lower() == "true"
                if _pre_watch:
                    add_log(f"[안전모드] 영상 시청 후 댓글 작성 (1~3분 시청 진행)", "info")
                    automation_state["current_task"] = f"[영상 시청 중] {task_url_short}"
                comment_url = bot.post_comment(task["youtube_url"], task["comment_text"], pre_watch=_pre_watch)

                if comment_url:
                    # 실제 댓글 URL 검증 (lc= 파라미터가 있어야 진짜 댓글)
                    _comment_verified = "&lc=" in comment_url
                    # ── 1단계: 댓글 작성 완료 ──
                    automation_state["current_task"] = f"[1/3 댓글완료] {task_url_short}"
                    if _comment_verified:
                        add_log(f"[1/3 댓글작성 완료] {comment_url}", "success")
                    else:
                        add_log(f"[1/3 댓글작성 완료] 댓글 URL 미확인 — 게시 여부 불확실 (좋아요 주문 스킵)", "warning")

                    # ── 2단계: 즉시 노션에 댓글완료 + URL 반영 ──
                    automation_state["current_task"] = f"[2/3 노션반영] {task_url_short}"
                    _ensure_network(adb_changer)  # 네트워크 확인 후 API 호출
                    add_log("[2/3 노션 반영 중] 상태→댓글완료, 댓글 URL 저장...", "info")
                    notion_ok, notion_err = notion.update_task_result(task["page_id"], comment_url, status="댓글완료")
                    if notion_ok:
                        add_log("[2/3 노션 반영 완료] 댓글완료 + URL 저장 성공", "success")
                    else:
                        add_log(f"[2/3 노션 반영 실패] {notion_err}", "warning")

                    # ── 3단계: 동적 좋아요 주문 (라이선스 서버 경유) ──
                    if not _comment_verified:
                        add_log("[3/3 좋아요 스킵] 댓글 URL 미확인으로 좋아요 주문을 건너뜁니다.", "warning")
                    elif license_client.is_active():
                        automation_state["current_task"] = f"[3/3 좋아요 분석] {task_url_short}"
                        like_auto_max = int(os.getenv("SMM_LIKE_AUTO_MAX", "500"))
                        default_qty = int(os.getenv("SMM_LIKE_QUANTITY", "20"))
                        # 자동 좋아요 티어 설정 (설정 페이지에서 선택)
                        auto_like_tier = _get_user_setting(
                            automation_state.get("user_id", 1),
                            "LIKE_AUTO_TIER", "standard"
                        )
                        if auto_like_tier not in LIKE_TIERS:
                            auto_like_tier = "standard"

                        # 상위 댓글 좋아요 스크래핑 (텍스트 포함)
                        top_comments_data = bot.get_top_comments_with_text(count=5)
                        top_likes = [c["likes"] for c in top_comments_data] if top_comments_data else bot.get_top_comment_likes(count=5)
                        # 상위 3개 댓글 텍스트 (승인 대기 팝업용)
                        top_comment_texts = [{"text": c["text"], "likes": c["likes"]} for c in top_comments_data[:3]] if top_comments_data else []
                        dynamic_qty = _calculate_dynamic_likes(top_likes, default_qty)

                        if top_likes:
                            add_log(
                                f"[3/3 좋아요 분석] 상위 댓글 좋아요: {top_likes} → "
                                f"목표: {dynamic_qty}개",
                                "info"
                            )
                        else:
                            add_log(f"[3/3 좋아요] 상위 댓글 분석 실패 → 기본 {default_qty}개", "info")

                        if dynamic_qty > like_auto_max:
                            # MAX 초과 → 기본 수량으로 즉시 주문 + 대기 리스트에 추가
                            add_log(
                                f"[3/3 좋아요] {dynamic_qty}개 > MAX {like_auto_max}개 → "
                                f"기본 {default_qty}개 우선 주문, 추가분 승인 대기",
                                "warning"
                            )
                            # 기본 수량 즉시 주문 (라이선스 서버 경유)
                            single = license_client.order_likes_via_server(comment_url, quantity=default_qty, tier=auto_like_tier, source="auto")
                            if single.get("success"):
                                automation_state["results"]["likes"] += 1
                                add_log(f"[3/3 좋아요 기본 주문] {default_qty}개 | 주문ID: {single.get('order_id')}", "success")
                                _save_like_order(single.get("order_id"), comment_url, default_qty, source="auto", tier=auto_like_tier, cost=single.get("cost", 0))
                            else:
                                add_log(f"[3/3 좋아요 주문 실패] {single.get('error', '?')}", "warning")

                            # 추가분 대기 리스트에 추가 (사용자가 대시보드에서 수동 승인)
                            pending_item = {
                                "id": str(uuid.uuid4())[:8],
                                "comment_url": comment_url,
                                "page_id": task["page_id"],
                                "qty": dynamic_qty,
                                "default_qty": default_qty,
                                "top_likes": top_likes,
                                "top_comments": top_comment_texts,
                                "my_comment": task.get("comment_text", "")[:200],
                                "video_url": task["youtube_url"],
                                "video_title": task.get("video_title", "")[:60],
                                "account": current_label,
                                "created_at": datetime.now().strftime("%H:%M:%S"),
                            }
                            with like_pending_lock:
                                like_pending_list.append(pending_item)
                            add_log(
                                f"[승인 대기 추가] {dynamic_qty}개 좋아요 | "
                                f"대시보드에서 수동 승인 가능",
                                "warning"
                            )
                        else:
                            # MAX 이하 → 자동 주문 (라이선스 서버 경유)
                            automation_state["current_task"] = f"[3/3 좋아요 {dynamic_qty}개] {task_url_short}"
                            tier_name = LIKE_TIERS.get(auto_like_tier, {}).get("name", auto_like_tier)
                            add_log(f"[3/3 좋아요 주문 중] {dynamic_qty}개 ({tier_name}) | {comment_url[:50]}...", "info")
                            single = license_client.order_likes_via_server(comment_url, quantity=dynamic_qty, tier=auto_like_tier, source="auto")
                            if single.get("success"):
                                automation_state["results"]["likes"] += 1
                                add_log(
                                    f"[3/3 좋아요 주문 성공] {dynamic_qty}개 | 주문ID: {single.get('order_id')}",
                                    "success"
                                )
                                _save_like_order(single.get("order_id"), comment_url, dynamic_qty, source="auto", tier=auto_like_tier, cost=single.get("cost", 0))
                                _check_credit_threshold()
                                try:
                                    notion.update_like_checkbox(task["page_id"])
                                except Exception:
                                    pass
                            else:
                                err = single.get("error", "?")
                                add_log(f"[3/3 좋아요 주문 실패] {err}", "warning")
                    else:
                        add_log("[3/3 좋아요] 라이선스 미인증 - 건너뜀", "info")
                    # 상태는 '댓글완료' 유지 (추후 대댓글 자동화에서 처리)

                    safety_rules.record_comment(current_label, task["youtube_url"], task["comment_text"])

                    # ── 토큰 차감 ──
                    token_result = license_client.use_tokens("comment_post")
                    if token_result is None:
                        add_log("토큰 부족 — 잔여 토큰을 확인해주세요.", "warning")
                    elif token_result != 999999999:  # owner 모드가 아닌 경우만 로그
                        add_log(f"토큰 차감: -10 (잔여: {token_result:,})", "info")

                    # DB에 댓글 히스토리 저장 (안전 규칙 + 관리자 통계)
                    _auto_user_id = current_user.id if (current_user and current_user.is_authenticated) else 1
                    save_comment_history_db(
                        _auto_user_id, current_label,
                        task["youtube_url"], safety_rules._extract_video_id(task["youtube_url"]),
                        task["comment_text"]
                    )
                    # DB에 자동화 로그 저장
                    save_automation_log(
                        "comment_post", user_id=_auto_user_id,
                        account_label=current_label,
                        video_url=task["youtube_url"],
                        video_title=task.get("video_title"),
                        comment_text=task["comment_text"],
                        comment_url=comment_url,
                        level="success"
                    )

                    # 트래킹 자동 등록 (상태: 트래킹 예정 - 4시간 후 자동 확인)
                    if comment_url:
                        comment_tracker.register_comment(
                            comment_url, task["youtube_url"],
                            current_label, task["comment_text"],
                            initial_status="pending_tracking"
                        )

                    automation_state["results"]["success"] += 1
                    save_automation_log("comment_success", account_label=current_label, video_url=task["youtube_url"])
                    # 계정별 마지막 댓글 시간 업데이트
                    try:
                        _acc_lc = YouTubeAccount.query.filter_by(
                            user_id=auto_user_id, account_email=account["email"]
                        ).first()
                        if _acc_lc:
                            _acc_lc.last_comment_at = datetime.datetime.now()
                            db.session.commit()
                    except Exception:
                        pass
                    automation_state["current_task"] = f"[완료] {task_url_short}"
                    add_log(f"--- 작업 {i+1}/{len(tasks)} 완료 ---", "success")
                    prev_task_success = True
                else:
                    automation_state["results"]["fail"] += 1
                    fail_reason = getattr(bot, '_last_error', '알 수 없는 오류')
                    notion.update_task_error(task["page_id"], f"댓글 작성 실패: {fail_reason}")
                    add_log(f"댓글 작성 실패: {fail_reason}", "error")
                    # DB에 실패 로그 저장
                    save_automation_log(
                        "comment_fail",
                        account_label=current_label,
                        video_url=task["youtube_url"],
                        video_title=task.get("video_title"),
                        detail="댓글 작성 실패",
                        level="error"
                    )
                    prev_task_success = False

            except Exception as e:
                automation_state["results"]["fail"] += 1
                prev_task_success = False
                err_detail = str(e)[:300]
                add_log(f"작업 오류: {err_detail}", "error")
                # Chromium/브라우저 관련 오류 시 사용자 안내 추가
                if any(kw in err_detail.lower() for kw in ["chromium", "browser", "playwright", "executable"]):
                    add_log("브라우저 실행 실패 — CMD에서 'playwright install chromium' 실행 후 재시도하세요.", "error")
                save_automation_log(
                    "task_error", account_label=current_label,
                    video_url=task.get("youtube_url"),
                    detail=err_detail[:200], level="error"
                )
            finally:
                # ETA: 작업 소요시간 기록
                if eta_tracker["current_task_start"] > 0:
                    elapsed = time.time() - eta_tracker["current_task_start"]
                    eta_tracker["task_times"].append(elapsed)
                    # 최근 20건만 유지 (이동평균)
                    if len(eta_tracker["task_times"]) > 20:
                        eta_tracker["task_times"] = eta_tracker["task_times"][-20:]
                bot.close_browser()
                prev_account_label = current_label

        summary_prefix = "[테스트 완료]" if test_mode else "[전체 완료]"
        dup = automation_state['results'].get('duplicate', 0)
        dup_text = f", 중복: {dup}" if dup > 0 else ""
        summary_msg = (
            f"{summary_prefix} 성공: {automation_state['results']['success']}, "
            f"실패: {automation_state['results']['fail']}, "
            f"건너뜀: {automation_state['results']['skip']}, "
            f"좋아요: {automation_state['results']['likes']}{dup_text}"
        )
        add_log(summary_msg, "success" if automation_state['results']['success'] > 0 else "warning")
        save_automation_log("automation_end", detail=summary_msg, level="success")

        # 자동화 완료 후 잔액 스냅샷 서버 전송
        try:
            _auto_uid = automation_state.get("user_id")
            if _auto_uid:
                _u = db.session.get(User, _auto_uid)
                if _u:
                    _sync_balance_snapshot(
                        _u.email, "automation_end",
                        license_client.token_balance, license_client.like_credit_balance
                    )
        except Exception:
            pass

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        add_log(f"치명적 오류: {str(e)}", "error")
        for line in tb.split('\n'):
            if 'File' in line or 'Error' in line:
                add_log(f"  {line.strip()}", "error")
    finally:
        # 비행기모드 안전 해제 (ON 상태로 남아있을 수 있음)
        try:
            _adb = ADBIPChanger()
            if _adb.enabled:
                add_log("비행기모드 안전 해제 확인 중...", "info")
                _adb.force_airplane_off()
        except Exception as e:
            add_log(f"비행기모드 해제 실패: {e}", "warning")

        # ADB IP 변경 사용 시 유선 인터넷 복원
        try:
            if _ethernet_disabled:
                add_log("유선 인터넷 복원 중...", "info")
                ok, msg = adb_changer.enable_ethernet()
                if ok:
                    add_log(f"유선 인터넷 복원 완료: {msg}", "success")
                else:
                    add_log(f"유선 복원 실패: {msg} (수동으로 복원 필요)", "warning")
        except NameError:
            pass

        # Full Auto 모드에서는 _run_full_auto가 running을 관리하므로 여기서 끄지 않음
        if not automation_state.get("full_auto"):
            automation_state["running"] = False
            automation_state["current_task"] = None
            automation_state["stopping"] = False


def _run_full_auto():
    """Full Auto 모드: 대기 작업이 없을 때까지 반복 실행합니다."""
    global _credit_sync_paused
    try:
        with app.app_context():
            _run_full_auto_inner()
    except Exception as e:
        add_log(f"Full Auto 스레드 오류: {str(e)}", "error")
        automation_state["running"] = False
        automation_state["full_auto"] = False
    finally:
        _credit_sync_paused = False  # Full Auto 완료 → 크레딧 폴링 재개


def _run_full_auto_inner():
    import time

    round_num = 0
    total_success = 0
    total_fail = 0
    total_skip = 0
    total_likes = 0

    # Full Auto 모드에서는 여기서 유선 제어 (라운드별 반복 방지)
    adb_changer = ADBIPChanger()
    _fa_ethernet_disabled = False
    if adb_changer.enabled and adb_changer.auto_ethernet:
        add_log("유선 인터넷 비활성화 → USB 테더링으로 전환 중...", "info")
        ok, msg = adb_changer.disable_ethernet()
        if ok:
            _fa_ethernet_disabled = True
            add_log(f"유선 비활성화 완료: {msg}", "success")
        else:
            add_log(f"유선 비활성화 실패: {msg}", "warning")

    try:
        add_log("=== Full Auto 모드 시작 ===", "info")
        add_log("대기 작업이 없을 때까지 계속 실행합니다. 중지 버튼으로 멈출 수 있습니다.", "info")

        while automation_state["running"]:
            round_num += 1
            add_log(f"── 라운드 {round_num} 시작 ──", "info")

            # 매 라운드마다 결과 리셋
            automation_state["progress"] = 0
            automation_state["results"] = {"success": 0, "fail": 0, "skip": 0, "likes": 0, "duplicate": 0}

            # 자동화 실행 (전체 모드) - 이미 app_context 안이므로 inner 직접 호출
            _run_automation_inner(limit=0)

            # 라운드 결과 누적
            r = automation_state["results"]
            total_success += r["success"]
            total_fail += r["fail"]
            total_skip += r["skip"]
            total_likes += r["likes"]

            add_log(
                f"── 라운드 {round_num} 완료: 성공 {r['success']}, 실패 {r['fail']}, "
                f"건너뜀 {r['skip']}, 좋아요 {r['likes']} ──",
                "success" if r["success"] > 0 else "warning",
            )

            if not automation_state["running"]:
                break

            # 다음 라운드 전 대기 작업 확인
            automation_state["current_task"] = "[재수집 중] 노션 대기 작업 확인..."
            try:
                notion = _create_notion_manager()
                pending = notion.count_pending_tasks()
                add_log(f"남은 대기 작업: {pending}건", "info")
            except Exception as e:
                add_log(f"대기 작업 확인 실패: {e}", "warning")
                pending = 0

            if pending == 0:
                add_log("대기 작업이 없습니다. Full Auto 종료.", "success")
                break

            # 잠시 대기 후 다음 라운드
            add_log(f"10초 후 라운드 {round_num + 1} 시작...", "info")
            for _ in range(10):
                if not automation_state["running"]:
                    break
                time.sleep(1)

        add_log(
            f"=== Full Auto 종료 (총 {round_num}라운드) ===\n"
            f"총 성공: {total_success}, 실패: {total_fail}, "
            f"건너뜀: {total_skip}, 좋아요: {total_likes}",
            "success",
        )

    except Exception as e:
        add_log(f"Full Auto 오류: {str(e)}", "error")
    finally:
        # Full Auto 종료 시 유선 복원
        if _fa_ethernet_disabled:
            add_log("유선 인터넷 복원 중...", "info")
            ok, msg = adb_changer.enable_ethernet()
            if ok:
                add_log(f"유선 인터넷 복원 완료: {msg}", "success")
            else:
                add_log(f"유선 복원 실패: {msg}", "warning")

        automation_state["running"] = False
        automation_state["full_auto"] = False
        automation_state["current_task"] = None
        automation_state["stopping"] = False


def _run_reply_automation(limit=0):
    """백그라운드에서 대댓글 자동화를 실행합니다."""
    try:
        with app.app_context():
            _run_reply_automation_inner(limit)
    except Exception as e:
        add_log(f"대댓글 스레드 오류: {str(e)}", "error")
        reply_state["running"] = False


def _run_reply_automation_inner(limit=0):
    import time
    from src.youtube_bot import YouTubeBot

    test_mode = limit > 0

    try:
        mode_label = f"[대댓글 테스트 {limit}건]" if test_mode else "[대댓글 전체 실행]"
        add_log(f"=== 대댓글 자동화 시작 {mode_label} ===", "info")

        notion = _create_notion_manager()
        fingerprint_manager = FingerprintManager()
        reply_user_id = reply_state.get("user_id")
        accounts = load_accounts(user_id=reply_user_id)

        if not accounts:
            add_log("[대댓글] 계정이 없습니다. 대시보드에서 계정을 등록해주세요.", "error")
            reply_state["running"] = False
            return

        tasks = notion.get_reply_pending_tasks()
        if not tasks:
            add_log("[대댓글] 대댓글 대기 작업이 없습니다. (댓글완료 + 대댓글 원고 필요)", "warning")
            reply_state["running"] = False
            return

        if test_mode and len(tasks) > limit:
            add_log(f"[대댓글] 테스트 모드: 전체 {len(tasks)}건 중 {limit}건만 실행", "warning")
            tasks = tasks[:limit]

        reply_state["total"] = len(tasks)
        add_log(f"[대댓글] 총 {len(tasks)}개 작업 발견", "info")

        comment_interval = int(os.getenv("COMMENT_INTERVAL_SEC", "180"))
        delay_ip_change = int(os.getenv("DELAY_AFTER_IP_CHANGE", "3"))
        prev_account_label = None

        for i, task in enumerate(tasks):
            if not reply_state["running"]:
                add_log("[대댓글] 사용자에 의해 중지됨", "warning")
                break

            reply_state["progress"] = i + 1
            task_url_short = task.get("youtube_url", "")[:50]
            reply_state["current_task"] = task_url_short

            # 계정 결정
            account = None
            account_label = task.get("account", "")
            for acc in accounts:
                if acc.get("label") == account_label or acc.get("email", "").startswith(account_label):
                    account = acc
                    break
            if not account:
                account = accounts[0]

            current_label = account.get("label", account.get("email", "unknown"))

            # 대기 시간
            if prev_account_label and prev_account_label != current_label:
                add_log(f"[대댓글] 계정 전환: {prev_account_label} → {current_label} ({delay_ip_change}초 대기)", "info")
                time.sleep(delay_ip_change)
            elif prev_account_label == current_label and i > 0:
                human_delay = safety_rules.get_human_delay("comment")
                actual_delay = human_delay["delay_sec"]
                add_log(
                    f"[대댓글] 같은 계정 연속 - 🧑 {human_delay['description']} "
                    f"(타이핑 {human_delay['typing_delay_ms']}ms)",
                    "info"
                )
                time.sleep(actual_delay)

            bot = YouTubeBot(
                fingerprint_manager=fingerprint_manager,
                account_label=current_label,
                user_id=automation_state.get('user_id'),
            )
            try:
                reply_state["current_task"] = f"[브라우저 시작] {task_url_short}"
                add_log(f"[대댓글] 작업 {i+1}/{len(tasks)}: {task['youtube_url'][:50]}...", "info")
                bot.start_browser()

                reply_state["current_task"] = f"[로그인 중] {current_label}"
                login_ok = bot.login_youtube(account["email"], account["password"])
                if not login_ok:
                    add_log(f"[대댓글] 로그인 실패: {current_label}", "error")
                    reply_state["results"]["fail"] += 1
                    continue

                comment_url = task.get("result_url", "")
                reply_text = task.get("reply_text", "")

                reply_state["current_task"] = f"[대댓글 작성 중] {task_url_short}"
                add_log(f"[대댓글] 대댓글 작성 중: {comment_url[:50]}...", "info")
                success = bot.post_reply(comment_url, reply_text)

                if success:
                    add_log(f"[대댓글] 대댓글 작성 성공", "success")

                    # 노션 업데이트: 상태→대댓글완료 + 대댓글 완료 체크박스
                    reply_state["current_task"] = f"[노션 반영] {task_url_short}"
                    ok, err = notion.update_reply_result(task["page_id"])
                    if ok:
                        add_log("[대댓글] 노션 반영 완료: 대댓글완료", "success")
                    else:
                        add_log(f"[대댓글] 노션 반영 실패: {err}", "warning")

                    reply_state["results"]["success"] += 1
                    add_log(f"[대댓글] --- 작업 {i+1}/{len(tasks)} 완료 ---", "success")
                else:
                    reply_state["results"]["fail"] += 1
                    add_log("[대댓글] 대댓글 작성 실패", "error")

            except Exception as e:
                reply_state["results"]["fail"] += 1
                add_log(f"[대댓글] 오류: {str(e)}", "error")
            finally:
                bot.close_browser()
                prev_account_label = current_label

        add_log(
            f"[대댓글 완료] 성공: {reply_state['results']['success']}, "
            f"실패: {reply_state['results']['fail']}, "
            f"건너뜀: {reply_state['results']['skip']}",
            "success" if reply_state['results']['success'] > 0 else "warning",
        )

    except Exception as e:
        add_log(f"[대댓글] 자동화 오류: {str(e)}", "error")
    finally:
        reply_state["running"] = False
        reply_state["current_task"] = None


# ━━━ 댓글 트래킹 API ━━━

@app.route("/api/tracking/summary")
def api_tracking_summary():
    """트래킹 등록된 댓글 목록과 상태 요약"""
    try:
        summary = comment_tracker.get_summary()
        # 트래킹 사용 제한 정보
        from src.license_client import TRACKING_FREE_LIMIT
        tracking_unlimited = license_client.owner_mode or license_client.can_use_feature("tracking_unlimited")
        tracking_used = _get_tracking_usage_count()

        return jsonify({
            "ok": True,
            "comments": summary,
            "total": len(summary),
            "active": sum(1 for c in summary if c["status"] == "active"),
            "hidden": sum(1 for c in summary if c["status"] == "hidden"),
            "pending_tracking": sum(1 for c in summary if c["status"] == "pending_tracking"),
            "tracking_running": tracking_state["running"],
            "tracking_unlimited": tracking_unlimited,
            "tracking_used": tracking_used,
            "tracking_limit": 0 if tracking_unlimited else TRACKING_FREE_LIMIT,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/tracking/check-all", methods=["POST"])
def api_tracking_check_all():
    """모든 등록된 댓글의 노출 상태를 확인 (백그라운드)"""
    # 트래킹 사용 횟수 제한 체크
    err = _check_tracking_allowed()
    if err:
        return jsonify({"ok": False, "error": err, "upgrade_required": True})

    # Chromium 사전 점검
    from src.comment_tracker import _find_chromium_executable
    chromium_path = _find_chromium_executable()
    if not chromium_path:
        # YouTubeBot의 자동설치 로직 재사용
        try:
            from src.youtube_bot import YouTubeBot
            ready, msg = YouTubeBot.check_chromium_ready()
            if not ready:
                return jsonify({"ok": False, "error": f"Chromium 브라우저가 없습니다. 설정에서 먼저 Chromium을 설치해주세요.\n({msg})"})
        except Exception:
            pass

    with tracking_lock:
        if tracking_state["running"]:
            return jsonify({"ok": False, "error": "이미 트래킹 진행 중입니다."})
        tracking_state["running"] = True
        tracking_state["progress"] = 0

    _increment_tracking_usage()

    def run_tracking():
        try:
            result = comment_tracker.check_all()
            tracking_state["last_result"] = result
            add_log(
                f"[트래킹] 완료: {result['active']}/{result['total']}개 정상노출, "
                f"{result['hidden']}개 숨김",
                "info"
            )
        except Exception as e:
            add_log(f"[트래킹] 오류: {str(e)}", "error")
        finally:
            tracking_state["running"] = False

    thread = threading.Thread(target=run_tracking, daemon=True)
    thread.start()
    return jsonify({"ok": True, "message": "트래킹 시작됨"})


@app.route("/api/tracking/check-selected", methods=["POST"])
def api_tracking_check_selected():
    """선택된 댓글만 트래킹 (백그라운드)"""
    # 트래킹 사용 횟수 제한 체크
    err = _check_tracking_allowed()
    if err:
        return jsonify({"ok": False, "error": err, "upgrade_required": True})

    data = request.get_json(force=True, silent=True) or {}
    comment_ids = data.get("comment_ids", [])
    if not comment_ids:
        return jsonify({"ok": False, "error": "선택된 댓글이 없습니다."})

    _increment_tracking_usage()

    with tracking_lock:
        if tracking_state["running"]:
            return jsonify({"ok": False, "error": "이미 트래킹 진행 중입니다."})
        tracking_state["running"] = True
        tracking_state["progress"] = 0

    def run_selected_tracking():
        try:
            result = comment_tracker.check_selected(comment_ids)
            tracking_state["last_result"] = result
            add_log(
                f"[트래킹] 선택 {len(comment_ids)}건 완료: "
                f"{result['active']}/{result['total']}개 정상노출, "
                f"{result['hidden']}개 숨김",
                "info"
            )
        except Exception as e:
            add_log(f"[트래킹] 오류: {str(e)}", "error")
        finally:
            tracking_state["running"] = False

    thread = threading.Thread(target=run_selected_tracking, daemon=True)
    thread.start()
    return jsonify({"ok": True, "message": f"{len(comment_ids)}건 트래킹 시작됨"})


@app.route("/api/tracking/check/<comment_id>", methods=["POST"])
def api_tracking_check_one(comment_id):
    """개별 댓글 상태 확인"""
    try:
        result = comment_tracker.check_comment(comment_id)
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/tracking/register", methods=["POST"])
def api_tracking_register():
    """댓글을 트래킹 대상으로 등록"""
    data = request.json or {}
    comment_url = data.get("comment_url", "")
    video_url = data.get("video_url", "")
    account_label = data.get("account_label", "")
    comment_text = data.get("comment_text", "")

    if not comment_url:
        return jsonify({"ok": False, "error": "comment_url 필수"})

    ok = comment_tracker.register_comment(
        comment_url, video_url, account_label, comment_text
    )
    return jsonify({"ok": ok})


@app.route("/api/tracking/remove/<comment_id>", methods=["POST"])
def api_tracking_remove(comment_id):
    """트래킹 대상에서 제거"""
    ok = comment_tracker.remove_comment(comment_id)
    return jsonify({"ok": ok})


@app.route("/api/tracking/bulk-remove", methods=["POST"])
def api_tracking_bulk_remove():
    """선택된 댓글 일괄 삭제"""
    data = request.get_json(force=True, silent=True) or {}
    comment_ids = data.get("comment_ids", [])
    if not comment_ids:
        return jsonify({"ok": False, "error": "삭제할 항목이 없습니다"}), 400
    removed = 0
    for cid in comment_ids:
        if comment_tracker.remove_comment(cid):
            removed += 1
    return jsonify({"ok": True, "removed": removed})


@app.route("/api/tracking/status")
def api_tracking_status():
    """트래킹 실행 상태 (실시간 진행 정보 포함)"""
    progress = tracking_state["progress"]
    total = tracking_state["total"]
    started_at = tracking_state.get("started_at")

    # 실시간 소요 시간 + 예상 잔여 시간 계산
    elapsed_sec = 0
    eta_sec = 0
    per_item_sec = 0
    if started_at and tracking_state["running"] and progress > 0:
        elapsed_sec = time.time() - started_at
        per_item_sec = elapsed_sec / progress
        remaining = total - progress
        eta_sec = per_item_sec * remaining

    return jsonify({
        "running": tracking_state["running"],
        "progress": progress,
        "total": total,
        "elapsed_sec": round(elapsed_sec),
        "eta_sec": round(eta_sec),
        "per_item_sec": round(per_item_sec, 1),
        "last_result": tracking_state.get("last_result"),
    })


@app.route("/api/tracking/stop", methods=["POST"])
def api_tracking_stop():
    """트래킹 중지"""
    comment_tracker.stop_tracking()
    return jsonify({"ok": True})


# ━━━ 리포스팅 API ━━━

@app.route("/api/repost", methods=["POST"])
def api_repost():
    """
    블라인드/삭제된 댓글을 다른 계정 + IP 변경 후 리포스팅합니다.

    요청 body:
        comment_ids: [댓글ID 배열] - 리포스팅할 댓글 ID 목록
    """
    if not license_client.can_use_feature("auto_repost"):
        return jsonify({"error": "자동 리포스팅은 Business 플랜부터 사용 가능합니다."}), 403

    with repost_lock:
        if repost_state["running"]:
            return jsonify({"ok": False, "error": "이미 리포스팅 진행 중입니다."})
        repost_state["running"] = True
        repost_state["results"] = {"success": 0, "fail": 0}
        repost_state["progress"] = 0
        repost_state["user_id"] = current_user.id

    data = request.json or {}
    comment_ids = data.get("comment_ids", [])

    if not comment_ids:
        repost_state["running"] = False
        return jsonify({"ok": False, "error": "리포스팅할 댓글을 선택하세요."})

    def run_repost():
        try:
            from src.youtube_bot import YouTubeBot

            accounts = load_accounts(user_id=repost_state.get("user_id"))
            if not accounts:
                add_log("[리포스팅] 사용 가능한 계정이 없습니다.", "error")
                return

            safety_rules = SafetyRules()
            fingerprint_manager = FingerprintManager()
            smm_client = SMMClient()
            adb_changer = ADBIPChanger()

            # 리포스팅 대상 수집
            targets = []
            for cid in comment_ids:
                comment_data = comment_tracker.history["comments"].get(cid)
                if comment_data and comment_data["status"] == "hidden":
                    targets.append((cid, comment_data))

            if not targets:
                add_log("[리포스팅] 블라인드 상태인 댓글이 없습니다.", "warning")
                return

            repost_state["total"] = len(targets)
            add_log(f"[리포스팅] {len(targets)}개 댓글 리포스팅 시작", "info")

            # 원래 작성 계정을 제외한 사용 가능한 계정 목록
            prev_account_label = None

            for i, (cid, comment_data) in enumerate(targets):
                if not repost_state["running"]:
                    add_log("[리포스팅] 사용자에 의해 중지됨", "warning")
                    break

                repost_state["progress"] = i + 1
                video_url = comment_data["video_url"]
                comment_text = comment_data["comment_text"]
                original_account = comment_data["account_label"]

                # ── 스마트 계정 선택 ──
                # 우선순위:
                #   1) 원래 블라인드된 계정 제외
                #   2) 같은 영상에 이미 댓글 단 계정 제외 (중복 방지)
                #   3) 직전에 사용한 계정 회피 (IP 변경 최소화)
                #   4) 남은 일일 횟수가 가장 많은 계정 우선
                video_id = comment_data.get("video_id", "")

                # 해당 영상에 이미 댓글 단 계정 목록 수집
                already_commented = set()
                for _, cdata in comment_tracker.history["comments"].items():
                    if (cdata.get("video_id") == video_id
                            and cdata["status"] in ("active", "reposted")
                            and cdata.get("account_label")):
                        already_commented.add(cdata["account_label"])

                # 1차: 원래 계정 + 이미 해당 영상에 댓글 단 계정 제외
                available = [
                    a for a in accounts
                    if a.get("label") != original_account
                    and a.get("label") not in already_commented
                ]

                # 2차: 1차에서 후보가 없으면 원래 계정만 제외
                if not available:
                    available = [a for a in accounts if a.get("label") != original_account]
                    if available:
                        add_log(f"[리포스팅] 영상 중복 필터 완화: 모든 계정이 이미 해당 영상에 댓글 작성함", "warning")

                # 3차: 그래도 없으면 전체 계정
                if not available:
                    available = accounts

                # 남은 횟수 기준 내림차순 정렬 → 직전 계정은 후순위
                scored = []
                for acc in available:
                    label = acc.get("label", acc.get("email", "unknown"))
                    status = safety_rules.get_account_status(label)
                    remaining = status["remaining"]
                    if remaining <= 0:
                        continue  # 일일 한도 도달 → 스킵

                    # 점수: 남은 횟수가 높을수록 유리, 직전 계정이면 감점
                    score = remaining
                    if label == prev_account_label:
                        score -= 100  # 직전 계정 페널티 (가능하면 다른 계정 우선)

                    scored.append((score, remaining, label, acc))

                scored.sort(key=lambda x: x[0], reverse=True)

                if not scored:
                    add_log(f"[리포스팅] 사용 가능한 계정 없음 (모두 일일 한도 도달)", "error")
                    repost_state["results"]["fail"] += 1
                    continue

                # 최고 점수 계정 선택
                best_score, best_remaining, best_label, best_account = scored[0]
                current_label = best_label
                add_log(
                    f"[리포스팅] 계정 선택: {current_label} (남은횟수:{best_remaining}, "
                    f"후보:{len(scored)}개, 영상중복제외:{len(already_commented)}개)",
                    "debug"
                )
                repost_state["current_task"] = f"[리포스팅] {current_label} → {video_url[:40]}..."

                # IP 변경 (비행기모드)
                if prev_account_label and prev_account_label != current_label:
                    if adb_changer.enabled:
                        old_ip = adb_changer.get_current_ip()
                        add_log(f"[리포스팅] IP 변경 중... (현재: {old_ip or '?'})", "info")
                        success, msg = adb_changer.toggle_airplane_mode()
                        new_ip = adb_changer.get_current_ip()
                        add_log(f"[리포스팅] IP 변경: {old_ip or '?'} → {new_ip or '?'}", "info")
                elif prev_account_label == current_label and i > 0:
                    human_delay = safety_rules.get_human_delay("comment")
                    add_log(f"[리포스팅] 🧑 {human_delay['description']}", "info")
                    time.sleep(human_delay["delay_sec"])

                # 안전 규칙 검사
                passed, reason = safety_rules.check_all_rules(
                    current_label, video_url, comment_text, skip_interval=True
                )
                if not passed:
                    add_log(f"[리포스팅] 안전 규칙 위반: {reason}", "warning")
                    repost_state["results"]["fail"] += 1
                    prev_account_label = current_label
                    continue

                # 브라우저 시작 → 로그인 → 댓글 작성
                bot = YouTubeBot(
                    fingerprint_manager=fingerprint_manager,
                    account_label=current_label,
                )
                try:
                    bot.start_browser()
                    login_ok = bot.login_youtube(best_account["email"], best_account["password"])
                    if not login_ok:
                        add_log(f"[리포스팅] 로그인 실패: {current_label}", "error")
                        repost_state["results"]["fail"] += 1
                        continue

                    # 인간형 타이핑 속도
                    human_delay = safety_rules.get_human_delay("comment")
                    new_comment_url = bot.post_comment(
                        video_url, comment_text,
                        typing_delay_ms=human_delay["typing_delay_ms"]
                    )

                    if new_comment_url:
                        # 성공: 기록 업데이트
                        safety_rules.record_comment(current_label, video_url, comment_text)

                        # 원본 댓글에 리포스팅 기록
                        comment_data["reposted_as"] = new_comment_url
                        comment_data["reposted_by"] = current_label
                        comment_data["reposted_at"] = datetime.now().isoformat()
                        comment_data["status"] = "reposted"

                        # 새 댓글을 트래킹 등록
                        comment_tracker.register_comment(
                            new_comment_url, video_url, current_label, comment_text
                        )
                        comment_tracker._save_history()

                        # 좋아요 주문 (라이선스 서버 경유)
                        if license_client.is_active():
                            repost_qty = int(os.getenv("SMM_LIKE_QUANTITY", "20"))
                            single = license_client.order_likes_via_server(new_comment_url, quantity=repost_qty, source="repost")
                            if single.get("success"):
                                add_log(f"[리포스팅] 좋아요 주문 완료: {single.get('order_id')}", "success")
                                _save_like_order(single.get("order_id"), new_comment_url, repost_qty, source="repost")

                        repost_state["results"]["success"] += 1
                        add_log(
                            f"[리포스팅] 성공! {original_account}→{current_label} | {new_comment_url[:60]}",
                            "success"
                        )
                    else:
                        repost_state["results"]["fail"] += 1
                        add_log(f"[리포스팅] 댓글 작성 실패: {video_url[:50]}", "error")

                except Exception as e:
                    repost_state["results"]["fail"] += 1
                    add_log(f"[리포스팅] 오류: {e}", "error")
                finally:
                    bot.close_browser()

                prev_account_label = current_label

            s = repost_state["results"]["success"]
            f = repost_state["results"]["fail"]
            add_log(f"[리포스팅] 완료: 성공 {s}, 실패 {f}", "info")

        except Exception as e:
            add_log(f"[리포스팅] 전체 오류: {str(e)}", "error")
        finally:
            repost_state["running"] = False
            repost_state["current_task"] = None

    thread = threading.Thread(target=run_repost, daemon=True)
    thread.start()
    return jsonify({"ok": True, "message": f"{len(comment_ids)}개 리포스팅 시작"})


@app.route("/api/repost/stop", methods=["POST"])
def api_repost_stop():
    """리포스팅 중지"""
    repost_state["running"] = False
    return jsonify({"ok": True})


@app.route("/api/repost/status")
def api_repost_status():
    """리포스팅 진행 상태"""
    return jsonify(repost_state)


@app.route("/api/repost/hidden-list")
def api_repost_hidden_list():
    """블라인드 상태인 댓글 목록 (리포스팅 대상)"""
    summary = comment_tracker.get_summary()
    hidden = [c for c in summary if c["status"] == "hidden"]
    return jsonify({"ok": True, "comments": hidden, "count": len(hidden)})


@app.route("/api/tracking/import-notion", methods=["POST"])
def api_tracking_import_notion():
    """
    노션 DB에서 댓글 URL이 있는 작업을 일괄로 트래킹 등록합니다.
    (댓글완료 / 대댓글완료 / 좋아요작업완료 상태의 작업)
    """
    try:
        notion = _create_notion_manager()
        tasks = notion.get_all_tasks()

        imported = 0
        skipped = 0
        no_url = 0

        for task in tasks:
            result_url = task.get("result_url", "")
            if not result_url or "lc=" not in result_url:
                no_url += 1
                continue

            video_url = task.get("youtube_url", "")
            account_label = task.get("account", "")
            comment_text = task.get("comment_text", "")

            ok = comment_tracker.register_comment(
                result_url, video_url, account_label, comment_text
            )
            if ok:
                # 이미 등록된 건 register_comment에서 True 반환하지만 실제론 스킵
                comment_id = comment_tracker._extract_comment_id(result_url)
                existing = comment_tracker.history["comments"].get(comment_id, {})
                if len(existing.get("checks", [])) == 0 and existing.get("registered_at", "") >= datetime.now().strftime("%Y-%m-%d"):
                    imported += 1
                else:
                    skipped += 1
            else:
                skipped += 1

        return jsonify({
            "ok": True,
            "imported": imported,
            "skipped": skipped,
            "no_url": no_url,
            "total_tasks": len(tasks),
            "message": f"노션에서 {imported}개 신규 등록, {skipped}개 이미 등록됨, {no_url}개 댓글URL 없음",
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ━━━ 매일 아침 8시 자동 트래킹 스케줄러 ━━━
_scheduler_started = False


def _daily_tracking_job():
    """매일 아침 8시에 전체 댓글 트래킹을 실행합니다."""
    # 라이선스 미인증 시 자동 트래킹 스킵
    if not license_client.owner_mode and not license_client.is_active():
        add_log("[스케줄] 라이선스 미인증 → 자동 트래킹 스킵", "warning")
        return

    with tracking_lock:
        if tracking_state["running"]:
            add_log("[스케줄] 이미 트래킹 진행 중 - 스킵", "warning")
            return
        tracking_state["running"] = True

    try:
        add_log("[스케줄] 매일 아침 자동 트래킹 시작", "info")
        result = comment_tracker.check_all()
        tracking_state["last_result"] = result
        add_log(
            f"[스케줄] 자동 트래킹 완료: {result['active']}/{result['total']}개 정상노출, "
            f"{result['hidden']}개 숨김",
            "info"
        )
    except Exception as e:
        add_log(f"[스케줄] 자동 트래킹 오류: {str(e)}", "error")
    finally:
        tracking_state["running"] = False


def _check_pending_tracking():
    """4시간이 지난 'pending_tracking' 댓글을 자동으로 트래킹합니다."""
    # 라이선스 미인증 시 자동 트래킹 스킵
    if not license_client.owner_mode and not license_client.is_active():
        return

    try:
        summary = comment_tracker.get_summary()
        pending = [c for c in summary if c["status"] == "pending_tracking"]
        if not pending:
            return

        now = datetime.now()
        ready_ids = []
        for c in pending:
            registered = datetime.fromisoformat(c.get("registered_at", now.isoformat()))
            elapsed_hours = (now - registered).total_seconds() / 3600
            if elapsed_hours >= 4:
                ready_ids.append(c["comment_id"])

        if not ready_ids:
            return

        with tracking_lock:
            if tracking_state["running"]:
                return  # 이미 트래킹 중이면 다음 주기에 실행
            tracking_state["running"] = True
            tracking_state["progress"] = 0

        add_log(f"[자동 트래킹] {len(ready_ids)}건 트래킹 예정 → 확인 시작 (4시간 경과)", "info")

        def run_delayed():
            try:
                result = comment_tracker.check_selected(ready_ids)
                tracking_state["last_result"] = result
                add_log(
                    f"[자동 트래킹] 완료: {result.get('active', 0)}개 정상노출, "
                    f"{result.get('hidden', 0)}개 숨김",
                    "info"
                )
            except Exception as e:
                add_log(f"[자동 트래킹] 오류: {str(e)}", "error")
            finally:
                tracking_state["running"] = False

        thread = threading.Thread(target=run_delayed, daemon=True)
        thread.start()

    except Exception as e:
        add_log(f"[자동 트래킹] 예정 확인 오류: {str(e)}", "error")


def _start_scheduler():
    """백그라운드 스케줄러 스레드 시작 (구독 플랜 유저만)"""
    global _scheduler_started
    if _scheduler_started:
        return

    # 스케줄러는 구독 플랜(Business 이상)에서만 실행
    plan = (license_client.get_plan_name() or "Free")
    if plan == "Free" and not license_client.owner_mode:
        return  # Free 유저는 스케줄러 비활성

    _scheduler_started = True

    def scheduler_loop():
        import time as _time
        last_run_date = None
        target_hour = 8  # 매일 오전 8시

        while True:
            now = datetime.now()
            today = now.date()

            # 매일 오전 8시 전체 트래킹
            if now.hour == target_hour and last_run_date != today:
                last_run_date = today
                add_log(f"[스케줄] 오전 {target_hour}시 자동 트래킹 실행", "info")
                try:
                    _daily_tracking_job()
                except Exception as e:
                    add_log(f"[스케줄] 오류: {str(e)}", "error")

            # 10분마다 트래킹 예정 댓글 확인 (4시간 경과 체크)
            try:
                _check_pending_tracking()
            except Exception:
                pass

            _time.sleep(600)  # 10분마다 체크

    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()
    add_log("[스케줄] 스케줄러 시작됨 (매일 8시 전체 트래킹 + 10분마다 예정 트래킹 확인)", "info")


# ──────────────────────────── 라이선스 & 셋업 ────────────────────────────

@app.route("/setup")
def setup_page():
    """셋업 위자드 페이지."""
    return render_template("setup.html", notion_template_url=os.environ.get("NOTION_TEMPLATE_URL", "https://quirky-watch-e9e.notion.site/31e35cfd83d380a08acde2a493d047f7?source=copy_link"))


@app.route("/api/license/status")
def api_license_status():
    """현재 라이선스 상태 반환 (refresh=1이면 서버에서 최신 정보 조회)."""
    # DB 플랜 동기화 (관리자가 변경한 경우 즉시 반영)
    plan_name, token_balance, max_accounts, synced_features = _sync_plan_from_db()

    feature_keys = ["like_preview", "auto_repost", "duplicate_scan", "rank_check",
                    "auto_exposure_schedule", "multi_account_parallel",
                    "task_scheduling", "tracking_unlimited"]
    if synced_features:
        features = {feat: synced_features.get(feat, False) for feat in feature_keys}
    else:
        features = {feat: license_client.can_use_feature(feat) for feat in feature_keys}

    return jsonify({
        "active": license_client.is_active() or (plan_name and plan_name != "Free"),
        "owner_mode": license_client.owner_mode,
        "plan": plan_name,
        "max_accounts": max_accounts,
        "token_balance": token_balance,
        "like_credit_balance": license_client.like_credit_balance or 0,
        "license_key": (license_client.license_key or "")[:8] + "..." if license_client.license_key else None,
        "features": features,
    })


@app.route("/api/admin/toggle", methods=["POST"])
def api_admin_toggle():
    """관리자 모드 전환 (비밀 키 검증)."""
    global _admin_view_active
    data = request.get_json() or {}
    admin_key = data.get("admin_key", "").strip()

    # 관리자 비밀 키 검증
    expected_key = os.environ.get("ADMIN_SECRET_KEY")
    if not expected_key:
        return jsonify({"error": "관리자 기능을 사용할 수 없습니다."}), 403
    if admin_key != expected_key:
        return jsonify({"error": "관리자 인증 실패"}), 403

    _admin_view_active = not _admin_view_active
    return jsonify({
        "success": True,
        "admin_view": _admin_view_active,
    })


@app.route("/api/admin/status")
def api_admin_status():
    """현재 관리자 뷰 상태."""
    return jsonify({
        "admin_view": _admin_view_active,
    })


@app.route("/api/admin/users")
def api_admin_users():
    """관리자용: 가입 유저 목록 조회."""
    if not _admin_view_active:
        return jsonify({"error": "관리자 권한이 필요합니다."}), 403

    users = User.query.order_by(User.created_at.desc()).all()
    return jsonify({
        "users": [{
            "id": u.id,
            "email": u.email,
            "nickname": u.nickname,
            "plan": getattr(u, "plan", None) or "Free",
            "license_key": (u.license_key or "")[:12] + "..." if u.license_key else None,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login": u.last_login.isoformat() if u.last_login else None,
            "is_active": u.is_active_user,
        } for u in users],
        "total": len(users),
        "plans": User.VALID_PLANS,
    })


@app.route("/api/admin/activity-log")
def api_admin_activity_log():
    """관리자용: 유저 활동 로그 조회."""
    if not _admin_view_active:
        return jsonify({"error": "관리자 권한이 필요합니다."}), 403

    limit = request.args.get("limit", 100, type=int)
    logs = UserActivityLog.query.order_by(
        UserActivityLog.created_at.desc()
    ).limit(limit).all()
    return jsonify({
        "logs": [log.to_dict() for log in logs],
        "total": len(logs),
    })


@app.route("/api/admin/stats")
def api_admin_stats():
    """관리자용: 사용자별 댓글 통계 (오늘/전체 댓글 수, 성공률, 마지막 실행)."""
    if not _admin_view_active:
        return jsonify({"error": "관리자 권한이 필요합니다."}), 403

    from sqlalchemy import func

    today = datetime.utcnow().strftime("%Y-%m-%d")
    today_start = datetime.strptime(today, "%Y-%m-%d")

    # 사용자별 전체 댓글 수
    total_by_user = db.session.query(
        CommentHistory.user_id,
        func.count(CommentHistory.id).label("total_comments")
    ).group_by(CommentHistory.user_id).all()

    # 사용자별 오늘 댓글 수
    today_by_user = db.session.query(
        CommentHistory.user_id,
        func.count(CommentHistory.id).label("today_comments")
    ).filter(CommentHistory.created_at >= today_start).group_by(CommentHistory.user_id).all()

    # 사용자별 자동화 성공/실패 수
    success_by_user = db.session.query(
        AutomationLog.user_id,
        func.count(AutomationLog.id).label("count")
    ).filter(AutomationLog.action == "comment_post").group_by(AutomationLog.user_id).all()

    fail_by_user = db.session.query(
        AutomationLog.user_id,
        func.count(AutomationLog.id).label("count")
    ).filter(AutomationLog.action == "comment_fail").group_by(AutomationLog.user_id).all()

    # 사용자별 마지막 자동화 실행 시간
    last_run_by_user = db.session.query(
        AutomationLog.user_id,
        func.max(AutomationLog.created_at).label("last_run")
    ).filter(AutomationLog.action.in_(["automation_start", "comment_post"])).group_by(AutomationLog.user_id).all()

    # 조합
    total_map = {r.user_id: r.total_comments for r in total_by_user}
    today_map = {r.user_id: r.today_comments for r in today_by_user}
    success_map = {r.user_id: r.count for r in success_by_user}
    fail_map = {r.user_id: r.count for r in fail_by_user}
    last_run_map = {r.user_id: r.last_run.isoformat() if r.last_run else None for r in last_run_by_user}

    users = User.query.all()
    stats = []
    for u in users:
        s = success_map.get(u.id, 0)
        f = fail_map.get(u.id, 0)
        rate = round(s / (s + f) * 100, 1) if (s + f) > 0 else 0
        stats.append({
            "user_id": u.id,
            "email": u.email,
            "plan": u.plan or "Free",
            "total_comments": total_map.get(u.id, 0),
            "today_comments": today_map.get(u.id, 0),
            "success_count": s,
            "fail_count": f,
            "success_rate": rate,
            "last_run": last_run_map.get(u.id),
        })

    return jsonify({"stats": stats, "total_users": len(stats)})


@app.route("/api/admin/revenue")
def api_admin_revenue():
    """관리자용: 수익 대시보드 — 서버 데이터 우선, 로컬 폴백."""
    if not _admin_view_active:
        return jsonify({"error": "관리자 권한이 필요합니다."}), 403

    # 서버에서 전체 유저 수익 데이터 조회 시도
    period = request.args.get("period", "all")
    try:
        import requests as _req
        admin_key = os.environ.get("ADMIN_SECRET_KEY", "")
        resp = _req.get(
            f"{CENTRAL_SERVER_URL}/api/admin/revenue",
            headers={"X-Admin-Key": admin_key},
            params={"period": period},
            timeout=10
        )
        if resp.status_code == 200:
            server_data = resp.json()
            # SMM 잔액 추가
            try:
                smm_resp = _req.get(f"{CENTRAL_SERVER_URL}/api/admin/smm-balance",
                    headers={"X-Admin-Key": admin_key}, timeout=5)
                if smm_resp.status_code == 200:
                    server_data["smm_balance"] = smm_resp.json().get("balance", "0")
            except Exception:
                pass
            return jsonify(server_data)
    except Exception:
        pass

    # 서버 실패 시 로컬 폴백
    smm_cost_per_1000 = float(os.getenv("SMM_COST_PER_1000", "0.5"))
    orders = LikeOrder.query.all()
    total_revenue = 0
    total_quantity = 0
    total_orders = len(orders)
    by_user = {}
    by_tier = {}

    for o in orders:
        total_revenue += (o.cost or 0)
        total_quantity += (o.quantity or 0)
        uid = o.user_id
        if uid not in by_user:
            user = db.session.get(User, uid)
            by_user[uid] = {"email": user.email if user else f"user#{uid}", "revenue": 0, "quantity": 0, "orders": 0}
        by_user[uid]["revenue"] += (o.cost or 0)
        by_user[uid]["quantity"] += (o.quantity or 0)
        by_user[uid]["orders"] += 1
        tier = o.tier or "standard"
        if tier not in by_tier:
            by_tier[tier] = {"revenue": 0, "quantity": 0, "orders": 0}
        by_tier[tier]["revenue"] += (o.cost or 0)
        by_tier[tier]["quantity"] += (o.quantity or 0)
        by_tier[tier]["orders"] += 1

    smm_total_cost_usd = (total_quantity / 1000) * smm_cost_per_1000
    exchange_rate = float(os.getenv("USD_KRW_RATE", "1350"))
    smm_total_cost_krw = int(smm_total_cost_usd * exchange_rate)

    margin = total_revenue - smm_total_cost_krw
    margin_rate = round((margin / total_revenue * 100), 1) if total_revenue > 0 else 0

    return jsonify({
        "total_revenue": total_revenue,          # 총 판매 수익 (원)
        "total_quantity": total_quantity,         # 총 좋아요 수량
        "total_orders": total_orders,            # 총 주문 건수
        "smm_cost_usd": round(smm_total_cost_usd, 2),  # SMM 비용 (USD)
        "smm_cost_krw": smm_total_cost_krw,      # SMM 비용 (원)
        "margin": margin,                        # 마진 (원)
        "margin_rate": margin_rate,              # 마진율 (%)
        "exchange_rate": exchange_rate,           # 환율
        "smm_cost_per_1000": smm_cost_per_1000,  # 1000개당 비용
        "by_user": sorted(by_user.values(), key=lambda x: x["revenue"], reverse=True),
        "by_tier": by_tier,
    })


@app.route("/api/admin/automation-logs")
def api_admin_automation_logs():
    """관리자용: 자동화 실행 로그 조회 (영속 저장된 로그)."""
    if not _admin_view_active:
        return jsonify({"error": "관리자 권한이 필요합니다."}), 403

    limit = request.args.get("limit", 200, type=int)
    user_id = request.args.get("user_id", type=int)
    action = request.args.get("action")  # comment_post, comment_fail, automation_start, etc.

    query = AutomationLog.query
    if user_id:
        query = query.filter(AutomationLog.user_id == user_id)
    if action:
        query = query.filter(AutomationLog.action == action)

    logs = query.order_by(AutomationLog.created_at.desc()).limit(limit).all()
    return jsonify({
        "logs": [log.to_dict() for log in logs],
        "total": len(logs),
    })


@app.route("/api/admin/comment-history")
def api_admin_comment_history():
    """관리자용: 사용자별 댓글 히스토리 조회."""
    if not _admin_view_active:
        return jsonify({"error": "관리자 권한이 필요합니다."}), 403

    limit = request.args.get("limit", 100, type=int)
    user_id = request.args.get("user_id", type=int)
    date = request.args.get("date")  # YYYY-MM-DD

    query = CommentHistory.query
    if user_id:
        query = query.filter(CommentHistory.user_id == user_id)
    if date:
        try:
            date_start = datetime.strptime(date, "%Y-%m-%d")
            date_end = date_start + timedelta(days=1)
            query = query.filter(CommentHistory.created_at >= date_start, CommentHistory.created_at < date_end)
        except ValueError:
            pass

    records = query.order_by(CommentHistory.created_at.desc()).limit(limit).all()
    return jsonify({
        "records": [r.to_dict() for r in records],
        "total": len(records),
    })


@app.route("/api/admin/update-user", methods=["POST"])
def api_admin_update_user():
    """관리자용: 유저 플랜/상태 변경."""
    if not _admin_view_active:
        return jsonify({"error": "관리자 권한이 필요합니다."}), 403

    data = request.get_json() or {}
    user_id = data.get("user_id")
    if not user_id:
        return jsonify({"error": "user_id가 필요합니다."}), 400

    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "유저를 찾을 수 없습니다."}), 404

    # 플랜 변경
    new_plan = data.get("plan")
    if new_plan:
        if new_plan not in User.VALID_PLANS:
            return jsonify({"error": f"유효하지 않은 플랜: {new_plan}"}), 400
        user.plan = new_plan

        # license_client 캐시 즉시 동기화 (사용자 모드 전환 시 바로 반영)
        plan_key = new_plan.lower()
        if plan_key in PLAN_FEATURES:
            if license_client.license_info is None:
                license_client.license_info = {}
            license_client.license_info["plan"] = new_plan
            license_client.license_info["plan_name"] = plan_key
            license_client.license_info["max_accounts"] = PLAN_MAX_ACCOUNTS.get(plan_key, 3)
            license_client.license_info["is_permanent"] = (plan_key == "enterprise")
            license_client.token_balance = PLAN_TOKENS.get(plan_key, 0)

    # 활성/비활성 변경
    if "is_active" in data:
        user.is_active_user = bool(data["is_active"])

    db.session.commit()

    # 관리자 변경 활동 로그
    changes = []
    if new_plan:
        changes.append(f"플랜→{new_plan}")
    if "is_active" in data:
        changes.append(f"상태→{'활성' if user.is_active_user else '비활성'}")
    log = UserActivityLog(
        user_id=user.id,
        email=user.email,
        action="admin_update",
        detail=f"관리자 변경: {', '.join(changes)}",
        ip_address=request.headers.get("X-Forwarded-For", request.remote_addr),
    )
    db.session.add(log)
    db.session.commit()

    # 서버에도 동기화
    try:
        import requests as _req
        server_data = {"email": user.email}
        if new_plan:
            server_data["plan"] = new_plan
        if "is_active" in data:
            server_data["is_active"] = user.is_active_user
        _req.post(
            f"{CENTRAL_SERVER_URL}/api/admin/update-user",
            json=server_data,
            headers={"X-Admin-Key": os.environ.get("ADMIN_SECRET_KEY", "")},
            timeout=5,
        )
    except Exception:
        pass

    return jsonify({
        "success": True,
        "user": {
            "id": user.id,
            "email": user.email,
            "plan": user.plan,
            "is_active": user.is_active_user,
        },
    })


@app.route("/api/admin/grant-credit", methods=["POST"])
def api_admin_grant_credit():
    """관리자용: 유저에게 토큰/좋아요 크레딧을 임의로 지급합니다."""
    if not _admin_view_active:
        return jsonify({"error": "관리자 권한이 필요합니다."}), 403

    data = request.get_json() or {}
    user_id = data.get("user_id")
    tokens = int(data.get("tokens", 0))
    like_credits = int(data.get("like_credits", 0))

    if not user_id:
        return jsonify({"error": "user_id가 필요합니다."}), 400
    if tokens == 0 and like_credits == 0:
        return jsonify({"error": "tokens 또는 like_credits를 지정해주세요."}), 400

    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "유저를 찾을 수 없습니다."}), 404

    results = []

    # 토큰 지급 — 대상 유저의 DB에 직접 저장
    if tokens != 0:
        user.token_balance = (user.token_balance or 0) + tokens
        results.append(f"토큰 {'+' if tokens > 0 else ''}{tokens:,}")

    # 좋아요 크레딧 지급 — 대상 유저의 DB에 직접 저장
    if like_credits != 0:
        user.like_credit_balance = (user.like_credit_balance or 0) + like_credits
        results.append(f"좋아요 크레딧 {'+' if like_credits > 0 else ''}{like_credits:,}원")

    # 현재 로그인 유저가 대상이면 캐시도 동기화
    try:
        if current_user.is_authenticated and current_user.id == user.id:
            license_client.token_balance = user.token_balance
            license_client.like_credit_balance = user.like_credit_balance
    except Exception:
        pass

    # 활동 로그
    detail = f"관리자 크레딧 지급: {', '.join(results)} → {user.email}"
    log = UserActivityLog(
        user_id=user.id, email=user.email, action="admin_grant_credit",
        detail=detail, ip_address=request.headers.get("X-Forwarded-For", request.remote_addr),
    )
    db.session.add(log)
    db.session.commit()

    add_log(f"[관리자] {detail}", "success")

    # 서버에도 동기화
    try:
        import requests as _req
        server_data = {"email": user.email}
        if tokens != 0:
            server_data["add_tokens"] = tokens
        if like_credits != 0:
            server_data["add_credits"] = like_credits
        _req.post(
            f"{CENTRAL_SERVER_URL}/api/admin/update-user",
            json=server_data,
            headers={"X-Admin-Key": os.environ.get("ADMIN_SECRET_KEY", "")},
            timeout=5,
        )
    except Exception:
        pass

    return jsonify({
        "success": True,
        "granted": {"tokens": tokens, "like_credits": like_credits},
        "balance": {
            "tokens": user.token_balance,
            "like_credits": user.like_credit_balance,
        },
    })


@app.route("/api/license/activate", methods=["POST"])
def api_license_activate():
    """라이선스 키 활성화."""
    data = request.get_json() or {}
    key = data.get("license_key", "").strip()
    if not key:
        return jsonify({"error": "라이선스 키를 입력해주세요."}), 400

    result = license_client.activate(key)

    # 로그인 유저에게 라이선스 키 자동 바인딩
    if current_user.is_authenticated and result.get("success"):
        current_user.license_key = key
        db.session.commit()

    return jsonify(result)


@app.route("/api/license/features")
def api_license_features():
    """현재 플랜에서 사용 가능한 기능 목록."""
    features = {
        "comment_post": {"name": "댓글 작성", "available": license_client.can_use_feature("comment_post")},
        "exposure_check_manual": {"name": "수동 노출 확인", "available": license_client.can_use_feature("exposure_check_manual")},
        "notion_sync": {"name": "노션 연동", "available": license_client.can_use_feature("notion_sync")},
        "auto_repost": {"name": "자동 리포스팅", "available": license_client.can_use_feature("auto_repost"), "min_plan": "Business"},
        "rank_check": {"name": "순위 체크", "available": license_client.can_use_feature("rank_check"), "min_plan": "Business"},
        "duplicate_scan": {"name": "중복 스캔", "available": license_client.can_use_feature("duplicate_scan"), "min_plan": "Business"},
        "like_boost": {"name": "좋아요 부스팅", "available": license_client.can_use_feature("like_boost"), "min_plan": "Business"},
        "like_preview": {"name": "좋아요 수량 미리보기", "available": license_client.can_use_feature("like_preview"), "min_plan": "Business"},
        "auto_exposure_schedule": {"name": "자동 노출 체크 스케줄", "available": license_client.can_use_feature("auto_exposure_schedule"), "min_plan": "Agency"},
        "multi_account_parallel": {"name": "다중 계정 동시 작업", "available": license_client.can_use_feature("multi_account_parallel"), "min_plan": "Agency"},
        "task_scheduling": {"name": "작업 예약", "available": license_client.can_use_feature("task_scheduling"), "min_plan": "Agency"},
        "api_access": {"name": "API 접근", "available": license_client.can_use_feature("api_access"), "min_plan": "Enterprise"},
    }
    return jsonify(features)


@app.route("/api/mypage")
def api_mypage():
    """마이페이지 정보 반환."""
    # DB 플랜 우선 동기화 (관리자가 변경한 경우 반영)
    plan, balance, max_accounts, synced_features = _sync_plan_from_db()

    # features 구성 (synced가 있으면 사용, 아니면 license_client에서 조회)
    feature_keys = ["comment_post", "auto_repost", "rank_check", "duplicate_scan",
                    "like_boost", "auto_exposure_schedule", "multi_account_parallel",
                    "task_scheduling", "api_access"]
    if synced_features:
        features = {feat: synced_features.get(feat, False) for feat in feature_keys}
    else:
        features = {feat: license_client.can_use_feature(feat) for feat in feature_keys}

    # 토큰 비용 정보
    from src.license_client import TOKEN_COSTS
    token_prices = [
        {"tokens": 500, "price": 15000, "label": "500 토큰", "per_token": 30},
        {"tokens": 1200, "price": 30000, "label": "1,200 토큰", "per_token": 25, "popular": True},
        {"tokens": 3000, "price": 60000, "label": "3,000 토큰", "per_token": 20, "discount": "33%"},
        {"tokens": 7000, "price": 105000, "label": "7,000 토큰", "per_token": 15, "discount": "50%"},
    ]

    return jsonify({
        "plan": plan or "미인증",
        "owner_mode": license_client.owner_mode,
        "token_balance": balance,
        "max_accounts": max_accounts,
        "license_key": (license_client.license_key or "")[:8] + "..." if license_client.license_key else None,
        "features": features,
        "token_costs": TOKEN_COSTS,
        "token_prices": token_prices,
        "like_tiers": LIKE_TIERS,
    })


@app.route("/api/mypage/usage-history")
def api_mypage_usage_history():
    """토큰 사용/충전 내역 조회 (로컬 DB)."""
    return jsonify({"usage": [], "purchases": [], "balance": license_client.token_balance})


@app.route("/api/mypage/purchase-tokens", methods=["POST"])
def api_mypage_purchase_tokens():
    """토큰 충전 — 수동 결제 모드: 관리자만 토큰 추가 가능."""
    # 관리자 인증 필수 (클라이언트 직접 호출 차단)
    admin_key = request.headers.get("X-Admin-Key", "")
    if admin_key != os.environ.get("ADMIN_SECRET_KEY", ""):
        return jsonify({"error": "결제 시스템 준비 중입니다. 관리자에게 문의해주세요."}), 403

    data = request.get_json() or {}
    tokens = int(data.get("tokens", 0))
    if tokens <= 0:
        return jsonify({"error": "유효하지 않은 요청"}), 400

    license_client.token_balance += tokens
    license_client.save_balances()
    return jsonify({"success": True, "balance": license_client.token_balance})


@app.route("/api/like-boost/tiers")
def api_like_boost_tiers():
    """좋아요 부스팅 티어 정보."""
    return jsonify(LIKE_TIERS)


@app.route("/api/like-credits/balance")
def api_like_credit_balance():
    """좋아요 크레딧 잔액 조회."""
    balance = license_client.get_like_credit_balance()
    return jsonify({"balance": balance})


@app.route("/api/like-credits/history")
def api_like_credit_history():
    """좋아요 크레딧 충전/주문 이력 조회."""
    data = license_client.get_like_orders()
    return jsonify(data)


@app.route("/api/like-boost/order", methods=["POST"])
def api_like_boost_order():
    """좋아요 부스팅 주문 (라이선스 서버를 통해 크레딧 차감 + SMM 대행)."""
    if not license_client.can_use_feature("like_boost"):
        return jsonify({"error": "좋아요 부스팅은 Business 플랜부터 사용 가능합니다. 구독을 활성화해주세요."}), 403

    data = request.get_json() or {}
    comment_url = data.get("comment_url", "").strip()
    quantity = int(data.get("quantity", 10))
    tier = data.get("tier", "standard")
    if tier not in ("basic", "standard", "premium"):
        return jsonify({"error": "잘못된 티어입니다."}), 400

    if not comment_url:
        return jsonify({"error": "댓글 URL을 입력해주세요."}), 400
    if quantity < 10:
        return jsonify({"error": "최소 10개부터 주문 가능합니다."}), 400

    # 비용 계산 (원)
    cost = license_client.get_like_cost(quantity, tier)
    tier_info = LIKE_TIERS.get(tier, {})

    # 라이선스 서버를 통해 주문 (크레딧 차감 + SMM 대행)
    result = license_client.order_likes_via_server(comment_url, quantity, tier=tier, source="boost")

    if result.get("success"):
        # 로컬 DB에도 주문 이력 저장 (대시보드 표시용)
        try:
            order = LikeOrder(
                user_id=current_user.id,
                order_id=str(result.get("order_id", "")),
                comment_url=comment_url,
                quantity=quantity,
                tier=tier,
                cost=cost,
                status="Pending",
                source="boost",
            )
            db.session.add(order)
            db.session.commit()
        except Exception:
            pass

        return jsonify({
            "success": True,
            "order_id": result.get("order_id"),
            "cost": cost,
            "remaining_credits": result.get("remaining_credits", 0),
            "tier": tier_info.get("name", tier),
            "quantity": quantity,
        })
    else:
        error_msg = result.get("error", "주문 실패")
        status_code = 402 if "크레딧 부족" in error_msg else 500
        return jsonify({"error": error_msg, "balance_required": result.get("balance_required")}), status_code


@app.route("/api/like-orders")
@login_required
def api_like_orders():
    """좋아요 주문 이력 조회."""
    orders = LikeOrder.query.filter_by(user_id=current_user.id).order_by(LikeOrder.created_at.desc()).limit(50).all()
    return jsonify({"orders": [o.to_dict() for o in orders]})


@app.route("/api/like-orders/refresh-status", methods=["POST"])
@login_required
def api_like_orders_refresh_status():
    """SMM Kings API에서 직접 주문 상태를 조회하여 업데이트합니다."""
    from src.smm_client import SMMClient
    from src.license_client import _SMM_GLOBAL_CONFIG

    orders = LikeOrder.query.filter_by(user_id=current_user.id).filter(
        LikeOrder.status.in_(["Pending", "In progress", "Processing"])
    ).all()

    if not orders:
        return jsonify({"updated": 0, "message": "업데이트할 진행중 주문이 없습니다."})

    # SMM API로 직접 상태 조회
    smm = SMMClient(
        api_key=_SMM_GLOBAL_CONFIG.get("api_key"),
        enabled=True,
        service_id=_SMM_GLOBAL_CONFIG.get("service_id"),
    )
    order_ids = [o.order_id for o in orders]
    statuses = smm.check_multiple_orders(order_ids)

    updated = 0
    for order in orders:
        status_data = statuses.get(str(order.order_id)) or statuses.get(order.order_id)
        if status_data and isinstance(status_data, dict):
            new_status = status_data.get("status", order.status)
            order.status = new_status
            order.remains = status_data.get("remains")
            updated += 1

    if updated:
        db.session.commit()

    return jsonify({"updated": updated, "message": f"{updated}건 상태 업데이트 완료"})


@app.route("/api/setup/check")
def api_setup_check():
    """셋업 완료 상태 확인."""
    load_dotenv(override=True)

    license_ok = license_client.is_active()
    notion_token = os.getenv("NOTION_API_TOKEN", "")
    notion_db = os.getenv("NOTION_DATABASE_ID", "")
    notion_ok = bool(notion_token and notion_db)

    # 노션 연결 실제 테스트
    notion_verified = False
    if notion_ok:
        try:
            from notion_client import Client
            client = Client(auth=notion_token)
            client.databases.retrieve(database_id=notion_db)
            notion_verified = True
        except Exception:
            pass

    # 계정 등록 확인
    accounts = json.loads(os.getenv("YOUTUBE_ACCOUNTS", "[]"))
    accounts_ok = len(accounts) > 0

    return jsonify({
        "license": {"ok": license_ok, "plan": license_client.get_plan_name()},
        "notion": {"ok": notion_ok, "verified": notion_verified, "has_token": bool(notion_token), "has_db": bool(notion_db)},
        "accounts": {"ok": accounts_ok, "count": len(accounts)},
        "all_done": license_ok and notion_verified and accounts_ok,
    })


@app.route("/api/setup/notion/save", methods=["POST"])
def api_setup_notion_save():
    """노션 API 키 및 DB ID 저장."""
    data = request.get_json() or {}
    token = data.get("notion_token", "").strip()
    db_id = data.get("database_id", "").strip()

    if not token:
        return jsonify({"error": "노션 API 토큰을 입력해주세요."}), 400

    # 연결 테스트
    try:
        from notion_client import Client
        client = Client(auth=token)
        user_info = client.users.me()
    except Exception as e:
        return jsonify({"error": f"노션 연결 실패: {str(e)}"}), 400

    # .env 파일 업데이트
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    _update_env_var(env_path, "NOTION_API_TOKEN", token)
    if db_id:
        _update_env_var(env_path, "NOTION_DATABASE_ID", db_id)

    return jsonify({"success": True, "user": user_info.get("name", "연결됨")})


@app.route("/api/setup/notion/create-template", methods=["POST"])
def api_setup_notion_create_template():
    """노션 DB 템플릿을 자동 생성합니다."""
    data = request.get_json() or {}
    token = data.get("notion_token", "").strip()
    parent_page_id = data.get("parent_page_id", "").strip()

    if not token:
        return jsonify({"error": "노션 API 토큰이 필요합니다."}), 400
    if not parent_page_id:
        return jsonify({"error": "상위 페이지 ID가 필요합니다."}), 400

    try:
        from notion_client import Client
        client = Client(auth=token)

        # 댓글 작업용 DB 생성
        new_db = client.databases.create(
            parent={"type": "page_id", "page_id": parent_page_id},
            title=[{"type": "text", "text": {"content": "댓글 부스터"}}],
            properties={
                "작업명": {"title": {}},
                "영상 링크": {"url": {}},
                "댓글 원고": {"rich_text": {}},
                "대댓글 원고": {"rich_text": {}},
                "댓글 url": {"url": {}},
                "상태": {
                    "select": {
                        "options": [
                            {"name": "댓글작업전", "color": "gray"},
                            {"name": "댓글완료", "color": "green"},
                            {"name": "대댓글완료", "color": "blue"},
                            {"name": "좋아요완료", "color": "purple"},
                            {"name": "작업실패", "color": "red"},
                        ]
                    }
                },
                "댓글 계정": {"rich_text": {}},
                "댓글 완료": {"checkbox": {}},
                "대댓글 완료": {"checkbox": {}},
                "좋아요 완료": {"checkbox": {}},
            },
        )

        db_id = new_db["id"]

        # .env에 자동 저장
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        _update_env_var(env_path, "NOTION_API_TOKEN", token)
        _update_env_var(env_path, "NOTION_DATABASE_ID", db_id)

        # 샘플 데이터 1건 추가
        try:
            client.pages.create(
                parent={"database_id": db_id},
                properties={
                    "작업명": {"title": [{"text": {"content": "[샘플] 테스트 영상 댓글"}}]},
                    "영상 링크": {"url": "https://www.youtube.com/watch?v=SAMPLE"},
                    "댓글 원고": {"rich_text": [{"text": {"content": "이것은 샘플 댓글입니다. 수정 후 사용하세요."}}]},
                    "상태": {"select": {"name": "댓글작업전"}},
                },
            )
        except Exception:
            pass

        return jsonify({
            "success": True,
            "database_id": db_id,
            "url": new_db.get("url", ""),
            "message": "DB 템플릿이 생성되었습니다!",
        })

    except Exception as e:
        return jsonify({"error": f"DB 생성 실패: {str(e)}"}), 500


@app.route("/api/setup/notion/status-options")
def api_notion_status_options():
    """노션 DB의 실제 상태 옵션값을 조회합니다."""
    load_dotenv(override=True)
    token = os.getenv("NOTION_API_TOKEN", "")
    db_id = os.getenv("NOTION_DATABASE_ID", "")
    col_status = os.getenv("NOTION_COLUMN_STATUS", "상태")

    if not token or not db_id:
        return jsonify({"error": "노션 설정이 필요합니다."}), 400

    try:
        from notion_client import Client
        client = Client(auth=token)
        db_info = client.databases.retrieve(database_id=db_id)
        props = db_info.get("properties", {})

        # 상태 컬럼 찾기
        status_prop = props.get(col_status)
        if not status_prop:
            for name, prop in props.items():
                if "상태" in name or "status" in name.lower():
                    status_prop = prop
                    col_status = name
                    break

        if not status_prop:
            return jsonify({"error": "상태 컬럼을 찾을 수 없습니다.", "columns": list(props.keys())}), 404

        prop_type = status_prop.get("type", "select")
        options = status_prop.get(prop_type, {}).get("options", [])
        option_names = [o["name"] for o in options]

        # 실제 데이터에서 상태값 샘플링 (옵션에 없는 값도 감지)
        sample_statuses = {}
        try:
            response = client.databases.query(database_id=db_id, page_size=100)
            for page in response.get("results", []):
                sp = page.get("properties", {}).get(col_status, {})
                st = sp.get("type", "select")
                sv = (sp.get(st) or {}).get("name", "")
                if sv:
                    sample_statuses[sv] = sample_statuses.get(sv, 0) + 1
        except Exception:
            pass

        return jsonify({
            "column_name": col_status,
            "column_type": prop_type,
            "options": option_names,
            "sample_counts": sample_statuses,
            "expected_mapping": {
                "댓글작업전": "댓글작업전" if "댓글작업전" in option_names else None,
                "댓글완료": "댓글완료" if "댓글완료" in option_names else None,
            }
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _update_env_var(env_path, key, value):
    """env 파일에서 특정 변수를 업데이트하거나 추가합니다."""
    lines = []
    found = False
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    new_lines = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"{key}={value}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    # 환경변수도 즉시 반영
    os.environ[key] = value


# ━━━━━━━━━━━━━━ 자동 업데이트 ━━━━━━━━━━━━━━

@app.route("/api/update/status")
@login_required
def api_update_status():
    """현재 업데이트 상태 (새 버전 유무, 현재 버전 정보)."""
    return jsonify(get_update_status())


@app.route("/api/update/check", methods=["POST"])
@login_required
def api_update_check():
    """수동으로 업데이트 확인."""
    result = check_for_updates()
    current = get_current_version()
    result["current_version"] = current.get("version", "0.0.0")
    result["release_date"] = current.get("release_date", "")
    return jsonify(result)


@app.route("/api/update/apply", methods=["POST"])
@login_required
def api_update_apply():
    """업데이트 적용 시작 (ZIP 다운로드 → 덮어쓰기 → 재시작)."""
    progress = get_update_progress()
    if progress["status"] not in ("idle", "error", "done"):
        return jsonify({"error": "이미 업데이트가 진행 중입니다."}), 409

    status = get_update_status()
    if not status.get("needs_update"):
        return jsonify({"error": "이미 최신 버전입니다."}), 400

    result = perform_update()
    return jsonify(result)


@app.route("/api/update/progress")
@login_required
def api_update_progress():
    """업데이트 진행 상태 (프론트엔드 폴링용)."""
    return jsonify(get_update_progress())


# ━━━ Lemon Squeezy 결제 연동 ━━━

# 결제 상품 정의
PAYMENT_PRODUCTS = {
    # 구독 플랜 (Lemon Squeezy)
    "plan_business": {"name": "Business 월 구독", "amount": 590000, "type": "subscription", "plan_id": "business"},
    "plan_agency": {"name": "Agency 월 구독", "amount": 449000, "type": "subscription", "plan_id": "agency"},
    # 토큰 충전
    "token_500": {"name": "토큰 500개", "amount": 15000, "tokens": 500, "type": "token"},
    "token_1200": {"name": "토큰 1,200개", "amount": 30000, "tokens": 1200, "type": "token"},
    "token_3000": {"name": "토큰 3,000개", "amount": 60000, "tokens": 3000, "type": "token"},
    "token_7000": {"name": "토큰 7,000개", "amount": 105000, "tokens": 7000, "type": "token"},
    # 좋아요 크레딧 충전 (원 단위)
    "like_5000": {"name": "좋아요 크레딧 5,000원", "amount": 5000, "credits": 5000, "type": "like_credit"},
    "like_10000": {"name": "좋아요 크레딧 10,000원", "amount": 10000, "credits": 10000, "type": "like_credit"},
    "like_30000": {"name": "좋아요 크레딧 30,000원", "amount": 30000, "credits": 30000, "type": "like_credit"},
    "like_50000": {"name": "좋아요 크레딧 50,000원", "amount": 50000, "credits": 50000, "type": "like_credit"},
    "like_100000": {"name": "좋아요 크레딧 100,000원", "amount": 100000, "credits": 100000, "type": "like_credit"},
    "like_200000": {"name": "좋아요 크레딧 200,000원", "amount": 200000, "credits": 200000, "type": "like_credit"},
}


@app.route("/api/payment/config")
def api_payment_config():
    """프론트엔드에 Lemon Squeezy 설정 전달"""
    config = ls_client.get_config()
    return jsonify({
        "provider": "lemonsqueezy",
        "available": config["available"],
        "checkout_urls": config["checkout_urls"],
    })


@app.route("/api/payment/checkout", methods=["POST"])
def api_payment_checkout():
    """Lemon Squeezy 체크아웃 URL 생성 (구독 플랜 + 좋아요 크레딧 지원)"""
    data = request.get_json() or {}
    plan_id = data.get("plan_id", "")

    valid_ids = ("business", "agency", "like_5000", "like_10000", "like_30000", "like_50000")
    if plan_id not in valid_ids:
        return jsonify({"error": "잘못된 상품입니다."}), 400

    if not ls_client.is_available():
        return jsonify({"error": "결제 서비스가 준비되지 않았습니다. 잠시 후 다시 시도해주세요."}), 503

    # 사용자 정보 포함하여 체크아웃 URL 생성
    user_email = current_user.email if current_user.is_authenticated else None
    lk = license_client.license_key if license_client.license_key else None

    checkout_url = ls_client.get_checkout_url(plan_id, user_email=user_email, license_key=lk)

    if not checkout_url:
        return jsonify({"error": "체크아웃 URL 생성 실패"}), 500

    add_log(f"[결제] Lemon Squeezy 체크아웃 생성: {plan_id}", "info")

    return jsonify({
        "success": True,
        "checkout_url": checkout_url,
        "plan_id": plan_id,
    })


_processed_webhook_events = set()  # 웹훅 이벤트 중복 처리 방지 (idempotency)

@app.route("/api/payment/webhook", methods=["POST"])
def api_payment_webhook():
    """Lemon Squeezy 웹훅 수신 (구독 생성/갱신/취소 처리)"""
    import requests as _requests

    # 서명 검증
    signature = request.headers.get("X-Signature", "")
    if not ls_client.verify_webhook(request.get_data(), signature):
        print("[웹훅] 서명 검증 실패")
        return jsonify({"error": "Invalid signature"}), 403

    payload = request.get_json() or {}
    event_name = payload.get("meta", {}).get("event_name", "")
    custom_data = payload.get("meta", {}).get("custom_data", {})
    attrs = payload.get("data", {}).get("attributes", {})

    # 멱등성 체크: 동일 이벤트 중복 처리 방지
    event_id = payload.get("meta", {}).get("event_id", "") or payload.get("data", {}).get("id", "")
    idempotency_key = f"{event_name}:{event_id}"
    if idempotency_key in _processed_webhook_events:
        print(f"[웹훅] 중복 이벤트 무시: {idempotency_key}")
        return jsonify({"success": True, "message": "already processed"}), 200
    _processed_webhook_events.add(idempotency_key)
    # 메모리 관리: 오래된 이벤트 정리 (최대 1000개 유지)
    if len(_processed_webhook_events) > 1000:
        _processed_webhook_events.clear()

    print(f"[웹훅] Lemon Squeezy 이벤트: {event_name}")

    # 라이선스 키 (체크아웃 시 custom_data로 전달됨)
    lk = custom_data.get("license_key", "") or (license_client.license_key if license_client.license_key else "")
    user_email = custom_data.get("user_email", "") or attrs.get("user_email", "")

    if not user_email:
        return jsonify({"error": "user_email missing"}), 400

    # Verify user exists in DB
    target_user = User.query.filter_by(email=user_email).first()
    if not target_user:
        print(f"[Webhook] 등록되지 않은 이메일: {user_email}")
        return jsonify({"error": "unknown user"}), 400

    if event_name == "subscription_created":
        # 신규 구독 생성
        variant_id = str(attrs.get("variant_id", ""))
        plan_id = ls_client.get_plan_from_variant(variant_id)
        status = attrs.get("status", "")
        subscription_id = payload.get("data", {}).get("id", "")

        print(f"[웹훅] 구독 생성: plan={plan_id}, status={status}, sub_id={subscription_id}, email={user_email}")

        if plan_id and status == "active":
            upgraded = False
            # 로컬 DB에 플랜 직접 반영
            try:
                plan_name_map = {"business": "Business", "agency": "Agency"}
                db_plan_name = plan_name_map.get(plan_id, plan_id.capitalize())
                user = User.query.filter_by(email=user_email).first() if user_email else None
                if user:
                    user.plan = db_plan_name
                    db.session.commit()
                if license_client.license_info is None:
                    license_client.license_info = {}
                license_client.license_info["plan"] = db_plan_name
                license_client.license_info["plan_name"] = plan_id
                license_client.license_info["max_accounts"] = PLAN_MAX_ACCOUNTS.get(plan_id, 3)
                license_client.token_balance = PLAN_TOKENS.get(plan_id, 0)
                license_client.save_balances()
                upgraded = True
            except Exception as e:
                print(f"[웹훅] 로컬 DB 플랜 반영 오류: {e}")

            if upgraded:
                add_log(f"[결제] 구독 활성화 완료: {plan_id} (Lemon Squeezy)", "success")

    elif event_name == "subscription_updated":
        # 구독 상태 변경 (갱신, 일시정지, 재개 등)
        status = attrs.get("status", "")
        variant_id = str(attrs.get("variant_id", ""))
        plan_id = ls_client.get_plan_from_variant(variant_id)
        subscription_id = payload.get("data", {}).get("id", "")

        print(f"[웹훅] 구독 업데이트: plan={plan_id}, status={status}")

        if status == "active" and plan_id:
            # 로컬 DB에 플랜 반영
            try:
                plan_name_map = {"business": "Business", "agency": "Agency"}
                db_plan = plan_name_map.get(plan_id, plan_id.capitalize())
                u = User.query.filter_by(email=user_email).first() if user_email else None
                if u:
                    u.plan = db_plan
                    db.session.commit()
                add_log(f"[결제] 구독 갱신 완료: {plan_id}", "success")
            except Exception as e:
                print(f"[웹훅] 구독 갱신 오류: {e}")

        elif status in ("cancelled", "expired", "past_due"):
            add_log(f"[결제] 구독 상태 변경: {status}", "warning" if status == "past_due" else "error")

    elif event_name == "subscription_payment_success":
        subscription_id = attrs.get("subscription_id", "")
        print(f"[웹훅] 정기 결제 성공: sub_id={subscription_id}")
        add_log("[결제] 월간 구독 결제 완료", "success")

    elif event_name == "subscription_payment_failed":
        print(f"[웹훅] 결제 실패: email={user_email}")
        add_log("[결제] 구독 결제 실패 - 확인 필요", "error")

    elif event_name == "order_created":
        # 일회성 상품 결제 (좋아요 크레딧 등)
        order_id = payload.get("data", {}).get("id", "")
        status = attrs.get("status", "")
        total = attrs.get("total", 0)
        product_name = attrs.get("first_order_item", {}).get("product_name", "").lower()

        print(f"[웹훅] 주문 생성: order_id={order_id}, status={status}, total={total}, product={product_name}")

        if status == "paid" and lk:
            # 좋아요 크레딧 상품인지 확인
            like_credit_map = {
                "like_5000": 5000, "5000": 5000, "5,000": 5000,
                "like_10000": 10000, "10000": 10000, "10,000": 10000,
                "like_30000": 30000, "30000": 30000, "30,000": 30000,
                "like_50000": 50000, "50000": 50000, "50,000": 50000,
                "like_100000": 100000, "100000": 100000, "100,000": 100000,
                "like_200000": 200000, "200000": 200000, "200,000": 200000,
            }

            credits = 0
            for key, amount in like_credit_map.items():
                if key in product_name:
                    credits = amount
                    break

            # 상품명으로 매칭 안되면 결제 금액 기준으로 매칭
            if credits == 0 and total > 0:
                total_krw = total // 100 if total > 100000 else total  # cents 변환 고려
                if total_krw in (5000, 10000, 30000, 50000):
                    credits = total_krw

            if credits > 0:
                license_client.like_credit_balance += credits
                license_client.save_balances()
                add_log(f"[결제] 좋아요 크레딧 {credits:,}원 충전 완료", "success")
                print(f"[웹훅] 좋아요 크레딧 충전: +{credits}원")
                # 서버 원장에 결제 충전 기록
                try:
                    import requests as _ledger_req
                    _ledger_req.post(f"{CENTRAL_SERVER_URL}/api/credits/transact", json={
                        "email": license_client._current_email or "",
                        "entry_type": "plan_tokens",
                        "amount": credits,
                        "description": f"결제 충전 {credits:,}원 ({product_name})",
                        "idempotency_key": f"webhook_{order_id}" if order_id else str(uuid.uuid4())
                    }, timeout=5)
                except Exception:
                    pass

    return jsonify({"success": True}), 200


@app.route("/api/payment/request", methods=["POST"])
def api_payment_request():
    """토큰 충전용 결제 요청 정보 생성"""
    data = request.get_json() or {}
    product_id = data.get("product_id", "")

    product = PAYMENT_PRODUCTS.get(product_id)
    if not product:
        return jsonify({"error": "잘못된 상품입니다."}), 400

    # 구독 플랜은 Lemon Squeezy로 리다이렉트
    if product["type"] == "subscription":
        plan_id = product.get("plan_id", "")
        if ls_client.is_available():
            user_email = current_user.email if current_user.is_authenticated else None
            lk = license_client.license_key
            checkout_url = ls_client.get_checkout_url(plan_id, user_email=user_email, license_key=lk)
            if checkout_url:
                return jsonify({"redirect": checkout_url, "provider": "lemonsqueezy"})
        return jsonify({"error": "결제 서비스 준비 중입니다."}), 503

    # 좋아요 크레딧은 Lemon Squeezy로 리다이렉트
    if product["type"] == "like_credit":
        if ls_client.is_available():
            user_email = current_user.email if current_user.is_authenticated else None
            lk = license_client.license_key
            checkout_url = ls_client.get_checkout_url(product_id, user_email=user_email, license_key=lk)
            if checkout_url:
                return jsonify({"redirect": checkout_url, "provider": "lemonsqueezy"})
        return jsonify({"error": "결제 서비스 준비 중입니다."}), 503

    order_id = f"order_{product_id}_{uuid.uuid4().hex[:8]}_{int(time.time())}"
    payment_id = f"payment_{uuid.uuid4().hex[:12]}_{int(time.time())}"

    return jsonify({
        "orderId": order_id,
        "paymentId": payment_id,
        "orderName": product["name"],
        "amount": product["amount"],
        "productId": product_id,
    })


@app.route("/api/payment/success")
def api_payment_success():
    """결제 성공 리다이렉트 (Lemon Squeezy 체크아웃 완료 후)"""
    add_log("[결제] 결제 완료 (Lemon Squeezy 리다이렉트)", "success")
    return redirect("/?payment_result=success")


@app.route("/api/payment/fail")
def api_payment_fail():
    """결제 실패 리다이렉트"""
    message = request.args.get("message", "결제가 취소되었습니다.")
    add_log(f"[결제] 결제 실패: {message}", "error")
    return redirect("/?payment_result=fail")


# ─── 결제 폴링 API (데스크탑 앱용 - 웹훅 대체) ───

# 이미 처리된 주문/구독 ID 추적 (중복 충전 방지)
_processed_payment_ids = set()


@app.route("/api/payment/confirm", methods=["POST"])
@login_required
def api_payment_confirm():
    """결제 완료 후 플랜 적용. Checkout.Success 이벤트의 order_number 필수."""
    data = request.get_json() or {}
    plan_id = data.get("plan_id", "")
    order_number = data.get("order_number", "")

    valid_plans = {"business": "Business", "agency": "Agency"}
    if plan_id not in valid_plans:
        return jsonify({"error": "유효하지 않은 플랜입니다."}), 400

    # order_number 필수
    if not order_number:
        return jsonify({"error": "주문번호가 필요합니다."}), 403

    # 중복 방지
    if order_number in _processed_payment_ids:
        return jsonify({"error": "이미 처리된 결제입니다."}), 409

    # Checkout.Success 이벤트 자체가 결제 증명 (LS 오버레이에서만 발생)
    # LS API 검증은 추가 안전장치 (선택적)
    if ls_client.api_key:
        try:
            import requests as _req
            headers = {"Authorization": f"Bearer {ls_client.api_key}", "Accept": "application/vnd.api+json"}
            # 최근 활성 구독 확인 (이메일 무관 — 결제 이메일과 앱 이메일이 다를 수 있음)
            resp = _req.get(
                "https://api.lemonsqueezy.com/v1/subscriptions",
                headers=headers,
                params={"filter[status]": "active"},
                timeout=10,
            )
            if resp.status_code == 200:
                subs = resp.json().get("data", [])
                # 최근 5분 내 생성된 구독이 있으면 OK
                from datetime import datetime, timezone, timedelta
                recent_cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
                has_recent = any(
                    datetime.fromisoformat(s["attributes"].get("created_at", "2000-01-01T00:00:00").replace("Z", "+00:00")) > recent_cutoff
                    for s in subs
                )
                if has_recent:
                    print(f"[결제] LS API: 최근 구독 확인됨")
                else:
                    print(f"[결제] LS API: 최근 구독 없음 (Checkout.Success 이벤트 신뢰)")
        except Exception as e:
            print(f"[결제] LS API 검증 실패 (Checkout.Success 신뢰): {e}")

    _processed_payment_ids.add(order_number)

    db_plan = valid_plans[plan_id]

    # 로컬 DB에 플랜 + 토큰 반영
    current_user.plan = db_plan
    plan_tokens = PLAN_TOKENS.get(plan_id, 0)
    current_user.token_balance = plan_tokens
    db.session.commit()

    # license_client 캐시 동기화
    if license_client.license_info is None:
        license_client.license_info = {}
    license_client.license_info["plan"] = db_plan
    license_client.license_info["plan_name"] = plan_id
    license_client.license_info["max_accounts"] = PLAN_MAX_ACCOUNTS.get(plan_id, 3)
    license_client.token_balance = plan_tokens
    license_client._current_user_id = current_user.id

    add_log(f"[결제] 플랜 적용: {db_plan} (order: {order_number or 'admin'})", "success")
    _sync_user_full(current_user)

    return jsonify({
        "success": True,
        "plan": db_plan,
        "message": f"{db_plan} 플랜이 적용되었습니다.",
    })


@app.route("/api/payment/confirm-credit", methods=["POST"])
@login_required
def api_payment_confirm_credit():
    """좋아요 크레딧 충전 확인. Checkout.Success 이벤트에서 호출."""
    data = request.get_json() or {}
    credits = int(data.get("credits", 0))
    order_number = data.get("order_number", "")

    if credits <= 0:
        return jsonify({"error": "유효하지 않은 크레딧"}), 400

    valid_credits = [5000, 10000, 30000, 50000]
    if credits not in valid_credits:
        return jsonify({"error": "유효하지 않은 상품입니다."}), 400

    # order_number 필수
    if not order_number:
        return jsonify({"error": "주문번호가 필요합니다."}), 403
    if order_number in _processed_payment_ids:
        return jsonify({"error": "이미 처리된 결제입니다."}), 409
    _processed_payment_ids.add(order_number)

    # 서버 원장에 결제 충전 기록
    try:
        import requests as _ledger_req
        _ledger_req.post(f"{CENTRAL_SERVER_URL}/api/credits/transact", json={
            "email": current_user.email,
            "entry_type": "plan_tokens",
            "amount": credits,
            "description": f"결제 충전 {credits:,}원 (order: {order_number})",
            "idempotency_key": f"payment_{order_number}"
        }, timeout=5)
    except Exception:
        pass

    # 서버 원장에서 최신 잔액 조회
    new_balance = (current_user.like_credit_balance or 0) + credits
    try:
        import requests as _ledger_req
        resp = _ledger_req.post(f"{CENTRAL_SERVER_URL}/api/credits/balance",
            json={"email": current_user.email}, timeout=5)
        if resp.status_code == 200:
            new_balance = resp.json().get("balance", new_balance)
    except Exception:
        pass

    # 유저 DB에 크레딧 동기화
    current_user.like_credit_balance = new_balance
    db.session.commit()

    # license_client 캐시 동기화
    license_client.like_credit_balance = current_user.like_credit_balance
    license_client._current_user_id = current_user.id

    add_log(f"[결제] 좋아요 크레딧 {credits:,}원 충전 (order: {order_number or 'admin'})", "success")
    _sync_user_full(current_user)

    return jsonify({
        "success": True,
        "credits": credits,
        "balance": current_user.like_credit_balance,
        "message": f"좋아요 크레딧 {credits:,}원이 충전되었습니다.",
    })


@app.route("/api/payment/poll", methods=["POST"])
@login_required
def api_payment_poll():
    """결제 완료 여부를 Lemon Squeezy API로 직접 확인 (폴링)

    데스크탑 앱에서 결제 후 이 엔드포인트를 주기적으로 호출하여
    웹훅 없이도 결제 완료를 감지하고 자동 충전합니다.
    """
    import requests as _requests

    if not ls_client.api_key:
        return jsonify({"error": "API 키 미설정", "no_api_key": True}), 503

    data = request.get_json() or {}
    payment_type = data.get("type", "")  # "subscription" 또는 "like_credit"
    user_email = current_user.email if current_user.is_authenticated else None
    lk = license_client.license_key

    results = {"found": False, "processed": []}

    # 구독 결제 확인
    if payment_type in ("subscription", ""):
        subs = ls_client.get_recent_subscriptions(user_email=user_email, since_minutes=10)
        for sub in subs:
            sub_id = sub["subscription_id"]
            if sub_id in _processed_payment_ids:
                continue
            if sub["status"] != "active":
                continue

            plan_id = sub.get("plan_id")
            if not plan_id:
                continue

            # 로컬 DB에 직접 플랜 반영
            upgraded = False
            try:
                plan_name_map = {"business": "Business", "agency": "Agency"}
                db_plan_name = plan_name_map.get(plan_id, plan_id.capitalize())
                if current_user and current_user.is_authenticated:
                    current_user.plan = db_plan_name
                    db.session.commit()
                if license_client.license_info is None:
                    license_client.license_info = {}
                license_client.license_info["plan"] = db_plan_name
                license_client.license_info["plan_name"] = plan_id
                license_client.license_info["max_accounts"] = PLAN_MAX_ACCOUNTS.get(plan_id, 3)
                license_client.token_balance = PLAN_TOKENS.get(plan_id, 0)
                upgraded = True
            except Exception as e:
                print(f"[폴링] 로컬 DB 플랜 반영 오류: {e}")

            if upgraded:
                add_log(f"[결제] 구독 활성화 완료: {plan_id}", "success")
                _processed_payment_ids.add(sub_id)
                results["found"] = True
                results["processed"].append({
                    "type": "subscription",
                    "plan": plan_id,
                    "subscription_id": sub_id,
                })

    # 일회성 결제 (좋아요 크레딧) 확인
    if payment_type in ("like_credit", ""):
        orders = ls_client.get_recent_orders(user_email=user_email, since_minutes=10)
        like_credit_map = {
            "like_5000": 5000, "5000": 5000, "5,000": 5000,
            "like_10000": 10000, "10000": 10000, "10,000": 10000,
            "like_30000": 30000, "30000": 30000, "30,000": 30000,
            "like_50000": 50000, "50000": 50000, "50,000": 50000,
            "like_100000": 100000, "100000": 100000, "100,000": 100000,
            "like_200000": 200000, "200000": 200000, "200,000": 200000,
        }

        for order in orders:
            order_id = order["order_id"]
            if order_id in _processed_payment_ids:
                continue
            if order["status"] != "paid":
                continue

            product_name = (order.get("product_name") or "").lower()
            credits = 0
            for key, amount in like_credit_map.items():
                if key in product_name:
                    credits = amount
                    break

            # 상품명 매칭 실패 시 금액 기준
            if credits == 0 and order.get("total"):
                total = order["total"]
                total_krw = total // 100 if total > 100000 else total
                if total_krw in (5000, 10000, 30000, 50000):
                    credits = total_krw

            if credits > 0:
                license_client.like_credit_balance += credits
                add_log(f"[결제] 좋아요 크레딧 {credits:,}원 충전 완료", "success")
                _processed_payment_ids.add(order_id)
                # 서버 원장에 결제 충전 기록
                try:
                    import requests as _ledger_req
                    _ledger_req.post(f"{CENTRAL_SERVER_URL}/api/credits/transact", json={
                        "email": current_user.email if current_user.is_authenticated else (license_client._current_email or ""),
                        "entry_type": "plan_tokens",
                        "amount": credits,
                        "description": f"결제 충전 {credits:,}원 (poll, {product_name})",
                        "idempotency_key": f"poll_{order_id}"
                    }, timeout=5)
                except Exception:
                    pass
                results["found"] = True
                results["processed"].append({
                    "type": "like_credit",
                    "credits": credits,
                    "order_id": order_id,
                })

    return jsonify(results)


@app.route("/api/payment/open-checkout", methods=["POST"])
@login_required
def api_payment_open_checkout():
    """결제 페이지를 외부 브라우저로 열기 (데스크탑 앱용)"""
    data = request.get_json() or {}
    product_id = data.get("product_id", "")

    if not product_id:
        return jsonify({"error": "상품 ID가 필요합니다."}), 400

    user_email = current_user.email if current_user.is_authenticated else None
    lk = license_client.license_key
    checkout_url = ls_client.get_checkout_url(product_id, user_email=user_email, license_key=lk)

    if not checkout_url:
        return jsonify({"error": "결제 URL을 생성할 수 없습니다."}), 503

    # 데스크탑 모드: 외부 브라우저로 열기
    is_desktop = os.environ.get("DESKTOP_MODE") == "1"

    return jsonify({
        "checkout_url": checkout_url,
        "desktop_mode": is_desktop,
        "poll_endpoint": "/api/payment/poll",
    })


# 시작 시 라이선스 로컬 초기화
try:
    if is_owner_mode():
        print("[License] Owner 모드 - 무제한")
    else:
        print("[License] 로컬 모드 - 유저 로그인 시 플랜 로드")
except Exception as _lic_err:
    print(f"[License] 초기화 오류 (무시): {_lic_err}")

# Lemon Squeezy 결제 초기화
try:
    if ls_client:
        ls_client.initialize()
except Exception as _ls_err:
    print(f"[LemonSqueezy] 초기화 오류 (무시하고 계속): {_ls_err}")

# 시작 시 업데이트 체크 (비동기)
try:
    check_updates_async()
except Exception as _upd_err:
    print(f"[Updater] 업데이트 체크 오류 (무시하고 계속): {_upd_err}")


if __name__ == "__main__":
    _start_scheduler()

    import ssl
    cert_file = os.path.join(os.path.dirname(__file__), "cert_disabled.pem")
    key_file = os.path.join(os.path.dirname(__file__), "key_disabled.pem")

    if os.path.exists(cert_file) and os.path.exists(key_file):
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(cert_file, key_file)
        print("[서버] HTTPS 모드로 시작: https://localhost:5000")
        app.run(host="0.0.0.0", port=5000, debug=True, ssl_context=ssl_ctx)
    else:
        print("[서버] 인증서 없음 - HTTP 모드로 시작: http://localhost:5000")
        print("[서버] HTTPS 전환하려면 프로젝트 루트에 cert.pem, key.pem 파일을 생성하세요.")
        app.run(host="0.0.0.0", port=5000, debug=True)
