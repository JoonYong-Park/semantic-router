# AWS 서버리스 전환 작업 기록

목표: 온프레미스(Docker Compose: FastAPI + Postgres)로 배포하던 Joomini 백엔드를
**API Gateway → Lambda(Flask, Zappa) → DynamoDB** 구조로 옮긴다. 프론트(Next.js)는 유지한다.

- 브랜치: `feat/aws-serverless` (`main`은 손대지 않음. `main` 푸시는 온프레미스 자동 배포가 돌기 때문)
- 기존 백엔드 `backend/`는 그대로 두고, 새 백엔드는 `backend-flask/`에 기능 단위로 옮긴다.

---

## 2026-11-14 1차 작업

### 한 일

| 순서 | 내용 | 커밋 |
|---|---|---|
| 1 | 코드의 DB 조회 패턴 23개(AP1~AP23)를 뽑아 DynamoDB 단일 테이블 + GSI 3개로 설계 | `docs: add DynamoDB access pattern and key design` |
| 2 | AWS 공식 문서로 제약 확인 후 결정 사항 기록 (단일 테이블, 온디맨드, 앱 내 검색, 새로 시작, KST 유지) | `docs: record DynamoDB design decisions with AWS references` |
| 3 | 로컬 DynamoDB 환경(`docker-compose.serverless.yml`)과 테이블 생성 스크립트 | `feat: add Flask skeleton and local DynamoDB stack with table script` |
| 4 | 데이터 접근 계층 `backend-flask/app/db/repo/` (조회 패턴마다 함수 하나) + 스모크 테스트 46개 통과 | `feat: add DynamoDB repository layer with local smoke test` |
| 5 | 첫 엔드포인트 이전: `/settings` 전체(메모리 화면 포함), `/users/me` (조회, 이름 수정). Zappa 설정 예시 | 아래 커밋 |

### 확인한 것

- **스모크 테스트 46개 통과** (`backend-flask/scripts/smoke_test.py`): 대화 최신순과 고정, 메시지와 첨부 한 번에 조회, 트랜잭션(없는 첨부를 연결하면 전체 취소), 고아 첨부 TTL, 프로젝트 연쇄 삭제, 사용량 카운터, 검색, 다른 사용자 데이터 접근 차단까지.
- **기존 API와 응답 형태 동일**: `GET /users/me`, `GET /settings`를 기존 FastAPI(8000)와 새 Flask(5001)에서 각각 호출해 키가 같은 것을 확인. 에러도 기존처럼 `{"detail": "..."}`와 같은 상태 코드(422, 404).

### 설계하며 정한 규칙 (코드에 반영됨)

1. **키 문자열은 `app/db/keys.py`에서만 만든다.** `"USER#"` 같은 접두사를 다른 곳에 직접 쓰지 않는다.
2. **업데이트는 `dynamo.update_expr()`로만 만든다.** `name`, `ttl`, `source`, `size`, `status` 같은 예약어와 부딪히지 않도록 모든 속성 이름을 자동으로 별칭 처리한다.
3. **float는 저장 전에 Decimal로.** boto3가 float를 받지 않는다. `to_dynamo()`, `from_dynamo()`가 자동 변환한다.
4. **대화의 `updated_at`은 `_touch_update()`로만 바꾼다.** GSI 정렬 키(GSI1SK, GSI2SK)가 같이 움직여야 목록 순서가 맞다.
5. **by-id 조회는 GSI3로 찾은 뒤 `owner_pk`로 소유자를 확인한다.** 기존 코드에 남아 있던 "소유자 확인 없는 by-id 엔드포인트" 문제를 새 구조에서는 처음부터 막는다.
6. **고아 첨부 정리는 DynamoDB TTL.** 업로드 때 `ttl`을 걸고 메시지에 연결되면 지운다. TTL 삭제는 만료 후 며칠까지 늦어질 수 있어, 조회 시 만료된 아이템은 걸러낸다.

### 로컬 실행

```bash
docker compose -f docker-compose.serverless.yml up -d --build
docker compose -f docker-compose.serverless.yml run --rm flask python scripts/create_table.py
docker compose -f docker-compose.serverless.yml run --rm flask python scripts/smoke_test.py
```

`docker-compose.serverless.yml`은 compose 프로젝트 이름을 `joomini-serverless`로 따로 둬서 기존 개발 환경(`docker-compose.dev.yml`)의 컨테이너와 섞이지 않는다.

---

## 남은 일 (다음 순서)

| 순서 | 할 일 | 비고 |
|---|---|---|
| 1 | AWS 계정 준비: 배포용 IAM 사용자, AWS CLI, Zappa 배포 버킷 | 사용자 확인 필요 (계정과 비용) |
| 2 | `/health`와 `/settings`만으로 첫 `zappa deploy dev` | 권한, 리전, 패키징 문제를 먼저 해결 |
| 3 | 프로젝트, 대화 CRUD 엔드포인트 이전 (`/projects`, `/conversations` 목록과 수정, 삭제) | repo 계층은 준비됨 |
| 4 | 파일 저장을 S3로 (첨부, 프로젝트 파일, 아바타). 업로드는 presigned URL | 추출 텍스트도 S3 (아이템 400KB 제한) |
| 5 | 라우터 모델 결정: torch + mmBERT + e5-large(3GB 이상)는 zip(250MB)에 안 들어감 | 임베딩 API 전환 또는 컨테이너 이미지. 바꾼 뒤 `notebooks/eval`로 재검증 |
| 6 | 채팅 전송을 비동기 작업 + 폴링으로 (SSE 대체) | 프론트 `lib/api.ts` 수신부만 수정 |
| 7 | 백그라운드 작업 이전: 제목 생성, 메모리 추출(하루 2회) → Zappa `@task`, `events` | |
| 8 | Cognito 로그인, `auth.current_user_id()` 교체 | 라우트 코드는 그대로 |
| 9 | 프론트 배포 (Amplify 또는 S3 + CloudFront), API 주소를 API Gateway로 | |
