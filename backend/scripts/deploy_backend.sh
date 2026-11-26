#!/usr/bin/env bash
# 백엔드를 배포한다: Lambda 하나(joomini-api) + API Gateway(REGIONAL, 응답 스트리밍) + 예약 작업.
# 리소스 정의는 infra/backend.yaml(CloudFormation 스택 joomini-backend) 한 곳에 있다. 저장소 루트에서:
#   bash backend/scripts/deploy_backend.sh
#   FRONTEND_ORIGIN=https://... bash backend/scripts/deploy_backend.sh
#
# 순서
#   1) 이미지 빌드 (backend/Dockerfile target=lambda: Lambda Web Adapter + gunicorn + Flask)
#   2) 데이터 리소스 확인 (S3 파일 버킷, DynamoDB 테이블. 없으면 생성)
#   3) ECR 업로드
#   4) 모델 API 키, Cognito client secret을 Secrets Manager(joomini/app)에 동기화 (값은 출력하지 않음)
#   5) CloudFormation 스택 배포 (이미지 URI만 바뀌면 Lambda 코드만 갱신된다)
#   6) CloudFront의 API 오리진이 이 스택의 API를 가리키는지 확인하고, 다르면 바꾼다(로컬 실행에서만)
#
# 필요한 것: Docker, AWS CLI, `joomini` 프로필. GitHub Actions에서는 AWS_PROFILE_NAME= (빈 값)으로
# 실행해 OIDC 임시 자격 증명을 쓰고, 비밀값은 환경 변수(GitHub Secrets)에서 읽는다.
set -euo pipefail

PROFILE="${AWS_PROFILE_NAME-joomini}"
REGION="ap-northeast-2"
REPO="joomini-backend"
STACK="joomini-backend"
CF_STACK="joomini-cloudfront"
TABLE="joomini"
APP_SECRET_ID="joomini/app"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# 로그인(Cognito) 설정 중 비밀이 아닌 값. 환경 변수로 바꿀 수 있다.
COGNITO_USER_POOL_ID="${COGNITO_USER_POOL_ID:-ap-northeast-2_eQhPcU4Lt}"
COGNITO_CLIENT_ID="${COGNITO_CLIENT_ID:-3e0c78p3tdkuenvn455j1rd8b1}"
COGNITO_DOMAIN="${COGNITO_DOMAIN:-https://ap-northeast-2eqhpcu4lt.auth.ap-northeast-2.amazoncognito.com}"

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$1"; }
aws() { if [ -n "$PROFILE" ]; then command aws --profile "$PROFILE" --region "$REGION" "$@"; else command aws --region "$REGION" "$@"; fi; }
# 도구 컨테이너에 자격 증명을 넘기는 docker 인자. $1 = 컨테이너 안 ~/.aws 위치
aws_docker_args() {
  if [ -n "$PROFILE" ]; then
    echo "-v $HOME/.aws:$1:ro -e AWS_PROFILE=$PROFILE"
  else
    echo "-e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN"
  fi
}

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
TAG="$(git rev-parse --short HEAD)-$(date +%Y%m%d%H%M%S)"
URI="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${REPO}:${TAG}"
FILES_BUCKET="joomini-files-${ACCOUNT}"

# 서비스 주소: 환경 변수가 없으면 CloudFront 스택 출력에서 읽는다
if [ -z "${FRONTEND_ORIGIN:-}" ]; then
  FRONTEND_ORIGIN="$(aws cloudformation describe-stacks --stack-name "$CF_STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='PublicUrl'].OutputValue" --output text 2>/dev/null || true)"
fi
[ -n "$FRONTEND_ORIGIN" ] && [ "$FRONTEND_ORIGIN" != "None" ] || { echo "FRONTEND_ORIGIN을 알 수 없습니다"; exit 1; }

log "1) 이미지 빌드 (target=lambda)"
# --provenance=false: 빌드 증명 메타데이터가 붙은 이미지 형식은 Lambda가 받지 않는다.
docker build --target lambda --provenance=false --platform linux/amd64 -t "joomini-api:${TAG}" backend

log "2) 데이터 리소스 확인 (없으면 생성): S3 파일 버킷, DynamoDB 테이블"
if ! aws s3api head-bucket --bucket "$FILES_BUCKET" >/dev/null 2>&1; then
  aws s3api create-bucket --bucket "$FILES_BUCKET" \
    --create-bucket-configuration LocationConstraint="$REGION" >/dev/null   # 기본값: 퍼블릭 접근 차단
  log "    S3 버킷 생성: $FILES_BUCKET"
