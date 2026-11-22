# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Joomini: a chat app (Flask + Next.js 14 App Router + DynamoDB, moving to AWS Lambda via Zappa) that routes each question to one of 9 LLMs (OpenAI / Google / Anthropic × small / medium / large). In `auto` mode, an mmBERT classifier picks the **company** from the question's category, and E5-embedding similarity picks the **size** from a complexity score. Manual mode lets the user pick company and size directly. See README.md for the feature list, the memory mechanisms, and per-format attachment handling.

## Commands

Everything runs in Docker. There is no local venv or `node_modules`, so IDE "cannot find module" diagnostics are noise. Verify changes by building the containers.

```bash
# Local dev: Flask backend (:8000) + DynamoDB Local (:8001, admin GUI :8002) + frontend (:3000).
# docker-compose.yml is the on-prem server variant (gunicorn, uses SERVER_IP and UPLOAD_HOST_DIR from .env).
docker compose -f docker-compose.dev.yml up -d --build            # all services
docker compose -f docker-compose.dev.yml up -d --build backend    # backend only
docker compose -f docker-compose.dev.yml logs -f backend

# Quick backend syntax check
python3 -m py_compile backend/app/<package>/<file>.py

# Frontend typecheck = the `next build` step inside the frontend image build
docker compose -f docker-compose.dev.yml up -d --build frontend

# Data layer check against DynamoDB Local (46 checks over every access pattern; cleans up after itself)
docker compose -f docker-compose.dev.yml exec backend python scripts/smoke_test.py

curl -s http://localhost:8000/health | python3 -m json.tool   # DynamoDB table status + whether the router models have loaded
```

In dev the backend code is bind-mounted and served by the Flask dev server, so edits reload automatically. A reload while torch threads are running can abort the process (`terminate called without an active exception`); the container has `restart: unless-stopped` and comes back, then reloads the router models (2–3 min, manual mode works meanwhile).

