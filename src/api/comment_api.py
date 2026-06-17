"""
AI 댓글 생성 API (Phase 2)
- 기본: n8n webhook(N8N_COMMENT_WEBHOOK_URL) 경유로 생성 (워크플로우 안에서 모델 선택, 예: OpenAI gpt-4o-mini)
- webhook 미설정 시: Claude(Haiku)로 직접 생성 폴백
"""
import os
import requests
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
import anthropic
from src.models import db, Campaign, VideoTarget, CommentTask
from src.license_client import license_client

comment_bp = Blueprint('comment_api', __name__)


def _generate_via_n8n(webhook_url, prompt_text, video_title, video_description):
    """n8n webhook으로 생성 요청 → 댓글 텍스트 반환.
    n8n 'Respond to Webhook' 노드가 {"comment": "..."} 형태로 주는 것을 권장하며,
    text/output/content/message.content 및 평문 응답도 폭넓게 파싱한다."""
    resp = requests.post(webhook_url, json={
        "prompt": prompt_text,
        "video_title": video_title,
        "video_description": (video_description or "")[:300],
    }, timeout=60)
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError:
        return resp.text.strip()
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict):
        for key in ("comment", "text", "output", "content"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        # OpenAI 노드 흔한 형태: {"message": {"content": "..."}}
        msg = data.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("content"), str):
            return msg["content"].strip()
    return str(data).strip()


def generate_comment(prompt_text, video_title, video_description):
    """댓글 생성 디스패처: n8n webhook 설정 시 그쪽으로, 없으면 Claude 직접."""
    webhook_url = os.getenv("N8N_COMMENT_WEBHOOK_URL", "").strip()
    if webhook_url:
        return _generate_via_n8n(webhook_url, prompt_text, video_title, video_description)
    return generate_claude_comment(prompt_text, video_title, video_description)

# 댓글 생성은 영상 수집과 동일하게 Agency 이상 전용
COMMENT_FEATURE = "youtube_collect"
COMMENT_UPGRADE_MSG = "댓글 생성은 Agency 플랜(월 990,000원)부터 사용 가능합니다. 구독을 업그레이드해주세요."


def _require_comment_feature():
    if not license_client.can_use_feature(COMMENT_FEATURE):
        return jsonify({'error': COMMENT_UPGRADE_MSG}), 403
    return None


def generate_claude_comment(prompt_text, video_title, video_description):
    """Anthropic Claude API 호출로 리뷰 생성"""
    api_key = os.getenv("CLAUDE_API_KEY")
    if not api_key:
        raise ValueError("CLAUDE_API_KEY 환경변수가 설정되지 않았습니다.")

    client = anthropic.Anthropic(api_key=api_key)

    # 영상 정보를 이용한 기본 프롬프트 강화
    system_prompt = "유튜브 영상에 달릴 자연스러운 한글 일반 사용자 댓글을 작성해주세요."
    user_prompt = f"""
다음 정보를 참고하여 요청사항에 맞는 댓글을 1개만 딱 작성해줘. 인사말이나 다른 말 없이 댓글 내용만 출력해.

[영상 제목]: {video_title}
[영상 설명]: {video_description[:300]}

[사용자 요청 프롬프트]: {prompt_text}
"""
    # 다수 사용자가 쓰고 비용을 우리가 부담하므로 최저가 모델(Haiku) 사용.
    # 짧은 댓글 생성엔 품질 충분 — 입력 $1/출력 $5 (Sonnet의 약 1/3).
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=256,
        temperature=0.8,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )

    # 텍스트 추출 (호환성 목적)
    try:
        if isinstance(response.content, list):
            result_text = response.content[0].text
        else:
            result_text = response.content

        return result_text.strip()
    except Exception as e:
        return f"AI 생성 오류: {e}"

