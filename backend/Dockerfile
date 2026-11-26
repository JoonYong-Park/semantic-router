# Joomini 백엔드 (Flask + DynamoDB). Lambda 런타임과 같은 Python 3.12.
# 단계(target)
#   server (기본): gunicorn으로 띄운다. 로컬 개발(docker-compose.dev.yml).
#   lambda       : 배포 Lambda(joomini-api). Lambda Web Adapter + gunicorn, 응답 스트리밍.
#                  docker build --target lambda -t joomini-api backend
FROM python:3.12-slim AS base

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
RUN pip install --no-cache-dir -r requirements.txt

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1


# ---------------------------------------------------------------- lambda
# 배포 Lambda(joomini-api) 하나가 모든 API를 받는다. Lambda Web Adapter가 gunicorn 앞에서
# 요청을 HTTP로 넘기고, 응답 조각을 그대로 흘려보낸다(invoke mode = response_stream).
# 일반 JSON 응답도 같은 방식으로 나가고, SSE(답변 스트리밍)는 조각마다 바로 브라우저로 간다.
# EventBridge Scheduler 같은 HTTP가 아닌 이벤트는 POST /events 로 넘어온다(routes/events.py).
FROM base AS lambda
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:1.1.0 /lambda-adapter /opt/extensions/lambda-adapter
RUN pip install --no-cache-dir gunicorn==23.0.0
# Lambda는 /tmp만 쓸 수 있다. LibreOffice 프로필, 폰트 캐시, 업로드 캐시, 모델 캐시를 모두 /tmp로.
# 준비 확인은 TCP(포트가 열리면 준비 완료). /health는 X-Origin-Verify가 없으면 403이고 DynamoDB도 부르므로
# 초기화 판단에 쓰지 않는다.
ENV HOME=/tmp XDG_CACHE_HOME=/tmp/.cache HF_HOME=/tmp/hf-cache UPLOAD_DIR=/tmp/uploads PYTHONPATH=/app \
    AWS_LWA_PORT=8000 AWS_LWA_INVOKE_MODE=response_stream AWS_LWA_READINESS_CHECK_PROTOCOL=tcp \
    AWS_LWA_PASS_THROUGH_PATH=/events
COPY app ./app
COPY scripts ./scripts
# Lambda는 root가 아닌 사용자로 실행한다. 체크아웃 환경에 따라 소스가 600 권한일 수 있어 읽기 권한을 연다.
RUN chmod -R a+rX /app
CMD ["gunicorn", "--bind", "127.0.0.1:8000", "--workers", "1", "--worker-class", "gthread", "--threads", "8", "--timeout", "900", "app:app"]


# ---------------------------------------------------------------- server (기본)
FROM base AS server
RUN pip install --no-cache-dir gunicorn==23.0.0
COPY app ./app
COPY scripts ./scripts
ENV HF_HOME=/hf-cache UPLOAD_DIR=/data/uploads
EXPOSE 8000
# 테이블이 없으면 만들고(있으면 그대로) 서버를 띄운다.
# 진행 중인 응답(SSE)을 프로세스 메모리(chat/stream.py)에 두므로 워커는 1개, 동시 요청은 스레드로 받는다.
CMD ["sh", "-c", "python scripts/create_table.py && exec gunicorn --bind 0.0.0.0:8000 --workers 1 --worker-class gthread --threads 16 --timeout 300 app:app"]
