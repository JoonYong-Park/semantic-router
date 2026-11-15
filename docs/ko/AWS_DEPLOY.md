# AWS 서버리스 배포 (Lambda + API Gateway + DynamoDB + Zappa)

2026-11-15에 처음 배포했다. 이 문서는 "어떻게 배포했는지"를 면접에서 설명할 수 있는 수준으로 정리한 것이다.

- 화면: https://alfx3kmfqqltjokbowculfwwwq0mlqxa.lambda-url.ap-northeast-2.on.aws/
- API: https://lpowuegiud.execute-api.ap-northeast-2.amazonaws.com/dev
- 리전: 서울(ap-northeast-2)

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
API_URL=https://lpowuegiud.execute-api.ap-northeast-2.amazonaws.com/dev bash scripts/deploy_frontend_lambda.sh
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
- 로컬 개발, 온프레미스, Lambda가 **같은 Dockerfile**을 쓰고 마지막 단계만 다르다. 환경 차이로 인한 버그가 줄어든다.

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

## 5. 아직 남은 한계 (면접에서 물어보면 솔직하게)

| 한계 | 이유 | 다음 단계 |
|---|---|---|
| 답변이 실시간으로 흐르지 않고 한 번에 보임 | API Gateway(REST)가 응답을 모아서 보낸다 | 비동기 작업 + 폴링, 또는 Lambda 응답 스트리밍 |
| 긴 작업(파일 생성 등)이 29초를 넘으면 화면에 오류 | API Gateway 통합 제한 29초. Lambda는 끝까지 실행해 답은 저장된다 | 위와 같음 (비동기 처리) |
| 업로드 약 6MB 상한 | Lambda 요청 크기 제한 | S3 presigned URL로 브라우저가 S3에 직접 업로드 |
| auto 라우팅 꺼짐 | 모델 로딩 시간 | 모델을 이미지에 넣고 프로비저닝 동시성, 또는 임베딩 API로 교체 후 재평가 |
| 로그인 없음 (모두 같은 데모 사용자) | Cognito 미연동 | Cognito + `auth.current_user_id()` 교체 |
| 하루 2회 메모리 자동 추출 미연결 | | Zappa `events`에 `memory.extract.run_scheduled` 등록 |

## 6. 면접용 한 문단 요약

> API Gateway, Lambda, DynamoDB로 서버리스 구조를 구성했습니다. 앱에 LibreOffice와 ML 모델이 있어 zip 용량을 넘기 때문에 Zappa의 컨테이너 이미지 배포를 썼고, 로컬과 같은 Dockerfile에서 단계만 나눠 환경 차이를 줄였습니다. Lambda의 디스크가 임시 공간이라 파일은 S3를 정본으로 두고, 응답 뒤 실행이 멈추는 특성에 맞춰 백그라운드 작업을 요청 안으로 옮겼습니다. 콜드 스타트는 라우터 모델을 끈 상태에서 0.8~2.6초로 측정했고, 실시간 스트리밍과 29초 제한은 비동기 처리로 풀 다음 과제로 남겨 두었습니다.
