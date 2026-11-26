# AWS 서버리스 전환 작업 기록

목표: 온프레미스(Docker Compose: FastAPI + Postgres)로 배포하던 Joomini 백엔드를
**API Gateway → Lambda(Flask, Zappa) → DynamoDB** 구조로 옮긴다. 프론트(Next.js)는 유지한다.

- 브랜치: `feat/aws-serverless` (`main`은 손대지 않음. `main` 푸시는 온프레미스 자동 배포가 돌기 때문)
- 1~2차 작업 동안은 기존 FastAPI 백엔드 `backend/`를 두고 새 백엔드를 `backend-flask/`에 옮겼다. 3차 작업에서 FastAPI 코드를 지우고 `backend-flask/`를 `backend/`로 바꿨다. 아래 1~2차 기록의 경로와 포트는 그 당시 기준이다.

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

## 2026-11-15 2차 작업: 나머지 API 전부 이전, 로컬에서 화면까지 동작

### 한 일

| 내용 | 파일 |
|---|---|
| 엔진 코드(라우팅, 모델 호출, 툴, 문서 생성/수정, 웹 검색, 토큰 계산)를 그대로 복사 | `app/routing`, `app/llm`, `app/tools`, `app/documents`, `app/websearch`, `app/core/tokens.py` |
| 대화 CRUD, 메시지 조회, 메시지 전송(SSE), 재접속 스트림 | `routes/conversations.py`, `chat/turn.py`, `chat/history.py`, `chat/stream.py` |
| 첨부 업로드/취소/미리보기, 프로젝트 CRUD/참고자료/메모리 추출 | `routes/attachments.py`, `routes/projects.py`, `routes/file_views.py`, `services/files.py` |
| 툴이 만든 파일 저장, 수정 툴의 파일 조회를 DynamoDB로 | `services/generated.py`, `tools/edit.py` |
| 사용량(카운터 기반), 검색, 메모리 추출 트리거, 프로필 사진, 구버전 `/chat` | `routes/usage.py`, `routes/misc.py`, `routes/users.py`, `memory/extract.py` |
| Flask를 보는 프론트를 3001에 추가 (프론트 코드는 그대로, API 주소만 5001) | `docker-compose.serverless.yml` |

### 기존과 달라진 점

- **파일 경로 대신 저장소 키.** 아이템에는 `chats/{cid}/{id}.docx` 같은 키만 저장하고 실제 읽기/쓰기는 `storage.py`가 한다. S3로 갈 때 이 파일만 바꾸면 된다.
- **추출 텍스트는 아이템이 아니라 파일로.** DynamoDB 아이템 상한(400KB) 때문에 `{폴더}/{id}.extracted.json`에 두고 아이템에는 `extracted_key`만 둔다.
- **비동기 작업은 스레드.** Flask는 동기 서버라, 응답 생성은 스레드 하나가 `asyncio.run()`으로 돈다(엔진 코드가 async). 업로드 추출, 제목 생성도 스레드. Lambda에서는 응답 뒤 실행이 멈추므로 이 부분은 남은 일 6, 7번에서 바꾼다.
- **사용량**은 답변 저장 때 일별/시간별 카운터에 더하고 화면은 카운터만 읽는다.

### 확인한 것

