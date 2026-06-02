"""
라이선스 서버 - Flask API
독립 서버로 배포 가능 (포트 5100)
"""

import os
import functools
from datetime import datetime
from flask import Flask, request, jsonify, render_template
import requests as http_requests

from models import (
    init_db,
    create_customer,
    get_customer,
    list_customers,
    create_license,
    get_license_by_key,
    list_licenses,
    revoke_license,
    upgrade_license_plan,
    bind_device,
    unbind_device,
    get_token_balance,
    consume_tokens,
    add_tokens,
    refill_monthly_tokens,
    log_api_call,
    get_like_credit_balance,
    add_like_credits,
    consume_like_credits,
    create_like_order,
    get_like_orders,
    get_like_credit_purchases,
    update_like_order_status,
)

app = Flask(__name__, template_folder="templates")

# 관리자 API 키 (환경변수로 설정)
ADMIN_API_KEY = os.environ.get("LICENSE_ADMIN_KEY", "change-me-in-production")

# PortOne 결제 설정
PORTONE_STORE_ID = os.environ.get("PORTONE_STORE_ID", "")
PORTONE_API_SECRET = os.environ.get("PORTONE_API_SECRET", "")

# SMM Kings API 설정 (서버에서만 관리 - 고객에게 노출하지 않음)
SMM_API_KEY = os.environ.get("SMM_API_KEY", "")
SMM_API_URL = "https://smmkings.com/api/v2"
SMM_SERVICE_IDS = {
    "basic": os.environ.get("SMM_LIKE_SERVICE_ID_BASIC", ""),
    "standard": os.environ.get("SMM_LIKE_SERVICE_ID_STANDARD", ""),
    "premium": os.environ.get("SMM_LIKE_SERVICE_ID_PREMIUM", ""),
}

# 좋아요 티어별 가격 (원/개)
LIKE_TIER_PRICES = {
    "basic": 10,
    "standard": 15,
    "premium": 20,
}


# ─── 미들웨어 ───

def admin_required(f):
    """관리자 API 키 검증 데코레이터"""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get("X-Admin-Key", "")
        if api_key != ADMIN_API_KEY:
            return jsonify({"error": "관리자 인증 실패"}), 403
        return f(*args, **kwargs)
    return decorated


def get_client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr)


# ═══════════════════════════════════════════
#  클라이언트 API (프로그램에서 호출)
# ═══════════════════════════════════════════

@app.route("/api/license/verify", methods=["POST"])
def api_verify_license():
    """
    라이선스 키 검증 + 디바이스 바인딩
    클라이언트가 프로그램 시작 시 호출
    """
    data = request.get_json() or {}
    license_key = data.get("license_key", "").strip()
    hardware_id = data.get("hardware_id", "").strip()
    hostname = data.get("hostname", "")
    ip = get_client_ip()

    if not license_key or not hardware_id:
        log_api_call(license_key, "/verify", ip, False, "키 또는 하드웨어ID 누락")
        return jsonify({"valid": False, "error": "라이선스 키와 하드웨어 ID가 필요합니다."}), 400

    # 라이선스 조회
    lic = get_license_by_key(license_key)
    if not lic:
        log_api_call(license_key, "/verify", ip, False, "존재하지 않는 키")
        return jsonify({"valid": False, "error": "유효하지 않은 라이선스 키입니다."}), 404

    # 상태 확인
    if lic["status"] != "active":
        log_api_call(license_key, "/verify", ip, False, f"비활성 상태: {lic['status']}")
        return jsonify({"valid": False, "error": f"라이선스가 비활성 상태입니다. ({lic['status']})"}), 403

    # 만료 확인
    if not lic["is_permanent"] and lic["expires_at"]:
        expires = datetime.fromisoformat(lic["expires_at"])
        if datetime.utcnow() > expires:
            log_api_call(license_key, "/verify", ip, False, "만료됨")
            return jsonify({"valid": False, "error": "라이선스가 만료되었습니다. 구독을 갱신해주세요."}), 403

    # 디바이스 바인딩
    try:
        binding, is_new = bind_device(lic["id"], hardware_id, ip, hostname)
    except ValueError as e:
        log_api_call(license_key, "/verify", ip, False, str(e))
        return jsonify({"valid": False, "error": str(e)}), 403

    # 토큰 잔액
    balance = get_token_balance(lic["id"])

    # 좋아요 크레딧 잔액
    like_balance = get_like_credit_balance(lic["id"])

    log_api_call(license_key, "/verify", ip, True, "검증 성공")

    return jsonify({
        "valid": True,
        "license": {
            "plan": lic["plan_display"],
            "plan_name": lic["plan_name"],
            "customer": lic["customer_name"],
            "expires_at": lic["expires_at"],
            "is_permanent": bool(lic["is_permanent"]),
            "max_accounts": lic["max_accounts"],
            "max_devices": lic["max_devices"],
        },
        "tokens": {
            "balance": balance["balance"] if balance else 0,
            "extra_token_price": lic["extra_token_price"],
        },
        "like_credits": {
            "balance": like_balance["balance"] if like_balance else 0,
        },
        "device": {
            "is_new": is_new,
            "hardware_id": hardware_id,
        },
    })