@comment_bp.route('/api/youtube/generate-comments', methods=['POST'])
@login_required
def generate_comments_for_campaign():
    """캠페인에 등록된 특정 영상들에 대해 AI 댓글 일괄 생성"""
    gate = _require_comment_feature()
    if gate:
        return gate
    uid = current_user.id
    data = request.json or {}
    video_ids = data.get('video_ids', [])
    prompt_text = data.get('prompt', '').strip()
    account_label = data.get('account_label', None)

    if not prompt_text:
        return jsonify({'error': '프롬프트를 입력해주세요.'}), 400

    if not video_ids:
        return jsonify({'error': '영상을 하나 이상 선택해주세요.'}), 400

    generated_count = 0
    errors = []

    # 본인 소유 캠페인의 영상만 대상 (남의 video_id는 무시)
    videos = (VideoTarget.query
              .join(Campaign, VideoTarget.campaign_id == Campaign.id)
              .filter(VideoTarget.id.in_(video_ids), Campaign.user_id == uid)
              .all())

    for video in videos:
        # 기존 대기중인 댓글이 있는지 확인
        existing_task = CommentTask.query.filter_by(video_id=video.id, status='대기').first()
        if existing_task:
            continue

        try:
            generated_text = generate_comment(prompt_text, video.title, video.description or "")

            new_task = CommentTask(
                video_id=video.id,
                account_label=account_label,
                prompt=prompt_text,
                generated_text=generated_text,
                status='생성완료'
            )
            db.session.add(new_task)
            generated_count += 1

        except Exception as e:
            errors.append(f"Video {video.id} Error: {str(e)}")

    db.session.commit()

    return jsonify({
        'success': True,
        'generated_count': generated_count,
        'errors': errors,
        'message': f"{generated_count}개의 댓글이 성공적으로 생성되었습니다."
    })

@comment_bp.route('/api/youtube/comments', methods=['GET'])
@login_required
def get_comments():
    """상태별 댓글 조회 (생성완료/업로드중/완료/실패) — 본인 소유만"""
    gate = _require_comment_feature()
    if gate:
        return gate
    status_filter = request.args.get('status')
    # CommentTask → VideoTarget → Campaign 조인으로 본인 소유만, 고아 행은 제외
    query = (db.session.query(CommentTask, VideoTarget)
             .join(VideoTarget, CommentTask.video_id == VideoTarget.id)
             .join(Campaign, VideoTarget.campaign_id == Campaign.id)
             .filter(Campaign.user_id == current_user.id))
    if status_filter:
        query = query.filter(CommentTask.status == status_filter)

    rows = query.order_by(CommentTask.created_at.desc()).all()
    results = []
    for c, v in rows:
        results.append({
            'id': c.id,
            'campaign_id': v.campaign_id,
            'video_id': v.video_id,
            'video_title': v.title,
            'prompt': c.prompt,
            'generated_text': c.generated_text,
            'status': c.status,
            'account_label': c.account_label,
            'created_at': c.created_at.isoformat()
        })
    return jsonify({'comments': results})

# 사용자가 AI 결과를 직접 수정하고 저장할 때 사용하는 라우트
@comment_bp.route('/api/youtube/comments/<int:comment_id>', methods=['PUT'])
@login_required
def update_comment(comment_id):
    gate = _require_comment_feature()
    if gate:
        return gate
    data = request.json or {}
    new_text = data.get('generated_text')
    if not new_text:
        return jsonify({'error': '텍스트가 없습니다.'}), 400

    # 본인 소유 댓글만 수정 가능
    comment = (db.session.query(CommentTask)
               .join(VideoTarget, CommentTask.video_id == VideoTarget.id)
               .join(Campaign, VideoTarget.campaign_id == Campaign.id)
               .filter(CommentTask.id == comment_id, Campaign.user_id == current_user.id)
               .first())
    if not comment:
        return jsonify({'error': '리소스를 찾을 수 없습니다.'}), 404

    comment.generated_text = new_text
    db.session.commit()

    return jsonify({'success': True})
