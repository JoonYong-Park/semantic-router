# backend-flask

Joomini 백엔드를 Lambda + Zappa + DynamoDB로 옮긴 Flask 버전이다. 프론트가 쓰는 API는
전부 옮겼고(URL과 응답 형태는 기존 FastAPI와 같음), 로컬에서는 DynamoDB Local로 돈다.
기존 FastAPI 백엔드(`../backend`) 코드는 참고용으로 남아 있다.

```
app/
├── __init__.py   create_app, CORS, 라우터 모델 백그라운드 로드, /health
├── auth.py       현재 사용자 (지금은 데모 사용자, Cognito 붙일 자리)
├── storage.py    업로드 파일 저장소 (키 기반, 지금은 로컬 디스크 → S3로 교체할 자리)
├── serializers.py 응답 모양 (기존 schemas.py의 *Out과 같은 키)
├── db/           DynamoDB 연결(dynamo.py), 키 규칙(keys.py), 조회 패턴별 함수(repo/)
├── routes/       HTTP 엔드포인트 (기존 각 router.py와 1:1)
├── chat/         메시지 전송 한 턴(turn.py), 이력(history.py), SSE 버퍼(stream.py), 제목
├── services/     첨부/참고자료 저장·추출·미리보기(files.py), 생성 파일 저장(generated.py)
├── memory/       자동 메모리 추출
└── routing/ llm/ tools/ documents/ websearch/ core/tokens.py
                  기존 엔진 코드 그대로 (DB를 쓰는 부분만 repo 호출로 바꿈)
```

- 설계: [docs/ko/DYNAMODB_DESIGN.md](../docs/ko/DYNAMODB_DESIGN.md)
- 진행 기록: [docs/ko/AWS_MIGRATION_LOG.md](../docs/ko/AWS_MIGRATION_LOG.md)

## 로컬 실행

```bash
# 저장소 루트에서
docker compose -f docker-compose.serverless.yml up -d --build
docker compose -f docker-compose.serverless.yml run --rm flask python scripts/create_table.py
curl -s localhost:5001/health
docker compose -f docker-compose.serverless.yml run --rm flask python scripts/smoke_test.py
```

- 모델 API 키는 저장소 루트 `.env`를 그대로 읽는다.
- auto 라우팅 모델(3GB+)은 기존 개발 환경이 받아 둔 Docker 볼륨(`semantic-router_backend_hf_cache`)을
  같이 쓴다. 처음 뜰 때 로드에 2~3분 걸리고, 그동안 `/health`의 `*_ready`가 false이며
  수동 모델 선택은 바로 된다.
- 업로드 파일은 `../uploads-flask/`에 쌓인다 (기존 `uploads/`와 분리, git 제외).

| 서비스 | 주소 |
|---|---|
| 화면 (Flask를 쓰는 프론트) | http://localhost:3001 |
| Flask API | http://localhost:5001 |
| DynamoDB Local | http://localhost:8001 |
| DynamoDB Admin (GUI) | http://localhost:8002 |

## 배포 (아직 실행 전)

`zappa_settings.example.json`을 `zappa_settings.json`으로 복사해 `CHANGE-ME`를 채운 뒤,
AWS 자격 증명이 있는 Python 3.12 환경에서:

```bash
pip install -r requirements-dev.txt
DYNAMODB_ENDPOINT= python scripts/create_table.py   # AWS에 테이블 생성 (최초 1회)
zappa deploy dev
zappa update dev   # 이후 배포
```
