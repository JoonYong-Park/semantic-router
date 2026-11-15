# 배포 (CI/CD)

> English: [../en/DEPLOYMENT.md](../en/DEPLOYMENT.md)

이 프로젝트는 온프레미스 서버(Rocky Linux)에 등록된 GitHub Actions **self-hosted runner**로 배포합니다. runner가 GitHub에 push 이벤트가 있는지 직접 물어보는 방식이라, 배포 서버에 들어오는 포트를 열 필요가 없습니다.

## 실행 조건

- `.github/workflows/deploy.yml`은 `main` 브랜치에 push할 때만 실행됩니다.
- 같은 브랜치의 배포는 `concurrency`로 하나씩 순서대로 실행되어, 진행 중인 배포와 새 배포가 겹치지 않습니다.

## 배포 흐름

1. `actions/checkout`이 최신 `main` 코드를 runner 작업 폴더로 받습니다.
2. `scripts/deploy.sh`를 실행합니다.
   1. 지금 실행 중인 `backend`/`frontend` 이미지에 `:previous` 태그를 붙여 백업합니다.
   2. `docker compose build`로 새 이미지를 빌드합니다.
   3. `docker compose up -d`로 새 컨테이너를 띄웁니다.
   4. `scripts/healthcheck.sh`가 backend의 `GET /health`를 3초 간격으로 최대 40번(2분) 확인합니다. 백엔드가 시작할 때 torch를 불러오고 DynamoDB 테이블을 확인해서 30초보다 오래 걸릴 수 있습니다.
   5. 빌드, 실행, 헬스체크 중 하나라도 실패하면 자동 롤백합니다.
3. 배포 결과는 GitHub Actions 실행 요약(step summary)에 기록됩니다.

## 롤백 조건과 의미

- **롤백 조건:** `docker compose build`가 실패하거나, `docker compose up -d`가 실패하거나, 새 컨테이너가 재시도 횟수 안에 `/health` 확인을 통과하지 못한 경우입니다.
- **롤백의 의미:** `scripts/deploy.sh`의 롤백은 **배포 롤백**입니다. 빌드 직전에 `:previous`로 백업해 둔 Docker 이미지로 컨테이너를 되돌리는 것이고, `git revert` 같은 소스 코드 롤백이 아닙니다. `main`의 커밋 기록은 그대로입니다.
- 롤백이 성공하면(이전 이미지가 다시 떠서 헬스체크를 통과하면) 워크플로는 실패로 표시되지만, 서비스는 마지막으로 정상이던 버전으로 계속 돌아갑니다.

## FastAPI + Postgres에서 Flask + DynamoDB로 바뀐 뒤 첫 배포

이 변경이 `main`에 처음 들어가는 배포는 아래를 확인해야 합니다.

- **데이터는 옮기지 않습니다.** 새 백엔드는 서버의 DynamoDB Local(`dynamodb_data` 볼륨)에 빈 테이블로 시작합니다. 기존 Postgres 데이터(`postgres_data` 볼륨)는 지우지 않고 남아 있습니다.
- **기존 `postgres` 컨테이너는 그대로 남습니다.** `docker-compose.yml`에서 빠졌기 때문에 compose가 고아 컨테이너로 경고만 하고 건드리지 않습니다. 새 버전이 정상인 것을 확인한 뒤 서버에서 직접 `docker stop postgres && docker rm postgres`로 정리합니다(볼륨은 남음).
- **이 배포에서는 자동 롤백이 동작하지 않습니다.** `:previous` 이미지는 Postgres가 필요한 FastAPI 이미지인데 새 `docker-compose.yml`에는 Postgres 설정이 없어서, 되돌려도 `/health`를 통과하지 못합니다. 실패하면 이전 커밋으로 체크아웃해 `docker compose up -d --build`로 직접 되돌립니다.
- 업로드 폴더(`UPLOAD_HOST_DIR`)는 같은 호스트 폴더를 컨테이너의 `/data/uploads`로 마운트합니다. 기존 파일은 남지만 새 DB에는 연결 정보가 없어 쓰이지 않습니다.

## 직접 조치가 필요한 경우

아래 경우는 자동으로 복구되지 않아서, runner 서버에 직접 접속해야 합니다.