@app.route("/api/license/tokens/use", methods=["POST"])
def api_use_tokens():
    """토큰 소모 (클라이언트에서 작업 수행 시 호출)"""
    data = request.get_json() or {}
    license_key = data.get("license_key", "").strip()
    action = data.get("action", "")
    tokens = data.get("tokens", 0)
    description = data.get("description", "")
    ip = get_client_ip()

    if not license_key or not action or tokens <= 0:
        return jsonify({"error": "필수 파라미터 누락"}), 400

    lic = get_license_by_key(license_key)
    if not lic or lic["status"] != "active":
        log_api_call(license_key, "/tokens/use", ip, False, "유효하지 않은 라이선스")
        return jsonify({"error": "유효하지 않은 라이선스"}), 403

    try:
        remaining = consume_tokens(lic["id"], action, tokens, description)
        log_api_call(license_key, "/tokens/use", ip, True, f"{action}: -{tokens}")
        return jsonify({"success": True, "remaining": remaining})
    except ValueError as e:
        log_api_call(license_key, "/tokens/use", ip, False, str(e))
        return jsonify({"error": str(e)}), 402  # Payment Required


@app.route("/api/license/tokens/balance", methods=["POST"])
def api_token_balance():
    """토큰 잔액 조회"""
    data = request.get_json() or {}
    license_key = data.get("license_key", "").strip()

    lic = get_license_by_key(license_key)
    if not lic:
        return jsonify({"error": "유효하지 않은 라이선스"}), 404

    balance = get_token_balance(lic["id"])
    return jsonify({
        "balance": balance["balance"] if balance else 0,
        "plan": lic["plan_display"],
    })


def verify_portone_payment(payment_id):
    """PortOne V2 API로 결제 검증"""
    if not PORTONE_API_SECRET:
        return None, "PortOne API 시크릿이 설정되지 않았습니다."

    resp = http_requests.get(
        f"https://api.portone.io/payments/{payment_id}",
        headers={
            "Authorization": f"PortOne {PORTONE_API_SECRET}",
            "Content-Type": "application/json",
        },
        timeout=10,
    )

    if resp.status_code != 200:
        return None, f"결제 조회 실패 (HTTP {resp.status_code})"

    payment = resp.json()

    if payment.get("status") != "PAID":
        return None, f"결제 미완료 상태: {payment.get('status')}"

    return payment, None


@app.route("/api/license/tokens/purchase", methods=["POST"])
def api_purchase_tokens():
    """고객 토큰 충전 요청 (결제 완료 후 호출)"""
    data = request.get_json() or {}
    license_key = data.get("license_key", "").strip()
    tokens = int(data.get("tokens", 0))
    payment_id = data.get("payment_id", "")  # PortOne 결제 ID
    ip = get_client_ip()

    if not license_key or tokens <= 0 or not payment_id:
        return jsonify({"error": "필수 파라미터 누락 (license_key, tokens, payment_id)"}), 400

    lic = get_license_by_key(license_key)
    if not lic or lic["status"] != "active":
        log_api_call(license_key, "/tokens/purchase", ip, False, "유효하지 않은 라이선스")
        return jsonify({"error": "유효하지 않은 라이선스"}), 403

    # PortOne 결제 검증
    payment, error = verify_portone_payment(payment_id)
    if error:
        log_api_call(license_key, "/tokens/purchase", ip, False, f"결제 검증 실패: {error}")
        return jsonify({"error": f"결제 검증 실패: {error}"}), 400

    amount = payment.get("amount", {}).get("total", 0)

    new_balance = add_tokens(lic["id"], tokens, amount)
    log_api_call(license_key, "/tokens/purchase", ip, True,
                 f"+{tokens} tokens, ₩{amount}, payment_id={payment_id}")

    return jsonify({
        "success": True,
        "balance": new_balance,
        "purchased": tokens,
        "amount": amount,
        "payment_id": payment_id,
    })


