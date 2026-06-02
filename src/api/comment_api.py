"""
Claude AI 기반 댓글 생성 API (Phase 2)
"""
import os
from flask import Blueprint, jsonify, request
import anthropic
from src.models import db, VideoTarget, CommentTask

comment_bp = Blueprint('comment_api', __name__)

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
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
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
def generate_comments_for_campaign():
    """캠페인에 등록된 특정 영상들에 대해 AI 댓글 일괄 생성"""
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

    videos = VideoTarget.query.filter(VideoTarget.id.in_(video_ids)).all()
    
    for video in videos:
        # 기존 대기중인 댓글이 있는지 확인
        existing_task = CommentTask.query.filter_by(video_id=video.id, status='대기').first()
        if existing_task:
            continue
            
        try:
            generated_text = generate_claude_comment(prompt_text, video.title, video.description or "")
            
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
def get_comments():
    """상태별 댓글 조회 (생성완료/업로드중/완료/실패)"""
    status_filter = request.args.get('status')
    query = CommentTask.query
    if status_filter:
        query = query.filter_by(status=status_filter)
        
    comments = query.order_by(CommentTask.created_at.desc()).all()
    results = []
    for c in comments:
        v = VideoTarget.query.get(c.video_id)
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
def update_comment(comment_id):
    data = request.json or {}
    new_text = data.get('generated_text')
    if not new_text:
        return jsonify({'error': '텍스트가 없습니다.'}), 400
        
    comment = CommentTask.query.get(comment_id)
    if not comment:
        return jsonify({'error': '리소스를 찾을 수 없습니다.'}), 404
        
    comment.generated_text = new_text
    db.session.commit()
    
    return jsonify({'success': True})