- **기존 API와 응답 형태 비교** (같은 요청을 FastAPI 8000과 Flask 5001에 보냄): 23개 중 19개 키 구조 동일. 나머지 4개는 형태 차이가 아니다. 사용량 3개는 새 DB에 아직 데이터가 없어 `by_model`이 빈 목록이었고, `/search`는 **기존 FastAPI가 로컬에서 500**(로컬 Postgres에 pg_trgm 확장이 없는 것으로 보임)이었다.
- **종단 테스트 21개 통과** (실제 모델 호출 + SSE): 수동/auto 모드, 맥락 유지, docx 첨부 질문, 엑셀 파일 생성과 표 미리보기, 프로젝트 지침과 참고자료 반영, 제목 생성, 목록 순서, 사용량 카운터, 검색, 재접속 204.
- **스모크 테스트 46개** 다시 통과.
- **브라우저**(http://localhost:3001)에서 메시지 전송, auto 라우팅 배지, 스트리밍 표시 확인.

### 발견한 문제

- **auto 모드 준비 판정이 이르다 (기존 코드에도 있음).** `routing/complexity.py`의 `is_ready()`가 모델 객체만 보고, 참조 문장 임베딩이 끝나기 전에 true가 됐다. 그 사이 auto 요청은 "복잡도 모델이 아직 로드되지 않았습니다"로 실패한다. Flask 쪽은 마지막 단계(`_ref_label`)까지 보도록 고쳤다. 기존 `backend/`는 고치지 않았다.
- **개발 서버 재시작 시 컨테이너가 죽는다.** 코드 수정으로 리로더가 재시작할 때 torch 스레드 때문에 프로세스가 비정상 종료됐다. compose에 `restart: unless-stopped`를 넣었다.
- `.gitignore`에서 `uploads/`와 `backend-flask/zappa_settings.json`이 한 줄로 붙어 둘 다 무시되지 않던 것을 고쳤다 (1차 작업 때 생긴 실수).

### 로컬 환경 정리

- 기존 FastAPI 개발 환경(`docker-compose.dev.yml`의 backend, frontend, postgres) 컨테이너와 빌드 이미지를 지웠다. **데이터 볼륨(`semantic-router_postgres_data`)과 모델 캐시 볼륨은 남겼다.** 다시 쓰려면 `docker compose -f docker-compose.dev.yml up -d --build`.
- 멈춰 있던 `mattermost-postgres` 컨테이너도 지웠다 (볼륨은 남김).

---

## 2026-11-15 3차 작업: 이전 FastAPI 버전 제거, 구조와 포트 정리

| 내용 | 비고 |
|---|---|
| `backend/`(FastAPI) 삭제, `backend-flask/` → `backend/` | FastAPI 코드는 `main`과 git 기록에 있다. 평가 노트북(`notebooks/eval`)이 `backend` 컨테이너의 `app.routing`, `app.llm`을 그대로 쓸 수 있다 |
| 이전 `uploads/`(git에 없던 업로드 파일 13개)는 macOS 휴지통으로 이동, `uploads-flask/` → `uploads/` | 휴지통 이름 `joomini-uploads-fastapi-20261115` |
| `docker-compose.serverless.yml` 삭제, `docker-compose.dev.yml`을 Flask + DynamoDB Local로 | 포트 원래대로: 화면 3000, API 8000 (DynamoDB Local 8001, 관리 화면 8002) |
| `docker-compose.yml`(온프레미스 서버)도 Flask + DynamoDB Local로, Postgres 제거 | 같은 `backend/Dockerfile`을 gunicorn(워커 1, 스레드 16)으로 띄운다. 시작할 때 테이블이 없으면 만든다 |
| `scripts/deploy.sh` 헬스체크 대기 30초 → 2분 | torch 로드와 테이블 확인 때문에 시작이 30초를 넘을 수 있다 |
| `CLAUDE.md`, `README.md`, 배포·모델 교체 문서를 새 구조로 | 배포 문서에 "전환 후 첫 배포" 주의 사항 추가 (데이터 미이전, 기존 postgres 컨테이너 수동 정리, 이 배포는 자동 롤백 불가) |

---

## 2026-11-15 4차 작업: AWS 첫 배포

자세한 구조, 결정 근거, 실측 수치, 한계는 [AWS_DEPLOY.md](AWS_DEPLOY.md).

- 백엔드: API Gateway + Lambda(컨테이너 이미지, Zappa) + DynamoDB + S3. auto 라우팅은 끈 상태(`ROUTER_MODELS=off`)
- 프론트: Lambda(Lambda Web Adapter) + Function URL. 기본 모드는 OpenAI 수동 선택
- 코드 변경: `storage.py` S3 지원, Lambda에서는 백그라운드 작업을 요청 안에서 처리, 프론트 기본 모드 빌드 옵션, `backend/Dockerfile`에 `lambda` 단계, 배포 스크립트 2개
- 확인: 배포된 주소에서 `/health`, 실제 채팅(답변 저장, 제목 생성), 브라우저 전체 흐름, CORS(프론트 주소만 허용)
- 보호 장치: 예산 알림(월 $10), API Gateway 요청 제한(초당 2건)

---

## 2026-11-26 5차 작업: 백엔드 Lambda 하나로 통합

자세한 구조와 근거는 [AWS_DEPLOY.md](AWS_DEPLOY.md) 3장, 6장.

- 문제: Zappa Lambda(일반 API)와 스트리밍 Lambda(SSE)에 같은 Flask 코드를 두 벌 배포했다. API Gateway는 EDGE라 우리 CloudFront 뒤에서 CloudFront를 두 번 지났고, 모델 API 키가 Lambda 환경 변수에 있었다.
- 바꾼 것
  - Lambda 하나(`joomini-api`, Lambda Web Adapter 1.1.0, `response_stream`). API Gateway REST는 REGIONAL, `/`와 `/{proxy+}` 모두 STREAM 통합
  - 예약 작업: Zappa `events`, `keep_warm` 대신 EventBridge Scheduler. Lambda Web Adapter가 이벤트를 `POST /events`로 넘기고 `routes/events.py`가 허용 목록 명령만 실행
  - 비밀값: Secrets Manager `joomini/app`. Lambda 환경 변수에는 비밀 이름만
  - IaC: 백엔드 리소스 전부 `infra/backend.yaml` (CloudFormation). Zappa, `deploy_lambda.sh`, `deploy_stream_lambda.sh`, `make_zappa_settings.py` 삭제
  - 스레드: `run_in_background`는 Lambda에서 항상 요청 안에서 실행, 답변 생성만 `run_alongside_response`(스레드)
  - 프론트: 답변 요청에서 `/stream` 접두사 제거 (백엔드는 이전 경로도 계속 받음)
- 로컬 확인 (lambda 이미지를 gunicorn으로 띄우고 DynamoDB Local 연결)
  - `/events`: `x-amzn-request-context: null`일 때만 실행, 헤더가 없거나 객체면 404, 모르는 명령 400
  - origin 비밀값을 못 읽는 상태에서 일반 요청은 503, 이벤트만 통과
  - Lambda 모드(`AWS_LAMBDA_FUNCTION_NAME`)에서 채팅: chunk 스트리밍, done 뒤 제목 저장까지 응답 유지 후 종료
  - `APP_SECRET_ID`를 읽지 못하면 앱이 뜨지 않음(fail closed)
  - CloudFormation `validate-template` 통과
- 확인 못 한 것: 실제 Lambda Web Adapter 안에서의 동작(Lambda 런타임 필요). 배포 후 `/api/health`, 채팅, 예약 작업 로그로 확인한다.
- 배포 순서(처음 한 번): 배포 역할 정책 갱신(`infra/github-deploy-policy.json`), 로컬에서 `deploy_backend.sh`(CloudFront 오리진 전환 포함), 프론트 배포, 확인 후 `cleanup_legacy_zappa.sh`

- 같은 날 이어서: 프론트를 Next.js 서버(Lambda)에서 React SPA(Vite, React Router)로 바꾸고 비공개 S3 + CloudFront(OAC)로 서빙. SSR, API route, server action을 쓰지 않아 서버가 필요 없었다. 동적 경로는 CloudFront Function이 `/index.html`로 돌려준다. 프론트 Lambda와 ECR 저장소 삭제.

---

## 남은 일 (다음 순서)

| 순서 | 할 일 | 비고 |
|---|---|---|
| 1 | ~~AWS 계정 준비~~ | 4차 작업에서 완료 |
| 2 | ~~첫 `zappa deploy dev`~~ | 4차 작업에서 완료 (컨테이너 이미지, auto 라우팅 끔) |
| 3 | ~~프로젝트, 대화 CRUD 엔드포인트 이전~~ | 2차 작업에서 완료 (전체 API) |
| 4 | 파일 저장을 S3로 | 저장은 완료(4차). 남은 것: 업로드를 presigned URL로 (Lambda 요청 6MB 제한) |
| 5 | 라우터 모델 결정: torch + mmBERT + e5-large(3GB 이상)는 zip(250MB)에 안 들어감 | 임베딩 API 전환 또는 컨테이너 이미지. 바꾼 뒤 `notebooks/eval`로 재검증 |
| 6 | 채팅 전송을 비동기 작업 + 폴링으로 (SSE 대체) | 프론트 `lib/api.ts` 수신부만 수정 |
| 7 | ~~백그라운드 작업 이전: 제목 생성, 메모리 추출(하루 2회)~~ | 5차 작업에서 완료 (스트리밍 응답 안에서 제목 생성, EventBridge Scheduler) |
| 8 | Cognito 로그인, `auth.current_user_id()` 교체 | 라우트 코드는 그대로 |
| 9 | ~~프론트 배포~~ | 4차 작업에서 완료 (Lambda + Function URL) |
