#!/usr/bin/env bash
# 백엔드를 AWS Lambda(컨테이너 이미지)로 배포한다. 저장소 루트에서:
#   bash backend/scripts/deploy_lambda.sh            # 처음: zappa deploy
#   bash backend/scripts/deploy_lambda.sh update     # 이후: zappa update
#   FRONTEND_ORIGIN=https://... bash backend/scripts/deploy_lambda.sh update
#
# 순서: zappa_settings.json 생성 → zappa_settings.py 생성 → Lambda 이미지 빌드
#       → ECR 업로드 → zappa deploy/update (Lambda + API Gateway + 실행 역할)
# 필요한 것: Docker, AWS CLI, `joomini` 프로필(aws configure --profile joomini)
# GitHub Actions에서는 AWS_PROFILE_NAME= (빈 값)으로 실행한다. 그러면 프로필 대신 OIDC로 받은
# 임시 자격 증명(환경 변수)을 쓰고, 모델 키·Cognito secret도 환경 변수(GitHub Secrets)에서 읽는다.
set -euo pipefail

MODE="${1:-deploy}"
PROFILE="${AWS_PROFILE_NAME-joomini}"
REGION="ap-northeast-2"
REPO="joomini-backend"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$1"; }
# 프로필이 있으면 --profile, 없으면(CI) 환경 변수 자격 증명
aws() { if [ -n "$PROFILE" ]; then command aws --profile "$PROFILE" "$@"; else command aws "$@"; fi; }
# 도구 컨테이너에 자격 증명을 넘기는 docker 인자. $1 = 컨테이너 안 ~/.aws 위치
aws_docker_args() {
  if [ -n "$PROFILE" ]; then
    echo "-v $HOME/.aws:$1:ro -e AWS_PROFILE=$PROFILE"
  else
    echo "-e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN -e AWS_REGION=$REGION -e AWS_DEFAULT_REGION=$REGION"
  fi
}

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
TAG="$(git rev-parse --short HEAD)-$(date +%Y%m%d%H%M%S)"
URI="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${REPO}:${TAG}"

# Zappa CLI는 가상환경 안에서만 돈다. Mac에 Python을 따로 깔지 않도록 작은 도구 이미지로 실행한다.
if ! docker image inspect joomini-zappa-cli >/dev/null 2>&1; then
  log "Zappa CLI 도구 이미지 빌드"
  docker build -q -t joomini-zappa-cli - <<'EOF' >/dev/null
FROM python:3.12-slim
RUN python -m venv /venv && /venv/bin/pip install --no-cache-dir zappa==0.63.0
ENV VIRTUAL_ENV=/venv PATH=/venv/bin:$PATH
WORKDIR /work
EOF
fi
zappa() {
  # shellcheck disable=SC2046
  docker run --rm $(aws_docker_args /root/.aws) -v "$ROOT/backend:/work" joomini-zappa-cli zappa "$@"
}

log "1) zappa_settings.json 생성"
python3 backend/scripts/make_zappa_settings.py --account "$ACCOUNT" --profile "$PROFILE" \
  --frontend-origin "${FRONTEND_ORIGIN:-*}"

log "2) zappa_settings.py 생성 (이미지에 들어갈 비밀 아닌 설정)"
zappa save-python-settings-file dev >/dev/null

log "3) Lambda 이미지 빌드 (target=lambda)"
# --provenance=false: 빌드 증명 메타데이터가 붙은 이미지 형식은 Lambda가 받지 않는다.
docker build --target lambda --provenance=false --platform linux/amd64 -t "joomini-lambda:${TAG}" backend

log "3-1) 데이터 리소스 확인 (없으면 생성): S3 파일 버킷, DynamoDB 테이블"
FILES_BUCKET="joomini-files-${ACCOUNT}"
if ! aws s3api head-bucket --bucket "$FILES_BUCKET" >/dev/null 2>&1; then
  aws s3api create-bucket --bucket "$FILES_BUCKET" --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION" >/dev/null   # 기본값: 퍼블릭 접근 차단
  log "    S3 버킷 생성: $FILES_BUCKET"
fi
# 테이블 스크립트는 이미 있으면 그대로 둔다. 이미지 안 HOME이 /tmp라 자격 증명을 /tmp/.aws로 마운트한다.
# shellcheck disable=SC2046
docker run --rm --entrypoint python -e AWS_REGION="$REGION" $(aws_docker_args /tmp/.aws) \
  "joomini-lambda:${TAG}" scripts/create_table.py

log "4) ECR 업로드: ${REPO}:${TAG}"
aws ecr describe-repositories --repository-names "$REPO" --region "$REGION" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "$REPO" --region "$REGION" \
       --image-scanning-configuration scanOnPush=false >/dev/null
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com" >/dev/null
docker tag "joomini-lambda:${TAG}" "$URI"
docker push -q "$URI"

log "5) zappa ${MODE} dev"
zappa "$MODE" dev -d "$URI"

log "6) API Gateway 요청 제한 (초당 2건, 순간 5건) - 공개 데모가 모델 API 비용을 과하게 쓰지 않게"
API_ID="$(aws apigateway get-rest-apis --region "$REGION" --query "items[?name=='joomini-dev'].id" --output text)"
aws apigateway update-stage --region "$REGION" --rest-api-id "$API_ID" --stage-name dev \
  --patch-operations 'op=replace,path=/*/*/throttling/rateLimit,value=2' 'op=replace,path=/*/*/throttling/burstLimit,value=5' >/dev/null

log "완료: https://${API_ID}.execute-api.${REGION}.amazonaws.com/dev"
