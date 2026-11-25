# Joomini 백엔드 (Flask + DynamoDB). Lambda 런타임과 같은 Python 3.12.
# 단계(target)
#   server (기본): gunicorn으로 띄운다. 로컬 개발(docker-compose.dev.yml).
#   lambda       : 같은 앱을 AWS Lambda 컨테이너 이미지로. Zappa 핸들러로 요청을 받는다.
#                  docker build --target lambda -t joomini-lambda backend
#   stream       : 답변 스트리밍 전용 Lambda. Lambda Web Adapter + gunicorn (응답 스트리밍)
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
FROM base AS lambda
# Lambda는 /tmp만 쓸 수 있다. LibreOffice 프로필, 폰트 캐시, 업로드 캐시, 모델 캐시를 모두 /tmp로.
RUN pip install --no-cache-dir awslambdaric==4.2.3 zappa==0.63.0
ENV HOME=/tmp XDG_CACHE_HOME=/tmp/.cache HF_HOME=/tmp/hf-cache UPLOAD_DIR=/tmp/uploads PYTHONPATH=/app
COPY app ./app
COPY scripts ./scripts
# zappa save-python-settings-file로 만든 설정 (배포 스크립트가 빌드 직전에 생성, git 제외)
COPY zappa_settings.py ./zappa_settings.py
ENTRYPOINT ["python", "-m", "awslambdaric"]
CMD ["zappa.handler.lambda_handler"]


# ---------------------------------------------------------------- stream
# 답변 스트리밍(SSE) 전용 Lambda (joomini-stream). Zappa 핸들러는 응답을 모아 한 번에 돌려주므로,
# Lambda Web Adapter가 gunicorn 앞에서 응답 조각을 그대로 흘려보낸다(invoke mode = response_stream).
# API Gateway가 /stream/* 만 이 함수로 보낸다(backend/scripts/deploy_stream_lambda.sh).
FROM base AS stream
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.8.4 /lambda-adapter /opt/extensions/lambda-adapter
RUN pip install --no-cache-dir gunicorn==23.0.0
ENV HOME=/tmp XDG_CACHE_HOME=/tmp/.cache HF_HOME=/tmp/hf-cache UPLOAD_DIR=/tmp/uploads PYTHONPATH=/app \
    AWS_LWA_PORT=8000 AWS_LWA_INVOKE_MODE=response_stream AWS_LWA_READINESS_CHECK_PATH=/health \
    RESPONSE_STREAMING=1
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
