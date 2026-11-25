# AWS 서버리스 배포 (Lambda + API Gateway + DynamoDB + Zappa)

2026-11-15에 처음 배포했다. 이 문서는 "어떻게 배포했는지"를 정리한 것이다.

- 리전: 서울(ap-northeast-2)
- 사용자는 CloudFront 한 도메인으로 들어온다(`infra/cloudfront.yaml`). 지금은 main에 push하면 GitHub Actions가 배포한다(`.github/workflows/deploy-lambda.yml`).

---

## 1. 구조 한눈에 보기

```
브라우저 ──화면──▶ 프론트 Lambda (Next.js, Function URL)
   │
   └──API 호출──▶ API Gateway (REST) ──▶ 백엔드 Lambda (Flask, Zappa, 컨테이너 이미지)
                                              ├──▶ DynamoDB (단일 테이블, GSI 3개)
                                              ├──▶ S3 (첨부, 생성 파일)
                                              └──▶ LLM API (OpenAI, Gemini, Claude)
```

| 구성 요소 | 하는 일 |
|---|---|
| 프론트 Lambda | Next.js 서버를 Lambda에서 실행. Lambda Web Adapter가 HTTP 요청을 Next.js에 그대로 넘겨 준다. Function URL로 공개 주소를 받는다 |
| API Gateway | HTTPS 주소, 요청 제한(초당 2건)을 맡고 요청을 백엔드 Lambda로 넘긴다. Zappa가 만든다 |
| 백엔드 Lambda | Flask 앱 전체. Zappa 핸들러가 API Gateway 이벤트를 Flask 요청으로 바꾼다 |
| DynamoDB | 대화, 메시지, 메모리, 프로젝트, 사용량. 설계는 [DYNAMODB_DESIGN.md](DYNAMODB_DESIGN.md) |
| S3 | 업로드 원본, 추출 이미지, 미리보기 PDF, 모델이 만든 파일 |
| ECR | 두 Lambda의 컨테이너 이미지 저장소 |
| EventBridge | 4분마다 백엔드를 깨워 첫 요청이 느려지는 것(콜드 스타트)을 줄인다. Zappa가 만든다 |
| IAM 실행 역할 | 백엔드는 자기 테이블과 버킷에만 접근할 수 있다 |
| 예산 알림 | 월 $10 기준, 80% 실제 지출과 100% 예상 지출에서 메일 |

## 2. 배포 방법 (명령 두 줄)

```bash
bash backend/scripts/deploy_lambda.sh update        # 백엔드 (처음엔 인자 없이 = zappa deploy)
bash scripts/deploy_frontend_lambda.sh                 # 프론트 (API는 같은 도메인의 /api)
```

백엔드 스크립트가 하는 일:
1. `zappa_settings.json` 생성. 모델 API 키는 `.env`에서 읽어 **Lambda 환경 변수로만** 넣는다(이미지와 git에 남지 않음).
2. `zappa save-python-settings-file`로 이미지에 넣을 설정 파일 생성
3. `backend/Dockerfile`의 `lambda` 단계로 이미지 빌드. 서버용 이미지와 무거운 레이어(LibreOffice, torch)를 공유한다.
4. S3 버킷과 DynamoDB 테이블이 없으면 만든다.
5. ECR에 올리고 `zappa deploy/update -d <이미지>`
6. API Gateway 요청 제한을 다시 건다.

## 3. 왜 이렇게 했나 (결정과 근거)

**컨테이너 이미지로 배포**
- 일반 Lambda zip은 250MB가 상한인데, 이 앱은 LibreOffice(문서 미리보기, PDF 변환)와 torch(라우터 모델)가 들어가서 수 GB다. 컨테이너 이미지는 10GB까지 된다.
- 로컬 개발과 Lambda가 **같은 Dockerfile**을 쓰고 마지막 단계만 다르다. 환경 차이로 인한 버그가 줄어든다.

**파일은 S3, `/tmp`는 캐시**
- Lambda 디스크(`/tmp`)는 실행 환경마다 따로이고 사라질 수 있다. 그래서 정본은 S3에 두고, 추출기나 LibreOffice처럼 로컬 파일이 필요한 코드만 `/tmp`에 내려받아 쓴다.
- 코드에서는 `storage.py` 한 파일만 바꿨다. DB에는 처음부터 경로가 아니라 키만 저장하게 설계해서 가능했다.

**백그라운드 작업을 요청 안에서 처리**
- Lambda는 응답을 보내면 실행 환경을 멈춘다. 로컬에서 스레드로 돌리던 첨부 추출과 제목 생성이 중간에 멈추는 문제가 생긴다.
- 그래서 Lambda에서는 요청 안에서 끝내도록 했다(`services/files.run_in_background`). 업로드 응답이 추출 시간만큼 늦어지는 대신 결과가 확실하다.

**auto 라우팅은 첫 배포에서 끔 (`ROUTER_MODELS=off`)**
- 라우터 모델(mmBERT + E5, 3GB 이상)을 Lambda가 뜰 때마다 불러오면 수십 초가 걸려 API Gateway 29초 제한에 걸릴 수 있다.
- 먼저 수동 모델 선택으로 서비스가 확실히 뜨게 하고, 콜드 스타트를 실측한 뒤 켜기로 했다. 프론트 기본 모드도 빌드 옵션(`NEXT_PUBLIC_DEFAULT_MODE`)으로 수동 모드로 바꿨다.