fi
# 브라우저가 presigned POST로 파일을 바로 올린다(Lambda 요청 크기 6MB 상한 회피). 서비스 도메인에서만 허용.
aws s3api put-bucket-cors --bucket "$FILES_BUCKET" --cors-configuration \
  "{\"CORSRules\":[{\"AllowedOrigins\":[\"${FRONTEND_ORIGIN}\"],\"AllowedMethods\":[\"POST\"],\"AllowedHeaders\":[\"*\"],\"MaxAgeSeconds\":3000}]}"
# 테이블 스크립트는 이미 있으면 그대로 둔다. 이미지 안 HOME이 /tmp라 자격 증명을 /tmp/.aws로 마운트한다.
# shellcheck disable=SC2046
docker run --rm --entrypoint python -e AWS_REGION="$REGION" -e AWS_DEFAULT_REGION="$REGION" \
  $(aws_docker_args /tmp/.aws) "joomini-api:${TAG}" scripts/create_table.py

log "3) ECR 업로드: ${REPO}:${TAG}"
aws ecr describe-repositories --repository-names "$REPO" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "$REPO" >/dev/null
aws ecr get-login-password | docker login --username AWS --password-stdin "${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com" >/dev/null
docker tag "joomini-api:${TAG}" "$URI"
docker push -q "$URI"

log "4) 비밀값 동기화: Secrets Manager ${APP_SECRET_ID}"
# SKIP_SECRET_SYNC=1: 로컬 .env가 운영 키와 다를 때, 비밀에 이미 있는 값을 그대로 쓴다.
if [ "${SKIP_SECRET_SYNC:-}" = "1" ]; then
  log "    건너뜀 (SKIP_SECRET_SYNC=1)"
else
  python3 backend/scripts/sync_app_secret.py --profile "$PROFILE" --secret-id "$APP_SECRET_ID"
fi

log "5) CloudFormation 스택 배포: ${STACK} (infra/backend.yaml)"
# 비어 있으면 넘기지 않는다(템플릿 기본값 또는 직전 값 유지)
OPTIONAL=()
[ -n "${COGNITO_REDIRECT_URI:-}" ] && OPTIONAL+=("CognitoRedirectUri=${COGNITO_REDIRECT_URI}")
[ -n "${COGNITO_LOGOUT_URI:-}" ] && OPTIONAL+=("CognitoLogoutUri=${COGNITO_LOGOUT_URI}")
aws cloudformation deploy --stack-name "$STACK" --template-file infra/backend.yaml \
  --capabilities CAPABILITY_NAMED_IAM --no-fail-on-empty-changeset \
  --parameter-overrides \
    "ImageUri=${URI}" "TableName=${TABLE}" "FilesBucket=${FILES_BUCKET}" "AppSecretId=${APP_SECRET_ID}" \
    "FrontendOrigin=${FRONTEND_ORIGIN}" "CognitoUserPoolId=${COGNITO_USER_POOL_ID}" \
    "CognitoClientId=${COGNITO_CLIENT_ID}" "CognitoDomain=${COGNITO_DOMAIN}" ${OPTIONAL[@]+"${OPTIONAL[@]}"}

out() { aws cloudformation describe-stacks --stack-name "$1" --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text; }
param() { aws cloudformation describe-stacks --stack-name "$1" --query "Stacks[0].Parameters[?ParameterKey=='$2'].ParameterValue" --output text; }
API_ID="$(out "$STACK" ApiId)"
API_STAGE="$(out "$STACK" ApiStage)"

log "6) CloudFront API 오리진 확인 (${API_ID}/${API_STAGE})"
if [ "$(param "$CF_STACK" ApiId)" != "$API_ID" ] || [ "$(param "$CF_STACK" ApiStage)" != "$API_STAGE" ]; then
  if [ -z "$PROFILE" ]; then
    # CI의 배포 역할에는 CloudFront 권한을 주지 않았다. 오리진 전환은 처음 한 번 로컬에서 한다.
    # 전환 전에 새 프론트를 배포하면 답변 요청이 이전 API로 가므로 여기서 멈춘다.
    echo "CloudFront가 아직 이전 API를 가리킵니다. 로컬에서 이 스크립트를 한 번 실행해 전환하세요."
    exit 1
  fi
  # 넘기지 않은 파라미터(도메인, 인증서 등)는 직전 값을 그대로 쓴다. 배포 완료까지 몇 분 걸린다.
  log "    CloudFront 스택 갱신: API 오리진을 ${API_ID}로 바꾼다"
  aws cloudformation deploy --stack-name "$CF_STACK" --template-file infra/cloudfront.yaml \
    --no-fail-on-empty-changeset --parameter-overrides "ApiId=${API_ID}" "ApiStage=${API_STAGE}"
else
  log "    변경 없음"
fi

log "완료: ${FRONTEND_ORIGIN}/api -> https://${API_ID}.execute-api.${REGION}.amazonaws.com/${API_STAGE}"