# ═══════════════════════════════════════════
#  좋아요 크레딧 API (고객 로컬앱에서 호출)
# ═══════════════════════════════════════════

@app.route("/api/license/likes/balance", methods=["POST"])
def api_like_credit_balance():
    """좋아요 크레딧 잔액 조회."""
    data = request.get_json() or {}
    license_key = data.get("license_key", "").strip()

    lic = get_license_by_key(license_key)
    if not lic:
        return jsonify({"error": "유효하지 않은 라이선스"}), 404

    balance = get_like_credit_balance(lic["id"])
    return jsonify({
        "balance": balance["balance"],
        "plan": lic["plan_display"],
    })


@app.route("/api/license/likes/order", methods=["POST"])
def api_like_order():
    """
    좋아요 주문 대행.
    1. 크레딧 잔액 확인 및 차감
    2. SMM Kings에 주문
    3. 주문 기록 저장
    """
    data = request.get_json() or {}
    license_key = data.get("license_key", "").strip()
    comment_url = data.get("comment_url", "").strip()
    quantity = int(data.get("quantity", 10))
    tier = data.get("tier", "standard")
    source = data.get("source", "boost")
    ip = get_client_ip()

    if not license_key or not comment_url:
        return jsonify({"error": "필수 파라미터 누락"}), 400

    if quantity < 10:
        return jsonify({"error": "최소 10개부터 주문 가능합니다."}), 400

    if tier not in LIKE_TIER_PRICES:
        return jsonify({"error": f"잘못된 티어: {tier}"}), 400

    lic = get_license_by_key(license_key)
    if not lic or lic["status"] != "active":
        log_api_call(license_key, "/likes/order", ip, False, "유효하지 않은 라이선스")
        return jsonify({"error": "유효하지 않은 라이선스"}), 403

    # 비용 계산
    price_per_unit = LIKE_TIER_PRICES[tier]
    cost = quantity * price_per_unit

    # 크레딧 잔액 확인 및 차감
    try:
        remaining = consume_like_credits(lic["id"], cost)
    except ValueError as e:
        log_api_call(license_key, "/likes/order", ip, False, str(e))
        return jsonify({"error": str(e), "balance_required": cost}), 402

    # SMM Kings 주문 실행
    service_id = SMM_SERVICE_IDS.get(tier, "")
    if not service_id or not SMM_API_KEY:
        # SMM 미설정 시 크레딧 환불
        add_like_credits(lic["id"], cost)
        log_api_call(license_key, "/likes/order", ip, False, "SMM 서비스 미설정")
        return jsonify({"error": "좋아요 서비스가 일시적으로 이용 불가합니다."}), 503

    try:
        smm_resp = http_requests.post(SMM_API_URL, data={
            "key": SMM_API_KEY,
            "action": "add",
            "service": service_id,
            "link": comment_url,
            "quantity": quantity,
        }, timeout=30)
        smm_result = smm_resp.json()
    except Exception as e:
        # SMM 요청 실패 시 크레딧 환불
        add_like_credits(lic["id"], cost)
        log_api_call(license_key, "/likes/order", ip, False, f"SMM 요청 실패: {e}")
        return jsonify({"error": "좋아요 주문 처리 중 오류가 발생했습니다."}), 500

    if "order" in smm_result:
        smm_order_id = str(smm_result["order"])
        order = create_like_order(lic["id"], smm_order_id, comment_url, quantity, tier, cost, source)
        log_api_call(license_key, "/likes/order", ip, True,
                     f"주문 성공: {quantity}개 {tier} (₩{cost}), SMM#{smm_order_id}")
        return jsonify({
            "success": True,
            "order_id": smm_order_id,
            "cost": cost,
            "remaining_credits": remaining,
            "tier": tier,
            "quantity": quantity,
        })
    else:
        # SMM 주문 실패 시 크레딧 환불
        add_like_credits(lic["id"], cost)
        error_msg = smm_result.get("error", "알 수 없는 오류")
        log_api_call(license_key, "/likes/order", ip, False, f"SMM 주문 실패: {error_msg}")
        return jsonify({"error": f"좋아요 주문 실패: {error_msg}"}), 500


