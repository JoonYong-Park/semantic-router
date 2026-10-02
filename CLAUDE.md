# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

ASAP: a chat app (FastAPI + Next.js 14 App Router + Postgres) that routes each question to one of 9 LLMs (OpenAI / Google / Anthropic × small / medium / large). In `auto` mode, an mmBERT classifier picks the **company** from the question's category, and E5-embedding similarity picks the **size** from a complexity score. Manual mode lets the user pick company and size directly. See README.md for the feature list, the memory mechanisms, and per-format attachment handling.

## Commands

Everything runs in Docker. There is no local venv or `node_modules`, so IDE "cannot find module" diagnostics are noise. Verify changes by building the containers.

```bash
# Local dev (docker-compose.yml is the server/prod variant: uses SERVER_IP and UPLOAD_HOST_DIR from .env)
docker compose -f docker-compose.dev.yml up -d --build            # all services
docker compose -f docker-compose.dev.yml up -d --build backend    # backend only
docker compose -f docker-compose.dev.yml logs -f backend

# Quick backend syntax check before rebuilding
python3 -m py_compile backend/app/<file>.py

# Frontend typecheck = the `next build` step inside the frontend image build
docker compose -f docker-compose.dev.yml up -d --build frontend

# DB access (container name `postgres`, db/user `asap`)
docker exec postgres psql -U asap -d asap -c "..."

curl -s http://localhost:8000/health | python3 -m json.tool   # also reports whether the classifier/complexity models have loaded
```

There is no test suite and no linter config. Verification is a container build, then hitting endpoints with `curl` and inspecting rows with `psql`.

Deploys run on every push to `main`: a GitHub Actions self-hosted runner executes `scripts/deploy.sh`, which builds, health-checks, and rolls back to the `:previous` image on failure. Details are in docs/DEPLOYMENT.md.

## Schema changes: there are no migrations

`init_db()` (backend/app/db.py) runs `Base.metadata.create_all()` at startup. It creates missing tables but **never alters existing ones**. Any column change to an existing table needs a manual `ALTER TABLE` (or a DROP and recreate), run against the local DB **and** the deployed server's DB. Forgetting the server side causes `UndefinedColumnError` after deploy. When you change `db_models.py`, give the user the exact SQL to run on the server.

## Feature boundaries: don't let one feature's fix break another

There is no test suite, so a cross-feature regression only surfaces when a real user hits it after deploy. The app looks like separate features (routing, memory, attachments, projects, streaming), but the backend is a flat module layout and several of them run through the **same shared code**. Before finishing any change, explicitly check whether it touches something another feature also depends on.

Known shared/coupled points:
- **`conversations.py` `send_message` is the single hot path for every feature.** Routing, memory injection, attachment loading, and SSE streaming all converge here. A change scoped to one concern (e.g. "fix memory injection") still runs through shared helpers (e.g. `_build_history`) on *every* message, so it can silently affect routing or streaming too.
- **Normal chat and project chat share the same pipeline.** `/c/[id]` and `/projects/[id]` both go through `send_message`, `_build_history`, and the same SSE contract. A fix verified only in a normal chat must also be re-checked in a project chat, and vice versa.
- **Memory schema is read in two separate places**: `memory.py` (extraction/recompute) and `conversations.py` (system-prompt injection). Changing `UserMemory`/`ProjectMemory` shape and updating only one of them leaves the other silently reading stale or malformed data.
- **The SSE contract (`StreamEvent`) is hand-duplicated** between the backend (`conversations.py`) and the frontend (`frontend/lib/types.ts`). Adding, renaming, or removing an event `type` or field on one side without the other breaks the frontend with no compiler error, because the two live in different languages/processes.
- **`frontend/lib/types.ts` mirrors `backend/app/schemas.py` by hand** for every endpoint, not just SSE — same silent-break risk for any Pydantic schema change.
- **No `relationship()`, no migrations.** Every join is a manual `select()`, so a schema change can require updating every hand-written join site, not just the model. And every column change needs a manual `ALTER TABLE` on both local and server DB (see "Schema changes" above) — a fix that works locally against a fresh `create_all()` can still crash production.
- **`SettingsWindow.tsx` is one tabbed modal** (personal instruction / import / usage / memory / appearance) sharing one piece of modal state. A change to one tab's logic must not alter the open/close or tab-switch behavior of the others.

