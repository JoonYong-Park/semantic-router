#!/usr/bin/env bash
# 프론트(Next.js)를 AWS Lambda(컨테이너 이미지 + Lambda Web Adapter)로 배포하고
# 공개 Function URL을 만든다. 저장소 루트에서:
#   API_URL=https://xxxx.execute-api.ap-northeast-2.amazonaws.com/dev bash scripts/deploy_frontend_lambda.sh
#
# 순서: 이미지 빌드(API 주소를 빌드 때 넣음) → ECR 업로드 → 실행 역할 확인 → Lambda 생성/갱신
#       → Function URL(공개) 확인
set -euo pipefail

: "${API_URL:?API_URL(백엔드 API Gateway 주소)이 필요합니다}"
PROFILE="${AWS_PROFILE_NAME-joomini}"   # GitHub Actions에서는 빈 값 → OIDC 임시 자격 증명
REGION="ap-northeast-2"
REPO="joomini-frontend"
FUNC="joomini-frontend"
ROLE="joomini-frontend-lambda-role"
DEFAULT_MODE="${DEFAULT_MODE:-openai}"   # auto 라우팅을 끈 동안은 수동 모드가 기본
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
aws_() { if [ -n "$PROFILE" ]; then aws --profile "$PROFILE" --region "$REGION" "$@"; else aws --region "$REGION" "$@"; fi; }
log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$1"; }

ACCOUNT="$(aws_ sts get-caller-identity --query Account --output text)"
TAG="$(git rev-parse --short HEAD)-$(date +%Y%m%d%H%M%S)"
URI="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${REPO}:${TAG}"

log "1) 이미지 빌드 (API_URL=${API_URL}, 기본 모드=${DEFAULT_MODE})"
docker build --target lambda --provenance=false --platform linux/amd64 \
  --build-arg NEXT_PUBLIC_API_URL="$API_URL" --build-arg NEXT_PUBLIC_DEFAULT_MODE="$DEFAULT_MODE" \
  -t "joomini-frontend-lambda:${TAG}" frontend

log "2) ECR 업로드"
aws_ ecr describe-repositories --repository-names "$REPO" >/dev/null 2>&1 \
  || aws_ ecr create-repository --repository-name "$REPO" >/dev/null
aws_ ecr get-login-password | docker login --username AWS --password-stdin "${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com" >/dev/null
docker tag "joomini-frontend-lambda:${TAG}" "$URI"
docker push -q "$URI"

log "3) 실행 역할 확인"
if ! aws_ iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws_ iam create-role --role-name "$ROLE" --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  aws_ iam attach-role-policy --role-name "$ROLE" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  sleep 10   # 새 역할이 Lambda에서 보이기까지 잠깐 걸린다
fi
ROLE_ARN="$(aws_ iam get-role --role-name "$ROLE" --query Role.Arn --output text)"

log "4) Lambda 생성 또는 갱신"
if aws_ lambda get-function --function-name "$FUNC" >/dev/null 2>&1; then
  aws_ lambda update-function-code --function-name "$FUNC" --image-uri "$URI" >/dev/null
else
  aws_ lambda create-function --function-name "$FUNC" --package-type Image --code ImageUri="$URI" \
    --role "$ROLE_ARN" --memory-size 1024 --timeout 30 --architectures x86_64 >/dev/null
fi
aws_ lambda wait function-updated-v2 --function-name "$FUNC" 2>/dev/null || aws_ lambda wait function-updated --function-name "$FUNC"

log "5) 공개 Function URL"
if ! aws_ lambda get-function-url-config --function-name "$FUNC" >/dev/null 2>&1; then
  aws_ lambda create-function-url-config --function-name "$FUNC" --auth-type NONE >/dev/null
  aws_ lambda add-permission --function-name "$FUNC" --statement-id public-url \
    --action lambda:InvokeFunctionUrl --principal '*' --function-url-auth-type NONE >/dev/null
  # 최근 Function URL은 InvokeFunction 권한도 필요하다. Function URL을 통한 호출로만 제한한다.
  aws_ lambda add-permission --function-name "$FUNC" --statement-id public-invoke-via-url \
    --action lambda:InvokeFunction --principal '*' --invoked-via-function-url >/dev/null 2>&1 || true
fi
URL="$(aws_ lambda get-function-url-config --function-name "$FUNC" --query FunctionUrl --output text)"
log "완료: ${URL}"
