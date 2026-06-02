# 데스크탑 앱 전환 계획 (v2)

## 목표
Flask 웹 대시보드 → PyWebView 기반 데스크탑 앱(.exe)으로 전환
결제/구독 시스템을 웹훅 의존 → 클라이언트 폴링 방식으로 변경

## 아키텍처

```
commentboost.cloud (Fly.io)
├── 랜딩 페이지 (소개, 가격, .exe 다운로드)
└── 라이선스 서버 (api.commentboost.cloud)

고객 PC (Windows)
└── CommentBoost.exe (PyWebView + Flask 내장)
    ├── 네이티브 앱 창 (브라우저 URL바 없음)
    ├── 대시보드 UI (Flask 내부 서버)
    ├── 유튜브 자동화 (Playwright)
    ├── 결제: 외부 브라우저 → Lemon Squeezy → 앱이 폴링으로 확인
    └── 라이선스 검증 → api.commentboost.cloud
```

## Phase 1: PyWebView 데스크탑 래퍼 (desktop.py)
- Flask를 별도 스레드로 실행, PyWebView 네이티브 창에서 로드
- HTTPS 불필요 (내부 통신이므로 http://127.0.0.1 사용)
- 앱 종료 시 Flask 서버도 자동 종료

## Phase 2: 결제 폴링 시스템 (웹훅 대체)
- 결제 버튼 클릭 → 외부 브라우저로 Lemon Squeezy 체크아웃 열기
- 앱에서 주기적으로 Lemon Squeezy API 폴링 → 결제 완료 감지
- 자동으로 구독 활성화 / 크레딧 충전 처리
- 기존 웹훅 엔드포인트는 유지 (서버 배포 시 사용 가능)

## Phase 3: PyInstaller .exe 빌드
- build.py 스크립트로 .exe 패키징
- Playwright Chromium 브라우저 번들링
- 단일 폴더 zip 배포 (불사자 방식)

## Phase 4: 랜딩페이지 + 배포
- 다운로드 페이지에 .exe 링크 추가
- GitHub Releases 또는 landing/releases/ 활용
- 자동 업데이트 (기존 updater.py 활용)

## 이번 세션 작업
1. desktop.py 생성 (PyWebView 래퍼)
2. 결제 폴링 시스템 구현
3. app.py 수정 (데스크탑 모드 지원, HTTPS 코드 정리)
4. requirements.txt 업데이트
5. build.py 생성 (PyInstaller 빌드)
6. start.bat 업데이트
