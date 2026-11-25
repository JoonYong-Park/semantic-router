# DynamoDB 설계 (PostgreSQL → DynamoDB 전환 1단계)

Lambda + Zappa 배포를 위해 PostgreSQL(테이블 9개)을 DynamoDB로 옮길 때의 조회 패턴과 키 설계.
코드를 고치기 전에 이 문서를 먼저 확정한다. 조회 패턴이 바뀌면 GSI 추가와 기존 데이터 재기록이 필요하기 때문이다.

- 기준 코드: 당시 FastAPI 백엔드의 `backend/app/core/models.py`와 각 `router.py`의 `select()` / `delete()` / `update()`. 이 코드는 지금 저장소에서 지웠고 `main` 브랜치와 git 기록(커밋 `b6d9da8` 이전)에 있다. 아래 표의 "코드 위치"도 그 기준이다.
- 구현: `backend/app/db/` (키 `keys.py`, 조회 패턴별 함수 `repo/`)
- 전제: 로그인(Cognito)이 붙으면 모든 요청에 `user_id`가 있다. 지금의 `DEMO_USER_ID`는 그 자리를 대신한다.

---

## 1. 지금 코드의 조회 패턴 (AP)

| # | 조회 패턴 | 지금 코드 위치 | 빈도 |
|---|---|---|---|
| AP1 | 사용자 프로필 조회 | `users/router.py` | 높음 |
| AP2 | 사용자 설정 (개인 지침, 가져온 메모리) 조회와 수정 | `settings/router.py`, `chat/router.py` 240 | 메시지마다 |
| AP3 | 사용자 메모리 전체 조회 (최신 수정순), 카테고리별 조회 | `settings/router.py` 131, `chat/router.py` 247, `memory/extract.py` 202 | 메시지마다 |
| AP4 | 메모리 토픽 하나를 id로 삭제 (전역인지 프로젝트인지 모름) | `settings/router.py` 195 | 낮음 |
| AP5 | 사용자의 대화 목록 (고정 먼저, 그다음 최신 수정순) | `chat/router.py` 98 | 높음 |
| AP6 | 대화 하나 조회, 제목, 고정, 순서, 프로젝트 변경 | `chat/router.py` | 높음 |
| AP7 | 대화의 메시지 전체 (시간순) + 메시지에 달린 첨부 | `chat/router.py` 171, 183, `chat/history.py` 153, 185 | 메시지마다 |
| AP8 | 대화에 메시지가 있는지 (첫 메시지 여부) | `chat/router.py` 232 | 메시지마다 |
| AP9 | 메시지 저장, token_count 수정 | `chat/router.py` 440 | 메시지마다 |
| AP10 | 첨부를 메시지에 연결 (message_id 채우기) | `chat/router.py` 306, 651 | 메시지마다 |
| AP11 | 첨부 하나를 id로 조회, 다운로드, 삭제 | `attachments/router.py` | 중간 |
| AP12 | 대화 안 첨부 파일명 목록, 확장자로 존재 여부 확인 | `tools/edit.py` 118, `chat/history.py` 127 | 편집 의도 턴 |
| AP13 | 고아 첨부 정리 (message_id 없음 + 24시간 또는 7일 경과) | `attachments/service.py` 372 | 배치 |
| AP14 | 대화 삭제 (메시지, 첨부, 파일까지 연쇄 삭제) | `chat/router.py` 147 | 낮음 |
| AP15 | 사용자의 프로젝트 목록 (생성순), 프로젝트 하나 조회 | `projects/router.py` 111 | 높음 |
| AP16 | 프로젝트 소속 대화 목록 | `projects/router.py` 152, 437 | 중간 |
| AP17 | 프로젝트 파일 목록 (생성순, 추출 완료만), 파일 하나 삭제, 용량 합계 | `projects/router.py` 191, 241, `chat/router.py` 413 | 중간 |
| AP18 | 프로젝트 메모리 조회 | `chat/router.py` 262, `memory/extract.py` 188 | 프로젝트 메시지마다 |
| AP19 | 프로젝트 삭제 (파일, 메모리, 소속 대화와 그 메시지까지 연쇄 삭제) | `projects/router.py` 139 | 낮음 |
| AP20 | 이번 결제 주기 토큰 사용량 합계 | `usage/router.py` 73 | 화면 열 때 |
| AP21 | 기간별 사용량 그래프 (오늘: 시간별, 주와 월: 일별, 연: 월별) + 모델별 합계 | `usage/router.py` 170 | 화면 열 때 |
| AP22 | 채팅 검색 (제목, 메시지 본문, 첨부 파일명 부분 일치, 대화당 1건, 관련도순) | `chat/search.py` | 낮음 |
| AP23 | 메모리 추출 대상: 사용자 대화 전체 + 커서 이후 메시지 | `memory/extract.py` 165, 403 | 배치 (하루 2회) |

