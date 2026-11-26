#!/usr/bin/env bash
# 이전 구조(Zappa Lambda + 스트리밍 Lambda + EDGE API Gateway)를 지운다. 한 번만, 손으로 실행한다.
# deploy_backend.sh로 새 구조가 뜨고 CloudFront가 새 API를 가리키는 것을 확인한 뒤에 실행한다.
#   bash backend/scripts/cleanup_legacy_zappa.sh
#
# 지우는 것
#   - Lambda joomini-dev (Zappa), joomini-stream (스트리밍 전용)
#   - EventBridge 규칙: Zappa keep_warm, 메모리 추출 2개
#   - CloudFormation 스택 joomini-dev (Zappa가 만든 EDGE REST API)
#   - IAM 역할 joomini-dev-ZappaLambdaExecutionRole
#   - Zappa 업로드 버킷 joomini-zappa-<계정 ID> (비어 있으면)
# ECR 저장소(joomini-backend)는 새 구조도 쓰므로 그대로 둔다.
set -euo pipefail

PROFILE="${AWS_PROFILE_NAME-joomini}"
REGION="ap-northeast-2"
aws() { if [ -n "$PROFILE" ]; then command aws --profile "$PROFILE" --region "$REGION" "$@"; else command aws --region "$REGION" "$@"; fi; }
log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$1"; }

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
NEW_API="$(aws cloudformation describe-stacks --stack-name joomini-backend --query "Stacks[0].Outputs[?OutputKey=='ApiId'].OutputValue" --output text)"
CF_API="$(aws cloudformation describe-stacks --stack-name joomini-cloudfront --query "Stacks[0].Parameters[?ParameterKey=='ApiId'].ParameterValue" --output text)"
if [ -z "$NEW_API" ] || [ "$NEW_API" != "$CF_API" ]; then
  echo "CloudFront가 아직 새 API(${NEW_API:-없음})를 가리키지 않습니다(현재 ${CF_API}). 중단합니다."
  exit 1
fi

echo "계정 ${ACCOUNT}에서 이전 Zappa 리소스를 지웁니다. 되돌릴 수 없습니다."
read -r -p "계속하려면 delete 를 입력하세요: " answer
[ "$answer" = "delete" ] || { echo "취소"; exit 1; }

RULES=(joomini-dev-zappa-keep-warm-handler.keep_warm_callback
       joomini-dev-app.memory.extract.run_scheduled
       joomini-dev-1-app.memory.extract.run_scheduled)
for rule in "${RULES[@]}"; do
  if aws events describe-rule --name "$rule" >/dev/null 2>&1; then
    ids="$(aws events list-targets-by-rule --rule "$rule" --query 'Targets[].Id' --output text)"
    # shellcheck disable=SC2086
    [ -n "$ids" ] && aws events remove-targets --rule "$rule" --ids $ids >/dev/null
    aws events delete-rule --name "$rule"
    log "EventBridge 규칙 삭제: $rule"
  fi
done

for fn in joomini-dev joomini-stream; do
  if aws lambda get-function --function-name "$fn" >/dev/null 2>&1; then
    aws lambda delete-function --function-name "$fn"
    log "Lambda 삭제: $fn"
  fi
done

if aws cloudformation describe-stacks --stack-name joomini-dev >/dev/null 2>&1; then
  aws cloudformation delete-stack --stack-name joomini-dev
  aws cloudformation wait stack-delete-complete --stack-name joomini-dev
  log "스택 삭제: joomini-dev (EDGE REST API)"
fi

ROLE="joomini-dev-ZappaLambdaExecutionRole"
if aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  for p in $(aws iam list-role-policies --role-name "$ROLE" --query 'PolicyNames[]' --output text); do
    aws iam delete-role-policy --role-name "$ROLE" --policy-name "$p"
  done
  for a in $(aws iam list-attached-role-policies --role-name "$ROLE" --query 'AttachedPolicies[].PolicyArn' --output text); do
    aws iam detach-role-policy --role-name "$ROLE" --policy-arn "$a"
  done
  aws iam delete-role --role-name "$ROLE"
  log "IAM 역할 삭제: $ROLE"
fi

BUCKET="joomini-zappa-${ACCOUNT}"
if aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  if aws s3api delete-bucket --bucket "$BUCKET" 2>/dev/null; then
    log "S3 버킷 삭제: $BUCKET"
  else
    log "S3 버킷 $BUCKET 이 비어 있지 않아 남겨 둡니다(내용 확인 후 직접 지우세요)"
  fi
fi

log "완료"
