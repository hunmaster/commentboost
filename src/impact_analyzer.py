"""
YouTube 영상 임팩트 분석 (vidIQ식 우선순위) — Apps Script "임팩트 분석 v5" 포팅.

YouTube Data API v3(videos/commentThreads/playlistItems)로 지표를 수집해
0~100점 임팩트 스코어와 우선순위 티어를 산출한다. 산식·가중치는 원본 시트와 동일.
별도 의존성 없이 표준 라이브러리(urllib)만 사용한다.
"""
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API_BASE = "https://www.googleapis.com/youtube/v3"

# ── CONFIG (원본 Apps Script와 동일) ───────────────────────────────────────
# 영상 연령별 VPH(시간당 조회수) 기준값: (최대 일수, 기준 VPH)
AGE_THRESHOLDS = [(7, 500), (30, 100), (90, 50), (float('inf'), 20)]

TIER_RED = 35       # 🔴 최우선
TIER_YELLOW = 20    # 🟡 우선
TIER_GREEN = 10     # 🟢 보통
RED_MOMENTUM_MIN = 0.5  # 🔴 게이트: 최근 7일 일평균 댓글 ≥ 0.5

CHANNEL_AVG_VIDEO_COUNT = 20  # 채널 평균 산출 시 최근 영상 수
BATCH_SIZE = 50

MIN_COMMENTS = 3       # 댓글 이하(≤) 영상은 리스트 제외 (묻힘·대댓글 유도 약함)
EXCLUDE_TIER = '⚪ 제외 (댓글 3↓)'


def age_threshold(elapsed_days):
    for max_days, vph in AGE_THRESHOLDS:
        if elapsed_days <= max_days:
            return vph
    return 20


def classify_duration(seconds):
    if seconds <= 60:
        return '쇼츠'
    if 480 <= seconds <= 900:
        return '미드폼'
    return '롱폼'


def parse_duration(iso):
    """ISO8601 기간(PT#H#M#S) → 초. 파싱 실패 시 0."""
    if not iso:
        return 0
    import re
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', iso)
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def compute_impact_score(d):
    """지표 dict → 0~100 임팩트 스코어. 원본 산식 그대로."""
    score = 0.0

    # 1. 누적 VPH 비율 (25점) — 연령가중. 기준의 2배 이상이면 만점
    if d['threshold'] > 0:
        ratio = d['cumulative_vph'] / d['threshold']
        score += min(25, ratio * 12.5)

    # 2. 댓글 모멘텀 (30점) — 최근 7일 일평균 댓글 수(log)
    if d['comment_momentum'] > 0:
        score += min(30, math.log10(d['comment_momentum'] + 1) * 22)

    # 3. Engagement Rate (15점) — (좋아요+댓글)/조회수, 5%+ 만점
    score += min(15, d['engagement'] * 3)

    # 4. 채널 배율 (15점) — 조회수/채널평균(log)
    if d['channel_multiplier'] is not None and d['channel_multiplier'] > 0:
        score += min(15, math.log10(d['channel_multiplier'] + 1) * 15)

    # 5. 골든타임 (10점) — 업로드 경과 시간
    eh = d['elapsed_hours']
    if eh <= 12:
        score += 10
    elif eh <= 48:
        score += 8
    elif eh <= 168:
        score += 5
    elif eh <= 720:
        score += 2

    # 6. 영상 타입 (5점)
    if d['video_type'] == '쇼츠':
        score += 5
    elif d['video_type'] == '미드폼':
        score += 3

    return min(100, round(score * 10) / 10)


def classify_by_score(score, comment_momentum):
    if score >= TIER_RED and comment_momentum >= RED_MOMENTUM_MIN:
        return '🔴 최우선'
    if score >= TIER_YELLOW:
        return '🟡 우선'
    if score >= TIER_GREEN:
        return '🟢 보통'
    return '⚪ 패스'


# ── YouTube Data API 호출 ──────────────────────────────────────────────────
def _get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode('utf-8', 'ignore'))


def fetch_videos_batch(video_ids, api_key):
    """videos.list — id별 statistics/snippet/contentDetails. {id: item}."""
    url = (API_BASE + "/videos?part=statistics,snippet,contentDetails&id="
           + ",".join(video_ids) + "&key=" + urllib.parse.quote(api_key))
    status, data = _get_json(url)
    if status != 200:
        raise RuntimeError(f"videos.list {status}: {str(data)[:300]}")
    return {it['id']: it for it in (data.get('items') or [])}


def fetch_comment_timeline(video_id, api_key):
    """commentThreads.list — 최근 100개 댓글의 시간 분포. 실패 시 available=False."""
    url = (API_BASE + "/commentThreads?part=snippet&order=time&maxResults=100&videoId="
           + video_id + "&key=" + urllib.parse.quote(api_key))
    try:
        status, data = _get_json(url)
        if status != 200:
            return {'recent7d': 0, 'recent24h': 0, 'total': 0, 'available': False}
        items = data.get('items') or []
        now = datetime.now(timezone.utc)
        recent24h = recent7d = 0
        for it in items:
            try:
                pub = it['snippet']['topLevelComment']['snippet']['publishedAt']
                t = datetime.fromisoformat(pub.replace('Z', '+00:00'))
            except (KeyError, ValueError, TypeError):
                continue
            hours_ago = (now - t).total_seconds() / 3600
            if hours_ago <= 24:
                recent24h += 1
            if hours_ago <= 168:
                recent7d += 1
        return {'recent7d': recent7d, 'recent24h': recent24h, 'total': len(items), 'available': True}
    except Exception:
        return {'recent7d': 0, 'recent24h': 0, 'total': 0, 'available': False}


