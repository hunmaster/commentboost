FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir flask requests

COPY . .

# DB 파일 영속화 경로
RUN mkdir -p /data
ENV LICENSE_DB_PATH=/data/license.db
ENV LICENSE_ADMIN_KEY=change-me-in-production
ENV LICENSE_PORT=8080

EXPOSE 8080

CMD ["python", "server.py"]