---

## 2. 설계 방향

**단일 테이블 1개 + GSI 3개**로 간다.

- 거의 모든 조회가 "사용자 하나" 또는 "대화 하나" 범위라, 파티션을 `USER#id`와 `CONV#id`로 나누면 대부분 Query 한 번으로 끝난다.
- 대화 화면(AP7)은 메시지와 첨부를 같은 파티션에 둬서 **Query 한 번으로 둘 다** 가져온다. 지금 코드의 쿼리 2번(메시지, 첨부)이 1번으로 줄어든다.
- 대화, 첨부 같은 by-id 엔드포인트도 `USER#{토큰의 user_id}` 아래에서 찾게 돼서, CLAUDE.md에 적힌 "아직 소유자 확인이 없는 by-id 엔드포인트" 문제가 키 구조로 같이 해결된다.

---

## 3. 테이블: `joomini`

모든 아이템은 `PK`, `SK`(문자열)를 갖는다. 시각은 ISO 8601 UTC 문자열(`2026-11-14T03:21:09.123Z`)로 저장해 문자열 정렬이 곧 시간 정렬이 되게 한다. id는 지금처럼 UUID를 쓴다.

| 엔티티 | PK | SK | 주요 속성 | 지금 테이블 |
|---|---|---|---|---|
| 사용자 | `USER#{uid}` | `PROFILE` | email, name, role, is_active, created_at, avatar_updated_at | users |
| 설정 | `USER#{uid}` | `SETTINGS` | personal_instruction, imported_memory, updated_at | user_settings |
| 전역 메모리 | `USER#{uid}` | `MEM#{category}#{mem_id}` | name, summary, details(리스트), updated_at | user_memory |
| 프로젝트 | `USER#{uid}` | `PROJ#{pid}` | name, instruction, pinned, color, sort_order, max_capacity_bytes, **files_total_bytes**, created_at, updated_at | projects |
| 대화 | `USER#{uid}` | `CONV#{cid}` | title, pinned, project_id, sort_order, created_at, updated_at, **message_count**, last_extracted_sk | conversations |
| 메시지 | `CONV#{cid}` | `MSG#{created_at}#{mid}` | role, content, token_count, model_used, task_category, complexity_score, input/output_tokens, web_search_used, web_search_query | messages |
| 첨부 | `CONV#{cid}` | `ATT#{aid}` | message_id, original_name, s3_key, mime_type, size_bytes, extraction_status, source, source_attachment_id, generation_meta, created_at, **ttl** | attachments |
| 프로젝트 파일 | `PROJ#{pid}` | `FILE#{created_at}#{fid}` | original_name, s3_key, mime_type, size_bytes, extraction_status | project_files |
| 프로젝트 메모리 | `PROJ#{pid}` | `MEM#{mem_id}` | name, summary, details, updated_at | project_memory |
| 사용량 (일별) | `USER#{uid}` | `USAGE#D#{YYYY-MM-DD}#{model}` | input_tokens, output_tokens, calls (원자적 ADD) | messages 집계 대체 |
| 사용량 (시간별) | `USER#{uid}` | `USAGE#H#{YYYY-MM-DDTHH}#{model}` | input_tokens, output_tokens, calls, ttl (3일) | messages 집계 대체 |

날짜와 시각의 기준은 지금 코드와 같은 **KST**로 맞춘다(사용량 그래프가 KST 기준이므로).

### GSI

| GSI | PK | SK | 들어가는 아이템 | 쓰는 곳 |
|---|---|---|---|---|
| **GSI1 ByUpdated** | `GSI1PK = USER#{uid}` | `GSI1SK = {updated_at}` | 대화 | AP5 대화 목록 최신순 (페이지네이션 가능) |
| **GSI2 ByProject** | `GSI2PK = PROJ#{pid}` | `GSI2SK = {updated_at}` | 프로젝트에 속한 대화만 (희소 인덱스) | AP16, AP19 |
| **GSI3 ById** | `GSI3PK = {id}` | 없음 | 첨부, 프로젝트 파일, 전역과 프로젝트 메모리 | AP4, AP11, 프로젝트 파일 삭제 |

