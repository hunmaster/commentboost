# 라이선스 서버 배포 가이드

## 추천: Fly.io (간편 Docker 배포)

### 1. Fly CLI 설치 & 로그인
```bash
# macOS
brew install flyctl

# Linux
curl -L https://fly.io/install.sh | sh

# 가입 & 로그인
fly auth signup
fly auth login
```

### 2. 배포
```bash
cd license_server

# 앱 생성 (이미 fly.toml이 있으므로)
fly launch --copy-config --yes

# 영구 볼륨 생성 (DB 데이터 보존용, 1GB)
fly volumes create license_data --size 1 --region nrt

# 관리자 키 설정 (반드시 강력한 키로!)
fly secrets set ADMIN_API_KEY="여기에-강력한-관리자키-입력"

# 배포
fly deploy

# 상태 확인
fly status
```

### 3. 확인
```bash
# 헬스체크
curl https://openclaw-license.fly.dev/api/health

# 관리자 대시보드
# 브라우저에서: https://openclaw-license.fly.dev/admin
```

### 4. 커스텀 도메인 (선택)
```bash
# 도메인 연결
fly certs add license.yourdomain.com

# DNS 설정: CNAME → openclaw-license.fly.dev
# SSL 자동 발급됨
```

### 5. 유용한 명령어
```bash
# 로그 확인
fly logs

# 앱 재시작
fly apps restart

# DB 백업 (SSH 접속)
fly ssh console
cp /data/license.db /tmp/backup.db
exit
fly sftp get /tmp/backup.db ./backup_$(date +%Y%m%d).db
```

## 고객 프로그램에 서버 URL 반영

고객 `.env` 또는 `docker-compose.yml`의 `LICENSE_SERVER_URL`을 실제 URL로 변경:
```yaml
environment:
  - LICENSE_SERVER_URL=https://openclaw-license.fly.dev
  # 또는 커스텀 도메인 사용 시:
  # - LICENSE_SERVER_URL=https://license.yourdomain.com
```

## 비용
- **무료 티어**: 공유 CPU 1x, 256MB RAM (라이선스 서버에 충분)
- **볼륨**: 1GB → 약 $0.15/월
- **총 예상 비용**: 거의 무료 ~ $1/월 이하

---

## 대안: Oracle Cloud Free Tier

Oracle Cloud 가입이 가능한 경우:

### 1. 서버 생성
1. [Oracle Cloud](https://cloud.oracle.com) 가입 (신용카드 필요, 과금 없음)
2. Compute > Instances > Create Instance
3. Shape: VM.Standard.E2.1.Micro (Always Free)
4. OS: Ubuntu 22.04
5. SSH 키 등록 후 생성

### 2. 서버 접속 및 Docker 설치
```bash
ssh ubuntu@<서버IP>

# Docker 설치
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker ubuntu
# 재접속
exit
ssh ubuntu@<서버IP>
```

### 3. 라이선스 서버 배포
```bash
# 파일 업로드 (로컬에서)
scp -r license_server/ ubuntu@<서버IP>:~/license-server/

# 서버에서 실행
ssh ubuntu@<서버IP>
cd ~/license-server

# 관리자 키 설정 (반드시 변경!)
export LICENSE_ADMIN_KEY="여기에-안전한-키-입력"

# 실행
docker compose up -d --build

# 확인
curl http://localhost:5100/api/health
```

### 4. 도메인 연결 (선택)
```bash
# Nginx 리버스 프록시 설치
sudo apt install -y nginx certbot python3-certbot-nginx

# Nginx 설정
sudo tee /etc/nginx/sites-available/license << 'NGINX'
server {
    server_name license.yourdomain.com;
    location / {
        proxy_pass http://localhost:5100;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
NGINX

sudo ln -s /etc/nginx/sites-available/license /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# SSL 인증서 (HTTPS)
sudo certbot --nginx -d license.yourdomain.com
```

### 5. 방화벽 설정
Oracle Cloud 콘솔에서:
- Networking > Virtual Cloud Networks > Security Lists
- Ingress Rule 추가: TCP 80, 443 허용
