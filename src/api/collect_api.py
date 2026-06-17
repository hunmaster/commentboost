"""
YouTube Data Collection API (Phase 1)
"""
from io import BytesIO
import base64
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import yt_dlp
from flask import Blueprint, jsonify, request, send_file
from flask_login import login_required, current_user
from src.models import db, Campaign, VideoTarget, CommentTask, UserSettings
from src.license_client import license_client
from src import impact_analyzer

collect_bp = Blueprint('collect_api', __name__)

# 자막 추출 설정
_TRANSCRIPT_LANGS = ('ko', 'en')
_TRANSCRIPT_MAX_CHARS = 1500  # 분석 프롬프트 비용 절감 위해 평문 길이 제한

# ── 봇 차단 회피 ──────────────────────────────────────────────────────────
# 실제 브라우저처럼 보이도록 UA·헤더를 쓰고, 영상 간 요청을 사람처럼 띄운다.
# (연속 고속 스크래핑은 YouTube가 봇으로 탐지해 일시 차단[HTTP 429/IP 제한]할 수 있음)
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_HTTP_HEADERS = {
    'User-Agent': _UA,
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
}
# 자막 추출 시 영상 간 지연(초) — 랜덤 지터로 패턴화 방지
_TRANSCRIPT_MIN_DELAY = 1.5
_TRANSCRIPT_MAX_DELAY = 4.0


def _ydl_opts(extra=None):
    """봇 탐지 회피용 공통 yt-dlp 옵션(실제 UA·내부 요청 스로틀·재시도)."""
    opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'socket_timeout': 15,
        'http_headers': dict(_HTTP_HEADERS),
        'sleep_interval_requests': 1,   # yt-dlp 내부 요청 간 최소 지연(초)
        'retries': 2,
        'extractor_retries': 2,
    }
    if extra:
        opts.update(extra)
    return opts


def _polite_delay():
    """영상 간 사람처럼 보이는 랜덤 지연."""
    time.sleep(random.uniform(_TRANSCRIPT_MIN_DELAY, _TRANSCRIPT_MAX_DELAY))


def _download_caption(url):
    """자막 트랙 URL을 받아 평문으로. json3 포맷 우선 파싱. 429는 한 번 백오프 후 포기."""
    raw = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers=dict(_HTTP_HEADERS))
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode('utf-8', 'ignore')
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                time.sleep(random.uniform(3.0, 6.0))  # 레이트리밋 — 잠시 쉬고 1회 재시도
                continue
            return ''
        except Exception:
            return ''
    if raw is None:
        return ''
    # json3: {"events":[{"segs":[{"utf8":"..."}]}]}
    try:
        data = json.loads(raw)
        parts = []
        for ev in data.get('events', []):
            for seg in (ev.get('segs') or []):
                t = seg.get('utf8', '')
                if t and t != '\n':
                    parts.append(t)
        text = ''.join(parts).strip()
        if text:
            return text
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return ''


def _fetch_transcript(video_id):
    """단일 영상의 자막(수동 우선, 없으면 자동) 평문. 실패 시 ''. 느린 작업."""
    try:
        with yt_dlp.YoutubeDL(_ydl_opts()) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    except Exception:
        return ''
    for src in ((info or {}).get('subtitles') or {}, (info or {}).get('automatic_captions') or {}):
        for lang in _TRANSCRIPT_LANGS:
            tracks = src.get(lang)
            if not tracks:
                continue
            url = next((t.get('url') for t in tracks if t.get('ext') == 'json3'), None)
            if not url:
                # json3 미제공 시 fmt 파라미터로 강제 요청
                base = tracks[0].get('url')
                if base:
                    url = base + ('&' if '?' in base else '?') + 'fmt=json3'
            if not url:
                continue
            text = _download_caption(url)
            if text:
                return text[:_TRANSCRIPT_MAX_CHARS]
    return ''

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
    include_transcript = bool(data.get('include_transcript'))  # 자막 추출(느림)

    if not keyword:
        return jsonify({'error': '키워드가 필요합니다.'}), 400

    # 1. 새 캠페인 생성 (현재 유저 소유)
    campaign = Campaign(user_id=uid, name=campaign_name, keyword=keyword, status='수집중')
    db.session.add(campaign)
    db.session.commit()

    # 2. 길이필터가 있으면 더 많이 받아 거른다(오버페치). 정렬·기간은 YouTube sp 필터로.
    over = 8 if video_type == 'shorts' else (3 if video_type == 'long' else 1)
    fetch_n = min(max_videos * over, 50)
    ydl_opts = _ydl_opts({'extract_flat': True, 'playlistend': fetch_n})
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
            transcript = None
            if include_transcript:
                # 첫 영상 제외, 영상 간 사람처럼 지연 → 봇 탐지·차단 회피
                if videos_collected > 0:
                    _polite_delay()
                transcript = _fetch_transcript(video_id)

            db.session.add(VideoTarget(
                campaign_id=campaign.id,
                video_id=video_id,
                title=title,
                url=url[:300],
                description=description,
                transcript=transcript,
                channel_id=(entry.get('channel_id') or entry.get('uploader_id') or None),
                channel_title=((entry.get('channel') or entry.get('uploader') or '')[:200] or None),
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
            'has_transcript': bool(v.transcript),
            'impact_score': v.impact_score,
            'impact_tier': v.impact_tier,
            'channel_title': v.channel_title or '',
            'excluded': bool(v.impact_tier and '제외' in v.impact_tier),
            'collected_at': v.collected_at.isoformat()
        })
    # 제외(댓글3↓) 맨 뒤 → 임팩트 점수 높은 순 → 미분석(None)은 그 뒤
    results.sort(key=lambda r: (r['excluded'], r['impact_score'] is None, -(r['impact_score'] or 0)))
    return jsonify({'videos': results})


