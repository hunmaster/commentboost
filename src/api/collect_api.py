"""
YouTube Data Collection API (Phase 1)
"""
from io import BytesIO
import base64
import urllib.parse
import yt_dlp
from flask import Blueprint, jsonify, request, send_file
from flask_login import login_required, current_user
from src.models import db, Campaign, VideoTarget, CommentTask
from src.license_client import license_client

collect_bp = Blueprint('collect_api', __name__)

# 영상 수집은 Agency 이상 전용 기능
COLLECT_FEATURE = "youtube_collect"
COLLECT_UPGRADE_MSG = "영상 수집은 Agency 플랜(월 990,000원)부터 사용 가능합니다. 구독을 업그레이드해주세요."

# YouTube 검색필터(sp) 코드 — 기간(업로드일)
_PERIOD_SP = {'today': 2, 'week': 3, 'month': 4, 'year': 5}


def _build_sp(sort_by, period):
    """YouTube 검색필터 sp 파라미터 생성 (정렬+기간, 타입=영상). 실측 검증된 protobuf 인코딩."""
    sub = b''
    pcode = _PERIOD_SP.get(period)
    if pcode:
        sub += bytes([0x08, pcode])      # field2.1 = 기간
    sub += bytes([0x10, 0x01])            # field2.2 = 타입(영상)
    msg = b''
    if sort_by == 'views':
        msg += bytes([0x08, 0x03])        # field1 = 정렬(조회수)
    msg += bytes([0x12, len(sub)]) + sub
    return base64.b64encode(msg).decode()


def _require_collect_feature():
    """Agency 이상 권한 확인. 권한 없으면 (응답, 상태코드), 있으면 None."""
    if not license_client.can_use_feature(COLLECT_FEATURE):
        return jsonify({'error': COLLECT_UPGRADE_MSG}), 403
    return None


@collect_bp.route('/api/youtube/collect', methods=['POST'])
@login_required
def start_collect():
    gate = _require_collect_feature()
    if gate:
        return gate
    uid = current_user.id
    data = request.json or {}
    keyword = data.get('keyword', '').strip()
    try:
        max_videos = int(data.get('max_videos', 10))
    except (TypeError, ValueError):
        max_videos = 10
    max_videos = max(1, min(max_videos, 50))  # 1~50개로 제한
    campaign_name = (data.get('campaign_name') or f"{keyword} 캠페인").strip()[:100]
    # 필터: 영상타입(all/shorts/long) · 정렬(relevance/views) · 기간(all/today/week/month/year)
    video_type = (data.get('video_type') or 'all')
    sort_by = (data.get('sort_by') or 'relevance')
    period = (data.get('period') or 'all')

    if not keyword:
        return jsonify({'error': '키워드가 필요합니다.'}), 400

    # 1. 새 캠페인 생성 (현재 유저 소유)
    campaign = Campaign(user_id=uid, name=campaign_name, keyword=keyword, status='수집중')
    db.session.add(campaign)
    db.session.commit()

    # 2. 길이필터가 있으면 더 많이 받아 거른다(오버페치). 정렬·기간은 YouTube sp 필터로.
    over = 8 if video_type == 'shorts' else (3 if video_type == 'long' else 1)
    fetch_n = min(max_videos * over, 50)
    ydl_opts = {
        'extract_flat': True,
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'socket_timeout': 15,
        'playlistend': fetch_n,
    }
    if sort_by == 'views' or period in _PERIOD_SP:
        sp = _build_sp(sort_by, period)
        query = ("https://www.youtube.com/results?search_query="
                 + urllib.parse.quote(keyword) + "&sp=" + urllib.parse.quote(sp))
    else:
        query = f"ytsearch{fetch_n}:{keyword}"

    def _dur_ok(entry):
        d = entry.get('duration')
        if video_type == 'shorts':
            return d is not None and d <= 60
        if video_type == 'long':
            return d is None or d > 60
        return True

    try:
        # 이 유저가 이미 수집한 영상 ID (유저별 중복 방지)
        existing_ids = {
            row[0] for row in db.session.query(VideoTarget.video_id)
            .join(Campaign, VideoTarget.campaign_id == Campaign.id)
            .filter(Campaign.user_id == uid).all()
        }

        videos_collected = 0
        duplicates_skipped = 0
        seen = set()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            results = ydl.extract_info(query, download=False)

        entries = (results or {}).get('entries') or []
        for entry in entries:
            if videos_collected >= max_videos:
                break
            if not entry:
                continue
            video_id = entry.get('id')
            if not video_id:
                continue
            if not _dur_ok(entry):
                continue

            # 중복: 이미 보유했거나 이번 검색에서 본 영상은 건너뜀 (유저 범위)
            if video_id in existing_ids or video_id in seen:
                duplicates_skipped += 1
                continue
            seen.add(video_id)

            title = (entry.get('title') or '(제목 없음)')[:300]
            url = entry.get('url') or f"https://www.youtube.com/watch?v={video_id}"
            description = entry.get('description') or ''

            db.session.add(VideoTarget(
                campaign_id=campaign.id,
                video_id=video_id,
                title=title,
                url=url[:300],
                description=description,
            ))
            videos_collected += 1

        db.session.commit()
        campaign.status = '완료'
        db.session.commit()

        msg = f"키워드 '{keyword}'로 {videos_collected}개 영상을 수집했습니다."
        if duplicates_skipped:
            msg += f" (이미 수집된 {duplicates_skipped}개 제외)"
        return jsonify({
            'success': True,
            'campaign_id': campaign.id,
            'videos_collected': videos_collected,
            'duplicates_skipped': duplicates_skipped,
            'message': msg
        })
    except Exception as e:
        # 수집 실패 시 부분 추가분 폐기 후 캠페인 상태만 '실패'로 기록
        db.session.rollback()
        campaign.status = '실패'
        db.session.commit()
        return jsonify({'error': str(e)}), 500