**프론트도 Lambda**
- Next.js 서버(SSR)를 띄워야 해서 정적 호스팅(S3)으로는 안 된다. Amplify는 GitHub 연결이 필요해, 같은 컨테이너 방식으로 Lambda Web Adapter를 썼다. 백엔드와 배포 방식이 같아진다.

**보안과 비용**
- 백엔드 CORS는 프론트 주소 하나만 허용한다.
- 로그인이 없는 데모라 누구나 쓸 수 있어서, API Gateway에 초당 2건 제한을 걸고 예산 알림을 만들었다.

## 4. 실측 수치

| 항목 | 값 |
|---|---|
| 백엔드 콜드 스타트 (Lambda 초기화) | 0.8 ~ 2.6초 (라우터 모델 끔) |
| 프론트 콜드 스타트 | 0.6 ~ 0.75초 |
| 따뜻한 상태의 `/health` | 약 0.3 ~ 0.7초 (서울에서 호출) |
| 백엔드 메모리 / 제한 시간 | 3,008MB / 120초 |

## 4-1. 답변 스트리밍

Zappa 핸들러는 Flask 응답을 끝까지 모아 한 번에 돌려줘서 SSE가 흐르지 않는다. 그래서 같은 백엔드 이미지를
Lambda Web Adapter + gunicorn으로 띄운 **스트리밍 전용 Lambda(`joomini-stream`)**를 하나 더 두고,
API Gateway REST의 응답 스트리밍(통합 `responseTransferMode=STREAM`, 최대 15분)으로 `/stream/*` 만 그쪽으로 보낸다.

| 경로 | 받는 Lambda |
|---|---|
| `/stream/conversations/{id}/messages`, `/stream/conversations/{id}/stream` | `joomini-stream` (응답 스트리밍) |
| 그 밖의 API | `joomini-dev` (Zappa) |

- 앱은 어느 쪽에서 돌든 같은 라우트를 쓴다. WSGI 미들웨어가 `/stream` 접두사를 뗀다.
- 스트리밍 Lambda에서는 답변 생성을 스레드로 돌리고, 브라우저가 중간에 떠나도 생성이 끝나 저장될 때까지 응답(=호출)을 붙잡아 둔다.
- 이벤트가 없는 동안 15초마다 `: ping` 주석을 보내 CloudFront·API Gateway가 조용한 연결을 끊지 않게 한다.
- 배포: `backend/scripts/deploy_stream_lambda.sh` (Zappa 배포 다음 단계, CI에 포함).

## 4-2. 파일 업로드 (S3 직접 업로드)

Lambda 요청 본문은 6MB가 상한이라, 첨부·참고자료 파일은 서버를 거치지 않고 브라우저가 S3에 바로 올린다.

1. `POST .../upload-url {filename, size}` → 서버가 형식·크기·프로젝트 용량을 검사하고 presigned POST(`url`, `fields`, `upload_id`)를 준다. 키와 크기 상한(25MB)이 서명에 들어 있어 다른 위치나 더 큰 파일은 S3가 거절한다.
2. 브라우저가 S3에 바로 올린다(XHR, 진행률 표시). 버킷 CORS는 서비스 도메인의 POST만 허용한다(`deploy_lambda.sh`).
3. `POST .../complete {upload_id, filename}` → 서버가 S3에 올라간 실제 크기를 확인하고 첨부/참고자료를 등록한 뒤 추출을 시작한다.

버킷이 없는 로컬에서는 1)이 `{"mode": "multipart"}`를 돌려주고 프론트는 예전처럼 본문을 서버로 보낸다.

## 5. 아직 남은 한계

| 한계 | 이유 | 다음 단계 |
|---|---|---|
| 일반 API(채팅 외)는 29초 제한 | API Gateway 기본 통합 제한 | 오래 걸리는 작업은 SQS + Worker Lambda |
| auto 라우팅 꺼짐 | 모델 로딩 시간 | 모델을 이미지에 넣고 프로비저닝 동시성, 또는 임베딩 API로 교체 후 재평가 |

## 6. 한 문단 요약

> API Gateway, Lambda, DynamoDB로 서버리스 구조를 구성했습니다. 앱에 LibreOffice와 ML 모델이 있어 zip 용량을 넘기 때문에 Zappa의 컨테이너 이미지 배포를 썼고, 로컬과 같은 Dockerfile에서 단계만 나눠 환경 차이를 줄였습니다. Lambda의 디스크가 임시 공간이라 파일은 S3를 정본으로 두고, 응답 뒤 실행이 멈추는 특성에 맞춰 백그라운드 작업을 요청 안으로 옮겼습니다. 콜드 스타트는 라우터 모델을 끈 상태에서 0.8~2.6초로 측정했고, 실시간 스트리밍과 29초 제한은 비동기 처리로 풀 다음 과제로 남겨 두었습니다.