- GSI2는 `project_id`가 있는 대화에만 속성을 넣는 희소 인덱스라, 일반 대화는 인덱스 용량을 차지하지 않는다.
- GSI3 조회 결과로 PK, SK를 얻은 뒤에는 반드시 **소유자를 다시 확인**한다(첨부의 대화가 내 대화인지 등).

---

## 4. 조회 패턴별 처리

| # | DynamoDB 연산 |
|---|---|
| AP1, AP2 | `GetItem(USER#uid, PROFILE)`, `GetItem(USER#uid, SETTINGS)`. 둘 다 필요하면 `BatchGetItem` |
| AP3 | `Query(PK=USER#uid, SK begins_with MEM#)`. 카테고리별은 `MEM#{category}#`. 최신순 정렬은 앱에서 (사용자당 토픽 수십 개 수준) |
| AP4 | `GSI3 Query(id)` → 찾은 PK, SK로 소유 확인 후 `DeleteItem` |
| AP5 | `GSI1 Query(USER#uid, 내림차순)`. 고정 대화는 개수가 적어서 앱에서 맨 앞으로 정렬 |
| AP6 | `GetItem(USER#uid, CONV#cid)`, `UpdateItem`. updated_at을 바꾸면 GSI1SK, GSI2SK도 같이 갱신 |
| AP7 | `Query(PK=CONV#cid)` 한 번 → SK가 `MSG#`인 것과 `ATT#`인 것을 앱에서 나눠 message_id로 묶음 |
| AP8 | 대화 아이템의 `message_count`를 보고 판단. 메시지 저장 때 `ADD message_count 1` |
| AP9 | `PutItem` (SK에 created_at이 들어가므로 저장 직후 SK를 변수로 들고 있다가 token_count 수정 때 사용) |
| AP10 + AP9 + AP6 | 사용자 메시지 저장, 첨부 연결(`SET message_id`, `REMOVE ttl`), 대화 updated_at 갱신을 `TransactWriteItems`로 한 번에 |
| AP11 | `GSI3 Query(aid)` → 소유 확인 → `GetItem`. 파일 본문은 S3 presigned URL로 내려줌 |
| AP12 | `Query(PK=CONV#cid, SK begins_with ATT#)` 후 파일명 필터는 앱에서 |
| AP13 | **DynamoDB TTL로 대체.** 업로드 시 `ttl = now + 24h`(생성 파일은 7일), 메시지에 연결될 때 `REMOVE ttl`. 만료 삭제 이벤트를 DynamoDB Streams → Lambda로 받아 S3 파일도 삭제. 정리 배치 루프가 필요 없어짐 |
| AP14 | `Query(PK=CONV#cid)`로 키 수집 → `BatchWriteItem` 삭제(25개씩) → `DeleteItem(USER#uid, CONV#cid)` → S3 `chats/{cid}/` 접두사 삭제 |
| AP15 | `Query(PK=USER#uid, SK begins_with PROJ#)`, 생성순 정렬은 앱에서 (프로젝트 수가 적음) |
| AP16 | `GSI2 Query(PROJ#pid)` |
| AP17 | 목록: `Query(PK=PROJ#pid, SK begins_with FILE#)`(SK에 created_at이 있어 생성순). 추출 완료 필터는 `FilterExpression`. 용량 합계는 프로젝트 아이템의 `files_total_bytes`를 업로드와 삭제 때 `ADD`로 증감 |
| AP18 | `Query(PK=PROJ#pid, SK begins_with MEM#)` |
| AP19 | GSI2로 소속 대화 목록 → 대화마다 AP14 → `Query(PK=PROJ#pid)` 전체 삭제 → `DeleteItem(USER#uid, PROJ#pid)` → S3 `projects/{pid}/` 삭제. 아이템이 많을 수 있어 비동기 작업(Zappa `@task`)으로 처리 |
| AP20 | `Query(PK=USER#uid, SK between USAGE#D#{주기 시작} and USAGE#D#{오늘}~)` 후 합산 (최대 31일 × 9모델) |
| AP21 | 오늘: `USAGE#H#{오늘}` 범위 Query. 주와 월: `USAGE#D#` 범위 Query. 연: `USAGE#D#` 1년치 Query 후 월별로 묶기 (최대 365 × 9 아이템). 모델별 합계는 같은 결과에서 계산 |
| AP22 | 아래 6번 참고 |
| AP23 | `Query(PK=USER#uid, SK begins_with CONV#)`로 대화 목록 → 대화마다 `Query(PK=CONV#cid, SK > last_extracted_sk)` |

