# AWS 서버리스 배포 (CloudFront + API Gateway + Lambda + DynamoDB)

2026-11-15에 처음 배포했고(Zappa), 2026-11-26에 백엔드를 Lambda 하나로 합쳤다(5장 이력 참고). 이 문서는 지금 구조를 정리한 것이다.

- 리전: 서울(ap-northeast-2)
- 사용자는 CloudFront 한 도메인으로 들어온다(`infra/cloudfront.yaml`). 지금은 main에 push하면 GitHub Actions가 배포한다(`.github/workflows/deploy-lambda.yml`).

---

## 1. 구조 한눈에 보기

```
브라우저 ──HTTPS──▶ CloudFront ─┬─ "/"       ─▶ S3 (React SPA 정적 파일, 비공개, OAC)
                                └─ "/api/*"  ─▶ API Gateway REST (REGIONAL, 응답 스트리밍)
                                                    │
                                                    ▼
                                         백엔드 Lambda joomini-api (하나)
                                         Lambda Web Adapter ─▶ gunicorn ─▶ Flask
                                              ├──▶ DynamoDB (단일 테이블, GSI 3개)
                                              ├──▶ S3 (첨부, 생성 파일)
                                              ├──▶ Secrets Manager (모델 API 키, origin 비밀값)
                                              └──▶ LLM API (OpenAI, Gemini, Claude)
EventBridge Scheduler ──(비동기 호출)──▶ joomini-api  (Lambda Web Adapter가 POST /events 로 넘김)
```

| 구성 요소 | 하는 일 |
|---|---|
| CloudFront | 한 도메인으로 화면과 API를 묶는다. HTTP는 HTTPS로 넘기고, 정적 파일을 캐시하고, 파일이 아닌 경로(`/c/abc`)는 `index.html`로 돌려주고, API 요청에 `X-Origin-Verify` 헤더를 붙인다 (`infra/cloudfront.yaml`) |
| S3 (프론트) | React SPA 빌드 결과(`index.html`, `assets/`). 비공개 버킷이고 CloudFront OAC 서명 요청만 읽을 수 있다 (`infra/frontend.yaml`) |
| API Gateway | REGIONAL REST API. 모든 경로(`/`, `/{proxy+}`)를 응답 스트리밍(STREAM) 통합으로 백엔드 Lambda에 넘기고, 요청 제한(초당 2건, 순간 5건)을 건다 |
| 백엔드 Lambda | Flask 앱 전체. Lambda Web Adapter가 API Gateway 이벤트를 HTTP 요청으로 바꿔 gunicorn에 넘기고 응답을 조각 단위로 흘려보낸다. 일반 JSON과 SSE를 같은 함수가 처리한다 |
| EventBridge Scheduler | 메모리 자동 추출(매일 07:00, 12:30 KST), 웜업(5분마다) |
| Secrets Manager | `joomini/app`(모델 API 키, Cognito client secret), `joomini/origin-verify`(CloudFront 비밀 헤더) |
| DynamoDB | 대화, 메시지, 메모리, 프로젝트, 사용량. 설계는 [DYNAMODB_DESIGN.md](DYNAMODB_DESIGN.md) |
| S3 | 업로드 원본, 추출 이미지, 미리보기 PDF, 모델이 만든 파일 |
| ECR | 백엔드 컨테이너 이미지 |
| IAM 실행 역할 | 백엔드는 자기 테이블, 버킷, 비밀에만 접근할 수 있다 |
| 예산 알림 | 월 $10 기준, 80% 실제 지출과 100% 예상 지출에서 메일 |

백엔드 리소스(Lambda, API Gateway, 실행 역할, 예약 작업, 로그 그룹)는 모두 `infra/backend.yaml` 한 템플릿(스택 `joomini-backend`)에 있다.

## 2. 배포 방법

main에 push하면 GitHub Actions가 OIDC로 배포 역할을 받아 아래 두 스크립트를 실행한다. 로컬에서도 같은 명령이다.