@app.route("/api/license/likes/orders", methods=["POST"])
def api_like_order_history():
    """좋아요 주문 이력 조회."""
    data = request.get_json() or {}
    license_key = data.get("license_key", "").strip()

    lic = get_license_by_key(license_key)
    if not lic:
        return jsonify({"error": "유효하지 않은 라이선스"}), 404

    orders = get_like_orders(lic["id"])
    purchases = get_like_credit_purchases(lic["id"])
    balance = get_like_credit_balance(lic["id"])

    return jsonify({
        "orders": orders,
        "purchases": purchases,
        "balance": balance["balance"],
    })


@app.route("/api/license/likes/order-status", methods=["POST"])
def api_like_order_status():
    """좋아요 주문 상태 업데이트 (SMM Kings에서 확인)."""
    data = request.get_json() or {}
    license_key = data.get("license_key", "").strip()
    order_ids = data.get("order_ids", [])

    lic = get_license_by_key(license_key)
    if not lic:
        return jsonify({"error": "유효하지 않은 라이선스"}), 404

    if not order_ids or not SMM_API_KEY:
        return jsonify({"updated": 0})

    # SMM Kings에 상태 조회
    try:
        orders_str = ",".join(str(oid) for oid in order_ids)
        smm_resp = http_requests.post(SMM_API_URL, data={
            "key": SMM_API_KEY,
            "action": "status",
            "orders": orders_str,
        }, timeout=30)
        statuses = smm_resp.json()
    except Exception:
        return jsonify({"updated": 0, "error": "SMM 상태 조회 실패"})

    updated = 0
    if isinstance(statuses, dict):
        for oid, status_data in statuses.items():
            if isinstance(status_data, dict) and "status" in status_data:
                update_like_order_status(
                    str(oid),
                    status_data["status"],
                    status_data.get("remains"),
                )
                updated += 1

    return jsonify({"updated": updated})


@app.route("/api/license/likes/purchase", methods=["POST"])
def api_like_credit_purchase():
    """좋아요 크레딧 충전 (결제 완료 후 호출)."""
    data = request.get_json() or {}
    license_key = data.get("license_key", "").strip()
    credits = int(data.get("credits", 0))
    payment_id = data.get("payment_id", "")
    ip = get_client_ip()

    if not license_key or credits <= 0 or not payment_id:
        return jsonify({"error": "필수 파라미터 누락"}), 400

    lic = get_license_by_key(license_key)
    if not lic or lic["status"] != "active":
        log_api_call(license_key, "/likes/purchase", ip, False, "유효하지 않은 라이선스")
        return jsonify({"error": "유효하지 않은 라이선스"}), 403

    new_balance = add_like_credits(lic["id"], credits, credits, payment_id)
    log_api_call(license_key, "/likes/purchase", ip, True,
                 f"+{credits}원 크레딧, payment_id={payment_id}")

    return jsonify({
        "success": True,
        "balance": new_balance,
        "purchased": credits,
        "payment_id": payment_id,
    })


@app.route("/api/license/plan/upgrade", methods=["POST"])
def api_upgrade_plan():
    """구독 결제 완료 후 플랜 업그레이드"""
    data = request.get_json() or {}
    license_key = data.get("license_key", "").strip()
    plan_id = data.get("plan_id", "").strip()
    payment_id = data.get("payment_id", "")
    ip = get_client_ip()

    if not license_key or not plan_id or not payment_id:
        return jsonify({"error": "필수 파라미터 누락 (license_key, plan_id, payment_id)"}), 400

    updated, error = upgrade_license_plan(license_key, plan_id)
    if error:
        log_api_call(license_key, "/plan/upgrade", ip, False, error)
        return jsonify({"error": error}), 400

    log_api_call(license_key, "/plan/upgrade", ip, True,
                 f"플랜 업그레이드: {plan_id}, payment_id={payment_id}")

    return jsonify({
        "success": True,
        "plan": updated.get("plan_display", plan_id),
        "plan_id": plan_id,
        "payment_id": payment_id,
    })


