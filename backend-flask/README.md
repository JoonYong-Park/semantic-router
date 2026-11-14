# backend-flask

Joomini 백엔드를 Lambda + Zappa + DynamoDB로 옮기는 작업 공간이다.
기존 FastAPI 백엔드(`../backend`)는 그대로 두고, 기능을 하나씩 이쪽으로 옮긴다.

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

| 서비스 | 주소 |
|---|---|
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
