# Updating the chat models

> 한국어: [../ko/MODEL_UPDATE.md](../ko/MODEL_UPDATE.md)

ASAP routes every message to one of 9 chat models: 3 companies (OpenAI, Google, Anthropic) × 3 sizes (small, medium, large). When a vendor ships a newer model, this guide covers what to edit, what to test, and the vendor quirks that have broken past swaps.

## What to edit

A routine swap touches 3 files:

| File | What to change | Required |
|---|---|---|
| [`backend/app/routing/models.json`](../../backend/app/routing/models.json) | `model_id` (the exact API ID) and `display_name` for the company × size slot | Yes |
| [`frontend/lib/models.ts`](../../frontend/lib/models.ts) `MODEL_OPTIONS` | `label` for the same slot. It **must equal `display_name`** | Yes |
| [`README.md`](../../README.md) model table | The model IDs | Docs only |

### `models.json`

```json
{"company": "openai", "size": "large", "model_id": "gpt-6-astra", "display_name": "GPT-6 Astra"}
```

- `model_id` is sent to the vendor API as-is. Use a pinned ID, not a moving alias such as `gemini-pro-latest`, so the model doesn't change underneath you.
- `display_name` is shown in the UI and stored in `messages.model_used` for every answer.
- `routing/catalog.py` loads this file at startup and **refuses to start** if any of the 9 company × size slots is missing or duplicated. A bad edit fails the deploy health check instead of failing at request time.

### `frontend/lib/models.ts`

The frontend has no API for the model list. It keeps its own copy, so the labels must match by hand:

- The model picker shows `label`.
- `ModelBadge` finds a message's size by looking up `messages.model_used` among the labels. If a label doesn't equal the backend `display_name`, the badge loses its Small/Medium/Large tag.

### What follows the catalog automatically

These code paths read the catalog through `get_model()`, so they need no edits:

- **Auto routing:** `routing/selector.py` picks a company from the question category and a size from the complexity score.
- **Manual selection:** `chat/router.py` and the legacy `/chat` in `main.py`.
- **Background calls on OpenAI small:**
  - title generation: `chat/title.py` `TITLE_MODEL`
  - memory extraction: `memory/extract.py` `MEMORY_MODEL`

### Not part of the 9 models

- **Image generation model:** the `IMAGE_MODEL` constant in [`backend/app/tools/image.py`](../../backend/app/tools/image.py). It goes through the OpenAI Images API, which is separate from chat.
- **Which company a category goes to:** `CATEGORY_TO_COMPANY` in `routing/catalog.py`. Edit it only when you want to change routing policy, not for a model swap.

## Steps

### 1. Confirm the model ID exists for our keys

Vendor docs sometimes list a model before your key can use it, or under a different ID. Check the ID against the vendor's model-list API from inside the backend container, which already has the keys:

```bash
# OpenAI
docker exec backend python -c "import os,httpx; print(sorted(m['id'] for m in httpx.get('https://api.openai.com/v1/models', headers={'Authorization':'Bearer '+os.environ['OPENAI_API_KEY']}).json()['data'] if m['id'].startswith('gpt')))"

# Google
docker exec backend python -c "import os,httpx; print([m['name'].split('/')[-1] for m in httpx.get('https://generativelanguage.googleapis.com/v1beta/models', params={'key':os.environ['GEMINI_API_KEY'],'pageSize':200}).json()['models'] if m['name'].split('/')[-1].startswith('gemini')])"

# Anthropic (sends the workspace header if ANTHROPIC_WORKSPACE_ID is set)
docker exec backend python -c "import os,httpx; h={'x-api-key':os.environ['ANTHROPIC_API_KEY'],'anthropic-version':'2023-06-01'}; w=os.environ.get('ANTHROPIC_WORKSPACE_ID'); w and h.update({'anthropic-workspace-id':w}); print([m['id'] for m in httpx.get('https://api.anthropic.com/v1/models', headers=h, params={'limit':50}).json()['data']])"
```

### 2. Edit the files

Edit `models.json` and `frontend/lib/models.ts`, and update the README table.

### 3. Rebuild

```bash
docker compose -f docker-compose.dev.yml up -d --build backend frontend
docker compose -f docker-compose.dev.yml logs --tail 30 backend   # a models.json error shows up here
```

