# Joomini 백엔드 (Flask + DynamoDB). Lambda 런타임과 같은 Python 3.12.
# 서버 배포(docker-compose.yml)는 이 이미지를 gunicorn으로 돌리고,
# 로컬 개발(docker-compose.dev.yml)은 코드를 마운트하고 flask 개발 서버로 command만 바꾼다.
FROM python:3.12-slim

WORKDIR /app

# 시스템 패키지 (LibreOffice: 미리보기/PDF 변환, 폰트: 문서 생성)
RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice \
      poppler-utils \
      fonts-nanum \
      fonts-noto-cjk \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

# 무거운 모델 의존성은 별도 레이어 - 앱 의존성만 바뀌면 이 레이어 캐시를 그대로 쓴다.
COPY requirements-models.txt .
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-models.txt

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn==23.0.0

COPY app ./app
COPY scripts ./scripts

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/hf-cache UPLOAD_DIR=/data/uploads

EXPOSE 8000

# 테이블이 없으면 만들고(있으면 그대로) 서버를 띄운다.
# 진행 중인 응답(SSE)을 프로세스 메모리(chat/stream.py)에 두므로 워커는 1개, 동시 요청은 스레드로 받는다.
CMD ["sh", "-c", "python scripts/create_table.py && exec gunicorn --bind 0.0.0.0:8000 --workers 1 --worker-class gthread --threads 16 --timeout 300 app:app"]