- **첫 배포가 실패한 경우:** 되돌아갈 `:previous` 이미지가 아직 없습니다(`scripts/deploy.sh` 종료 코드 `2`).
- **롤백한 뒤에도 재시작이나 헬스체크가 실패하는 경우:** 이전 이미지 자체의 문제(예: 맞지 않는 DB 스키마)이거나, 디스크 공간·포트 충돌 같은 인프라 문제일 가능성이 큽니다(종료 코드 `2`).
- 이때는 서버에서 `docker ps`, `docker compose -p semantic-router logs`, `docker image ls | grep semantic-router`로 상태를 확인하고, 필요하면 이전 이미지를 직접 다시 띄우거나 원인을 더 살펴봅니다.

## 채팅 첨부 파일 저장소와 배포

`docker-compose.yml`의 `backend` 서비스는 채팅 첨부 파일용으로 호스트 폴더를 컨테이너의 `/data/uploads`에 마운트합니다. 호스트 경로는 서버마다 달라서 저장소가 아니라 `.env`의 `UPLOAD_HOST_DIR`로 지정합니다. 이 마운트는 `scripts/deploy.sh`의 백업·롤백과 서로 영향을 주지 않습니다.

- 스크립트는 **빌드한 이미지**(`semantic-router-backend`, `semantic-router-frontend`)만 `:previous`로 태그하고 복원합니다. 볼륨이나 바인드 마운트는 건드리지 않습니다.
- `UPLOAD_HOST_DIR`은 Docker가 관리하는 이름 있는 볼륨(`backend_hf_cache`, `dynamodb_data`)이 아니라 평범한 호스트 폴더입니다. 컨테이너 수명과 상관없이 존재하므로, 일반 재배포든 자동 롤백이든 같은 호스트 경로를 마운트하고 업로드 파일은 영향을 받지 않습니다.
- runner 서버에 그 폴더가 없으면 자동으로 만들어집니다(Docker가 처음 `up`할 때, 또는 앱이 `Path.mkdir(parents=True, exist_ok=True)`로). 그래서 runner의 `.env`에 `UPLOAD_HOST_DIR`만 넣으면 따로 준비할 것은 없습니다.
- 마운트 안에서는 용도별로 폴더가 나뉩니다. 채팅 첨부는 `uploads/chats/{chat_id}/`(구현됨), 프로젝트 참고자료는 `uploads/projects/{project_id}/`입니다. 이 구조가 바뀌어도 같은 마운트 폴더 안의 하위 폴더 구조일 뿐이라, 볼륨 마운트 설정은 바꿀 필요가 없습니다.

### 업로드 파일 수명 규칙

| | 채팅 첨부 | 프로젝트 참고자료 (예정) |
| --- | --- | --- |
| 사용 전 | 보낸 메시지에 아직 붙지 않은 상태로 24시간이 지나면 정리되어 삭제 | 해당 없음. 올리는 순간 확정되어 미사용 상태가 없음 |
| 삭제 | 사용자가 하나씩 지우거나, 채팅방을 지우면 `uploads/chats/{chat_id}/` 폴더 전체가 삭제 | 삭제 API로 하나씩 지우거나(디스크 파일과 DB 행을 바로 지우고 프로젝트 사용량 다시 계산), 프로젝트를 지우면 `uploads/projects/{project_id}/` 폴더 전체가 삭제 |

## 비밀값 관리

- 워크플로 파일과 배포 스크립트에는 비밀값이 하나도 직접 적혀 있지 않습니다.
- 민감한 설정(DB 계정, `SERVER_IP` 등)은 runner 서버에 미리 둔 `.env` 파일로 제공하고, `docker-compose.yml`의 `env_file`로 넣습니다. 이 파일은 gitignore되어 저장소에 커밋되지 않습니다.
- 워크플로는 그 `.env` 파일의 내용을 읽거나 로그에 남기지 않습니다.

## 로컬에서 직접 실행

runner와 같은 방식으로 배포 스크립트를 직접 실행해 로컬에서 확인할 수 있습니다.

```bash
chmod +x scripts/deploy.sh scripts/healthcheck.sh
./scripts/deploy.sh
```

헬스체크만 실행하려면:

```bash
./scripts/healthcheck.sh http://localhost:8000/health 10 3
```
