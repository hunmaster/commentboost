"""
AI 댓글/대댓글 생성 API (Phase 2)
앱 내장 생성 엔진(comment_generator, OpenAI)으로 영상분석→댓글→대댓글을 생성하고
로컬 CommentTask에 저장한다. (n8n 런타임 불필요 — 올인원)
"""
import os
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from src.models import db, Campaign, VideoTarget, CommentTask, UserSettings, AccountType
from src.license_client import license_client
from src import comment_generator

comment_bp = Blueprint('comment_api', __name__)

# 댓글 생성은 영상 수집과 동일하게 Agency 이상 전용
COMMENT_FEATURE = "youtube_collect"
COMMENT_UPGRADE_MSG = "댓글 생성은 Agency 플랜(월 990,000원)부터 사용 가능합니다. 구독을 업그레이드해주세요."


def _require_comment_feature():
    if not license_client.can_use_feature(COMMENT_FEATURE):
        return jsonify({'error': COMMENT_UPGRADE_MSG}), 403
    return None


def _openai_cfg(uid):
    """유저별 OpenAI 설정(키/모델/브랜드)을 읽어 반환. 비면 .env 폴백."""
    us = UserSettings.query.filter_by(user_id=uid).first()
    key = (us.get('OPENAI_API_KEY') if us else None) or os.getenv('OPENAI_API_KEY')
    model = (us.get('OPENAI_COMMENT_MODEL') if us else None) or None
    brand = (us.get('OPENAI_COMMENT_BRAND') if us else None) or None
    return key, model, brand


@comment_bp.route('/api/youtube/generate-comments', methods=['POST'])
@login_required
def generate_comments_for_campaign():
    """선택한 영상들에 대해 AI 댓글+대댓글 생성 (영상분석 기반, 자동)."""
    gate = _require_comment_feature()
    if gate:
        return gate
    uid = current_user.id
    data = request.json or {}
    video_ids = data.get('video_ids', [])
    account_label = (data.get('account_label') or None)

    if not video_ids:
        return jsonify({'error': '영상을 하나 이상 선택해주세요.'}), 400

    # 생성 설정 — 유저별 설정(설정 탭)에서 OpenAI 키/모델/브랜드를 읽어 사용.
    # 비어 있으면 generator가 환경변수(OPENAI_*)로 폴백한다.
    cfg_key, cfg_model, cfg_brand = _openai_cfg(uid)
    if not cfg_key:
        return jsonify({'error': '댓글 생성을 위해 설정 탭에서 OpenAI API 키를 입력해주세요.'}), 400

    # 본인 소유 캠페인의 영상만 대상 (남의 video_id는 무시)
    rows = (db.session.query(VideoTarget, Campaign)
            .join(Campaign, VideoTarget.campaign_id == Campaign.id)
            .filter(VideoTarget.id.in_(video_ids), Campaign.user_id == uid)
            .all())

    # 계정 풀 — account_label(콤마 구분 가능) 우선, 없으면 유저 계정유형(AccountType).
    # 동일 채널의 여러 영상은 채널별 라운드로빈으로 '서로 다른 계정'에 배정(탐지 위험↓).
    pool = [s.strip() for s in str(account_label).split(',') if s.strip()] if account_label else []
    if not pool:
        pool = [a.name for a in AccountType.query.filter_by(user_id=uid).all() if a.name]
    _seen = set()
    pool = [x for x in pool if not (x in _seen or _seen.add(x))]  # 중복 제거(순서 유지)
    chan_counter = {}

    def _assign_account(video):
        if not pool:
            return account_label
        ch = video.channel_id or f"v{video.id}"  # 채널 미상이면 영상 단위로 분리(겹침 방지)
        idx = chan_counter.get(ch, 0)
        chan_counter[ch] = idx + 1
        return pool[idx % len(pool)]

    generated = irrelevant = skipped = 0
    errors = []
    for video, campaign in rows:
        # 이미 처리된(실패 아님) 영상은 건너뜀 — 실패한 건만 재시도 허용
        existing = (CommentTask.query
                    .filter_by(video_id=video.id)
                    .filter(CommentTask.status != '실패').first())
        if existing:
            skipped += 1
            continue

        # 재시도 시 이전 '실패' 행은 정리(중복 방지)
        CommentTask.query.filter_by(video_id=video.id, status='실패').delete()

        # 자막이 있으면 영상 분석에 함께 제공 (더 정확한 댓글)
        desc = video.description or ""
        if getattr(video, 'transcript', None):
            desc = (desc + "\n\n[영상 자막]\n" + video.transcript).strip()

        result = comment_generator.generate(
            keyword=campaign.keyword,
            title=video.title,
            description=desc,
            url=video.url,
            brand=cfg_brand,
            api_key=cfg_key,
            model=cfg_model,
        )
        status = result.get('status', '실패')
        db.session.add(CommentTask(
            video_id=video.id,
            account_label=_assign_account(video),
            generated_text=result.get('comment_text') or '',
            reply_text=result.get('reply_text') or '',
            status=status,
        ))
        if status == '생성완료':
            generated += 1
        elif status == '관련없음':
            irrelevant += 1
        else:
            errors.append(f"Video {video.id}: {result.get('error', '생성 실패')}")

    db.session.commit()

    parts = [f"{generated}개 생성"]
    if irrelevant:
        parts.append(f"{irrelevant}개 관련없음")
    if skipped:
        parts.append(f"{skipped}개 이미 처리됨")
    if errors:
        parts.append(f"{len(errors)}개 실패")
    return jsonify({
        'success': True,
        'generated_count': generated,
        'irrelevant_count': irrelevant,
        'skipped_count': skipped,
        'errors': errors,
        'message': " · ".join(parts),
    })