```bash
bash backend/scripts/deploy_backend.sh      # 백엔드
bash scripts/deploy_frontend.sh             # 프론트 (빌드해서 S3에 업로드, API는 같은 도메인의 /api)
```

백엔드 스크립트가 하는 일:
1. `backend/Dockerfile`의 `lambda` 단계로 이미지 빌드. 로컬 개발 이미지와 무거운 레이어(LibreOffice, torch)를 공유한다.
2. S3 버킷과 DynamoDB 테이블이 없으면 만든다. 버킷 CORS는 서비스 도메인의 POST만 허용한다.
3. ECR에 올린다.
4. 모델 API 키와 Cognito client secret을 Secrets Manager `joomini/app`에 동기화한다(`sync_app_secret.py`, 값은 출력하지 않음).
5. `infra/backend.yaml` 스택을 배포한다. 평소에는 이미지 URI만 바뀌어 Lambda 코드만 갱신된다.
6. CloudFront의 API 오리진이 이 스택의 API를 가리키는지 확인한다(처음 한 번 로컬에서 전환, CI는 다르면 멈춤).

## 3. 왜 이렇게 했나 (결정과 근거)

**컨테이너 이미지로 배포**
- 일반 Lambda zip은 250MB가 상한인데, 이 앱은 LibreOffice(문서 미리보기, PDF 변환)와 torch(라우터 모델)가 들어가서 수 GB다. 컨테이너 이미지는 10GB까지 된다.
- 로컬 개발과 Lambda가 **같은 Dockerfile**을 쓰고 마지막 단계만 다르다. 환경 차이로 인한 버그가 줄어든다.

**파일은 S3, `/tmp`는 캐시**
- Lambda 디스크(`/tmp`)는 실행 환경마다 따로이고 사라질 수 있다. 그래서 정본은 S3에 두고, 추출기나 LibreOffice처럼 로컬 파일이 필요한 코드만 `/tmp`에 내려받아 쓴다.
- 코드에서는 `storage.py` 한 파일만 바꿨다. DB에는 처음부터 경로가 아니라 키만 저장하게 설계해서 가능했다.

**백그라운드 작업을 요청 안에서 처리**
- Lambda는 응답이 끝나면 실행 환경을 멈춘다. 로컬에서 스레드로 돌리던 첨부 추출과 메모리 추출이 중간에 멈추는 문제가 생긴다.
- 그래서 Lambda에서는 요청 안에서 끝내도록 했다(`services/files.run_in_background`). 업로드 응답이 추출 시간만큼 늦어지는 대신 결과가 확실하다.
- 답변 생성만 예외다. SSE 응답을 흘려보내는 동안은 호출이 살아 있으므로 스레드로 돌리고(`run_alongside_response`), 답변 저장과 제목 생성이 끝날 때까지 응답을 닫지 않는다(`TurnStream.settle`).

**Lambda 하나 + API Gateway REGIONAL (2026-11-26)**
- 예전에는 Zappa Lambda(일반 API)와 스트리밍 Lambda(SSE)에 같은 Flask 코드를 두 벌 배포했다. Zappa 핸들러가 응답을 모아 한 번에 돌려줘서 SSE가 흐르지 않았기 때문이다.
- API Gateway REST가 응답 스트리밍을 지원하고(2025-11), Lambda Web Adapter의 `response_stream` 모드는 일반 JSON 응답도 같은 형식으로 내보낸다. 그래서 모든 경로를 STREAM 통합으로 바꾸고 함수를 하나로 합쳤다. 이미지, 환경 변수, 배포가 한 벌이 됐고 Zappa 의존성이 없어졌다.
- 주의: Lambda Web Adapter를 `response_stream`으로 띄우면 모든 응답이 스트리밍 형식이라, API Gateway 통합 중 하나라도 BUFFERED면 그 경로는 본문이 비어 나간다. 그래서 `/`와 `/{proxy+}` 둘 다 STREAM이다.
- API Gateway를 EDGE에서 REGIONAL로 바꿨다. EDGE는 AWS가 관리하는 CloudFront를 하나 더 거쳐 우리 CloudFront 뒤에서는 CloudFront를 두 번 지난다. 스트리밍 idle 제한도 EDGE 30초, REGIONAL 5분이다.
- API Gateway를 빼고 CloudFront에서 Function URL로 바로 보내지 않은 이유: OAC로 Function URL을 보호하면 POST, PUT마다 클라이언트가 본문 SHA256을 `x-amz-content-sha256` 헤더로 보내야 한다. 채팅과 업로드가 많은 API에는 맞지 않는다. 요청 제한도 API Gateway가 맡는다.
- Fargate나 App Runner는 요청이 없어도 비용이 나가서, 사용량이 적은 개인 서비스의 비용 목표와 맞지 않았다.

