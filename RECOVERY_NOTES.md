# 복구 노트 — openclaw_power (댓글부스트 / CommentBoost)

> 작성: 2026-05-26 · 윈도우 리셋 후 로컬 소스 복구 작업 기록

## 1. 이 폴더는 어떻게 만들어졌나

원본 개발 폴더는 윈도우 리셋 때 삭제되어 로컬에 없습니다. 이 폴더는 두 소스를 합쳐 **재구성**한 것입니다.

| 구성 요소 | 출처 | 버전 |
|---|---|---|
| 빌드/배포/서버 스캐폴딩 (`license_server/`, `build.py`, `desktop.py`, `fly.toml`, `Dockerfile`, `requirements.txt`, `landing/`, 설치 스크립트 등) | GitHub `hunmaster/openclaw_power` main | **v1.1.0** (2026-03-15) |
| 런타임 소스 (`app.py`, `src/`, `templates/`, `static/`, `config/`, `version.json`) | 설치된 앱 `C:\Users\PC\AppData\Local\Programs\CommentBoost` (자동업데이트로 서버에서 받은 최신본) | **v2.5.35** |

## 2. ⚠️ 버전 불일치 — GitHub에 안 올라간 코드가 있음

- **GitHub은 v1.1.0에서 멈춤**, 실제 배포본은 **v2.5.35**까지 진행됨 (`updater.py` 자동업데이트로 서버 배포).
- v2.5.35에서 추가된 파일 (GitHub에 없음): `src/binding_proxy.py`, `src/models_v2.py`, `src/network_utils.py`, `src/api/collect_api.py`, `src/api/comment_api.py`
- `src/proxy_manager.py` 는 v1.1.0 잔재(GitHub 베이스에서 따라옴). v2.5.35에서는 `binding_proxy.py` + `network_utils.py` 로 대체된 것으로 보임. **사용 여부 확인 후 정리 권장.**

## 3. 🚨 아직 복구 안 된 것 — 서버에만 존재

- **중앙/라이선스 서버 코드의 v1.1.0 이후 변경분**: 이 폴더의 `license_server/` 는 GitHub v1.1.0 기준입니다. 서버(`api.commentboost.cloud`, `commentboost-app.fly.dev`)가 그 이후 수정됐다면, 그 코드는 **배포 서버에만 있고 여기엔 없습니다.** → Fly.io에서 회수 필요.
- **`.env` (오너용 비밀값)**: 로컬에 실제 `.env`는 없고 `.env.example` 템플릿만 있음. 아래 4번 참고.

## 4. .env 복구

설계상 클라이언트는 서버에서 `.env`를 자동으로 받지 않습니다. 고객용 글로벌 키(SMM 등)는 소스에 하드코딩 폴백으로 내장 → 이미 복구됨. **오너 전용 비밀값은 배포 서버의 환경변수로 살아있을 가능성이 높음.**

필요한 키 (`.env.example` 참고): `ADMIN_SECRET_KEY`, `OWNER_SECRET_KEY`, `LICENSE_MODE`, `NOTION_API_TOKEN`, `NOTION_DATABASE_ID`, `LEMONSQUEEZY_API_KEY`, `LEMONSQUEEZY_WEBHOOK_SECRET`, `LEMONSQUEEZY_STORE_ID`, `SMM_API_KEY` 등

### Fly.io에서 회수 (서버 안 건드리고 읽기만)
```powershell
# flyctl 설치 후
fly auth whoami                 # 로그인 계정 확인
fly apps list                   # commentboost-app 등 보이면 접근 권한 있음
fly ssh console -a commentboost-app   # 접속 후 ↓
#   printenv                    # 실제 환경변수 값 출력 (여기서 .env 값 회수)
fly secrets list -a commentboost-app  # (값은 안 보이고 이름만)
```
- 서드파티 키는 Lemonsqueezy / Notion 대시보드에서 재확인·재발급 가능.

## 5. 로그인 401 원인 (참고)

`app.py` `/api/auth/login` → 로컬 DB 확인 후 없으면 `commentboost-app.fly.dev/api/admin/auth-verify` 로 이메일/비번 검증. 서버 URL은 코드에 하드코딩이라 `.env` 없어도 도달함.
- **401 = 중앙서버가 이메일/비밀번호를 거부** (계정·비번 불일치). `.env` 문제 아님.
- 본인 계정 비번 정확히 입력하거나 서버 관리자에서 확인/리셋하면 해결. (본인 계정만 건드리므로 고객 영향 없음)

## 6. 🛑 고객 보호 — 반드시 지킬 것

기존에 로컬 PC에서 쓰는 고객들에게 피해가 가지 않도록:
- 라이브 서버(`api.commentboost.cloud`, `commentboost-app.fly.dev`)의 배포본 / 자동업데이트 파일(`commentboost-latest.zip`, `version.json`)을 함부로 바꾸지 말 것.
- GitHub에 push해서 자동업데이트/배포 파이프라인을 깨지 않도록 주의. (GitHub은 v1.1.0이라, 이 v2.5.35 복구본을 그냥 덮어 push하면 배포 흐름 점검 필수)
- 서버 환경변수/시크릿 변경 금지 (읽기만).

## 7. 다음 작업 권장 순서

1. Fly.io 접근 확인 → 서버 `.env` 값 + `license_server/` 최신 코드 회수
2. `.env.example` → `.env` 복사 후 회수한 값 채우기
3. `git init` + GitHub와 diff 떠서 v1.1.0→v2.5.35 변경분 커밋 (배포 영향 없는 선에서)
4. `proxy_manager.py` 등 잔재 정리