# n8n '닥터두드리 바이럴 v2' 워크플로우의 OpenAI 노드 모델 (export JSON에서 확인 — 3노드 모두 gpt-4o)
N8N_MODEL = "gpt-4o"


@comment_bp.route('/api/youtube/compare-generate', methods=['POST'])
@login_required
def compare_generate():
    """품질 비교용 — 같은 영상을 양쪽으로 자동 생성(저장 안 함).
      A(n8n 원본 재현): 워크플로우 모델 gpt-4o
      B(앱 현재 설정):  유저가 설정한 모델(기본 gpt-4o-mini)
    엔진/프롬프트/페르소나/금칙어는 n8n과 100% 동일(검증) — 차이는 모델뿐.
    """
    gate = _require_comment_feature()
    if gate:
        return gate
    uid = current_user.id
    data = request.json or {}
    video_id = data.get('video_id')
    if not video_id:
        return jsonify({'error': '영상을 선택해주세요.'}), 400

    row = (db.session.query(VideoTarget, Campaign)
           .join(Campaign, VideoTarget.campaign_id == Campaign.id)
           .filter(VideoTarget.id == video_id, Campaign.user_id == uid)
           .first())
    if not row:
        return jsonify({'error': '영상을 찾을 수 없습니다.'}), 404
    video, campaign = row

    cfg_key, cfg_model, cfg_brand = _openai_cfg(uid)
    if not cfg_key:
        return jsonify({'error': '설정 탭에서 OpenAI API 키를 입력해주세요.'}), 400

    desc = video.description or ""
    if getattr(video, 'transcript', None):
        desc = (desc + "\n\n[영상 자막]\n" + video.transcript).strip()

    def _gen(model):
        r = comment_generator.generate(
            keyword=campaign.keyword, title=video.title, description=desc,
            url=video.url, brand=cfg_brand, api_key=cfg_key, model=model,
        )
        return {
            'comment': r.get('comment_text') or '',
            'reply': r.get('reply_text') or '',
            'status': r.get('status'),
            'brand_fit': r.get('brand_fit'),
            'error': r.get('error'),
        }

    app_model = cfg_model or comment_generator._model()
    try:
        n8n_side = _gen(N8N_MODEL)
        app_side = _gen(app_model)
    except Exception as e:
        return jsonify({'error': f'생성 실패: {str(e)}'}), 500

    return jsonify({
        'success': True,
        'title': video.title,
        'n8n_model': N8N_MODEL,
        'app_model': app_model,
        'n8n': n8n_side,
        'app': app_side,
    })


@comment_bp.route('/api/youtube/compare-judge', methods=['POST'])
@login_required
def compare_judge():
    """품질 비교용 — A(n8n)와 B(앱) 댓글을 AI가 점수로 평가."""
    gate = _require_comment_feature()
    if gate:
        return gate
    uid = current_user.id
    data = request.json or {}
    title = (data.get('title') or '').strip()
    a = {'comment': (data.get('a_comment') or ''), 'reply': (data.get('a_reply') or '')}
    b = {'comment': (data.get('b_comment') or ''), 'reply': (data.get('b_reply') or '')}
    if not (a['comment'] or a['reply']) or not (b['comment'] or b['reply']):
        return jsonify({'error': 'A(n8n)·B(앱) 양쪽 원고를 모두 입력해주세요.'}), 400

    cfg_key, cfg_model, _ = _openai_cfg(uid)
    if not cfg_key:
        return jsonify({'error': '설정 탭에서 OpenAI API 키를 입력해주세요.'}), 400

    verdict = comment_generator.judge(title, a, b, api_key=cfg_key, model=cfg_model)
    if verdict.get('error'):
        return jsonify({'error': verdict['error']}), 500
    return jsonify({'success': True, 'verdict': verdict})


@comment_bp.route('/api/youtube/comments', methods=['GET'])
@login_required
def get_comments():
    """생성된 댓글/대댓글 조회 — 본인 소유만 (고아 행 제외)."""
    gate = _require_comment_feature()
    if gate:
        return gate
    status_filter = request.args.get('status')
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
            'video_url': v.url,
            'generated_text': c.generated_text,
            'reply_text': c.reply_text or '',
            'status': c.status,
            'account_label': c.account_label,
            'created_at': c.created_at.isoformat(),
        })
    return jsonify({'comments': results})


@comment_bp.route('/api/youtube/comments/<int:comment_id>', methods=['PUT'])
@login_required
def update_comment(comment_id):
    """생성된 댓글/대댓글 직접 수정 (본인 소유만)."""
    gate = _require_comment_feature()
    if gate:
        return gate
    data = request.json or {}
    new_comment = data.get('generated_text')
    new_reply = data.get('reply_text')
    if new_comment is None and new_reply is None:
        return jsonify({'error': '수정할 내용이 없습니다.'}), 400

    comment = (db.session.query(CommentTask)
               .join(VideoTarget, CommentTask.video_id == VideoTarget.id)
               .join(Campaign, VideoTarget.campaign_id == Campaign.id)
               .filter(CommentTask.id == comment_id, Campaign.user_id == current_user.id)
               .first())
    if not comment:
        return jsonify({'error': '리소스를 찾을 수 없습니다.'}), 404

    if new_comment is not None:
        comment.generated_text = new_comment
    if new_reply is not None:
        comment.reply_text = new_reply
    db.session.commit()
    return jsonify({'success': True})
