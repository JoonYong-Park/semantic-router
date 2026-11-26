#!/usr/bin/env bash
# 프론트(React SPA)를 빌드해 S3에 올린다. CloudFront(infra/cloudfront.yaml)가 이 버킷을 서빙한다. 저장소 루트에서:
#   bash scripts/deploy_frontend.sh
#
# 순서
#   1) 버킷 스택 확인 (infra/frontend.yaml, 스택 joomini-frontend. 없으면 생성)
#   2) 빌드 (frontend/Dockerfile target=dist, 로컬에 Node를 깔지 않는다)
#   3) S3 업로드
#        assets/*   : 파일 이름에 내용 해시가 있어 1년 캐시(immutable). 예전 파일은 지우지 않는다(열린 탭 대비)
#        index.html : no-cache. 배포하면 다음 요청부터 새 버전을 받는다
#        그 밖      : 아이콘 등 public/ 파일. 1시간 캐시
#
# 서버가 없으므로 배포는 파일 업로드뿐이다. GitHub Actions에서는 AWS_PROFILE_NAME= (빈 값)으로 OIDC 임시 자격 증명을 쓴다.
set -euo pipefail

API_URL="${API_URL:-/api}"                # 같은 도메인의 /api (CloudFront가 API Gateway로 넘김)
DEFAULT_MODE="${DEFAULT_MODE:-openai}"    # auto 라우팅을 끈 동안은 수동 모드가 기본
PROFILE="${AWS_PROFILE_NAME-joomini}"
REGION="ap-northeast-2"
STACK="joomini-frontend"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

aws() { if [ -n "$PROFILE" ]; then command aws --profile "$PROFILE" --region "$REGION" "$@"; else command aws --region "$REGION" "$@"; fi; }
log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$1"; }

log "1) 버킷 스택 확인: ${STACK} (infra/frontend.yaml)"
aws cloudformation deploy --stack-name "$STACK" --template-file infra/frontend.yaml --no-fail-on-empty-changeset >/dev/null
BUCKET="$(aws cloudformation describe-stacks --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text)"

log "2) 빌드 (API_URL=${API_URL}, 기본 모드=${DEFAULT_MODE})"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
docker build --target dist --build-arg VITE_API_URL="$API_URL" --build-arg VITE_DEFAULT_MODE="$DEFAULT_MODE" \
  --output "type=local,dest=${OUT}" frontend
[ -f "$OUT/index.html" ] || { echo "빌드 결과에 index.html이 없습니다"; exit 1; }

log "3) S3 업로드: s3://${BUCKET}"
# 순서가 중요하다: 새 index.html이 가리킬 assets를 먼저 올리고, index.html은 마지막에 올린다.
aws s3 sync "$OUT/assets" "s3://${BUCKET}/assets" --only-show-errors \
  --cache-control "public, max-age=31536000, immutable"
aws s3 sync "$OUT" "s3://${BUCKET}" --only-show-errors --delete \
  --exclude "assets/*" --exclude "index.html" --cache-control "public, max-age=3600"
aws s3 cp "$OUT/index.html" "s3://${BUCKET}/index.html" --only-show-errors \
  --cache-control "no-cache" --content-type "text/html; charset=utf-8"

log "완료: $(find "$OUT" -type f | wc -l | tr -d ' ')개 파일"