@app.route("/api/license/usage-history", methods=["POST"])
def api_usage_history():
    """고객 토큰 사용 내역 조회"""
    data = request.get_json() or {}
    license_key = data.get("license_key", "").strip()

    lic = get_license_by_key(license_key)
    if not lic:
        return jsonify({"error": "유효하지 않은 라이선스"}), 404

    from models import get_db
    conn = get_db()

    # 최근 사용 내역 50건
    usage = conn.execute(
        "SELECT action, tokens_used, description, used_at FROM token_usage "
        "WHERE license_id = ? ORDER BY used_at DESC LIMIT 50",
        (lic["id"],),
    ).fetchall()

    # 최근 충전 내역 20건
    purchases = conn.execute(
        "SELECT tokens, price, purchased_at FROM token_purchases "
        "WHERE license_id = ? ORDER BY purchased_at DESC LIMIT 20",
        (lic["id"],),
    ).fetchall()

    conn.close()

    return jsonify({
        "usage": [dict(r) for r in usage],
        "purchases": [dict(r) for r in purchases],
    })


@app.route("/api/license/verify-owner", methods=["POST"])
def api_verify_owner():
    """
    Owner(개발자) 모드 인증.
    OWNER_SECRET_KEY + 하드웨어ID를 검증하여 관리자만 Owner 모드 사용 가능.
    """
    data = request.get_json() or {}
    secret_key = data.get("secret_key", "").strip()
    hardware_id = data.get("hardware_id", "").strip()
    ip = get_client_ip()

    if not secret_key or not hardware_id:
        return jsonify({"valid": False, "error": "필수 파라미터 누락"}), 400

    # 관리자 키와 비교 (ADMIN_API_KEY 또는 별도 OWNER 키 목록)
    owner_keys = os.environ.get("OWNER_SECRET_KEYS", "").split(",")
    owner_keys = [k.strip() for k in owner_keys if k.strip()]

    if not owner_keys:
        # OWNER_SECRET_KEYS 미설정 → 관리자 키로 폴백
        owner_keys = [ADMIN_API_KEY]

    is_valid = secret_key in owner_keys

    if is_valid:
        log_api_call(f"OWNER:{hardware_id[:8]}", "/verify-owner", ip, True, "Owner 인증 성공")
    else:
        log_api_call(f"OWNER:{hardware_id[:8]}", "/verify-owner", ip, False, "Owner 인증 실패")

    return jsonify({"valid": is_valid})


@app.route("/api/license/heartbeat", methods=["POST"])
def api_heartbeat():
    """주기적 하트비트 (프로그램 실행 중 30분마다 호출)"""
    data = request.get_json() or {}
    license_key = data.get("license_key", "").strip()
    hardware_id = data.get("hardware_id", "").strip()
    ip = get_client_ip()

    lic = get_license_by_key(license_key)
    if not lic or lic["status"] != "active":
        return jsonify({"valid": False, "error": "라이선스 비활성"}), 403

    # 만료 확인
    if not lic["is_permanent"] and lic["expires_at"]:
        expires = datetime.fromisoformat(lic["expires_at"])
        if datetime.utcnow() > expires:
            return jsonify({"valid": False, "error": "라이선스 만료"}), 403

    # 디바이스 확인
    if hardware_id:
        try:
            bind_device(lic["id"], hardware_id, ip)
        except ValueError:
            return jsonify({"valid": False, "error": "디바이스 인증 실패"}), 403

    balance = get_token_balance(lic["id"])
    return jsonify({
        "valid": True,
        "tokens_remaining": balance["balance"] if balance else 0,
    })


# ═══════════════════════════════════════════
#  관리자 API (관리자 대시보드에서 호출)
# ═══════════════════════════════════════════

# ─── 고객 관리 ───

@app.route("/api/admin/customers", methods=["GET"])
@admin_required
def api_list_customers():
    return jsonify(list_customers())


@app.route("/api/admin/customers", methods=["POST"])
@admin_required
def api_create_customer():
    data = request.get_json() or {}
    email = data.get("email", "").strip()
    name = data.get("name", "").strip()
    phone = data.get("phone", "")
    company = data.get("company", "")
    memo = data.get("memo", "")

    if not email or not name:
        return jsonify({"error": "이메일과 이름은 필수입니다."}), 400

    try:
        customer = create_customer(email, name, phone, company, memo)
        return jsonify(customer), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ─── 라이선스 관리 ───

