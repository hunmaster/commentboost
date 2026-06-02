FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

WORKDIR /app

# 한글 폰트 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드 복사
COPY . .

# config, data 디렉토리 생성
RUN mkdir -p /app/config /app/data

# 기본 환경변수 (고객 모드)
ENV LICENSE_MODE=client
ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1

# 포트 노출
EXPOSE 5000

# 실행
CMD ["python", "app.py"]