def fetch_channel_average(channel_id, api_key, cache):
    """채널 최근 영상 평균 조회수. 캐시 사용. 실패 시 None."""
    if channel_id in cache:
        return cache[channel_id]
    try:
        playlist_id = 'UU' + channel_id[2:]  # UCxxx → UUxxx (uploads)
        url1 = (API_BASE + "/playlistItems?part=contentDetails&maxResults="
                + str(CHANNEL_AVG_VIDEO_COUNT) + "&playlistId=" + playlist_id
                + "&key=" + urllib.parse.quote(api_key))
        status, j1 = _get_json(url1)
        if status != 200:
            cache[channel_id] = None
            return None
        ids = [it['contentDetails']['videoId'] for it in (j1.get('items') or [])
               if it.get('contentDetails', {}).get('videoId')]
        if not ids:
            cache[channel_id] = None
            return None
        url2 = (API_BASE + "/videos?part=statistics&id=" + ",".join(ids)
                + "&key=" + urllib.parse.quote(api_key))
        status, j2 = _get_json(url2)
        if status != 200:
            cache[channel_id] = None
            return None
        views = [int(it.get('statistics', {}).get('viewCount', 0) or 0)
                 for it in (j2.get('items') or [])]
        if not views:
            cache[channel_id] = None
            return None
        avg = sum(views) / len(views)
        cache[channel_id] = avg
        return avg
    except Exception:
        cache[channel_id] = None
        return None


def analyze(video_ids, api_key, enable_timeline=True, enable_channel=True):
    """video_id 리스트 → 각 영상의 지표/점수/티어 dict 리스트.

    반환 항목: video_id, view_count, like_count, comment_count, duration,
    published_at(iso), elapsed_hours, cumulative_vph, threshold, engagement,
    recent7d, comment_momentum, channel_multiplier, video_type,
    impact_score, tier, available(bool).
    """
    if not api_key:
        raise ValueError("YouTube Data API 키가 필요합니다. 설정 탭에서 입력하세요.")

    now = datetime.now(timezone.utc)
    channel_cache = {}
    results = []

    for i in range(0, len(video_ids), BATCH_SIZE):
        batch = video_ids[i:i + BATCH_SIZE]
        by_id = fetch_videos_batch(batch, api_key)

        for vid in batch:
            item = by_id.get(vid)
            if not item:
                results.append({'video_id': vid, 'available': False,
                                'impact_score': None, 'tier': '⚪ 패스 (조회불가)'})
                continue

            stats = item.get('statistics', {})
            snippet = item.get('snippet', {})
            view_count = int(stats.get('viewCount', 0) or 0)
            like_count = int(stats.get('likeCount', 0) or 0)
            comment_count = int(stats.get('commentCount', 0) or 0)
            duration = parse_duration((item.get('contentDetails') or {}).get('duration'))
            try:
                published_at = datetime.fromisoformat(
                    (snippet.get('publishedAt') or '').replace('Z', '+00:00'))
            except (ValueError, TypeError):
                published_at = now
            channel_id = snippet.get('channelId')

            elapsed_hours = max(0.1, (now - published_at).total_seconds() / 3600)
            elapsed_days = elapsed_hours / 24
            cumulative_vph = view_count / elapsed_hours
            engagement = ((like_count + comment_count) / view_count * 100) if view_count > 0 else 0
            threshold = age_threshold(elapsed_days)
            video_type = classify_duration(duration)

            recent7d = 0
            comment_momentum = 0.0
            if enable_timeline:
                tl = fetch_comment_timeline(vid, api_key)
                recent7d = tl['recent7d']
                comment_momentum = recent7d / 7

            channel_multiplier = None
            if enable_channel and channel_id:
                ch_avg = fetch_channel_average(channel_id, api_key, channel_cache)
                if ch_avg and ch_avg > 0:
                    channel_multiplier = view_count / ch_avg

            score = compute_impact_score({
                'cumulative_vph': cumulative_vph, 'threshold': threshold,
                'comment_momentum': comment_momentum, 'engagement': engagement,
                'channel_multiplier': channel_multiplier,
                'elapsed_hours': elapsed_hours, 'video_type': video_type,
            })
            # 댓글 ≤3 영상은 리스트 제외(점수와 무관하게 우선순위에서 뺀다)
            excluded = comment_count <= MIN_COMMENTS
            tier = EXCLUDE_TIER if excluded else classify_by_score(score, comment_momentum)

            results.append({
                'video_id': vid,
                'channel_id': channel_id or '',
                'channel_title': (snippet.get('channelTitle') or ''),
                'excluded': excluded,
                'view_count': view_count,
                'like_count': like_count,
                'comment_count': comment_count,
                'duration': duration,
                'published_at': published_at.isoformat(),
                'elapsed_hours': round(elapsed_hours * 10) / 10,
                'cumulative_vph': round(cumulative_vph * 10) / 10,
                'threshold': threshold,
                'engagement': round(engagement * 100) / 100,
                'recent7d': recent7d,
                'comment_momentum': round(comment_momentum * 10) / 10,
                'channel_multiplier': (round(channel_multiplier * 100) / 100
                                       if channel_multiplier is not None else None),
                'video_type': video_type,
                'impact_score': score,
                'tier': tier,
                'available': True,
            })

    return results
