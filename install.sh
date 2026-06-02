#!/bin/bash
# YouTube 댓글 자동화 - 설치 스크립트
# 고객은 이 파일 하나만 실행하면 됩니다.

echo "========================================"
echo "  YouTube 댓글 자동화 설치"
echo "========================================"
echo ""

# Docker 확인
if ! command -v docker &> /dev/null; then
    echo "[!] Docker가 설치되어 있지 않습니다."
    echo "    https://docs.docker.com/get-docker/ 에서 설치해주세요."
    exit 1
fi

if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo "[!] Docker Compose가 설치되어 있지 않습니다."
    exit 1
fi

# .env 파일 생성
if [ ! -f .env ]; then
    echo "# YouTube 댓글 자동화 설정" > .env
    echo "LICENSE_SERVER_URL=https://license.yourservice.com" >> .env
    echo "" >> .env
    echo "[+] .env 파일이 생성되었습니다."
fi

# config 디렉토리
mkdir -p config

# Docker 이미지 빌드 & 실행
echo ""
echo "[*] Docker 이미지를 빌드합니다..."
docker compose up -d --build

echo ""
echo "========================================"
echo "  설치 완료!"
echo ""
echo "  브라우저에서 접속: http://localhost:5000"
echo "  (최초 접속 시 셋업 위자드가 실행됩니다)"
echo "========================================"
