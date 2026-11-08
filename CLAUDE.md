# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Joomini: a chat app (FastAPI + Next.js 14 App Router + Postgres) that routes each question to one of 9 LLMs (OpenAI / Google / Anthropic × small / medium / large). In `auto` mode, an mmBERT classifier picks the **company** from the question's category, and E5-embedding similarity picks the **size** from a complexity score. Manual mode lets the user pick company and size directly. See README.md for the feature list, the memory mechanisms, and per-format attachment handling.

## Commands

Everything runs in Docker. There is no local venv or `node_modules`, so IDE "cannot find module" diagnostics are noise. Verify changes by building the containers.

```bash
# Local dev (docker-compose.yml is the server/prod variant: uses SERVER_IP and UPLOAD_HOST_DIR from .env)
docker compose -f docker-compose.dev.yml up -d --build            # all services
docker compose -f docker-compose.dev.yml up -d --build backend    # backend only
docker compose -f docker-compose.dev.yml logs -f backend

# Quick backend syntax check before rebuilding
python3 -m py_compile backend/app/<package>/<file>.py

# Frontend typecheck = the `next build` step inside the frontend image build
docker compose -f docker-compose.dev.yml up -d --build frontend

# DB access (container name `postgres`, db/user `asap`)
docker exec postgres psql -U asap -d asap -c "..."

curl -s http://localhost:8000/health | python3 -m json.tool   # also reports whether the classifier/complexity models have loaded
```

There is no test suite and no linter config. Verification is a container build, then hitting endpoints with `curl` and inspecting rows with `psql`.

Deploys run on every push to `main`: a GitHub Actions self-hosted runner executes `scripts/deploy.sh`, which builds, health-checks, and rolls back to the `:previous` image on failure. Details are in docs/en/DEPLOYMENT.md (Korean: docs/ko/DEPLOYMENT.md). How to swap one of the 9 models: docs/en/MODEL_UPDATE.md (Korean: docs/ko/MODEL_UPDATE.md).

## Schema changes: there are no migrations

`init_db()` (backend/app/core/db.py) runs `Base.metadata.create_all()` at startup. It creates missing tables but **never alters existing ones**. Any column change to an existing table needs a manual `ALTER TABLE` (or a DROP and recreate), run against the local DB **and** the deployed server's DB. Forgetting the server side causes `UndefinedColumnError` after deploy. When you change `db_models.py`, give the user the exact SQL to run on the server. Write it to be idempotent (`ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`).

The `attachments` columns `source`, `source_attachment_id`, and `generation_meta` (and the `ix_attachments_message_source` index) were added for generated files. A server that predates them needs:

```sql
ALTER TABLE attachments ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'upload';
ALTER TABLE attachments ADD COLUMN IF NOT EXISTS source_attachment_id UUID NULL REFERENCES attachments(id) ON DELETE SET NULL;
ALTER TABLE attachments ADD COLUMN IF NOT EXISTS generation_meta TEXT NULL;
CREATE INDEX IF NOT EXISTS ix_attachments_message_source ON attachments (message_id, source);
```

