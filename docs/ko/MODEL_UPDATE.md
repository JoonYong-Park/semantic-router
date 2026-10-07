# 채팅 모델 교체 가이드

> English: [../en/MODEL_UPDATE.md](../en/MODEL_UPDATE.md)

ASAP는 메시지마다 9개 채팅 모델 중 하나로 보냅니다. 모델은 3개 회사(OpenAI, Google, Anthropic) × 3개 크기(small, medium, large)입니다. 새 모델이 나와서 교체할 때 무엇을 고치고 무엇을 테스트해야 하는지, 그리고 지난번 교체 때 실제로 문제를 일으킨 회사별 특이사항을 정리했습니다.

## 수정할 곳

일반적인 교체는 3개 파일만 고치면 됩니다.

| 파일 | 고칠 내용 | 필수 |
|---|---|---|
| [`backend/app/routing/models.json`](../../backend/app/routing/models.json) | 해당 회사 × 크기의 `model_id`(정확한 API ID)와 `display_name` | 필수 |
| [`frontend/lib/models.ts`](../../frontend/lib/models.ts) `MODEL_OPTIONS` | 같은 항목의 `label`. **`display_name`과 똑같아야 합니다** | 필수 |
| [`README.md`](../../README.md) 모델 표 | 모델 ID | 문서 |

### `models.json`

```json
{"company": "openai", "size": "large", "model_id": "gpt-6-astra", "display_name": "GPT-6 Astra"}
```

- `model_id`는 회사 API에 그대로 전달됩니다. `gemini-pro-latest` 같은 별칭은 가리키는 모델이 예고 없이 바뀔 수 있으니, 버전이 고정된 ID를 쓰세요.
- `display_name`은 화면에 표시되고, 답변마다 `messages.model_used`에 저장됩니다.
- `routing/catalog.py`가 시작할 때 이 파일을 읽습니다. 9개 회사 × 크기 조합 중 하나라도 빠지거나 중복되면 **백엔드가 시작되지 않습니다.** 그래서 설정을 잘못 고치면 요청 도중에 에러가 나는 대신, 배포 헬스체크에서 바로 실패합니다.

### `frontend/lib/models.ts`

프론트는 모델 목록을 API로 받지 않고 따로 복사본을 갖고 있어서, 이름을 손으로 맞춰야 합니다.

- 모델 선택 메뉴에는 `label`이 보입니다.
- `ModelBadge`는 메시지의 `messages.model_used`를 label 목록에서 찾아 크기를 알아냅니다. label이 백엔드 `display_name`과 다르면 배지에 Small/Medium/Large 표시가 빠집니다.

### 따로 고치지 않아도 따라가는 곳

아래는 `get_model()`로 카탈로그를 읽어서, 수정할 필요가 없습니다.

- **auto 라우팅:** `routing/selector.py`가 질문 카테고리로 회사를, 복잡도 점수로 크기를 고릅니다.
- **수동 선택:** `chat/router.py`, 그리고 `main.py`의 기존 `/chat`.
- **OpenAI small을 쓰는 백그라운드 호출:**
  - 제목 생성: `chat/title.py`의 `TITLE_MODEL`
  - 메모리 추출: `memory/extract.py`의 `MEMORY_MODEL`

### 9개 모델과 별개인 것

- **이미지 생성 모델:** [`backend/app/tools/image.py`](../../backend/app/tools/image.py)의 `IMAGE_MODEL` 상수입니다. 채팅과는 별개인 OpenAI Images API를 씁니다.
- **카테고리별로 어느 회사에 보낼지:** `routing/catalog.py`의 `CATEGORY_TO_COMPANY`입니다. 모델을 교체할 때가 아니라, 라우팅 정책을 바꾸고 싶을 때만 고칩니다.

## 교체 절차

### 1. 우리 키로 그 모델 ID를 쓸 수 있는지 확인

회사 문서에 나온 모델이라도 우리 키로는 아직 못 쓰거나, 실제 ID가 다를 때가 있습니다. 키가 이미 들어 있는 backend 컨테이너 안에서 각 회사의 모델 목록 API로 확인하세요.