@collect_bp.route('/api/youtube/videos/<int:video_id>/transcript', methods=['GET'])
@login_required
def get_video_transcript(video_id):
    """단일 영상의 추출된 자막(스크립트) 전문 반환 (본인 소유만)."""
    gate = _require_collect_feature()
    if gate:
        return gate
    v = (db.session.query(VideoTarget)
         .join(Campaign, VideoTarget.campaign_id == Campaign.id)
         .filter(VideoTarget.id == video_id, Campaign.user_id == current_user.id)
         .first())
    if not v:
        return jsonify({'error': '영상을 찾을 수 없습니다.'}), 404
    return jsonify({
        'video_id': v.video_id,
        'title': v.title,
        'transcript': v.transcript or '',
        'length': len(v.transcript or ''),
    })


@collect_bp.route('/api/youtube/analyze-impact', methods=['POST'])
@login_required
def analyze_impact():
    """선택 영상(또는 캠페인 전체)에 임팩트 스코어/우선순위를 산출·저장."""
    gate = _require_collect_feature()
    if gate:
        return gate
    uid = current_user.id
    data = request.json or {}
    video_ids = data.get('video_ids') or []
    campaign_id = data.get('campaign_id')

    # YouTube Data API 키 (유저 설정 → .env 폴백)
    us = UserSettings.query.filter_by(user_id=uid).first()
    api_key = (us.get('YOUTUBE_API_KEY') if us else None) or os.getenv('YOUTUBE_API_KEY')
    if not api_key:
        return jsonify({'error': '임팩트 분석을 위해 설정 탭에서 YouTube Data API 키를 입력해주세요.'}), 400

    # 대상 영상: video_ids 우선, 없으면 campaign_id 전체 (본인 소유만)
    q = (db.session.query(VideoTarget)
         .join(Campaign, VideoTarget.campaign_id == Campaign.id)
         .filter(Campaign.user_id == uid))
    if video_ids:
        q = q.filter(VideoTarget.id.in_(video_ids))
    elif campaign_id:
        q = q.filter(VideoTarget.campaign_id == campaign_id)
    else:
        return jsonify({'error': '분석할 영상을 선택해주세요.'}), 400
    rows = q.all()
    if not rows:
        return jsonify({'error': '대상 영상이 없습니다.'}), 404

    # video_id(유튜브 11자) → 우리 row 매핑 (동일 영상 중복 대비 리스트)
    by_yt = {}
    for r in rows:
        by_yt.setdefault(r.video_id, []).append(r)

    try:
        analyzed = impact_analyzer.analyze(list(by_yt.keys()), api_key)
    except Exception as e:
        return jsonify({'error': f'임팩트 분석 실패: {str(e)}'}), 500

    updated = 0
    excluded = 0
    for res in analyzed:
        for r in by_yt.get(res['video_id'], []):
            r.impact_score = res.get('impact_score')
            r.impact_tier = res.get('tier')
            r.impact_data = json.dumps(res, ensure_ascii=False)
            if res.get('channel_id'):
                r.channel_id = res['channel_id']
            if res.get('channel_title'):
                r.channel_title = res['channel_title']
            updated += 1
        if res.get('excluded'):
            excluded += 1
    db.session.commit()

    msg = f'{updated}개 영상의 임팩트 점수를 산출했습니다.'
    if excluded:
        msg += f' (댓글 3개 이하 {excluded}개는 제외 표시)'
    return jsonify({
        'success': True,
        'analyzed': len(analyzed),
        'updated': updated,
        'excluded': excluded,
        'results': analyzed,
        'message': msg
    })


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
               '임팩트점수', '우선순위', '자막', '댓글', '대댓글', '상태', '결과 URL', '수집일시'])
    for video, campaign in rows:
        task = (CommentTask.query.filter_by(video_id=video.id)
                .order_by(CommentTask.created_at.desc()).first())
        ws.append([
            campaign.name,
            campaign.keyword,
            video.title,
            video.url,
            video.video_id,
            (video.impact_score if video.impact_score is not None else ''),
            (video.impact_tier or ''),
            (video.transcript or ''),
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
