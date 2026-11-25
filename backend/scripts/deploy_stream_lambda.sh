#!/usr/bin/env bash
# 답변 스트리밍 Lambda(joomini-stream)를 배포하고 API Gateway의 /stream/* 경로를 이 함수로 연결한다.
# deploy_lambda.sh(Zappa) 다음에 실행한다. 저장소 루트에서:
#   bash backend/scripts/deploy_stream_lambda.sh
#
# 왜 따로 두나: Zappa 핸들러는 Flask 응답을 끝까지 모아 한 번에 돌려줘서 SSE가 흐르지 않는다.
# 같은 백엔드 코드를 Lambda Web Adapter + gunicorn으로 띄운 함수(응답 스트리밍)를 하나 더 두고,
# API Gateway REST의 응답 스트리밍(STREAM) 통합으로 /stream/* 만 이 함수로 보낸다.
# 나머지 API는 그대로 Zappa Lambda가 받는다.
#
# 순서: 이미지 빌드(target=stream) → ECR 업로드 → 환경 변수 준비(Zappa Lambda와 같게)
#       → Lambda 생성/갱신 → API Gateway /stream/{proxy+} 연결(STREAM, 15분) → 스테이지 재배포
set -euo pipefail

PROFILE="${AWS_PROFILE_NAME-joomini}"   # GitHub Actions에서는 빈 값 → OIDC 임시 자격 증명
REGION="ap-northeast-2"
REPO="joomini-backend"
FUNC="joomini-stream"
ZAPPA_FUNC="joomini-dev"
API_NAME="joomini-dev"
STAGE="dev"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$1"; }
aws() { if [ -n "$PROFILE" ]; then command aws --profile "$PROFILE" --region "$REGION" "$@"; else command aws --region "$REGION" "$@"; fi; }

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
TAG="stream-$(git rev-parse --short HEAD)-$(date +%Y%m%d%H%M%S)"
URI="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${REPO}:${TAG}"

log "1) 이미지 빌드 (target=stream, 기반 레이어는 Zappa 이미지와 공유)"
docker build --target stream --provenance=false --platform linux/amd64 -t "joomini-stream:${TAG}" backend

log "2) ECR 업로드: ${REPO}:${TAG}"
aws ecr get-login-password | docker login --username AWS --password-stdin "${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com" >/dev/null
docker tag "joomini-stream:${TAG}" "$URI"
docker push -q "$URI"

log "3) 환경 변수: Zappa Lambda의 환경 변수 + zappa_settings.json의 앱 설정 (값은 출력하지 않는다)"
ENV_FILE="$(mktemp)"
REMOTE_ENV="$(mktemp)"
trap 'rm -f "$ENV_FILE" "$REMOTE_ENV"' EXIT
chmod 600 "$ENV_FILE" "$REMOTE_ENV"
aws lambda get-function-configuration --function-name "$ZAPPA_FUNC" --query 'Environment.Variables' --output json > "$REMOTE_ENV"
python3 - "$REMOTE_ENV" "$ROOT/backend/zappa_settings.json" "$ENV_FILE" <<'PY'
import json, sys
remote_path, settings_path, out = sys.argv[1], sys.argv[2], sys.argv[3]
remote = json.load(open(remote_path)) or {}
try:
    app_env = json.load(open(settings_path))["dev"].get("environment_variables", {})
except FileNotFoundError:
    sys.exit("backend/zappa_settings.json이 없습니다. deploy_lambda.sh를 먼저 실행하세요.")
env = {**remote, **app_env, "RESPONSE_STREAMING": "1"}
json.dump({"Variables": env}, open(out, "w"))
print(f"    {len(env)}개 변수")
PY

ROLE_ARN="$(aws lambda get-function-configuration --function-name "$ZAPPA_FUNC" --query Role --output text)"

log "4) Lambda 생성 또는 갱신: ${FUNC}"
if aws lambda get-function --function-name "$FUNC" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FUNC" --image-uri "$URI" >/dev/null
  aws lambda wait function-updated-v2 --function-name "$FUNC"
  aws lambda update-function-configuration --function-name "$FUNC" --role "$ROLE_ARN" \
    --memory-size 3008 --timeout 900 --ephemeral-storage Size=2048 \
    --environment "file://${ENV_FILE}" >/dev/null
else
  aws lambda create-function --function-name "$FUNC" --package-type Image --code ImageUri="$URI" \
    --role "$ROLE_ARN" --memory-size 3008 --timeout 900 --ephemeral-storage Size=2048 \
    --architectures x86_64 --environment "file://${ENV_FILE}" >/dev/null
fi
aws lambda wait function-updated-v2 --function-name "$FUNC" 2>/dev/null || aws lambda wait function-active-v2 --function-name "$FUNC"
FUNC_ARN="$(aws lambda get-function --function-name "$FUNC" --query Configuration.FunctionArn --output text)"

log "5) API Gateway: /stream/{proxy+} → ${FUNC} (응답 스트리밍, 최대 15분)"
API_ID="$(aws apigateway get-rest-apis --query "items[?name=='${API_NAME}'].id" --output text)"
resource_id() { aws apigateway get-resources --rest-api-id "$API_ID" --limit 500 --query "items[?path=='$1'].id" --output text; }
ROOT_ID="$(resource_id /)"
STREAM_ID="$(resource_id /stream)"
[ -n "$STREAM_ID" ] || STREAM_ID="$(aws apigateway create-resource --rest-api-id "$API_ID" --parent-id "$ROOT_ID" --path-part stream --query id --output text)"
PROXY_ID="$(resource_id '/stream/{proxy+}')"
[ -n "$PROXY_ID" ] || PROXY_ID="$(aws apigateway create-resource --rest-api-id "$API_ID" --parent-id "$STREAM_ID" --path-part '{proxy+}' --query id --output text)"
if ! aws apigateway get-method --rest-api-id "$API_ID" --resource-id "$PROXY_ID" --http-method ANY >/dev/null 2>&1; then
  aws apigateway put-method --rest-api-id "$API_ID" --resource-id "$PROXY_ID" --http-method ANY \
    --authorization-type NONE --request-parameters method.request.path.proxy=true >/dev/null
fi
aws apigateway put-integration --rest-api-id "$API_ID" --resource-id "$PROXY_ID" --http-method ANY \
  --type AWS_PROXY --integration-http-method POST \
  --uri "arn:aws:apigateway:${REGION}:lambda:path/2021-11-15/functions/${FUNC_ARN}/response-streaming-invocations" \
  --response-transfer-mode STREAM --timeout-in-millis 900000 >/dev/null
aws lambda add-permission --function-name "$FUNC" --statement-id apigateway-stream \
  --action lambda:InvokeFunction --principal apigateway.amazonaws.com \
  --source-arn "arn:aws:execute-api:${REGION}:${ACCOUNT}:${API_ID}/*/*/stream/*" >/dev/null 2>&1 || true
aws apigateway create-deployment --rest-api-id "$API_ID" --stage-name "$STAGE" \
  --description "stream ${TAG}" >/dev/null

log "완료: /stream/* → ${FUNC}"