```bash
# OpenAI
docker exec backend python -c "import os,httpx; print(sorted(m['id'] for m in httpx.get('https://api.openai.com/v1/models', headers={'Authorization':'Bearer '+os.environ['OPENAI_API_KEY']}).json()['data'] if m['id'].startswith('gpt')))"

# Google
docker exec backend python -c "import os,httpx; print([m['name'].split('/')[-1] for m in httpx.get('https://generativelanguage.googleapis.com/v1beta/models', params={'key':os.environ['GEMINI_API_KEY'],'pageSize':200}).json()['models'] if m['name'].split('/')[-1].startswith('gemini')])"

# Anthropic (ANTHROPIC_WORKSPACE_ID가 설정돼 있으면 워크스페이스 헤더도 보냄)
docker exec backend python -c "import os,httpx; h={'x-api-key':os.environ['ANTHROPIC_API_KEY'],'anthropic-version':'2023-06-01'}; w=os.environ.get('ANTHROPIC_WORKSPACE_ID'); w and h.update({'anthropic-workspace-id':w}); print([m['id'] for m in httpx.get('https://api.anthropic.com/v1/models', headers=h, params={'limit':50}).json()['data']])"
```

### 2. 파일 수정

`models.json`과 `frontend/lib/models.ts`를 고치고, README의 모델 표도 바꿉니다.

### 3. 다시 빌드

```bash
docker compose -f docker-compose.dev.yml up -d --build backend frontend
docker compose -f docker-compose.dev.yml logs --tail 30 backend   # models.json 에러는 여기 나옵니다
```

### 4. 바꾼 모델마다 처음부터 끝까지 테스트

일반 대화만 확인하면 안 됩니다. 지난 교체 때 회사마다 **툴 호출**에서 서로 다른 이유로 깨졌습니다. 화면에서 모델을 직접 고르거나, `POST /conversations/{id}/messages`에 `"mode": "<회사>", "size": "<크기>"`를 보내서 아래를 확인하세요.

- [ ] 일반 메시지가 `meta → chunk → done` 순서로 옵니다.
- [ ] 웹 검색 질문("웹에서 검색해서 오늘 환율 알려줘")이 `meta → searching → chunk → done` 순서로 옵니다. 툴 호출 → 툴 결과 → 두 번째 모델 호출까지 툴 루프 전체를 거칩니다.
- [ ] 문서 요청("회의록을 docx 파일로 만들어줘")에서 `file` 이벤트가 옵니다. strict 툴 스키마와 큰 출력 상한을 쓰는 경로입니다.
- [ ] 첨부한 이미지와 PDF의 내용을 실제로 읽습니다.
- [ ] 긴 답이 중간에 잘리지 않습니다. 추론 모델은 특히 주의하세요(아래 Anthropic 참고).
- [ ] 답변 행의 `input_tokens` / `output_tokens`가 0이 아닙니다.
  ```bash
  docker exec postgres psql -U asap -d asap -c "select model_used, input_tokens, output_tokens from messages where role='assistant' order by created_at desc limit 5;"
  ```

OpenAI small을 바꿨다면 제목 생성과 메모리 추출도 한 번씩 확인하세요. 둘 다 이 모델로 돕니다.

### 5. 배포

`main`에 push하면 됩니다. 모델 ID는 DB 스키마와 무관해서 마이그레이션은 필요 없습니다. 다만 서버가 로컬과 다른 API 키를 쓴다면 먼저 확인하세요. 서버 키로 새 모델을 쓸 수 없으면, 배포 뒤 그 모델로 가는 호출이 전부 실패합니다.

## 회사별 확인할 것

아래는 모두 이전에 실제로 실패를 일으킨 항목입니다. 모델을 바꿀 때 새 모델도 같은 방식으로 동작하는지 확인하세요.

### OpenAI