사용량 기록: assistant 메시지를 저장할 때 일별, 시간별 아이템 두 개에 `UpdateItem ... ADD input_tokens :i, output_tokens :o, calls :one`을 한다. 지금은 사용량 화면을 열 때마다 메시지 전체를 읽어 합산하지만, 전환 후에는 미리 쌓아 둔 카운터만 읽는다.

---

## 5. RDB 기능별 대체 방법

| RDB 기능 | 대체 |
|---|---|
| `ON DELETE CASCADE` | AP14, AP19처럼 파티션 Query 후 일괄 삭제를 앱 코드로. 프로젝트 삭제는 비동기 |
| `ON DELETE SET NULL` (첨부의 message_id) | 메시지 단독 삭제 기능이 없으므로 당장은 불필요. 생기면 같은 트랜잭션에서 첨부의 message_id 제거 |
| 이메일 유일 인덱스 | Cognito가 이메일 유일성을 보장. 자체 관리가 필요하면 `PK=EMAIL#{lower}`, `SK=EMAIL` 아이템을 `attribute_not_exists(PK)` 조건부 쓰기 |
| `SUM`, `GROUP BY` | 사용량 카운터 아이템 (4번) |
| `COUNT(*)` | `message_count` 속성 |
| 트랜잭션 | 묶어야 하는 곳(AP10)만 `TransactWriteItems` (최대 100개) |
| `create_all` | 테이블과 GSI 생성 스크립트 하나로 로컬(DynamoDB Local)과 AWS에 같은 구조로 생성 |

---

## 6. 주의할 점

1. **아이템 크기 400KB 제한**
   - `Attachment.extracted_text`(문서 추출 텍스트)와 `ProjectFile.extracted_text`는 수백 KB를 넘을 수 있어 **S3에 JSON으로 저장**하고 아이템에는 `extracted_s3_key`만 둔다.
   - 메시지 `content`도 아주 긴 답변이 400KB에 가까워질 수 있다. 350KB를 넘으면 S3로 보내는 분기를 둔다.
2. **채팅 검색(AP22)**: DynamoDB에는 부분 문자열 검색과 관련도 정렬이 없다.
   - 1차: 사용자 대화 목록 + 각 대화의 메시지를 Query해서 앱에서 부분 일치 검색 (사용자 한 명 데모 규모에서는 충분).
   - 데이터가 커지면: 메시지 저장 시 검색 인덱스로 복제(OpenSearch). 다만 OpenSearch Serverless는 최소 비용이 커서 데모에는 과하다.
3. **일관성**: GSI는 최종 일관성이라 대화를 만든 직후 목록 조회(GSI1)에 바로 안 보일 수 있다. 새 대화는 프론트가 이미 로컬 상태에 추가하므로 실사용 문제는 작지만, 생성 API 응답으로 목록을 갱신하는 흐름을 유지한다.
4. **업데이트와 GSI 키**: 대화의 `updated_at`을 바꿀 때 `GSI1SK`(와 프로젝트 대화면 `GSI2SK`)를 **항상 같이** 바꾼다. 빠뜨리면 목록 순서가 어긋난다. 한 함수(`touch_conversation`)로만 갱신하게 만든다.
5. **메모리 추출 커서**: `last_extracted_message_id` 대신 메시지 SK(`last_extracted_sk`)를 저장하면 "커서 이후 메시지"를 SK 범위 조건으로 바로 가져올 수 있다.
6. **파일 저장 위치**: 업로드와 생성 파일은 S3 `chats/{cid}/...`, `projects/{pid}/...`, `users/{uid}/avatar.png`로 지금 폴더 구조를 그대로 옮긴다. 아이템의 `stored_path`는 `s3_key`로 바꾼다.

---

## 7. 결정 사항 (2026-11-14 확정)

