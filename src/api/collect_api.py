"""
YouTube Data Collection API (Phase 1)
"""
import yt_dlp
from flask import Blueprint, jsonify, request
from src.models import db, Campaign, VideoTarget

collect_bp = Blueprint('collect_api', __name__)

@collect_bp.route('/api/youtube/collect', methods=['POST'])
def start_collect():
    data = request.json or {}
    keyword = data.get('keyword', '').strip()
    max_videos = int(data.get('max_videos', 10))
    campaign_name = data.get('campaign_name', f"{keyword} 캠페인")

    if not keyword:
        return jsonify({'error': '키워드가 필요합니다.'}), 400

    # 1. 새 캠페인 생성
    campaign = Campaign(name=campaign_name, keyword=keyword, status='수집중')
    db.session.add(campaign)
    db.session.commit()

    # 2. yt-dlp 옵션 설정
    ydl_opts = {
        'extract_flat': True,
        'quiet': True,
        'simulate': True,
        'max_downloads': max_videos,
        'force_generic_extractor': False,
    }

    try:
        videos_collected = 0
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # ytsearch 쿼리
            results = ydl.extract_info(f"ytsearch{max_videos}:{keyword}", download=False)
            
            if 'entries' in results:
                for entry in results['entries']:
                    if not entry:
                        continue
                    
                    video_id = entry.get('id')
                    title = entry.get('title')
                    url = entry.get('url', f"https://www.youtube.com/watch?v={video_id}")
                    description = entry.get('description', '')

                    # 중복 확인
                    existing_video = VideoTarget.query.filter_by(video_id=video_id).first()
                    if not existing_video:
                        new_video = VideoTarget(
                            campaign_id=campaign.id,
                            video_id=video_id,
                            title=title,
                            url=url,
                            description=description
                        )
                        db.session.add(new_video)
                        videos_collected += 1

                db.session.commit()
        
        campaign.status = '완료'
        db.session.commit()

        return jsonify({
            'success': True,
            'campaign_id': campaign.id,
            'videos_collected': videos_collected,
            'message': f"키워드 '{keyword}'로 {videos_collected}개의 영상을 수집했습니다."
        })
    except Exception as e:
        campaign.status = '실패'
        db.session.commit()
        return jsonify({'error': str(e)}), 500

@collect_bp.route('/api/youtube/campaigns', methods=['GET'])
def get_campaigns():
    campaigns = Campaign.query.order_by(Campaign.created_at.desc()).all()
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
def get_campaign_videos(campaign_id):
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
