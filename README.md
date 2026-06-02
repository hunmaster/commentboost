# YouTube 댓글 자동화 프로그램

노션 데이터베이스에서 유튜브 링크와 댓글 원고를 읽어 자동으로 댓글을 작성하고, 결과 URL을 노션에 저장합니다.

## 주요 기능

- **노션 DB 연동**: 작업 목록 읽기 / 결과 저장
- **시크릿 모드**: 브라우저 세션 격리
- **안티디텍트 지문**: 계정별 고유 브라우저 fingerprint (User-Agent, 해상도, WebGL 등)
- **IP 관리**: 프록시 로테이션으로 부계정별 IP 분리
- **안전 규칙**: 1일 댓글 수 제한, 시간 간격, 유사 문구 차단, 링크 차단
- **대량 처리**: 하루 200개 이상 댓글 작업 대응

## 가이드라인 반영 사항

### IP 가이드라인
| 구분 | IP 관리 | 프로그램 동작 |
|------|---------|--------------|
| 찐 계정 | 같은 IP OK | 프록시 없이 실행 |
| 부계정 | 계정마다 IP 변경 필수 | 프록시 자동 로테이션 |
| 계정 전환 | 비행기모드 ON/OFF | 브라우저 종료 + 프록시 변경 + 대기 |

### 유튜브 바이럴 가이드라인
| 규칙 | 설정값 | 조절 |
|------|--------|------|
| 1일 1계정 최대 댓글 | 8개 (기본) | `MAX_COMMENTS_PER_DAY` |
| 같은 영상 다른 계정 간격 | 30분 | `SAME_VIDEO_INTERVAL_MIN` |
| 연속 댓글 간격 | 90초 | `COMMENT_INTERVAL_SEC` |
| 링크 포함 댓글 | 차단 | 자동 |
| 유사 문구 반복 | 차단 (80% 유사도) | 자동 |
| 안티디텍트 지문 | 계정별 자동 생성 | `config/fingerprints.json` |

### 대량 처리 예시
- 계정 30개 x 8개/일 = **240개/일** 처리 가능
- 계정 50개 x 5개/일 = **250개/일** 처리 가능
- `MAX_COMMENTS_PER_DAY` 값을 모니터링하며 조절

## 설치

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. Playwright 브라우저 설치
playwright install chromium

# 3. 환경 변수 설정
cp .env.example .env
# .env 파일을 열어서 Notion API 토큰 등 설정

# 4. 계정 설정
cp config/accounts.example.json config/accounts.json
# accounts.json에 YouTube 계정 정보 입력

# 5. (선택) 프록시 설정 - 부계정 사용 시
cp config/proxies.example.txt config/proxies.txt
# proxies.txt에 프록시 목록 입력
```

## 실행

```bash
python run.py
```

## 노션 DB 구조

| 컬럼명 | 타입 | 설명 |
|--------|------|------|
| 유튜브 링크 | URL / 텍스트 | 댓글 작성할 영상 URL |
| 댓글 원고 | 텍스트 | 작성할 댓글 내용 |
| 댓글 URL | URL | (자동) 작성된 댓글 URL |
| 상태 | 상태/셀렉트 | 대기 → 완료/에러 |
| 계정 | 텍스트 | (선택) 사용할 계정 라벨 |

> 컬럼명은 `.env`에서 변경 가능합니다.

## 프로젝트 구조

```
├── run.py                      # 실행 스크립트
├── .env.example                # 환경 변수 템플릿
├── requirements.txt            # Python 의존성
├── config/
│   ├── accounts.example.json   # 계정 설정 예시
│   ├── proxies.example.txt     # 프록시 목록 예시
│   ├── fingerprints.json       # (자동생성) 계정별 브라우저 지문
│   └── comment_history.json    # (자동생성) 댓글 히스토리
└── src/
    ├── main.py                 # 메인 오케스트레이터
    ├── notion_client.py        # Notion API 연동
    ├── proxy_manager.py        # 프록시/IP 관리
    ├── youtube_bot.py          # YouTube 브라우저 자동화
    ├── fingerprint.py          # 안티디텍트 브라우저 지문
    └── safety_rules.py         # 댓글 안전 규칙
```

## 주의사항

- YouTube는 자동화를 탐지할 수 있습니다. 안전 규칙 설정을 적절히 조절하세요.
- 2단계 인증(2FA)이 설정된 계정은 `HEADLESS=false`로 실행하여 수동 인증이 필요합니다.
- 계정 정보(`.env`, `accounts.json`)는 `.gitignore`에 포함되어 있으며 절대 커밋하지 마세요.
- 신규 계정은 최소 1개월 숙성 후 바이럴에 투입하세요.
- 구글 계정 매매는 구글 약관 위반이며, 대량 바이럴은 유튜브 정책 위반으로 채널 해지까지 이어질 수 있습니다.
