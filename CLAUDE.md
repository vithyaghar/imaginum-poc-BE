# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**imaginum_POC_BE** is a FastAPI backend for an AI-powered marketing campaign generation system. Users converse over WebSocket to build a multi-channel campaign brief; a multi-agent pipeline turns that brief into 3 candidate campaign routes, and after the user picks one, generates presentation slide content. It is a proof-of-concept by Trinom Digital Pvt Ltd.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Start the development server
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Interactive API docs (after server starts)
# http://localhost:8000/docs

# CLI / smoke-test mode (sync stdin loop, no WebSocket)
python business_communication.py
```

No test runner or linter is configured. Manual testing is done via the Swagger UI or a WebSocket client against the running server.

## Environment Setup

Requires a `.env` file with:
```
ANTHROPIC_API_KEY=...
GOOGLE_API_KEY=...
OPENAI_API_KEY=...
```

The SQLite database (`demo.db`) is auto-created on startup via the `lifespan` context manager in [main.py](main.py), which calls `init_db()`.

## Architecture

Three communication surfaces, all defined in `routes/` and wired in [main.py](main.py):
- **WebSocket** (`/api/ws`) — drives the real-time, multi-turn campaign generation loop ([routes/websocket_route.py](routes/websocket_route.py) → [controllers/websocket.py](controllers/websocket.py))
- **REST `/threads`** ([routes/thread_routes.py](routes/thread_routes.py)) — list/get persisted threads, `PATCH /threads/{thread_id}/title` to rename a thread, and `POST /threads/{thread_id}/upload-pdf` to seed a brief from a PDF
- **REST `/api/threads`** ([routes/pdf_routes.py](routes/pdf_routes.py)) — a second router that re-registers the *same* `upload-pdf` endpoint under a different prefix; both routes are live simultaneously, be aware when adding new PDF-related endpoints so you don't create a third copy

### Multi-Agent Pipeline ([business_communication.py](business_communication.py))

The core of the app is a LangGraph graph (built via `langchain.agents.create_agent`) orchestrating a coordinator ("Account Director") plus five specialist sub-agents, all defined as module-level agents at the top of the file:

| Agent | Model | Role |
|-------|-------|------|
| Account Director (coordinator) | `gemini_model` (websocket) / `claude_model` (CLI `main()`) | Runs the whole conversation + drives pipeline tool calls per `_ACCOUNT_DIRECTOR_PROMPT` |
| CEO | `claude_model` (`claude-sonnet-4-5`) | Three-stage strategic synthesis (direction → combined brief → final routes) |
| Brand Strategist | `gemini_specialist_model` | Consumer insight + brand framework |
| Media Planner | `gemini_specialist_model` | Channel strategy + budget allocation |
| Creative Director | `gemini_specialist_model` | 3 creative routes, platform adaptations |
| Digital Specialist | `gemini_specialist_model` | Performance/ROI plans per route |

`gemini_specialist_model` and `gemini_model` both currently point at `models/gemini-3.5-flash` (see the model constants near the top of the file) — the "Flash Lite" naming in code comments is stale.

#### Configurable Agent Tones

Each agent's system prompt contains a `{tone_instruction}` placeholder injected at creation time via `_format_prompt()`. Defaults live in the `AGENT_TONES` dict (keyed by agent name). Two entry points for overriding:

- **Sub-agents** (CEO, Brand Strategist, Media Planner, Creative Director, Digital Specialist, Presentation Strategist): call `configure_agent_tones({"agent_key": "new tone"})` before the first request — it rebuilds the module-level agent globals with the merged tones.
- **Account Director** (coordinator): call `get_system_prompt({"account_director": "new tone"})` — the websocket handler already calls this per-connection, so custom tones are picked up without restarting.

If a key is absent from the supplied dict or the tone string is empty, the placeholder resolves to an empty string and the agent behaves exactly as it did before this feature was added.

The pipeline (see `_ACCOUNT_DIRECTOR_PROMPT` near the bottom of the file) runs as **8 sequential tool calls** driven entirely by the coordinator LLM, not fixed Python control flow:
1. `run_ceo_stage1` — CEO sets strategic direction
2. `run_brand_and_media` — runs Brand Strategist + Media Planner **concurrently** via `asyncio.gather`, stores both results in one `Command`
3. `run_ceo_stage2` — CEO combines Brand + Media into one brief
4. `run_creative_and_digital` — runs Creative Director + Digital Specialist **concurrently** via `asyncio.gather`
5. `run_ceo_stage3` — CEO compiles 3 final routes + coverage summary (Concept Hub)
6. `select_campaign_route` — fires once the user picks a route
7. `generate_slides_content` — a dedicated `slide_content_agent` turns the approved route into markdown slide content
8. (individual single-agent tools `run_brand_strategist`, `run_media_planner`, `run_creative_director`, `run_digital_specialist` still exist in the file but are only wired into the CLI's tool list history — the live websocket/CLI pipelines use the merged `run_brand_and_media` / `run_creative_and_digital` tools)

All streaming to the client happens through `_run_agent_streaming()`, which looks up a per-thread send function via `services/connection_registry.py` (`get_send_fn`) — this is what lets sub-agents invoked deep inside a tool call push `agent_thinking` / `agent_stream` / `agent_stream_end` events to the websocket that opened the thread. It's a no-op (falls back silently) when nothing is registered, e.g. CLI mode.

### Campaign State Machine

The `status` column in the `threads` table tracks pipeline progress:

```
GATHERING_REQUIREMENTS
  → (mark_requirements_completed tool)          REQUIREMENT_COLLECTED  [chat_status = DISABLED]
  → run_ceo_stage1
  → run_brand_and_media                          → MEDIA_PLAN_COMPLETE
  → run_ceo_stage2
  → run_creative_and_digital                     → DIGITAL_PLANS_COMPLETE
  → run_ceo_stage3                               (Concept Hub — 3 routes presented)
  → (select_campaign_route tool)                 ROUTE_SELECTED
  → generate_slides_content                      → SLIDES_READY