| 항목 | 결정 | 이유 |
|---|---|---|
| 테이블 구성 | **단일 테이블 `joomini` + GSI 3개** | AWS 가이드는 함께 조회하는 데이터를 한 테이블에 모아 테이블 수를 최소로 유지하라고 권한다. 대화 화면(AP7)을 Query 한 번으로 끝낼 수 있고, GSI 3개는 테이블당 기본 한도 20개에 한참 못 미친다. |
| 용량 모드 | **온디맨드 (PAY_PER_REQUEST)** | 사용량이 적고 들쭉날쭉한 데모라 처리량을 미리 정할 필요가 없다. 쓴 만큼만 과금되어 Lambda의 비용 구조와 맞는다. |
| 채팅 검색 | **1차 배포에 포함, 앱 내 검색** | 사용자 한 명 규모라 대화와 메시지를 Query해 앱에서 걸러도 충분하다. OpenSearch는 데이터가 커질 때 검토한다. |
| 기존 데이터 | **새로 시작** | 이전 데이터는 데모 데이터다. 필요해지면 테이블별 일회성 이관 스크립트를 따로 만든다. |
| 시간대 | **KST 유지** | 사용량 그래프와 결제 주기가 지금 KST 기준이라 화면 동작을 바꾸지 않는다. |
| 숫자 타입 | **float 금지, Decimal 사용** | boto3는 DynamoDB Number를 `Decimal`로 주고받는다. `complexity_score`처럼 float인 값은 저장 전에 `Decimal(str(x))`로 바꾼다. |

## 8. 근거 (AWS 공식 문서와 Zappa 문서)

| 사실 | 설계에 미치는 영향 | 출처 |
|---|---|---|
| 아이템 최대 크기 400KB (속성 이름 포함) | 문서 추출 텍스트는 S3로, 긴 메시지는 S3 분기 | [DynamoDB Constraints](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Constraints.html) |
| 트랜잭션 최대 100개 아이템, 4MB. 같은 아이템을 한 트랜잭션에서 두 번 다룰 수 없음 | AP10 묶음 쓰기는 가능. 첨부가 많은 턴도 100개 안쪽 | 같은 문서 |
| BatchWriteItem 최대 25개, Query 결과는 호출당 1MB | 삭제는 25개씩 나눠서, 조회는 `LastEvaluatedKey`로 이어서 | 같은 문서 |
| TTL 만료 아이템은 "만료 후 보통 며칠 안에" 삭제되고, 그 전까지는 Query 결과에 나온다. TTL 삭제는 Streams에 서비스 삭제로 기록된다 | 고아 첨부는 화면에 노출되지 않으므로 지연 삭제로 충분. 만료 여부가 중요한 조회는 `ttl > now` 필터를 건다. S3 정리는 Streams로 연결 | [DynamoDB TTL](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/howitworks-ttl.html) |
| GSI 기본 한도 테이블당 20개 | GSI 3개로 여유 있음 | [DynamoDB Quotas](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/ServiceQuotas.html) |
| DynamoDB Local은 엔드포인트만 바꾸면 같은 코드로 동작. 단, 트랜잭션 충돌 예외가 발생하지 않고 읽기는 사실상 강한 일관성처럼 보인다 | 로컬 개발은 DynamoDB Local. GSI 지연 반영(최종 일관성) 문제는 로컬에서 재현되지 않으므로 코드에서 미리 대비 | [DynamoDB Local 사용 노트](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/DynamoDBLocal.UsageNotes.html) |
| Lambda zip 배포는 압축 해제 기준 250MB, 컨테이너 이미지는 10GB, 실행 최대 15분, /tmp 최대 10GB | torch와 라우터 모델(3GB 이상)은 zip에 못 들어감. 모델 경량화나 외부 API 전환이 필요 | [Lambda quotas](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html) |
| API Gateway REST API 통합 타임아웃은 기본 29초이고, 2024년 6월부터 Regional REST API는 쿼터 증가 요청으로 늘릴 수 있다 (계정의 리전 스로틀 한도가 줄어들 수 있음) | 긴 LLM 응답은 우선 비동기 작업과 폴링으로 설계하고, 타임아웃 증가는 보조 수단으로 | [AWS 발표 (2024-06)](https://aws.amazon.com/about-aws/whats-new/2024/06/amazon-api-gateway-integration-timeout-limit-29-seconds) |
| Zappa는 Python 3.9~3.14, Flask(WSGI), `@task` 비동기 실행(인자 256KB 이하), `events` cron 스케줄, ECR 컨테이너 이미지 배포를 지원 | 백그라운드 작업과 스케줄러를 Zappa 기능으로 옮김 | [Zappa README](https://github.com/zappa/Zappa) |

## 9. 다음 단계

1. 로컬 환경: `docker-compose.dev.yml`에 DynamoDB Local 추가, 테이블과 GSI 생성 스크립트 작성
2. 데이터 접근 계층(`repo`) 작성: 위 AP 번호 하나당 함수 하나 (`list_conversations(uid)`, `get_conversation_page(cid)` 등)
3. DynamoDB Local에서 스모크 테스트
