"""
SQLAlchemy 모델 정의 (v2 통합 SaaS 용)
- 자체 DB 기반 작업을 위한 테이블
"""

from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Campaign(db.Model):
    """유튜브 수집 캠페인 관리"""
    __tablename__ = 'campaigns'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    keyword = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(50), default='대기중')  # 대기중/수집중/완료
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    videos = db.relationship('VideoTarget', backref='campaign', lazy=True, cascade="all, delete-orphan")

class VideoTarget(db.Model):
    """수집된 유튜브 영상 데이터"""
    __tablename__ = 'videos'
    
    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.id'), nullable=False)
    video_id = db.Column(db.String(50), nullable=False, unique=True)
    title = db.Column(db.String(300), nullable=False)
    url = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, nullable=True)
    collected_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    comments = db.relationship('CommentTask', backref='video', lazy=True, cascade="all, delete-orphan")

class CommentTask(db.Model):
    """생성된 댓글 및 업로드 관리"""
    __tablename__ = 'comments'
    
    id = db.Column(db.Integer, primary_key=True)
    video_id = db.Column(db.Integer, db.ForeignKey('videos.id'), nullable=False)
    account_label = db.Column(db.String(100), nullable=True)  # 업로드할 계정
    prompt = db.Column(db.Text, nullable=True)               # AI 생성에 사용된 프롬프트
    generated_text = db.Column(db.Text, nullable=False)      # AI가 생성한 댓글 내용
    status = db.Column(db.String(50), default='대기')        # 대기/생성완료/업로드중/완료/실패
    result_url = db.Column(db.String(300), nullable=True)    # 업로드 후 결과 URL
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    executed_at = db.Column(db.DateTime, nullable=True)