**비밀값은 Secrets Manager**
- 모델 API 키를 Lambda 환경 변수에 두면 Lambda 설정 조회 권한만으로 보인다. 지금은 Secrets Manager `joomini/app`에 두고 Lambda가 콜드 스타트 때 한 번 읽는다(`app/core/app_secrets.py`). 환경 변수에는 비밀 이름만 있다.
- 키를 바꾸면 새 실행 환경부터 반영된다(배포하면 바로). 교체 주기가 짧아지면 Parameters and Secrets 확장(TTL 캐시)으로 바꾼다.

**예약 작업은 EventBridge Scheduler**
- Scheduler가 Lambda를 비동기 호출하면 Lambda Web Adapter가 이벤트 JSON을 `POST /events`로 넘긴다. `routes/events.py`는 허용 목록의 명령(`memory_extract`, `backfill_titles`, `warm`)만 실행한다.
- 외부 요청과의 구분: Lambda Web Adapter는 `x-amzn-request-context` 헤더를 항상 덮어쓴다(API Gateway 요청이면 요청 정보 객체, 이벤트면 `null`). `null`일 때만 이벤트로 보고, 그 밖의 `/events` 요청은 일반 API처럼 origin 헤더와 토큰 검사를 거친 뒤 404다.
- 남은 시간은 `x-amzn-lambda-context` 헤더의 `deadline`으로 계산한다. 메모리 추출은 20초 남으면 멈추고 다음 실행이 이어서 처리한다.
- Scheduler는 시간대(`Asia/Seoul`)를 직접 지정할 수 있어 UTC 변환이 필요 없다.

**auto 라우팅은 첫 배포에서 끔 (`ROUTER_MODELS=off`)**
- 라우터 모델(mmBERT + E5, 3GB 이상)을 Lambda가 뜰 때마다 불러오면 수십 초가 걸려 API Gateway 29초 제한에 걸릴 수 있다.
- 먼저 수동 모델 선택으로 서비스가 확실히 뜨게 하고, 콜드 스타트를 실측한 뒤 켜기로 했다. 프론트 기본 모드도 빌드 옵션(`VITE_DEFAULT_MODE`)으로 수동 모드로 바꿨다.