Before marking any task done:
1. **Find every other caller/reader** of whatever you changed (`_build_history`, `select_model`, the memory read/write functions, `StreamEvent`, a Pydantic schema, a DB column, etc.) — grep for it, don't assume it's only used by the feature you're focused on.
2. **Rebuild and smoke-test beyond the one feature you touched.** At minimum: send one normal chat message and one project chat message end-to-end, and confirm existing memory/attachment behavior still works if your change is anywhere near the chat send path.
3. **If a schema or the SSE contract changed, confirm both sides were updated together** — backend schema ↔ `frontend/lib/types.ts`, or `db_models.py` ↔ the server's `ALTER TABLE` — never just one.
4. If you're not sure whether something is shared, say so explicitly instead of assuming isolation — flat module layout with no tests means "it's probably fine" is exactly how regressions ship here.

## Backend architecture (backend/app/)

The backend is a flat module layout. Each feature module owns an `APIRouter`, and `main.py` mounts them all. The `lifespan` in `main.py` starts three background tasks:
- HF model loading. Manual mode works before the models finish loading; `auto` mode returns 503 until they are ready.
- `attachments.orphan_cleanup_loop`.
- `memory.scheduled_extraction_loop`, which runs at 07:00 and 12:30 KST.

**Routing engine** (independent of HTTP):
- `classifier.py`: category classification.
- `complexity.py` and `complexity_examples.py`: complexity score.
- `router.py`: `select_model()`, which combines the two. Note that this is *not* a FastAPI router.
- `models_config.py`: the 9-model catalog.

**Chat request path:** `conversations.py` `send_message`
1. Loads all context up front in its own session: personal instruction, imported memory, global memory, project instruction, project memory.
2. Returns a `StreamingResponse` whose generator:
   - selects a model;
   - builds a token-budgeted sliding-window history (`_build_history`, `MAX_CONTEXT_TOKENS`);
   - streams via `providers.stream_model_with_tools()`.
3. If the model emits a web-search tool call, it runs `search.py` (Tavily) and makes a second `stream_model()` call with the results.

System-prompt context is injected in a fixed precedence order: personal instruction → imported memory → global memory → project instruction → project memory.

**SSE contract:** the backend sends `data: {json}` lines with `type` ∈ `meta | searching | processing | chunk | done | error`. Mirror any change in `StreamEvent` in frontend/lib/types.ts.

**providers.py** abstracts the three vendors. It covers non-streaming `call_model`, streaming `stream_model`, and `stream_model_with_tools`, which buffers tool-call argument fragments per provider. `MAX_OUTPUT_TOKENS` caps every call.

**Memory** (`memory.py`):
- `UserMemory` is global. Each row is a topic: `category` (one of `나` / `주제` / `영역` / `사람`), `name`, `summary`, and `details` stored as a JSON-array string. The `나` category holds exactly one topic.
- `ProjectMemory` has the same shape without `category`.
- Extraction **recomputes everything** on each run: it sends the existing topics plus the new messages to `gpt-4o-mini`, deletes every row in that scope, and reinserts what the model returns. Row IDs therefore change on every extraction.
- A per-conversation cursor, `Conversation.last_extracted_message_id`, means only new messages are processed. Reset it to NULL to force re-extraction.
- Every trigger goes through `_extract_all_sequentially`, which holds an `asyncio.Lock`, because concurrent delete-and-reinsert runs would clobber each other.
- Memory topics are read in **two** places: extraction (`memory.py`) and system-prompt injection (`conversations.py`). Update both when the schema changes.

**Attachments:** `attachments.py` handles upload, lifecycle, and orphan sweep. `extraction.py` does per-format text and image extraction, with a LibreOffice → PDF fallback. Extraction runs in the background, and `send_message` waits briefly for pending attachments. Files live under `uploads/chats/{chat_id}/` and `uploads/projects/{project_id}/`.

## Backend conventions

- **No SQLAlchemy `relationship()`** anywhere. Use explicit `select()`/joins and assemble results by hand, which avoids async lazy-load `MissingGreenlet` errors.
- There is no auth. Everything is scoped to the constant `DEMO_USER_ID` in `db_models.py`.
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