@app.route("/api/admin/licenses", methods=["GET"])
@admin_required
def api_list_licenses():
    customer_id = request.args.get("customer_id")
    return jsonify(list_licenses(customer_id))


@app.route("/api/admin/licenses", methods=["POST"])
@admin_required
def api_create_license():
    """라이선스 발급"""
    data = request.get_json() or {}
    customer_id = data.get("customer_id", "").strip()
    plan_id = data.get("plan_id", "starter").strip()
    months = data.get("months", 1)

    if not customer_id:
        return jsonify({"error": "고객 ID가 필요합니다."}), 400

    try:
        lic = create_license(customer_id, plan_id, months)
        return jsonify(lic), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/admin/licenses/<license_id>/revoke", methods=["POST"])
@admin_required
def api_revoke_license(license_id):
    revoke_license(license_id)
    return jsonify({"message": "라이선스가 해지되었습니다."})


@app.route("/api/admin/licenses/<license_id>/tokens/add", methods=["POST"])
@admin_required
def api_add_tokens(license_id):
    """토큰 수동 추가"""
    data = request.get_json() or {}
    tokens = data.get("tokens", 0)
    price = data.get("price", 0)

    if tokens <= 0:
        return jsonify({"error": "토큰 수량이 필요합니다."}), 400

    new_balance = add_tokens(license_id, tokens, price)
    return jsonify({"balance": new_balance})


@app.route("/api/admin/licenses/<license_id>/refill", methods=["POST"])
@admin_required
def api_refill_tokens(license_id):
    """월간 토큰 리필"""
    refill_monthly_tokens(license_id)
    balance = get_token_balance(license_id)
    return jsonify({"balance": balance["balance"] if balance else 0})


@app.route("/api/admin/licenses/<license_id>/devices", methods=["GET"])
@admin_required
def api_list_devices(license_id):
    from models import get_db
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM device_bindings WHERE license_id = ? ORDER BY bound_at DESC",
        (license_id,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/devices/<binding_id>/unbind", methods=["POST"])
@admin_required
def api_unbind_device(binding_id):
    unbind_device(binding_id)
    return jsonify({"message": "디바이스 바인딩이 해제되었습니다."})


# ─── 통계 ───

@app.route("/api/admin/stats", methods=["GET"])
@admin_required
def api_stats():
    from models import get_db
    conn = get_db()

    total_customers = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    total_licenses = conn.execute("SELECT COUNT(*) FROM licenses WHERE status = 'active'").fetchone()[0]
    total_revenue = conn.execute(
        "SELECT COALESCE(SUM(price), 0) FROM token_purchases"
    ).fetchone()[0]

    # 플랜별 라이선스 수
    plan_stats = conn.execute(
        "SELECT p.display_name, COUNT(l.id) as count "
        "FROM plans p LEFT JOIN licenses l ON p.id = l.plan_id AND l.status = 'active' "
        "GROUP BY p.id"
    ).fetchall()

    # 최근 API 로그
    recent_logs = conn.execute(
        "SELECT * FROM api_logs ORDER BY logged_at DESC LIMIT 50"
    ).fetchall()

    conn.close()

    return jsonify({
        "total_customers": total_customers,
        "active_licenses": total_licenses,
        "total_token_revenue": total_revenue,
        "plan_stats": [dict(r) for r in plan_stats],
        "recent_logs": [dict(r) for r in recent_logs],
    })


# ─── 플랜 조회 ───

@app.route("/api/plans", methods=["GET"])
def api_list_plans():
    from models import get_db
    conn = get_db()
    rows = conn.execute("SELECT * FROM plans WHERE active = 1 ORDER BY monthly_price").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ─── 헬스체크 ───

@app.route("/api/health", methods=["GET"])
def api_health():
    return jsonify({"status": "ok", "server": "license-server", "version": "1.0.0"})


@app.route("/admin")
def admin_page():
    return render_template("admin.html")


# ═══════════════════════════════════════════

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("LICENSE_PORT", 5100))
    print(f"라이선스 서버 시작: http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