**프론트는 React SPA, S3 + CloudFront (2026-11-26)**
- Joomini는 로그인한 사용자만 쓰는 채팅 앱이다. 화면 내용이 전부 사용자별 대화 데이터라 검색 노출(SEO)이 필요 없고, 오히려 노출되면 안 된다. 그래서 서버에서 HTML을 그리는 SSR이 필요 없다.
- 처음에는 Next.js 서버를 Lambda(Lambda Web Adapter)에서 돌렸다. 그런데 코드를 보면 SSR, API route, server action, middleware를 하나도 쓰지 않고 모든 페이지가 브라우저에서 그려졌다. 쓰지 않는 서버 기능 때문에 서버를 운영한 셈이다.
- 그래서 React SPA(Vite 빌드, React Router)로 바꾸고, 빌드한 정적 파일을 비공개 S3에 올려 CloudFront로 서빙한다. 프론트에 서버가 없어서 콜드 스타트, 서버 비용, 런타임 관리가 없다. 로그인한 사용자 데이터가 없는 정적 파일이라 엣지 캐시가 그대로 먹힌다.
- Next.js 정적 빌드(`output: "export"`)도 검토했지만 `/c/[id]` 같은 동적 경로마다 우회 규칙이 필요하고, 서버 기능을 끈 Next.js는 프레임워크를 쓰는 이유가 약해서 SPA 표준 도구로 바꿨다.
- SPA 라우팅: S3에는 `index.html` 하나뿐이라 `/c/abc` 파일이 없다. CloudFront Function이 마지막 경로에 확장자가 없는 요청을 `/index.html`로 바꾸고, 브라우저의 React Router가 주소를 읽어 화면을 고른다. CloudFront의 사용자 정의 오류 응답(403/404를 index.html로)은 배포 전체에 걸려 API 오류까지 바꿔 버려서 쓰지 않았다.
- 캐시: `assets/*`는 파일 이름에 내용 해시가 있어 1년 캐시(immutable), `index.html`은 `no-cache`라 배포 즉시 새 버전을 받는다. 업로드는 assets 먼저, `index.html` 마지막이라 배포 중에도 깨진 화면이 나오지 않는다. 버킷 버전 관리로 이전 빌드로 되돌릴 수 있다.
- 버킷 스택(`infra/frontend.yaml`)을 CloudFront 스택과 나눈 이유: 파일을 먼저 올린 뒤 오리진을 바꿔야 빈 버킷을 서빙하는 순간이 없다.

**보안과 비용**
- 백엔드 CORS는 프론트 주소 하나만 허용한다.
- 로그인이 없는 데모라 누구나 쓸 수 있어서, API Gateway에 초당 2건 제한을 걸고 예산 알림을 만들었다.

## 4. 실측 수치

| 항목 | 값 |
|---|---|
| 백엔드 콜드 스타트 (Lambda 초기화) | 0.8 ~ 2.6초 (라우터 모델 끔) |
| 프론트 콜드 스타트 | 없음 (S3 정적 파일. Lambda였을 때 0.6 ~ 0.75초) |
| 따뜻한 상태의 `/health` | 약 0.3 ~ 0.7초 (서울에서 호출) |
| 백엔드 메모리 / 제한 시간 | 3,008MB / 120초 |

## 4-1. 답변 스트리밍

- API Gateway 통합이 STREAM이라 SSE 조각이 바로 브라우저로 간다. 최대 15분, idle 5분(REGIONAL).
- 이벤트가 없는 동안(웹 검색, 문서 생성) 15초마다 `: ping` 주석을 보내 CloudFront(오리진 응답 제한 60초)와 API Gateway가 조용한 연결을 끊지 않게 한다.
- 진행 중인 답변 레지스트리(`active_turns`)는 프로세스 메모리에 있다. 새로고침 후 같은 실행 환경에 붙으면 이어 받고, 다른 실행 환경으로 가면 저장된 메시지를 다시 읽는다.
- 예전 프론트가 쓰던 `/stream/...` 경로는 WSGI 미들웨어가 접두사를 떼서 그대로 받는다(배포 직후 열려 있던 탭 대비).

## 4-2. 파일 업로드 (S3 직접 업로드)

Lambda 요청 본문은 6MB가 상한이라, 첨부·참고자료 파일은 서버를 거치지 않고 브라우저가 S3에 바로 올린다.

1. `POST .../upload-url {filename, size}` → 서버가 형식·크기·프로젝트 용량을 검사하고 presigned POST(`url`, `fields`, `upload_id`)를 준다. 키와 크기 상한(25MB)이 서명에 들어 있어 다른 위치나 더 큰 파일은 S3가 거절한다.
2. 브라우저가 S3에 바로 올린다(XHR, 진행률 표시). 버킷 CORS는 서비스 도메인의 POST만 허용한다(`deploy_backend.sh`).
3. `POST .../complete {upload_id, filename}` → 서버가 S3에 올라간 실제 크기를 확인하고 첨부/참고자료를 등록한 뒤 추출을 시작한다.

버킷이 없는 로컬에서는 1)이 `{"mode": "multipart"}`를 돌려주고 프론트는 예전처럼 본문을 서버로 보낸다.

## 4-3. CloudFront를 거친 요청만 받기