### 4. Test each changed model end to end

Don't just test a plain chat. Each vendor has broken differently on **tool calls** in the past. Pick the model manually in the UI, or send `"mode": "<company>", "size": "<size>"` to `POST /conversations/{id}/messages`, and check:

- [ ] A plain message streams `meta → chunk → done`.
- [ ] A web-search question ("웹에서 검색해서 오늘 환율 알려줘") streams `meta → searching → chunk → done`. This runs the full tool loop: tool call → tool result → second model call.
- [ ] A document request ("회의록을 docx 파일로 만들어줘") produces a `file` event. This uses strict tool schemas and the larger output limit.
- [ ] An image attachment and a PDF attachment are actually read.
- [ ] A long answer isn't cut off, especially on thinking models (see below).
- [ ] The answer row has non-zero `input_tokens` / `output_tokens`:
  ```bash
  docker exec postgres psql -U asap -d asap -c "select model_used, input_tokens, output_tokens from messages where role='assistant' order by created_at desc limit 5;"
  ```

If OpenAI small changed, also check one title generation and one memory extraction, since those run on it.

### 5. Deploy

Push to `main`. No DB migration is needed, because model IDs aren't stored in the schema. If the server uses different API keys from local, check them first: a key that can't use the new model makes every call to it fail after deploy.

## Vendor-specific things to check

These have each caused a failure before. When a model changes, check whether the new model behaves the same way.

### OpenAI

- **We call the Responses API** (`/v1/responses`), not Chat Completions.
  - GPT-6 Astra and GPT-6.1 Sol reject function tools on Chat Completions unless `reasoning_effort` is `"none"`, and they don't support `"none"`.
  - If a future model works only on one API, the switch point is `_stream_openai_responses` in `backend/app/llm/client.py`.
- **No `reasoning_effort` is sent**, so each model runs at its default effort. A new model's default may be slower or more expensive than the old one's.
- Requests use `store: false`. Reasoning items are returned with `encrypted_content` and echoed back on the next tool round. If a new model returns a different item type there, tool rounds after the first can break.

### Anthropic

- **Thinking counts against the output limit.**
  - Some models think on every request (for example Claude Fable 5.1 and Opus 5.5 always do), and their thinking tokens use up `max_tokens`.
  - Our default is `MAX_OUTPUT_TOKENS = 16384` in `llm/client.py`.
  - Document turns use `DOC_MAX_OUTPUT_TOKENS = 32000` in `chat/router.py`. It must stay above `MAX_OUTPUT_TOKENS`, or document turns get the *smaller* limit.
  - If long answers get cut off, raise these, but stay under the model's documented max output.
- **Retirement dates:** Anthropic publishes a "not sooner than" retirement date per model. Check it when choosing a model, and replace a slot before its date passes.
- **Workspace header:** a user-level key (`sk-ant-usr…`) isn't tied to a workspace, so it needs `ANTHROPIC_WORKSPACE_ID` in `.env`. A normal API key (`sk-ant-api03-…`) doesn't.

### Google (Gemini)

- **We use the OpenAI-compatible Chat Completions endpoint.**
  - Tool calls carry a `thought_signature` that must be echoed back, and omitting it returns 400. This is handled in `llm/messages.py` `_prepare_openai_messages`.
  - Re-test tool calls on a new Gemini generation in case this changes.
- **429 "You exceeded your current quota"** means the key's plan doesn't include that model (seen with Gemini 3.1 Pro). Retrying won't fix it; it's a billing issue.
- **503 "high demand"** is temporary. `llm/client.py` retries 5xx errors twice (1s, 3s) before giving up.
- **Preview models** (`-preview` IDs) can change or be withdrawn with little notice. Prefer a stable ID when one exists.

## After a swap

- **Old answers keep their old `display_name`** in `messages.model_used`. They still render, but their model badge loses its size tag, and usage stats list old and new names separately.
- **Pricing changes the effective budget.** The token quota counts tokens, not money, so a pricier model in the same slot raises cost without the quota noticing.

## Sources

- [Anthropic: Models overview](https://platform.claude.com/docs/en/models/overview)
- [OpenAI: Models](https://developers.openai.com/api/docs/models)
- [Google: Gemini API models](https://ai.google.dev/gemini-api/docs/models?hl=ko)