@collect_bp.route('/api/youtube/campaigns', methods=['GET'])
@login_required
def get_campaigns():
    gate = _require_collect_feature()
    if gate:
        return gate
    campaigns = (Campaign.query
                 .filter_by(user_id=current_user.id)
                 .order_by(Campaign.created_at.desc()).all())
    results = []
    for c in campaigns:
        videos_count = VideoTarget.query.filter_by(campaign_id=c.id).count()
        results.append({
            'id': c.id,
            'name': c.name,
            'keyword': c.keyword,
            'status': c.status,
            'created_at': c.created_at.isoformat(),
            'videos_count': videos_count
        })
    return jsonify({'campaigns': results})


@collect_bp.route('/api/youtube/campaigns/<int:campaign_id>/videos', methods=['GET'])
@login_required
def get_campaign_videos(campaign_id):
    gate = _require_collect_feature()
    if gate:
        return gate
    # 소유권 확인 — 남의 캠페인 영상은 조회 불가
    campaign = Campaign.query.filter_by(id=campaign_id, user_id=current_user.id).first()
    if not campaign:
        return jsonify({'error': '캠페인을 찾을 수 없습니다.'}), 404

    videos = VideoTarget.query.filter_by(campaign_id=campaign_id).all()
    results = []
    for v in videos:
        results.append({
            'id': v.id,
            'video_id': v.video_id,
            'title': v.title,
            'url': v.url,
            'description': v.description[:100] + '...' if v.description else '',
            'collected_at': v.collected_at.isoformat()
        })
    return jsonify({'videos': results})


@collect_bp.route('/api/youtube/export', methods=['GET'])
@login_required
def export_data():
    """본인 수집 영상 + 생성 댓글/대댓글을 엑셀(.xlsx)로 내려받기."""
    gate = _require_collect_feature()
    if gate:
        return gate
    try:
        import openpyxl
    except ImportError:
        return jsonify({'error': '엑셀 내보내기 모듈(openpyxl)이 설치되지 않았습니다. pip install openpyxl'}), 500

    uid = current_user.id
    rows = (db.session.query(VideoTarget, Campaign)
            .join(Campaign, VideoTarget.campaign_id == Campaign.id)
            .filter(Campaign.user_id == uid)
            .order_by(Campaign.created_at.desc(), VideoTarget.id.asc())
            .all())

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "수집·생성"
    ws.append(['캠페인', '키워드', '영상 제목', '영상 URL', 'video_id',
               '댓글', '대댓글', '상태', '결과 URL', '수집일시'])
    for video, campaign in rows:
        task = (CommentTask.query.filter_by(video_id=video.id)
                .order_by(CommentTask.created_at.desc()).first())
        ws.append([
            campaign.name,
            campaign.keyword,
            video.title,
            video.url,
            video.video_id,
            (task.generated_text if task else ''),
            (task.reply_text if task else ''),
            (task.status if task else ''),
            (task.result_url if task else ''),
            video.collected_at.isoformat() if video.collected_at else '',
        ])

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return send_file(
        bio,
        as_attachment=True,
        download_name="commentboost_export.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