There is no linter config. Verification is the smoke test, a container build, then hitting endpoints with `curl` (and looking at items in the DynamoDB admin GUI at http://localhost:8002).

Deploys run on every push to `main`: `.github/workflows/deploy-lambda.yml` assumes the `joomini-github-deploy` IAM role through GitHub OIDC (no stored AWS keys; the role trusts only this repo's `main`), runs `backend/scripts/deploy_lambda.sh update` (Zappa) and then `scripts/deploy_frontend_lambda.sh`, and checks `/health`. Model API keys and `COGNITO_CLIENT_SECRET` come from GitHub Secrets and end up only in Lambda environment variables; `AWS_DEPLOY_ROLE_ARN`, `FRONTEND_ORIGIN`, `COGNITO_REDIRECT_URI`, `COGNITO_LOGOUT_URI` are repository variables. The scripts still run locally with the `joomini` AWS profile. (The old self-hosted `scripts/deploy.sh` flow is no longer triggered; docs/en/DEPLOYMENT.md describes it.) How to swap one of the 9 models: docs/en/MODEL_UPDATE.md (Korean: docs/ko/MODEL_UPDATE.md). The AWS move (Lambda + Zappa) is tracked in docs/ko/AWS_MIGRATION_LOG.md.

## Data: one DynamoDB table, no migrations

All data lives in one table, `joomini` (`PK`/`SK` strings, on-demand, GSI1 conversations by `updated_at`, GSI2 conversations by project, GSI3 lookup by id, TTL attribute `ttl`). The design, the 23 access patterns, and why: docs/ko/DYNAMODB_DESIGN.md.

| Item | PK | SK |
|---|---|---|
| profile / settings | `USER#{uid}` | `PROFILE` / `SETTINGS` |
| global memory topic | `USER#{uid}` | `MEM#{category}#{id}` |
| conversation | `USER#{uid}` | `CONV#{cid}` |
| project | `USER#{uid}` | `PROJ#{pid}` |
| usage counter | `USER#{uid}` | `USAGE#D#{day}#{model}` / `USAGE#H#{hour}#{model}` (KST) |
| message | `CONV#{cid}` | `MSG#{created_at}#{mid}` |
| attachment | `CONV#{cid}` | `ATT#{aid}` |
| project file / memory | `PROJ#{pid}` | `FILE#{created_at}#{fid}` / `MEM#{id}` |

- `scripts/create_table.py` runs at container start and is idempotent. There are no column migrations: a new attribute just starts appearing on new items, so **reading code must treat any non-key attribute as optional** (DynamoDB never stores `None`; `serializers.py` fills the gaps).
- Changing a **key or GSI** is a real migration (rewrite items). Don't do it casually; update DYNAMODB_DESIGN.md first.
- Items are capped at 400KB, which is why extracted attachment text lives in a storage file (`extracted_key`), not on the item.

## Feature boundaries## Feature boundaries: don't let one feature's fix break another

There is no end-to-end test suite (only the data-layer smoke test), so a cross-feature regression only surfaces when a real user hits it after deploy. The app looks like separate features (routing, memory, attachments, projects, streaming), but several of them run through the **same shared code**. Before finishing any change, explicitly check whether it touches something another feature also depends on.

Known shared/coupled points:
- **`chat/turn.py` (`start_turn` → `_run_turn`) is the single hot path for every feature.** Routing, memory injection, attachment loading, and SSE streaming all converge here. A change scoped to one concern (e.g. "fix memory injection") still runs through shared helpers (e.g. `chat/history.build_history`) on *every* message, so it can silently affect routing or streaming too.
- **Normal chat and project chat share the same pipeline.** `/c/[id]` and `/projects/[id]` both go through `start_turn`, `build_history`, and the same SSE contract. A fix verified only in a normal chat must also be re-checked in a project chat, and vice versa.
- **Memory topics are read in two separate places**: `memory/extract.py` (extraction) and `chat/turn.py` + `chat/context.py` (system-prompt injection), both through `db/repo/memory.py`. Changing the topic item shape and updating only one of them leaves the other silently reading stale or malformed data.
- **The SSE contract (`StreamEvent`) is hand-duplicated** between the backend (`chat/turn.py`) and the frontend (`frontend/lib/types.ts`). Adding, renaming, or removing an event `type` or field on one side without the other breaks the frontend with no compiler error, because the two live in different languages/processes.
- **`frontend/lib/types.ts` mirrors `backend/app/serializers.py` by hand** for every endpoint, not just SSE — same silent-break risk for any response-shape change. There is no request schema library; routes validate bodies by hand and raise `ApiError(status, detail)` (`{"detail": ...}`, same as FastAPI's shape).
- **Key strings and GSI sort keys are shared state.** Keys are built only in `db/keys.py`. A conversation's `updated_at` must change only through `conversations._touch_update()`, because `GSI1SK`/`GSI2SK` have to move with it or the sidebar order breaks.
- **`SettingsWindow.tsx` is one tabbed modal** (personal instruction / import / usage / memory / appearance) sharing one piece of modal state. A change to one tab's logic must not alter the open/close or tab-switch behavior of the others.

Before marking any task done:
1. **Find every other caller/reader** of whatever you changed (`build_history`, `select_model`, the memory repo functions, `StreamEvent`, a serializer, an item attribute, etc.) — grep for it, don't assume it's only used by the feature you're focused on.
2. **Rebuild and smoke-test beyond the one feature you touched.** At minimum: send one normal chat message and one project chat message end-to-end, and confirm existing memory/attachment behavior still works if your change is anywhere near the chat send path.
3. **If a schema or the SSE contract changed, confirm both sides were updated together** — `serializers.py` ↔ `frontend/lib/types.ts`, or a repo function's writer ↔ every reader of that attribute — never just one.
4. If you're not sure whether something is shared, say so explicitly instead of assuming isolation — flat module layout with no tests means "it's probably fine" is exactly how regressions ship here.

## Backend architecture (backend/app/)

Flask app built in `app/__init__.py` (`create_app()`; module-level `app` is what gunicorn and Zappa load). HTTP lives in `routes/` (one blueprint per area, same URLs as the old FastAPI routers); everything under it is plain functions. Engine packages have no HTTP and no DB.

```
app/
├── __init__.py     create_app, CORS, error handler, router-model loading on first request, /health
├── auth.py         authenticate() (Cognito JWT, before_request) · current_user_id()
├── errors.py       ApiError(status, detail) → {"detail": ...}
├── serializers.py  response shapes (mirrored by frontend/lib/types.ts)
├── storage.py      file storage by key (local disk under UPLOAD_DIR now; swap for S3)
├── db/             dynamo.py (client, update_expr, Decimal conversion) · keys.py · repo/ (one function per access pattern)
├── routes/         auth (/auth/login·callback·reissue·logout) · conversations · attachments · projects · settings · users · usage · misc (/search, /memory/extract-all, legacy /chat) · file_views (shared viewers)
├── chat/           turn.py (start_turn, _run_turn: the send path) · history.py (build_history) · context.py (memory text, DOC_TOOLS_GUIDE)
│                   stream.py (TurnStream, active_turns) · title.py
├── services/       files.py (upload extraction, previews, blocks_for_model, run_in_background) · generated.py (save_generated_file)
├── memory/         extract.py (extraction, lock, run_scheduled entry point)
├── core/           tokens.py
├── routing/        classifier.py · complexity.py · complexity_examples.py · catalog.py + models.json (9 models)
│                   selector.py (select_model, bump_size) · intent.py (needs_*_tools keyword detectors)
├── llm/            client.py (call_model, stream_model_with_tools) · messages.py (vendor converters)
│                   tool_format.py · images.py · http.py (API keys, logged raise)
├── websearch/      providers.py (Tavily → Perplexity → Brave, search_web)
├── documents/      generator.py (create) · editor.py (inspect/edit) · extractor.py (text/image extraction)
└── tools/          base.py (ToolSpec/Context/Result, schema helpers) · registry.py (get_tool_specs, run_tool)
                    web_search.py · documents.py · image.py · edit.py
```

Dependency direction: `routes` → `chat` / `services` / `db.repo`; `chat` → `tools` → `documents` / `websearch` / `llm`; `chat` → `routing`, `llm`, `memory`. `llm` must not import `tools` or `chat` (specs are passed in). `tools/*` import `services` and `documents` lazily inside executors to avoid import cycles; keep it that way.

**Sync server, async engine.** Flask is synchronous, while `llm/` and `tools/` are async (httpx). Long work runs in a thread (`services.files.run_in_background`): a chat turn is one thread running `asyncio.run(_run_turn(...))`; upload extraction and title generation are threads too. boto3 calls are blocking and are fine inside a turn's own event loop. On Lambda nothing runs after the response, so these threads must become Zappa `@task` / polling (AWS_MIGRATION_LOG remaining steps 6–7).

Background jobs:
- Router model loading starts in a thread on the first request (not at import, so the dev reloader doesn't load them twice). Manual mode works before they finish; `auto` returns 503 until `classifier.is_ready()` and `complexity.is_ready()` (which waits for the reference embeddings, not just the model object).
- Orphan attachments: no sweep loop. Attachments get a `ttl` (24h uploads, 7 days generated) that is removed when a message links them; DynamoDB TTL deletes the rest, and reads hide expired items.
- Scheduled memory extraction (07:00 and 12:30 KST): `memory.extract.run_scheduled` is the entry point for Zappa `events`. It is **not** wired up locally; only the manual `/memory/extract-all` triggers run.

**Routing engine** (`routing/`, independent of HTTP):
- `classifier.py`: category classification.
- `complexity.py`, `complexity_reference.json`, `complexity_examples.py`: size decision by **kNN**. multilingual-e5-large embeds the question; the `K_NEIGHBORS` (11) most similar reference sentences vote for their size, weighted by cosine similarity, and the top-voted size wins.
  - References: `complexity_reference.json` (the 100 eval-set-v2 questions, `source: eval_v2`, plus 80 realistic work requests, `source: v3_aug`, all with rubric-judged small/medium/large labels) and EASY (small) / HARD (large) from `complexity_examples.py`. All are embedded once at startup.
  - The `v3_aug` rows fix a **length bias**: without long-but-easy references, kNN learned "long = hard" and sent most long work requests to large. Keep reference lengths balanced across sizes when adding rows, and never add the eval-set-v3 `test` split (it is the held-out check).
  - `complexity_score()` keeps its 0–1 float interface: the chosen size is mapped into its band of `COMPLEXITY_SMALL_MAX` 0.4 / `COMPLEXITY_MEDIUM_MAX` 0.80 (`catalog.py`), so `selector.py`, `messages.complexity_score`, and the SSE `meta` event are unchanged. Don't change those thresholds without changing the band mapping too.
  - Eval and rationale: notebooks/eval/README.md. 5-fold CV on eval set v2: 3-tier agreement 71% → 80.8%, large recall 17% → 79%. Held-out realistic set v3 test: long easy requests routed small 17% → 83%, over-escalation 12 → 3. Re-running the service on v2 or v3 aug is not a measurement (they are references); measure only with v2 CV and v3 test.
  - The examples and rubric target **reasoning difficulty, not topic**: technical-term definitions ("쿠버네티스가 뭐야?") are small, and everyday multi-constraint/multi-step questions are hard, so jargon alone doesn't push a question up.
- `selector.py`: `select_model()`, which combines the two, and `bump_size()`.
- `intent.py`: keyword intent detectors that decide which tool groups are sent.
- `catalog.py`: loads the 9-model catalog from `models.json` (company × size → `model_id`, `display_name`) and fails at startup if any combination is missing or duplicated. To change a model, edit `models.json` and the matching `label` in `frontend/lib/models.ts` (the label must equal `display_name`; the frontend uses it to find the size badge).

**Chat request path:** `POST /conversations/{id}/messages` → `chat/turn.py` `start_turn`
1. Loads all context up front: personal instruction, imported memory, global memory, project instruction, project memory.
2. **Saves the user message immediately** and links this turn's attachments in one transaction (`conversations.add_message`: put message + touch conversation + link attachments, which also clears their `ttl`). `token_count` is recomputed later from the real model content.
3. **Generation is decoupled from the HTTP connection.**
   - A background thread (`_drive` → `asyncio.run(_run_turn)`) does all the work and pushes events into a `TurnStream`, registered in `active_turns[conversation_id]`.
   - The POST response only subscribes to it. If the browser disconnects (chat switch, new chat, refresh), only the subscriber stops; the thread still finishes and saves the answer.
   - `GET /conversations/{id}/stream` replays all events so far, then streams live (204 if nothing is running). The chat page calls it via `resumeStream` when the last loaded message is an unanswered user message.
   - `done` is emitted only after the answer is saved.
   - The registry lives in process memory, so this assumes one process (gunicorn runs `--workers 1 --threads 16`).
   - The turn body:
     - selects a model;
     - builds a token-budgeted sliding-window history (`build_history`, `MAX_CONTEXT_TOKENS`);
     - runs the **tool loop** (up to `MAX_TOOL_ROUNDS` = 5, or `EDIT_MAX_TOOL_ROUNDS` = 8 on edit-intent turns, since inspect → edit → answer plus a retry or a re-edit doesn't fit in 5):
       - each round is one `llm.client.stream_model_with_tools()` call;
       - text streams out immediately as `chunk`;
       - if the model returned tool calls, every call is executed through `tools.registry.run_tool()`, an assistant message (with `tool_calls`) and one `tool` message per call are appended to a working copy of the history, and the loop calls the model again;
       - the loop ends on a round with no tool calls;
       - the last round forces a final answer (`force_final`): all three vendors keep `tools` and send `tool_choice` none;
       - if the loop ends with empty text, a fixed message is streamed and saved instead ("파일을 생성했습니다." when files were made).
4. Text from different rounds is joined with `"\n\n"`. Per-round token usage is summed and also added to the user's daily/hourly usage counters (`repo/usage.record_usage`). Tool failures (executor exceptions, unknown tool name, unparseable arguments) are sent back to the model as `is_error` results and the loop continues. A model-call exception sends an `error` event and saves no answer; the question is already saved.
5. **Tool calls and results are never stored.** Only the final assistant text is saved, so the next turn's history has no tool messages. `web_search_used` / `web_search_query` on the message come from the web_search tool's `ToolResult.metadata`. The title is generated after the first successful answer.

System-prompt context is injected in a fixed precedence order: personal instruction → imported memory → global memory → project instruction → project memory. Tool definitions are not in the system prompt; they go in the provider's top-level `tools` parameter.

**SSE contract:** the backend sends `data: {json}` lines with `type` ∈ `meta | searching | processing | file | chunk | done | error`. Mirror any change in `StreamEvent` in frontend/lib/types.ts. Since the loop, `searching` can arrive after some `chunk`s (when the model writes text before calling a tool).
- `file` carries `{"attachment": attachment_out(...)}` (`serializers.py`) for a generated file. Adding or changing an event means updating three places together:
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
  1. Add a branch in the matching `render_*` and its `ext` to `MIME_BY_EXT` in `services/files.py`.
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
  - `source_attachment_id` points to the original upload or generated file. A project file's id goes in `generation_meta.source_id` instead.
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
  - A scan-converted upload whose `storage_key` became a PDF falls back to the original `{id}.{ext}` beside it.
  - `.hwp` (old 한글 format) is rejected with a "convert to hwpx" message.
- **Intent:** `intent.needs_edit_tools` fires on an edit verb plus a file reference (or an editable attachment), and only when an editable file exists in this turn's attachments, the chat, or the project (`chat/history.has_files_in_context`). It bumps auto mode to medium.
- **History markers:** `build_history` marks uploads as `[첨부 파일: name (id=...)]`, project files as `[참고자료: name (id=...)]`, and generated files as `[생성된 파일: ...]` so the model can pass ids. `DOC_TOOLS_GUIDE`, sent on doc or edit turns, tells it never to echo these.

**hwpx (한글)** is always enabled; there is no flag.
- `generator._build_hwpx` uses python-hwpx (pinned in `backend/requirements.txt`). Lists become prefixed paragraphs, reported in `warnings`.
- hwpx edits patch `Contents/section*.xml`.
- **Not verified:** whether files open in Hancom 한글 was not checked during development; it is being validated by hand. If it fails, the plan is to remove hwpx support entirely. That means removing:
  - `_build_hwpx`;
  - `_inspect_hwpx`;
  - `hwpx` from the enums, descriptions, `supported_formats()`, and `_HWPX_KEYWORDS`;
  - `python-hwpx` from `requirements.txt`.

**Adding a format or tool — places to touch:**
- **New output format:**
  1. A `documents/generator.py` renderer branch.
  2. The `MIME_BY_EXT` entry in `services/files.py`.
  3. The tool schema enum and description.
  4. The frontend icon in `AttachmentTypeIcon`.
- **New editable format:**
  1. An `editor._inspect_<fmt>` function plus its entry in `_INSPECTORS`.
  2. The format in `supported_formats()`.
  3. The scope rule in `_scope_filter`.
  4. The ext list used by `has_files_in_context`, which derives from `supported_formats()`.
- **New tool:**
  1. Write the executor and its `ToolSpec`.
  2. Register it in `_REGISTRY`.
  3. Add a `get_tool_specs` flag for it.
  4. Write an intent detector in `routing/intent.py`.
  5. Wire the flag in `chat/turn.py` `_run_turn`.
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
- Global memory is one item per topic under the user (`MEM#{category}#{id}`): `category` (one of `나` / `주제` / `영역` / `사람`), `name`, `summary`, and `details` (a list). The `나` category holds exactly one topic. Changing a topic's category moves the item (the category is in the SK).
- Project memory has the same shape without `category`, under the project (`MEM#{id}`).
- Extraction is **diff-based**. It sends the existing topics (each tagged with a short key `t1`, `t2`…) plus the new messages to the OpenAI small model (`MEMORY_MODEL = get_model("openai", "small")`). The model returns only `{add, update, delete}`, and the code applies just those (`_apply_user_changes` / `_apply_project_changes`).
  - A topic the model doesn't mention is left untouched, so a model that "forgets" a topic can no longer erase it, and topic ids stay stable.
  - `update` carries the full merged `details` list.
  - `delete` is for existing topics that fail the storage criteria, never for "not mentioned in this conversation".
  - Assistant replies are clipped to `ASSISTANT_CONTEXT_CHARS` (300) in the prompt; facts must come from user messages.
  - The `나` single-topic rule is enforced in code: an `add` of `나` when one exists is merged into it, and an `update` can't turn a second topic into `나`.
  - Each run logs `메모리 갱신` with add/update/delete counts and deleted topic names + reasons.
- **Storage criteria** live in the prompt: store only facts that would help in an unrelated future conversation (profile, preferences, people, ongoing work). One-off task requests, assistant-generated content, file names, sample data, and single knowledge questions are excluded, with real past mistakes listed as negative examples. Explicit user preferences (answer format, length, tone) must be stored.
- A per-conversation cursor, `last_extracted_sk` on the conversation item (the SK of the last processed message; message SKs sort by time), means only new messages are processed. Remove it to force re-extraction.
- Every trigger goes through `extract_all_sequentially`, which holds a `threading.Lock`, because concurrent runs would clobber each other.
- Memory topics are read in **two** places: extraction (`memory/extract.py`) and system-prompt injection (`chat/turn.py` via `chat/context.py`). Update both when the item shape changes.

**Attachments and project files:** `routes/attachments.py` and `routes/projects.py` have the endpoints (viewers shared in `routes/file_views.py`); `services/files.py` handles extraction, previews, model blocks, and deletion; `documents/extractor.py` does per-format text and image extraction, with a LibreOffice → PDF fallback.
- Upload saves the file and returns `pending` at once; extraction runs in a background thread and updates the item. `_run_turn` waits briefly for pending attachments.
- Items store a storage **key** (`storage_key`), never an absolute path. Files live under `uploads/chats/{chat_id}/` and `uploads/projects/{project_id}/` (`storage.ROOT`). Extracted text blocks go to `{folder}/{id}.extracted.json` (`extracted_key`), and image blocks in it reference keys.
- The project's `files_total_bytes` counter is updated in the same transaction as adding/removing a file and is what the capacity check reads.

**Generated files** are attachment items with `source='generated'`. User uploads are `source='upload'`.
- **Storage:** files go under `uploads/chats/{chat_id}/generated/` (`services/generated.py`), so deleting the chat removes them. Limits are 25MB per file and 5 files per turn.
- **Linking:** each tool reports its `generated_attachment_id` in `ToolResult.metadata`, and the assistant message is saved with those ids in the same `add_message` transaction (sets `message_id`, clears `ttl`).
- **Model-call failure:** if the call fails after files were already made, the partial text (or "파일을 생성했습니다.") is saved and linked instead of sending `error`.
- **Orphans:** unlinked attachments expire through DynamoDB TTL (24h uploads, 7 days generated). TTL only deletes the item; the file on disk stays until the chat is deleted (S3 lifecycle/Streams cleanup is planned for the AWS move).
- **History:** `build_history` never puts generated files into model content blocks. Assistant messages can't carry document or image blocks, so that would be a 400. Instead, it appends `[생성된 파일: name (id=...)]` to the model-facing text only; the stored message content is unchanged, so the UI and memory extraction see the original text.
- **Frontend:** `start_turn` only accepts unlinked `source='upload'` ids of this chat in `attachment_ids`. `ChatBubble` renders assistant attachments as file cards (open in the existing viewer, download via `?download=1`).

## Backend conventions

- **All DynamoDB access goes through `db/repo/`**, one function per access pattern. Routes never build keys or expressions themselves.
  - Keys come only from `db/keys.py`.
  - Updates go through `dynamo.update_expr()`, which aliases every attribute name (`name`, `ttl`, `source`, `size`, `status` are reserved words).
  - boto3 rejects `float`: `to_dynamo()` turns floats into `Decimal` and drops `None`; `from_dynamo()` turns them back.
  - Lookups by id across partitions use GSI3 and then check `owner_pk`. GSIs are eventually consistent, so right after a write read by key instead (e.g. `get_attachment_in_conv`).
- **Login: Google → Cognito → BFF endpoints in `routes/auth.py`.** `/auth/login` (state + PKCE, `identity_provider=Google`), `/auth/callback` (code → tokens with the client secret, server-side only; ID token verified; `ensure_profile(sub, email, name)`; refresh token set as an HttpOnly, SameSite=Strict, Path=/auth cookie), `/auth/reissue` (refresh cookie → new access token in the JSON body), `/auth/logout` (RevokeToken + clear cookie + Cognito logout URL). reissue/logout require `X-Requested-With: joomini` and a matching `Origin`.
  - `auth.authenticate()` runs before every request except `/health`, `/auth/*`, and OPTIONS: it verifies the Bearer access token (JWKS RS256, iss, `token_use=access`, `client_id`, exp) and puts the claims in `g.user`. Missing or invalid → 401 (fail closed). `current_user_id()` returns `g.user["sub"]`; routes don't change.
  - `AUTH_DISABLED=1` (local only, never deployed) falls back to the fixed `DEMO_USER_ID` and seeds its profile. Existing local data from before login lives under that demo id.
  - Config: `COGNITO_REGION`, `COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID`, `COGNITO_DOMAIN`, `COGNITO_REDIRECT_URI`, `COOKIE_SECURE` (0 only for local http) in `docker-compose.dev.yml`; `COGNITO_CLIENT_SECRET` only in `backend/.env` (git-ignored).
  - Frontend: `lib/auth.ts` keeps the access token in a module variable (never localStorage), and `apiFetch` adds the Bearer header and retries once after `/auth/reissue` on 401. Every call in `lib/api.ts` goes through it (uploads via `xhrUpload`). `<img>`/`<iframe>`/downloads can't send headers, so file URLs go through `useAuthedObjectUrl` / `downloadAuthed` (blob URLs). `components/AuthGate.tsx` wraps `AppShell` and shows the login screen when reissue fails.
  - Ownership is structural: conversations and projects live under `USER#{uid}`, so another user's id simply isn't found (404, same as missing).
  - `memory.extract.run_scheduled` still runs for `DEMO_USER_ID` only; it must iterate real users before scheduled extraction is enabled.
  - Background threads have no request context: pass `uid` explicitly, never call `current_user_id()` inside them.
- Enum-like fields are plain strings validated by hand at the route (e.g. `MODES`, `COLORS`).
- Response dicts are built in `serializers.py` so missing optional attributes come out as `null`, matching the old FastAPI shapes.
- Background work uses threads (`run_in_background`). There is no Celery or Redis. CPU-bound model inference inside a turn goes through `asyncio.to_thread`.
- Code comments and docstrings are written in Korean.

## Frontend architecture (frontend/)

- **Routes:** `/` is a new chat, `/c/[id]` is a chat room, and `/projects/[id]` is a project page.
- `components/AppShell.tsx` is the layout. It owns the sidebar and the global `conversations` and `projects` state, and exposes them to route pages through `contexts/AppDataContext.tsx`.
- **First message from `/`:** the page creates the conversation, then hands the unsent message to `/c/{id}` through `lib/pendingMessage.ts` (sessionStorage), because the two routes don't share React state.
- **Streaming UI** (`app/c/[id]/page.tsx`):
  - An `AbortController` lets the user switch chats mid-stream.
  - `isAutoScrollingRef` distinguishes programmatic scroll from user scroll, so auto-scroll doesn't fight the user.
- All HTTP calls go through `lib/api.ts`.
- `lib/types.ts` is kept in sync **by hand** with `backend/app/serializers.py`.
- Settings is one tabbed window (`components/SettingsWindow.tsx`): personal instruction, import, usage, memory, appearance.
- Dark mode is class-based (`.dark`). The `.scroll-thin` utility in `globals.css` provides themed scrollbars.

## Git

Commit messages are a single line, `type: description` (e.g. `feat: ...`, `fix: ...`), with no body. Commit only when asked.