- **Chat Completions가 아니라 Responses API(`/v1/responses`)를 씁니다.**
  - GPT-6 Astra와 GPT-6.1 Sol은 Chat Completions에서 `reasoning_effort`가 `"none"`일 때만 함수 툴을 허용하는데, 정작 `"none"`을 지원하지 않습니다.
  - 앞으로 어떤 모델이 한쪽 API에서만 동작한다면, 바꿀 지점은 `backend/app/llm/client.py`의 `_stream_openai_responses`입니다.
- **`reasoning_effort`를 보내지 않습니다.** 그래서 모델마다 기본 추론 강도로 동작하는데, 새 모델의 기본값은 이전 모델보다 느리거나 비쌀 수 있습니다.
- 요청에 `store: false`를 씁니다. 추론 내용은 `encrypted_content`로 받아서 다음 툴 회차에 그대로 되돌려 보냅니다. 새 모델이 여기서 다른 형식의 항목을 돌려주면, 두 번째 툴 회차부터 깨질 수 있습니다.

### Anthropic

- **추론 토큰도 출력 상한을 씁니다.**
  - 일부 모델은 요청마다 추론을 합니다(예: Claude Fable 5.1과 Opus 5.5는 항상 켜져 있음). 이 추론 토큰이 `max_tokens`를 같이 씁니다.
  - 기본값은 `llm/client.py`의 `MAX_OUTPUT_TOKENS = 16384`입니다.
  - 문서 턴은 `chat/router.py`의 `DOC_MAX_OUTPUT_TOKENS = 32000`을 씁니다. 이 값은 `MAX_OUTPUT_TOKENS`보다 커야 합니다. 작으면 문서 턴이 오히려 *더 낮은* 상한을 받습니다.
  - 긴 답이 잘리면 이 값을 올리되, 모델 문서에 나온 최대 출력보다는 작게 두세요.
- **모델 종료일:** Anthropic은 모델마다 "이 날짜 이후 종료 가능"이라는 날짜를 공개합니다. 모델을 고를 때 확인하고, 그 날짜가 지나기 전에 교체하세요.
- **워크스페이스 헤더:** 사용자 계정 키(`sk-ant-usr…`)는 워크스페이스에 묶여 있지 않아서 `.env`에 `ANTHROPIC_WORKSPACE_ID`가 필요합니다. 일반 API 키(`sk-ant-api03-…`)는 필요 없습니다.

### Google (Gemini)

- **OpenAI 호환 Chat Completions 엔드포인트를 씁니다.**
  - 툴 호출에는 `thought_signature`가 따라오는데, 다음 요청에 그대로 되돌려 보내야 합니다. 빠지면 400이 납니다. 이 처리는 `llm/messages.py`의 `_prepare_openai_messages`에 있습니다.
  - 이 동작이 바뀔 수 있으니, Gemini 세대가 바뀌면 툴 호출을 다시 테스트하세요.
- **429 "You exceeded your current quota"**는 키의 요금제에 그 모델이 포함되지 않았다는 뜻입니다(Gemini 3.1 Pro에서 겪음). 재시도로는 해결되지 않는 결제 문제입니다.
- **503 "high demand"**는 일시적인 과부하입니다. `llm/client.py`가 5xx 오류를 1초, 3초 간격으로 2번 재시도합니다.
- **preview 모델**(`-preview`가 붙은 ID)은 예고 없이 바뀌거나 내려갈 수 있습니다. 정식 ID가 있으면 그쪽을 쓰세요.

## 교체 후 영향

- **예전 답변에는 예전 `display_name`이 남습니다.** `messages.model_used`에 저장된 값이라 화면에는 그대로 보이지만, 모델 배지의 크기 표시가 빠지고 사용량 통계에는 옛 이름과 새 이름이 따로 나옵니다.
- **가격이 바뀌면 실제 비용도 달라집니다.** 사용량 한도는 돈이 아니라 토큰 수로 세기 때문에, 같은 자리에 더 비싼 모델을 넣으면 한도는 그대로인데 비용만 올라갑니다.

## 참고 자료

- [Anthropic: Models overview](https://platform.claude.com/docs/en/models/overview)
- [OpenAI: Models](https://developers.openai.com/api/docs/models)
- [Google: Gemini API models](https://ai.google.dev/gemini-api/docs/models?hl=ko)