API Gateway의 `execute-api` 주소는 공개라서, CloudFront(보안 헤더, 도메인)를 건너뛰고 직접 호출할 수 있었다.

- `infra/origin-secret.yaml`: Secrets Manager가 비밀값(`joomini/origin-verify`)을 만든다. 사람은 값을 보지 않는다.
- `infra/cloudfront.yaml` (`OriginVerify=true`): API 오리진 요청에 `X-Origin-Verify` 헤더로 붙인다(CloudFormation 동적 참조).
- 백엔드 `auth.check_origin` (모든 요청, 인증보다 먼저, 예약 작업 이벤트만 제외): 헤더가 없거나 다르면 403. 비밀값은 5분 캐시라 교체하면 5분 안에 반영된다. 비밀값을 못 읽으면 503(fail closed).
- `ORIGIN_VERIFY_SECRET_ID`가 없는 로컬에서는 검사하지 않는다.

## 5. 아직 남은 한계

| 한계 | 이유 | 다음 단계 |
|---|---|---|
| 동시 요청 시 콜드 스타트 | 웜업은 실행 환경 하나만 유지한다 | Provisioned Concurrency(비용 발생) 또는 SnapStart(Python 3.12 컨테이너 지원, 단 임시 저장소 512MB 제한 검토) |
| auto 라우팅 꺼짐 | 모델 로딩 시간 | 모델을 이미지에 넣고 SnapStart 또는 프로비저닝 동시성, 또는 임베딩 API로 교체 후 재평가 |
| WAF 없음 | 비용(월 $5+) | 공개 범위가 넓어지면 CloudFront WAF rate-based 규칙 |
| DynamoDB PITR 꺼짐 | 아직 켜지 않음 | `aws dynamodb update-continuous-backups`로 켜기 |

## 6. 이력

| 날짜 | 구조 |
|---|---|
| 2026-11-15 | API Gateway(EDGE, Zappa) + Zappa Lambda. SSE 불가 |
| 2026-11-25 | 스트리밍 전용 Lambda(`joomini-stream`)를 추가해 `/stream/*`만 응답 스트리밍. 같은 Flask를 두 벌 배포 |
| 2026-11-26 | Lambda 하나(`joomini-api`, Lambda Web Adapter) + API Gateway REGIONAL(모든 경로 STREAM) + EventBridge Scheduler. Zappa 제거, 비밀값 Secrets Manager로 이동, 전부 `infra/backend.yaml` |
| 2026-11-26 | 프론트를 Next.js 서버(Lambda)에서 React SPA(Vite)로 바꾸고 S3 + CloudFront(OAC)로 서빙. 프론트 Lambda 삭제 |

이전 리소스(`joomini-dev`, `joomini-stream`, EDGE API, Zappa 규칙)는 새 구조 확인 후 `backend/scripts/cleanup_legacy_zappa.sh`로 지운다.

## 7. 한 문단 요약

> CloudFront 한 도메인 뒤에 프론트(React SPA, S3)와 API Gateway(REGIONAL)를 두고, 백엔드는 Lambda Web Adapter로 Flask를 띄운 Lambda 하나가 일반 API와 SSE 스트리밍을 모두 처리합니다. 처음에는 Zappa가 응답을 모아 돌려줘서 스트리밍용 함수를 따로 두었는데, API Gateway REST의 응답 스트리밍을 모든 경로에 적용해 함수를 하나로 합쳤습니다. API Gateway는 Function URL OAC가 POST 본문 해시를 요구하는 문제와 요청 제한 때문에 유지했고, EDGE를 REGIONAL로 바꿔 CloudFront 이중 경유를 없앴습니다. 프론트는 로그인 사용자 전용 채팅 앱이라 SEO와 SSR이 필요 없어서 서버 없이 정적 파일로 서빙합니다. 예약 작업은 EventBridge Scheduler, 비밀값은 Secrets Manager, 리소스 정의는 CloudFormation 하나로 관리하고, 배포는 GitHub Actions OIDC로 저장된 키 없이 합니다.