```

Once `mark_requirements_completed()` fires, further free-form user messages are rejected client-side (`chat_status = DISABLED`); the same happens again once `slides_content` is produced (see the `chat_status: "DISABLED"` sent alongside the `slides_content` websocket event).

### LangGraph + SQLite Checkpointing

Conversation state (`CampaignState`, defined in `business_communication.py` as a subclass of `AgentState`) is persisted in `demo.db` via:
- `AsyncSqliteSaver` — used by the WebSocket handler ([controllers/websocket.py](controllers/websocket.py)) and by the PDF upload controller ([controllers/pdf_controller.py](controllers/pdf_controller.py)), both async
- `SqliteSaver`-equivalent async checkpointer is also used in the CLI `main()` (also async, despite being invoked from a sync-looking `input()` loop wrapped in `asyncio.run`)

State uses LangGraph's `add_messages` reducer for the message list, and a `reduce_latest` pattern (last-write-wins, defined near the top of `business_communication.py`) for every other field.

### Two Database Layers

There are **two separate database access patterns** — be aware of the difference:

| Layer | File | Pattern | Used for |
|-------|------|---------|---------|
| Direct connection | [database/database.py](database/database.py) | Simple `sqlite3` wrapper | `init_db()` on startup (creates the `threads` table); LangGraph checkpointer |
| Queue worker | [services/table_service.py](services/table_service.py) | Background daemon thread + `queue.Queue` | All CRUD from controllers and agent tools (`create_thread`, `update_thread`, `get_thread`) |

The queue worker in `table_service.py` is started at **import time** as a daemon thread. Writes submitted via `execute_db`/`update_thread` are **asynchronous to the caller** unless you pass `fetch=True` and block on the result queue — do not assume a freshly written column is visible to a subsequent read without accounting for this.

**Schema management caveat:** `table_service.py` also has its own `init_db()` (with WAL-mode PRAGMAs and incremental `ALTER TABLE` migrations) but it is **not wired to the `lifespan` event** — only `database.database.init_db()` runs at startup. To add a new column: update the `CREATE TABLE` in `database/database.py` (for fresh databases) **and** the incremental `ALTER TABLE` list in `table_service.py` (for existing databases that lack the column).

### Tools Are LangGraph Commands

All agent tools (`update_campaign_state`, `mark_requirements_completed`, `run_ceo_stage1`, `run_brand_and_media`, `run_ceo_stage2`, `run_creative_and_digital`, `run_ceo_stage3`, `select_campaign_route`, `generate_slides_content`, plus the legacy single-agent variants) return `Command()` objects, not plain values. Each tool both updates graph state **and** writes to the SQLite `threads` table via `update_thread()`.

### WebSocket Event Protocol

Client → server payloads are JSON with a `type` field (or a bare `{"text": "..."}` for a chat turn):

| Event (client → server) | Meaning |
|---|---|
| `create_thread` | Start a new thread; server replies with `thread_created` |
| `ping` | Heartbeat (ignored) |
| `stop` | Disconnect |
| *(default / `text`)* | A chat turn, or `thread_id` to (re)bind an existing thread to this connection |

| Event (server → client) | Meaning |
|---|---|
| `thread_created` | Confirms `thread_id` |
| `stream_start` / `message` (`streaming: true`) / `stream_end` | Token-by-token coordinator reply (only while `stream_tokens=True`, i.e. before `brief_complete`) |
| `message` | A complete coordinator reply (post-tool-call summaries, or during pipeline phases) |
| `agent_thinking` | A sub-agent is about to run (Brand Strategist, CEO stage, etc.) |
| `agent_stream_start` / `agent_stream` / `agent_stream_end` | Token-level streaming of a sub-agent's own output |
| `internal_message` | Raw output of a completed pipeline stage (`strategy_brief`, `media_plan`, `ceo_combined_brief`, `creative_routes`, `performance_scores`) |
| `concept_hub` | One message per campaign route once `final_routes` is compiled (parsed via `parse_final_routes`) |
| `coverage_summary` | Parsed scoring table from the CEO's final output (also persisted to `threads.coverage_summary`) |
| `route_selected` | The user's chosen route + `approved_campaign` |
| `slides_content` | Markdown slide content is ready; sent with `chat_status: "DISABLED"` |

The WebSocket handler creates a **new coordinator instance per connection** (`create_agent(...)` inside `handle_websocket`) but reuses the same `thread_id` across multiple messages on that connection — and across reconnects if the client resends `thread_id` in a payload — which is how LangGraph resumes checkpointed state. While `should_continue` is true (brief complete but no final routes yet, or route selected but no slides yet), the handler keeps invoking the coordinator with a synthetic `"continue"` message without waiting on `websocket.receive_text()`.

### PDF Brief Upload ([controllers/pdf_controller.py](controllers/pdf_controller.py), [services/pdf_service.py](services/pdf_service.py))

`POST /threads/{thread_id}/upload-pdf` (and its duplicate under `/api/threads/...`) lets a user seed a brief from an uploaded PDF instead of typing it:
1. Only allowed while the thread is still `GATHERING_REQUIREMENTS`; PDFs over 10 MB are rejected.
2. `analyze_pdf_with_claude()` sends the raw PDF bytes to Claude natively (`type: "document"` content block) and gets back `{extracted, missing, summary}` for the 14 campaign fields defined in `CAMPAIGN_FIELDS`.
3. Extracted fields are written straight into the LangGraph checkpoint via `coordinator.aupdate_state(config, extracted)` — bypassing tool-call extraction entirely, because Gemini Flash Lite was unreliably dropping fields when asked to call `update_campaign_state` with 13 params at once.
4. The coordinator is then invoked with a synthetic `[PDF UPLOAD — SYSTEM CONTEXT]` `HumanMessage`, purely to generate the user-facing greeting/follow-up. This message carries `additional_kwargs={"attachments": [{"file_name": ..., "mime_type": "application/pdf", "source": "upload"}]}` — which is how the PDF upload event appears in the thread history when the client reloads. The coordinator is explicitly told not to call `update_campaign_state` again.

**Important — never use a second `aupdate_state` for messages:** The `messages` channel is writable by both the `"agent"` and `"tools"` nodes, so any `aupdate_state` call that touches it without `as_node` raises `InvalidUpdateError: Ambiguous update`. Node names like `"__end__"` are not valid in this LangGraph version and raise `InvalidUpdateError: Node __end__ does not exist`. The correct pattern is to carry attachment metadata in `additional_kwargs` on the `HumanMessage` that already goes into `coordinator.ainvoke` — no second `aupdate_state` needed.

`_ACCOUNT_DIRECTOR_PROMPT` in `business_communication.py` has a matching "PDF Pre-Population" section so the *live chat* coordinator also knows how to handle a `[PDF UPLOAD — SYSTEM CONTEXT]`-prefixed message if one ever reaches it directly. The formatted `SYSTEM_PROMPT` constant (pre-rendered from the template using default tones) is still exported for backward compatibility with any code that imports it directly, but the websocket handler uses `get_system_prompt()` to allow per-connection tone injection.

### Canva Brand Guidelines Access Scoping ([services/canva_service.py](services/canva_service.py), [business_communication.py](business_communication.py), [controllers/websocket.py](controllers/websocket.py))

Only three agents are permitted to read Canva file contents: **CEO**, **Brand Strategist**, and **Creative Director**. All other agents (Account Director, Media Planner, Digital Specialist, Slide Content Agent) handle file references/metadata only.

Two entry paths lead to brand guidelines content being injected into the pipeline:

**Path 1 — Explicit @ mention:** The user tags a Canva file in chat. The WS handler calls `_build_attachment_context()`, which returns `[CANVA FILE REF: "{name}" (ID: {id})]` — metadata only, no download. The Account Director sees this string, acknowledges it to the user, and does not extract any content. Separately, the WS handler extracts `canva_file_refs` from the attachments list and persists them into LangGraph state via `coordinator.aupdate_state(...)` before the coordinator is invoked.

**Path 2 — Auto-discovery:** No @ mention. `run_ceo_stage1` calls `discover_brand_relevant_designs(session_id)`, which keyword-matches available Canva design names against a list of brand-related terms (brand, guideline, style, identity, logo, palette, etc.).

In both paths, once `canva_file_refs` is resolved, `run_ceo_stage1` calls `fetch_brand_guidelines_content(session_id, file_refs)` — this is the **single fetch point** that downloads and extracts content from Canva. The result is stored in `brand_guidelines_content` (a new `CampaignState` field). Downstream agents read from state — no re-download occurs.

**Agent injection:**
- `run_ceo_stage1` — appends brand guidelines to CEO prompt with "treat as authoritative" framing
- `run_brand_and_media` — appends to Brand Strategist prompt only (Media Planner unchanged)
- `run_creative_and_digital` — appends to Creative Director prompt only (Digital Specialist unchanged)

**New `CampaignState` fields:** `canva_file_refs: Optional[list]`, `brand_guidelines_content: Optional[str]` — both use `reduce_latest`.

**New `canva_service.py` functions:** `discover_brand_relevant_designs(session_id)`, `fetch_brand_guidelines_content(session_id, file_refs)`.

**Env vars required for Canva connector:** `CANVA_CLIENT_ID`, `CANVA_CLIENT_SECRET`, `CANVA_REDIRECT_URI` (must match exactly what's registered in the Canva Developer Portal).

### Google Drive File Access Scoping ([services/pdf_service.py](services/pdf_service.py), [controllers/websocket.py](controllers/websocket.py), [business_communication.py](business_communication.py))

Drive content is **only ever read when a user explicitly `@` mentions a file**. No auto-scan or auto-discovery of Drive files occurs under any circumstance.

Six agents are permitted to receive Drive file content: **CEO**, **Brand Strategist**, **Media Planner**, **Creative Director**, **Digital Specialist**, and **Slide Content Agent**. The Account Director sees only a metadata tag and never reads file content.

**Processing flow for @ mentioned Drive files:**

1. `_build_attachment_context()` returns `[DRIVE FILE REF: "{name}" (ID: {id})]` — metadata only — for the Account Director message.
2. `_process_drive_attachments(session_id, drive_refs, current_state)` runs before the coordinator is invoked:
   - Downloads each file via `download_file_as_bytes()`
   - Calls `process_drive_document(file_bytes, file_name, existing_fields)` — a **single Claude call on raw PDF bytes** that returns both brief field extraction and full document content simultaneously (same input quality as the PDF upload path; no intermediate text extraction step)
   - Returns `{brief_updates, drive_file_content, drive_file_refs, coordinator_note}`
3. `coordinator.aupdate_state()` writes extracted brief fields + `drive_file_content` + `drive_file_refs` to LangGraph checkpoint — before the coordinator sees the user message.
4. `coordinator_note` is prepended to the user message — tells the Account Director what was extracted and what is still missing.

**Dual use of every @ mentioned Drive file:**
- Brief fields extracted → written to campaign state (same mechanism as PDF upload), never overwriting already-known values
- Full document content stored as `drive_file_content` → injected into all 6 permitted pipeline agents as reference context

**`services/pdf_service.py` functions for Drive:**
- `process_drive_document(file_bytes, file_name, existing_fields)` — primary function; single Claude call on raw PDF bytes returning `{extracted, missing, already_known, summary, full_content}`
- `analyze_drive_document(extracted_text, file_name, existing_fields)` — superseded by `process_drive_document`; kept but no longer called from the main flow

**New `CampaignState` fields:** `drive_file_refs: Optional[list]`, `drive_file_content: Optional[str]` — both use `reduce_latest`.

**Agent injection:** All 6 permitted agents (`run_ceo_stage1`, both agents in `run_brand_and_media`, both agents in `run_creative_and_digital`, `generate_slides_content`) receive `drive_file_content` appended to their HumanMessage prompts when present.

### Message Attachments

File attachments (Canva or Drive @ mentions in WS chat; PDF uploads via the REST endpoint) are persisted alongside the LangGraph checkpoint message using `HumanMessage.additional_kwargs["attachments"]`. Each entry is a dict with `file_id`, `file_name`, `mime_type`, and `source` (e.g. `"canva"`, `"drive"`, `"upload"`). LangGraph serialises `additional_kwargs` as part of the checkpoint, so attachments survive connection drops and server restarts.

When adding new attachment sources, follow the same pattern: populate `additional_kwargs={"attachments": [...]}` on the `HumanMessage` before it enters `coordinator.ainvoke`. Do **not** insert a separate `aupdate_state` call to the `messages` channel — see the PDF Brief Upload section for why.

### Market Intelligence Agent ([business_communication.py](business_communication.py))

The first pipeline step after `mark_requirements_completed` is `run_market_intelligence` — a dedicated research step that fetches live trend data before any specialist agent runs.

**Architecture: single Gemini call with Google Search grounding**
`run_market_intelligence` creates a `ChatGoogleGenerativeAI("gemini-2.0-flash")` model with `google_search_retrieval` bound as a tool. Gemini handles the search internally (server-side) and streams back a fully grounded, synthesised report in one call — no separate fetch layer, no second LLM pass. The streaming response is forwarded to the client via manual `agent_stream_start` / `agent_stream` / `agent_stream_end` events. There is no separate `market_intelligence_agent` create_agent instance; the synthesis is self-contained within `run_market_intelligence`.

**Geography-aware with Myanmar default:**
`run_market_intelligence` reads `state.geography`. If the field is absent or blank at the time the tool runs (e.g. geography not yet collected), it falls back to `"Myanmar"`. The 5 queries are built from:
- `geography` (from state, default `"Myanmar"`)
- `business_description` (first 80 chars, default `"consumer brands"`)
- `platforms` (from state, default `"Facebook TikTok"`)

**New `CampaignState` field:** `market_intelligence_report: Annotated[Optional[str], reduce_latest]`

**Injected into three downstream agents:**
- `run_ceo_stage1` — grounds the CEO's "Macro Trend Analysis" section in real data
- `run_brand_and_media` — appended to Brand Strategist prompt only (Media Planner unchanged)
- `run_creative_and_digital` — appended to Creative Director prompt only (Digital Specialist unchanged)

**Error handling:** All 5 Tavily queries are individually guarded. If all fail, `combined_raw` = `"No search results returned."` and the synthesis agent notes the data gap. The pipeline always continues — worst case the report is thin, downstream agents degrade gracefully to training-knowledge behaviour.

**No new env vars or dependencies required** — uses the existing `GOOGLE_API_KEY` and `langchain_google_genai` which is already installed.

**`_AGENT_LABELS` key:** `"market_intelligence"` → ws name `"market_intelligence_analyst"`, streams via standard `agent_thinking` / `agent_stream_start` / `agent_stream` / `agent_stream_end` events.

**Pipeline step order after this change:** Market Intelligence (1) → CEO Stage 1 (2) → Brand + Media (3) → CEO Stage 2 (4) → Creative + Digital (5) → CEO Stage 3 (6) → Route Selection (7/8) → Slides (9).

### Chat History Retrieval

[services/thread_service.py](services/thread_service.py) deserialises LangGraph checkpoints directly to reconstruct chat history and also implements `update_thread_title`. It filters out tool messages and empty AI messages before returning — so the REST endpoint does not expose internal tool call state to the client.

**Attachment surfacing:** When a `HumanMessage` carries `additional_kwargs["attachments"]`, the serialised chat entry includes an `"attachments"` key with the same list. The client uses this to render file cards in the conversation replay.

**`[PDF UPLOAD — SYSTEM CONTEXT]` filter:** These messages are internal coordinator directives and their `content` is never shown to the client. However, because attachment metadata lives on the same message, `thread_service` emits an attachment-only entry (`content: ""`, `attachments: [...]`) before skipping the message text — so the PDF upload still appears as a file card in the reloaded thread history.

### PPT Generation (Unfinished — active branch work)

The pipeline now produces markdown slide content server-side (`generate_slides_content` tool → `slides_content` websocket event → `threads.slides_content` column), but nothing currently converts that markdown into an actual `.pptx` file for download. Three candidate implementations exist and are **not called from any route**:
- [services/ppt_anthropic_service.py](services/ppt_anthropic_service.py) — delegates slide structuring to Claude
- [services/ppt_service.py](services/ppt_service.py) — `ai_parse_markdown_to_slides()` parses markdown into slide JSON via Gemini
- [services/ppt_generator.py](services/ppt_generator.py) — renders slides with `python-pptx`

The `threads` table already has `ppt_bytes` (BLOB), `is_ppt_generated`, and `ppt_filename` columns reserved for this. This gap is unfinished work.

### REST Response Convention

All REST controllers use the `response_handler` singleton from [helper/response_handler.py](helper/response_handler.py). It wraps every response in `{"success": bool, "message": str, "data": ...}` and raises `HTTPException` on errors. Any new REST endpoint should call `response_handler.success(...)` or `response_handler.error(...)` rather than returning raw dicts.

### Token Usage Logging

[helper/logger.py](helper/logger.py) logs token consumption for every LLM call. It tries multiple metadata key names (`token_usage`, `usage`, `usage_metadata`) to handle differences between Anthropic and Google response formats.

### `business_communication_pptx_lib.py`

An alternate architecture experiment — a duplicate of the agent pipeline with a senior-reviewer agent added. It is not imported anywhere; treat it as a scratch file.