**`users` table** (added before login exists). A server that predates it needs this, **run before deploying the new code** (the code writes `projects.user_id`, which doesn't exist on the old schema). It aborts without changing anything if any `user_id` other than the fixed demo user exists:

```sql
BEGIN;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM conversations  WHERE user_id <> '00000000-0000-0000-0000-000000000001')
  OR EXISTS (SELECT 1 FROM user_memory    WHERE user_id <> '00000000-0000-0000-0000-000000000001')
  OR EXISTS (SELECT 1 FROM user_settings  WHERE user_id <> '00000000-0000-0000-0000-000000000001') THEN
    RAISE EXCEPTION '고정 사용자 외의 user_id가 있습니다 - 확인 후 진행하세요';
  END IF;
END $$;
CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY, email VARCHAR(255) NOT NULL, name VARCHAR(100) NOT NULL,
  role VARCHAR(20) NOT NULL DEFAULT 'user', is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email_lower ON users (lower(email));
INSERT INTO users (id, email, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'joonyong.park.dev@gmail.com', '박준용') ON CONFLICT DO NOTHING;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS user_id UUID;
UPDATE projects SET user_id = '00000000-0000-0000-0000-000000000001' WHERE user_id IS NULL;
ALTER TABLE projects ALTER COLUMN user_id SET NOT NULL;
CREATE INDEX IF NOT EXISTS ix_projects_user_id ON projects (user_id);
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['projects', 'conversations', 'user_memory', 'user_settings'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = t || '_user_id_fkey') THEN
      EXECUTE format('ALTER TABLE %I ADD CONSTRAINT %I FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE',
                     t, t || '_user_id_fkey');
    END IF;
  END LOOP;
END $$;
COMMIT;
```

`users.avatar_updated_at` (profile photo; NULL = no photo) was added after the users table. A server that has the users table but not this column needs:

```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_updated_at TIMESTAMPTZ NULL;
```

The photo itself is one re-encoded 256x256 PNG at `uploads/users/{id}/avatar.png` (`PUT/GET/DELETE /users/me/avatar`).

## Feature boundaries: don't let one feature's fix break another

There is no test suite, so a cross-feature regression only surfaces when a real user hits it after deploy. The app looks like separate features (routing, memory, attachments, projects, streaming), but several of them run through the **same shared code**. Before finishing any change, explicitly check whether it touches something another feature also depends on.

Known shared/coupled points:
- **`chat/router.py` `send_message` is the single hot path for every feature.** Routing, memory injection, attachment loading, and SSE streaming all converge here. A change scoped to one concern (e.g. "fix memory injection") still runs through shared helpers (e.g. `_build_history`) on *every* message, so it can silently affect routing or streaming too.
- **Normal chat and project chat share the same pipeline.** `/c/[id]` and `/projects/[id]` both go through `send_message`, `_build_history`, and the same SSE contract. A fix verified only in a normal chat must also be re-checked in a project chat, and vice versa.
- **Memory schema is read in two separate places**: `memory/extract.py` (extraction/recompute) and `chat/router.py` + `chat/context.py` (system-prompt injection). Changing `UserMemory`/`ProjectMemory` shape and updating only one of them leaves the other silently reading stale or malformed data.
- **The SSE contract (`StreamEvent`) is hand-duplicated** between the backend (`chat/router.py`) and the frontend (`frontend/lib/types.ts`). Adding, renaming, or removing an event `type` or field on one side without the other breaks the frontend with no compiler error, because the two live in different languages/processes.
- **`frontend/lib/types.ts` mirrors `backend/app/schemas.py` by hand** for every endpoint, not just SSE — same silent-break risk for any Pydantic schema change.
- **No `relationship()`, no migrations.** Every join is a manual `select()`, so a schema change can require updating every hand-written join site, not just the model. And every column change needs a manual `ALTER TABLE` on both local and server DB (see "Schema changes" above) — a fix that works locally against a fresh `create_all()` can still crash production.
- **`SettingsWindow.tsx` is one tabbed modal** (personal instruction / import / usage / memory / appearance) sharing one piece of modal state. A change to one tab's logic must not alter the open/close or tab-switch behavior of the others.

Before marking any task done:
1. **Find every other caller/reader** of whatever you changed (`_build_history`, `select_model`, the memory read/write functions, `StreamEvent`, a Pydantic schema, a DB column, etc.) — grep for it, don't assume it's only used by the feature you're focused on.
2. **Rebuild and smoke-test beyond the one feature you touched.** At minimum: send one normal chat message and one project chat message end-to-end, and confirm existing memory/attachment behavior still works if your change is anywhere near the chat send path.
3. **If a schema or the SSE contract changed, confirm both sides were updated together** — backend schema ↔ `frontend/lib/types.ts`, or `core/models.py` ↔ the server's `ALTER TABLE` — never just one.
4. If you're not sure whether something is shared, say so explicitly instead of assuming isolation — flat module layout with no tests means "it's probably fine" is exactly how regressions ship here.

## Backend architecture (backend/app/)

The backend is organized by domain. Each HTTP feature is a package with a `router.py` that owns its `APIRouter`, and `main.py` mounts them all. Engine packages have no HTTP.

```
app/
├── main.py         app, router mounts, lifespan, legacy /chat
├── schemas.py      all Pydantic schemas (one file until login work)
├── core/           db.py (engine, session, init_db) · models.py (all tables) · tokens.py
├── routing/        classifier.py · complexity.py · complexity_examples.py · catalog.py + models.json (9 models)
│                   selector.py (select_model, bump_size) · intent.py (needs_*_tools keyword detectors)
├── llm/            client.py (call_model, stream_model_with_tools) · messages.py (vendor converters)
│                   tool_format.py · images.py · http.py (API keys, logged raise)
├── websearch/      providers.py (Tavily → Perplexity → Brave, search_web)
├── documents/      generator.py (create) · editor.py (inspect/edit) · extractor.py (text/image extraction)
├── tools/          base.py (ToolSpec/Context/Result, schema helpers) · registry.py (get_tool_specs, run_tool)
│                   web_search.py · documents.py · image.py · edit.py
├── chat/           router.py (CRUD, send_message + run_turn, resume_stream) · history.py (_build_history)
│                   context.py (memory text, DOC_TOOLS_GUIDE) · stream.py (TurnStream) · title.py · search.py (chat search)
├── attachments/    router.py (endpoints) · service.py (storage, preview, generated files, orphan sweep; also used by projects/tools/chat)
├── memory/         extract.py (extraction, scheduler, lock) · router.py (/memory/extract-all)
└── projects/ · users/ · settings/ · usage/    router.py each
```

Dependency direction: `chat` → `tools` → `documents` / `websearch` / `llm`; `chat` → `routing`, `llm`, `memory`; everything → `core`. `llm` must not import `tools` or `chat` (specs are passed in). `tools/*` import `attachments.service` and `documents` lazily inside executors to avoid import cycles; keep it that way.

The `lifespan` in `main.py` starts three background tasks:
- HF model loading. Manual mode works before the models finish loading; `auto` mode returns 503 until they are ready.
- `attachments.service.orphan_cleanup_loop`.
- `memory.extract.scheduled_extraction_loop`, which runs at 07:00 and 12:30 KST.

**Routing engine** (`routing/`, independent of HTTP):
- `classifier.py`: category classification.
- `complexity.py` and `complexity_examples.py`: complexity score. multilingual-e5-large embeds the question; the score is `sigmoid((mean of top-5 hard similarities − mean of top-5 easy similarities) × 25)`. Size thresholds are `COMPLEXITY_SMALL_MAX` 0.4 / `COMPLEXITY_MEDIUM_MAX` 0.80 in `catalog.py`; large is deliberately conservative (only clearly hard questions).
  - The examples target **reasoning difficulty, not topic**: EASY includes technical-term definitions ("쿠버네티스가 뭐야?"), and HARD includes everyday multi-constraint/multi-step questions, so jargon alone doesn't push a question up.
- `selector.py`: `select_model()`, which combines the two, and `bump_size()`.
- `intent.py`: keyword intent detectors that decide which tool groups are sent.
- `catalog.py`: loads the 9-model catalog from `models.json` (company × size → `model_id`, `display_name`) and fails at startup if any combination is missing or duplicated. To change a model, edit `models.json` and the matching `label` in `frontend/lib/models.ts` (the label must equal `display_name`; the frontend uses it to find the size badge).

**Chat request path:** `chat/router.py` `send_message`
1. Loads all context up front in its own session: personal instruction, imported memory, global memory, project instruction, project memory.
2. **Saves the user message immediately** and links this turn's attachments, before any slow work. `token_count` is recomputed later from the real model content.
3. **Generation is decoupled from the HTTP connection.**
   - A background task (`run_turn`, started with `asyncio.create_task`) does all the work and pushes events into a `TurnStream`, registered in `_active_turns[conversation_id]`.
   - The POST response only subscribes to it. If the browser disconnects (chat switch, new chat, refresh), only the subscriber stops; the task still finishes and saves the answer, and DB work is never cancelled mid-transaction. Cancelling it mid-transaction used to leak `idle in transaction` connections holding locks.
   - `GET /conversations/{id}/stream` replays all events so far, then streams live (204 if nothing is running). The chat page calls it via `resumeStream` when the last loaded message is an unanswered user message.
   - `done` is emitted only after the answer is saved.
   - The registry lives in process memory, so this assumes a single uvicorn worker.
   - The task body:
     - selects a model;
     - builds a token-budgeted sliding-window history (`_build_history`, `MAX_CONTEXT_TOKENS`);
     - runs the **tool loop** (up to `MAX_TOOL_ROUNDS` = 5, or `EDIT_MAX_TOOL_ROUNDS` = 8 on edit-intent turns, since inspect → edit → answer plus a retry or a re-edit doesn't fit in 5):
       - each round is one `llm.client.stream_model_with_tools()` call;
       - text streams out immediately as `chunk`;
       - if the model returned tool calls, every call is executed through `tools.registry.run_tool()`, an assistant message (with `tool_calls`) and one `tool` message per call are appended to a working copy of the history, and the loop calls the model again;
       - the loop ends on a round with no tool calls;
       - the last round forces a final answer (`force_final`): all three vendors keep `tools` and send `tool_choice` none;
       - if the loop ends with empty text, a fixed message is streamed and saved instead ("파일을 생성했습니다." when files were made).
4. Text from different rounds is joined with `"\n\n"`. Per-round token usage is summed. Tool failures (executor exceptions, unknown tool name, unparseable arguments) are sent back to the model as `is_error` results and the loop continues. A model-call exception sends an `error` event and saves no answer; the question is already saved.
5. **Tool calls and results are never stored in the DB.** Only the final assistant text is saved, so the next turn's history has no tool messages. `Message.web_search_used` / `web_search_query` are set from the web_search tool's `ToolResult.metadata`.

System-prompt context is injected in a fixed precedence order: personal instruction → imported memory → global memory → project instruction → project memory. Tool definitions are not in the system prompt; they go in the provider's top-level `tools` parameter.

**SSE contract:** the backend sends `data: {json}` lines with `type` ∈ `meta | searching | processing | file | chunk | done | error`. Mirror any change in `StreamEvent` in frontend/lib/types.ts. Since the loop, `searching` can arrive after some `chunk`s (when the model writes text before calling a tool).
- `file` carries `{"attachment": AttachmentOut}` for a generated file. Adding or changing an event means updating three places together:
  - `StreamEvent` in `frontend/lib/types.ts`;
  - the `if/else` parser and the `handlers` type in `lib/api.ts` `streamMessage`, which silently ignores unknown types;
  - the handler in `app/c/[id]/page.tsx`.
- `onFile` also clears `processingMessage`.

**tools/** is the tool layer. A tool is a `ToolSpec` (`tools/base.py`):
- `name`, `description`, `parameters` (JSON Schema), `strict` (on for every tool except `web_search`);
- `executor(arguments, ctx) -> ToolResult`.

`ToolContext` carries the conversation/project/user ids, a session factory, and `emit()`, which sends an SSE event while the tool is still running (web_search uses it for `searching`). `ToolResult` carries:
- `content`, the string returned to the model;
- `is_error`;
- `events`, SSE events sent after the tool finishes;
- `metadata`, values for the loop that the model never sees.

Registered tools: `web_search` (always sent; Tavily → Perplexity → Brave fallback, see below), three document tools, `generate_image`, and `inspect_document` / `edit_document`. Search failure and zero results are both reported to the model. `get_tool_specs(include_doc_tools, include_image_tools, include_edit_tools)` decides what is sent each turn; each group has its own intent detector in `routing/intent.py`. `ToolContext.turn_state` is shared by all calls in one turn (generated-file count, image count). `run_tool` logs every `is_error` result (`툴 에러 결과`, first 200 chars), and the loop logs tool names per round (`툴 호출`), so model paraphrases of errors can be traced.

To add a tool:
1. Write an async executor that returns a `ToolResult`. Use `ctx.emit` for progress; don't raise for expected failures, return `is_error=True` instead.
2. Create a `ToolSpec` for it and add it to `_REGISTRY` in `tools/registry.py`.
3. If it needs a new SSE event type, update `frontend/lib/types.ts` and the `lib/api.ts` parser in the same change.
4. Smoke-test it on all three providers, in a normal chat and in a project chat.

**Web search fallback** (`websearch/providers.py` `search_web`, used by the `web_search` tool):
- **Order:** Tavily → Perplexity → Brave. A provider is tried next only when the previous one raises (HTTP error, timeout, network error). A provider whose API key is missing is skipped.
- **Zero results is not an error.** It does not trigger a fallback and is reported to the model as 0 results.
- **Timeout:** each provider gets its own short timeout, `SEARCH_TIMEOUT` (15s, connect 5s), so a hung provider costs at most that long.
- **Result shape:** every provider is normalized to `{title, url, content}`, so the tool's output format and source citations are unchanged. Brave's HTML tags are stripped and `extra_snippets` are appended to `content`.
- **Filters:** `time_range` maps to Tavily `time_range`, Perplexity `search_recency_filter`, and Brave `freshness` (`pd/pw/pm/py`). `site_hint` maps to Tavily `include_domains`, Perplexity `search_domain_filter`, and a `site:` operator in the Brave query.
- **Failure:** if every provider fails, `SearchUnavailableError` lists each provider's reason (HTTP status or "키 없음", never keys or response bodies). The tool then returns it to the model as an `is_error` result.
- **Logs:** a fallback logs `웹 검색 실패, 다음 제공자로 폴백` and `웹 검색 폴백 성공 (provider=...)`; normal searches log `웹 검색 (provider=...)`.
- **Keys:** `TAVILY_API_KEY`, `PERPLEXITY_API_KEY`, `BRAVE_API_KEY` in `.env` (see `.env.example`). The server's `.env` needs them too, and the backend container must be recreated to pick them up.

**Document tools** (`create_document` / `create_spreadsheet` / `create_presentation` in `tools/documents.py`, renderers in `documents/generator.py`):
- **Formats:**
  - `create_document`: docx, pdf, md, txt;
  - `create_spreadsheet`: xlsx, csv;
  - `create_presentation`: 16:9 pptx.
- **Schemas** are strict-mode compatible:
  - every field is required and every object has `additionalProperties: false`;
  - no optional fields, unions, or recursion; unused fields are sent empty (`""`, `[]`, `0`);
  - no cell addresses or coordinates (totals come from `total_row: "sum"`; the server writes the formula);
  - `strict` is sent to OpenAI and Anthropic only, not Gemini.
- **Executor flow** (`_render_and_save`):
  1. Enforce the per-turn limit (5 files).
  2. Emit `processing`.
  3. Run `generator.render_*` in `asyncio.to_thread`.
  4. Save with `attachments.service.save_generated_file`.
  5. Emit `file`.
  6. Return a JSON `ToolResult` whose `note` tells the model not to repeat the content.
- **Errors:** `generator.DocValidationError` messages name the exact field (e.g. `slides[3]...`) so the model can fix its arguments and retry. Recoverable issues (row-length mismatch, slide budget overruns) render anyway and are listed in `warnings`.
- **Spreadsheet values:** every cell arrives as a string and is converted by column type. Text cells are forced to string type so `=`, `+`, `-`, `@` never become formulas; in csv those cells are prefixed with `'`.
- **Fonts:**
  - docx/xlsx/pptx use "맑은 고딕". docx also sets `eastAsia` and strips theme fonts from the styles. pptx also sets `a:ea`.
  - pdf is a docx built with "Noto Sans CJK KR", converted through the shared LibreOffice path (one conversion at a time, shared with previews and extraction), because the server has no 맑은 고딕.
  - pptx colors, sizes, and margins live in `PPT_THEME`.
- **To add a format:**
  1. Add a branch in the matching `render_*` and its `ext` to `MIME_BY_EXT` in `attachments/service.py`.
  2. Extend the tool's `format` enum and description.
  3. Check that the frontend card icon (`AttachmentTypeIcon`) handles the extension.

**Image tool** (`generate_image` in `tools/image.py`, API call `llm/images.py` `generate_image`):
- **API:** OpenAI Images API (`/v1/images/generations`, `b64_json`). The model is the `IMAGE_MODEL` constant in `tools/image.py` (`gpt-image-2.5-flare`).
- **Arguments:**
  - `aspect_ratio` square / landscape / portrait → 1024x1024 / 1536x1024 / 1024x1536;
  - `quality` standard / high → medium / high;
  - `format` png / jpg (jpg is re-encoded by Pillow, RGB, quality 90).
- **Timeout:** its own 180s client (`IMAGE_TIMEOUT`), separate from streaming.
- **Limits:**
  - 2 images per turn;
  - no per-user daily limit (removed by decision). Note that image API cost is also not counted in the token quota (see below), so nothing else caps it.
- **Errors:** 429, content-policy rejections, and other failures each return a different `is_error` message.
- **What is recorded:**
  - `generation_meta` stores kind, model, size, quality, and format, never the prompt;
  - usage is not added to the token quota ring; only model/size/quality/elapsed time are logged.
- **Intent:** `intent.needs_image_tools` only fires on an image noun plus a create verb, or explicit phrases ("그려줘"). Phrases like "도커 이미지" or "이미지 분석" are excluded. No model-size bump.
- **Display:** the frontend card shows an inline thumbnail for `image/*` attachments.

**Edit tools** (`inspect_document` / `edit_document` in `tools/edit.py`, implementation in `documents/editor.py`) edit an existing xlsx/docx/pptx/hwpx in place.
- **Inspect:** returns `doc_revision` (sha256 of the file, first 12 chars), then one line per element: opaque id (`e12`), location (sheet/cell with header and row-label context, paragraph number, table row/column, slide/shape, notes), current value (≤200 chars), format summary, and editable or the reason it isn't.
  - Ids are deterministic for a given revision: the whole file is traversed and `scope` (`all`, `sheet:이름`, `slide:3`, `section:제목`) only filters the output.
  - Output is capped at 8000 chars and 400 elements per group.
- **Edit:** takes flat `{op, target_id, expected_old_value, new_value}` operations.
  - `op` is `set_cell_value` for xlsx and `replace_text` for docx/pptx/hwpx. Adding or deleting rows, paragraphs, or slides is not supported.
  - At most 50 operations per call.
  - Everything is validated first: revision, id, editability, `expected_old_value` after whitespace normalization (numeric equality for number cells), and value type. Any failure applies nothing and lists every failing operation.
- **Output:** always a new generated attachment named `원본_수정.ext` (then `_수정2`…).
  - `source_attachment_id` points to the original upload or generated file. Project files can't be FK targets, so their id goes in `generation_meta.source_id`.
  - `generation_meta.kind` is `"edit"`, with `base_revision`, ops, and `changed_parts`.
  - The original file is only ever read.
  - The new file has a new id and revision, so a follow-up edit must inspect the new id.
- **Formatting is preserved by XML patching, not library re-save:**
  - openpyxl re-save also rewrote `drawing1.xml`, so only edited parts are re-serialized with zip + lxml; every other part is copied byte-for-byte.
  - Changed parts are xlsx `sheetN.xml` (+ `workbook.xml` `calcPr fullCalcOnLoad` when formulas exist, so totals recalc on open), docx `word/document.xml`, pptx `slideN.xml` / `notesSlideN.xml`, and hwpx `Contents/sectionN.xml`.
  - xlsx text cells become `inlineStr` (sharedStrings untouched, never a formula) and number/date cells keep their `s` style.
  - `replace_text` puts the new text in the first run, keeping its formatting, and empties the other runs.
- **Not editable:** formula cells, hyperlinks, fields, tracked changes, text boxes, headers/footers, content controls, groups, SmartArt, and charts are listed as 수정 불가 and rejected.
- **File access:**
  - Files resolve only within this chat, or this chat's project files.
  - A scan-converted upload whose `stored_path` became a PDF falls back to the original `{id}.{ext}` beside it.
  - `.hwp` (old 한글 format) is rejected with a "convert to hwpx" message.
- **Intent:** `intent.needs_edit_tools` fires on an edit verb plus a file reference (or an editable attachment), and only when an editable file exists in this turn's attachments, the chat, or the project (`_has_files_in_context`). It bumps auto mode to medium.
- **History markers:** `_build_history` marks uploads as `[첨부 파일: name (id=...)]`, project files as `[참고자료: name (id=...)]`, and generated files as `[생성된 파일: ...]` so the model can pass ids. `DOC_TOOLS_GUIDE`, sent on doc or edit turns, tells it never to echo these.

**hwpx (한글)** is always enabled; there is no flag.
- `generator._build_hwpx` uses python-hwpx (pinned in `backend/requirements-hwpx.txt`, installed in its own Dockerfile layer). Lists become prefixed paragraphs, reported in `warnings`.
- hwpx edits patch `Contents/section*.xml`.
- **Not verified:** whether files open in Hancom 한글 was not checked during development; it is being validated by hand. If it fails, the plan is to remove hwpx support entirely. That means removing:
  - `_build_hwpx`;
  - `_inspect_hwpx`;
  - `hwpx` from the enums, descriptions, `supported_formats()`, and `_HWPX_KEYWORDS`;
  - `requirements-hwpx.txt` and its Dockerfile layer.

**Adding a format or tool — places to touch:**
- **New output format:**
  1. A `documents/generator.py` renderer branch.
  2. The `MIME_BY_EXT` entry in `attachments/service.py`.
  3. The tool schema enum and description.
  4. The frontend icon in `AttachmentTypeIcon`.
- **New editable format:**
  1. An `editor._inspect_<fmt>` function plus its entry in `_INSPECTORS`.
  2. The format in `supported_formats()`.
  3. The scope rule in `_scope_filter`.
  4. The ext list used by `_has_files_in_context`, which derives from `supported_formats()`.
- **New tool:**
  1. Write the executor and its `ToolSpec`.
  2. Register it in `_REGISTRY`.
  3. Add a `get_tool_specs` flag for it.
  4. Write an intent detector in `routing/intent.py`.
  5. Wire the flag in `send_message`.
  6. Write a guide line if needed.

**Known limits:**
- Intent detection is keyword-based and misses phrasing like "이거 정리해서 줘".
- LibreOffice runs one conversion at a time; PDF generation, previews, and the scan fallback share it.
- Edit preserves formatting only at the part level. Inside an edited paragraph, emphasis on later runs is lost.
- No add/delete operations.

**File intent and routing:** `intent.needs_file_tools(content, attachments)` is a lenient keyword detector.
- What triggers it:
  - format words;
  - a create verb plus a document noun;
  - an office/PDF attachment plus a convert/summarize verb.
- What happens when it fires:
  - the three document tools are exposed alongside `web_search`;
  - `DOC_TOOLS_GUIDE` is appended to the end of the system prompt;
  - every round uses `max_output_tokens=DOC_MAX_OUTPUT_TOKENS` (32000; must stay above Anthropic's default `MAX_OUTPUT_TOKENS` 16384 to mean anything);
  - auto mode is raised to at least medium via `bump_size`, while manual mode keeps the chosen size.
- Only the matched keyword is logged (`파일 의도 감지`), never the message.
- When it doesn't fire, the request is exactly as before (web_search only).

**llm/** abstracts the three vendors (`client.py`): `call_model` (non-streaming, no tools) and `stream_model_with_tools` (one streaming round).
- **OpenAI uses the Responses API** (`/v1/responses`, `_stream_openai_responses`); Gemini stays on its OpenAI-compatible Chat Completions endpoint. GPT-6 Astra / 6.1 Sol only allow function tools on Chat Completions with `reasoning_effort: "none"`, which they reject, so Chat Completions can't run our tools on them.
  - Requests use `store: false` and `include: ["reasoning.encrypted_content"]`. Reasoning items from a tool-calling round are kept in the first tool call's `provider_extra["reasoning_items"]` and echoed back before the `function_call` items next round (same idea as Gemini's thought_signature).
  - Tools are flat (`{type, name, description, parameters, strict}`); `strict` is always sent explicitly (`false` for `web_search`).
  - Text comes from `response.output_text.delta`; finished `function_call` items from `response.output_item.done`; usage (`input_tokens` / `output_tokens`, reasoning included) from `response.completed` / `response.incomplete`. `incomplete_details.reason == "max_output_tokens"` marks the last call truncated.
- **Anthropic workspace:** if the key isn't scoped to a workspace, set `ANTHROPIC_WORKSPACE_ID`; `_anthropic_headers` then sends `anthropic-workspace-id`.
- **Tools:** `build_tools_for_company` converts `ToolSpec`s into each vendor's format.
- **Neutral message format:** the loop builds messages that don't depend on the vendor:
  - assistant: `{"role":"assistant","content","tool_calls":[{id,name,arguments,provider_extra}]}`
  - tool result: `{"role":"tool","tool_call_id","name","content","is_error"}`
- **Converters:** `_prepare_openai_responses_input` (OpenAI), `_prepare_openai_messages` (Gemini), and `_prepare_anthropic_messages`.
  - Anthropic: all tool results go into one user message as `tool_result` blocks. The prompt-cache breakpoint is placed only within the pre-loop history (`history_len`).
  - Gemini: its `extra_content.google.thought_signature` is kept in `provider_extra` and echoed back. Omitting it is a 400.
- **Tool-call events:** `stream_model_with_tools` yields every tool call at the end of the response, as `{"type":"tool_call",id,name,arguments,raw_arguments,parse_error,truncated,provider_extra}`. Truncated output (`max_tokens` / `length`) marks the last call `parse_error` and `truncated`.
- **Gemini streaming quirk:** tool calls finish with `finish_reason: "stop"` and have no `index`.
- **Parallel tool calls are disabled:**
  - OpenAI: `parallel_tool_calls: false`.
  - Anthropic: `disable_parallel_tool_use`.
  - Gemini: has no working switch for this.
  - The loop still executes and answers every call it receives.
- **Output limits:** `max_output_tokens` is per call. When it's `None`, Anthropic uses `MAX_OUTPUT_TOKENS` (16384; Fable 5.1 / Opus 5.5 always think, and thinking counts against it) and OpenAI/Gemini send no limit. When set, it goes out as OpenAI `max_output_tokens` (includes reasoning tokens), Gemini `max_tokens`, or Anthropic `max_tokens`. No `reasoning_effort` is sent, so each OpenAI model uses its default effort.
- **Retries:** `call_model` and `stream_model_with_tools` retry transient failures (HTTP 500/502/503/504/529, connect errors) up to 2 times with 1s/3s waits (`RETRY_STATUS`, `RETRY_DELAYS`). A stream is retried only if it failed before yielding anything, so no text is duplicated. 429 is not retried, because quota-exceeded and rate-limit share the code. Retries log `모델 호출 재시도`.
- **Truncated tool calls:** a call cut off by the output limit is marked `truncated`, and the tool returns an `is_error` result telling the model to shrink the content.

**Memory** (`memory/extract.py`):
- `UserMemory` is global. Each row is a topic: `category` (one of `나` / `주제` / `영역` / `사람`), `name`, `summary`, and `details` stored as a JSON-array string. The `나` category holds exactly one topic.
- `ProjectMemory` has the same shape without `category`.
- Extraction is **diff-based**. It sends the existing topics (each tagged with a short key `t1`, `t2`…) plus the new messages to the OpenAI small model (`MEMORY_MODEL = get_model("openai", "small")`). The model returns only `{add, update, delete}`, and the code applies just those (`_apply_user_changes` / `_apply_project_changes`).
  - A topic the model doesn't mention is left untouched, so a model that "forgets" a topic can no longer erase it, and row IDs stay stable.
  - `update` carries the full merged `details` list.
  - `delete` is for existing topics that fail the storage criteria, never for "not mentioned in this conversation".
  - Assistant replies are clipped to `ASSISTANT_CONTEXT_CHARS` (300) in the prompt; facts must come from user messages.
  - The `나` single-topic rule is enforced in code: an `add` of `나` when one exists is merged into it, and an `update` can't turn a second topic into `나`.
  - Each run logs `메모리 갱신` with add/update/delete counts and deleted topic names + reasons.
- **Storage criteria** live in the prompt: store only facts that would help in an unrelated future conversation (profile, preferences, people, ongoing work). One-off task requests, assistant-generated content, file names, sample data, and single knowledge questions are excluded, with real past mistakes listed as negative examples. Explicit user preferences (answer format, length, tone) must be stored.
- A per-conversation cursor, `Conversation.last_extracted_message_id`, means only new messages are processed. Reset it to NULL to force re-extraction.
- Every trigger goes through `_extract_all_sequentially`, which holds an `asyncio.Lock`, because concurrent delete-and-reinsert runs would clobber each other.
- Memory topics are read in **two** places: extraction (`memory/extract.py`) and system-prompt injection (`chat/router.py` via `chat/context.py`). Update both when the schema changes.

**Attachments:** `attachments/router.py` has the endpoints; `attachments/service.py` handles storage, lifecycle, previews, and orphan sweep. `documents/extractor.py` does per-format text and image extraction, with a LibreOffice → PDF fallback. Extraction runs in the background, and `send_message` waits briefly for pending attachments. Files live under `uploads/chats/{chat_id}/` and `uploads/projects/{project_id}/`.

**Generated files** reuse the `attachments` table with `source='generated'`. User uploads are `source='upload'`.
- **Storage:** files go under `uploads/chats/{chat_id}/generated/`, so deleting the chat removes them. Limits are 25MB per file and 5 files per turn.
- **Linking:** each tool reports its `generated_attachment_id` in `ToolResult.metadata`. When the assistant message is saved, `send_message` pre-generates the message id, flushes, and sets `message_id` on those attachments.
- **Model-call failure:** if the call fails after files were already made, the partial text (or "파일을 생성했습니다.") is saved and linked instead of sending `error`.
- **Orphan sweep:** rows with `message_id IS NULL` are deleted after 24h for uploads and 7 days for generated files (a disconnect mid-stream leaves them unlinked).
- **History:** `_build_history` never puts generated files into model content blocks. Assistant messages can't carry document or image blocks, so that would be a 400. Instead, it appends `[생성된 파일: name (id=...)]` to the model-facing text only; `Message.content` in the DB is unchanged, so the UI and memory extraction see the original text.
- **Frontend:** `send_message` only accepts `source='upload'` ids in `attachment_ids`. `ChatBubble` renders assistant attachments as file cards (open in the existing viewer, download via `?download=1`).

## Backend conventions

- **No SQLAlchemy `relationship()`** anywhere. Use explicit `select()`/joins and assemble results by hand, which avoids async lazy-load `MissingGreenlet` errors.
- **There is no login yet, but there is a `users` table.**
  - `init_db()` seeds one fixed user (`DEMO_USER_ID`, 박준용 / joonyong.park.dev@gmail.com). Existing data already used that id, so nothing was rewritten.
  - The frontend reads the current user from `GET /users/me` (sidebar user button, 계정 설정 tab). The sidebar 로그아웃 button is a deliberate no-op until login exists.
  - The seed's `ON CONFLICT DO NOTHING` does not update an existing row. If the server DB still has an old name or email, run: `UPDATE users SET email = 'joonyong.park.dev@gmail.com', name = '박준용' WHERE id = '00000000-0000-0000-0000-000000000001';`
  - Code still uses the `DEMO_USER_ID` constant as "the current user". When login is added, replace those usages with the logged-in user.
  - `conversations`, `projects`, `user_memory`, and `user_settings` have a `user_id` FK to `users` with `ON DELETE CASCADE`. `projects.user_id` is the newest, and project child tables (`project_files`, `project_memory`) get their owner through the project.
  - Deleting a user cascades in the DB but leaves uploaded files on disk, so deactivate with `is_active=false` instead.
  - Every project endpoint goes through `projects.router._get_owned_project`, which returns 404 for another user's project. Assigning a chat to a project also requires the same owner.
  - **Still unscoped by owner** (fine with one user, must be fixed with login): by-id endpoints for conversations, attachments, and memory topics (`settings/router.py`), the memory scheduler, and usage stats.
- Enum-like fields are plain `String` columns validated with Pydantic `Literal[...]` at the API boundary, never native Postgres enums.
- Structured data is stored as JSON text in `Text` columns (e.g. `Attachment.extracted_text`, memory `details`).
- Use `asyncio.create_task` for background work. There is no Celery or Redis.
- CPU-bound model inference goes through `asyncio.to_thread`.
- Code comments and docstrings are written in Korean.

## Frontend architecture (frontend/)

- **Routes:** `/` is a new chat, `/c/[id]` is a chat room, and `/projects/[id]` is a project page.
- `components/AppShell.tsx` is the layout. It owns the sidebar and the global `conversations` and `projects` state, and exposes them to route pages through `contexts/AppDataContext.tsx`.
- **First message from `/`:** the page creates the conversation, then hands the unsent message to `/c/{id}` through `lib/pendingMessage.ts` (sessionStorage), because the two routes don't share React state.
- **Streaming UI** (`app/c/[id]/page.tsx`):
  - An `AbortController` lets the user switch chats mid-stream.
  - `isAutoScrollingRef` distinguishes programmatic scroll from user scroll, so auto-scroll doesn't fight the user.
- All HTTP calls go through `lib/api.ts`.
- `lib/types.ts` is kept in sync **by hand** with `backend/app/schemas.py`.
- Settings is one tabbed window (`components/SettingsWindow.tsx`): personal instruction, import, usage, memory, appearance.
- Dark mode is class-based (`.dark`). The `.scroll-thin` utility in `globals.css` provides themed scrollbars.

## Git

Commit messages are a single line, `type: description` (e.g. `feat: ...`, `fix: ...`), with no body. Commit only when asked.