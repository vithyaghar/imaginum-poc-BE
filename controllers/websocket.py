#!/usr/bin/env python
# -- coding:utf-8 --
###
# Filename: c:\Projects\imaginum\backend\controllers\websocket.py
# Path: c:\Projects\imaginum\backend\controllers
# Created Date: Monday, March 9th 2026, 2:15:37 pm
# Author: Vithyaghar M
#
# Copyright (c) 2026 Trinom Digital
###
import asyncio
import json
import re
import uuid
from fastapi import WebSocket, WebSocketDisconnect
from helper.logger import log_token_usage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langchain.messages import HumanMessage, AIMessage
from langchain.agents import create_agent

# Add backend root to sys.path so we can import business_communication
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from business_communication import (
    CampaignState,
    update_campaign_state,
    mark_requirements_completed,
    run_market_intelligence_core,
    run_ceo_stage1,
    run_brand_and_media,
    run_ceo_stage2,
    run_creative_and_digital,
    run_ceo_stage3,
    select_campaign_route,
    generate_slides_content,
    get_system_prompt,
    normalize_llm_output,
    gemini_model,
    parse_final_routes,
)
from services.connection_registry import register, unregister
from services.table_service import create_thread, get_thread, update_thread, get_thread_session_id
from services.google_drive_service import download_file_as_bytes as drive_download
from services.pdf_service import process_drive_document, CAMPAIGN_FIELDS

ROLE_LIST = {
    "account_director": "Account Director",
    "ceo_agent": "CEO",
    "brand_strategist": "Brand Strategist",
    "media_planner": "Media Planner",
    "creative_director": "Creative Director",
    "digital_specialist": "Digital Specialist",
}

# Maps state fields to their producing role — sent as agent_result events when newly populated
AGENT_RESULT_FIELDS = [
    ("ceo_direction", "ceo_agent"),
    ("strategy_brief", "brand_strategist"),
    ("media_plan", "media_planner"),
    ("ceo_combined_brief", "ceo_agent"),
    ("creative_routes", "creative_director"),
    ("performance_scores", "digital_specialist"),
]


MAX_ATTACHMENTS = 5


def _build_attachment_context(thread_id: str, attachments: list) -> str:
    """
    Build a metadata-only context string for the Account Director.
    Returns [CANVA FILE REF: ...] and [DRIVE FILE REF: ...] tags — no file content.
    Deduplicates by file_id, caps at MAX_ATTACHMENTS.
    Actual Drive content is fetched separately by _process_drive_attachments.
    """
    seen_ids = set()
    unique_attachments = []
    for att in attachments:
        fid = att.get("file_id")
        if fid and fid not in seen_ids:
            seen_ids.add(fid)
            unique_attachments.append(att)
        if len(unique_attachments) >= MAX_ATTACHMENTS:
            break

    context_blocks = []
    for att in unique_attachments:
        file_id = att.get("file_id")
        file_name = att.get("file_name", "Untitled")
        source = att.get("source", "google_drive")
        if source == "canva":
            context_blocks.append(f"[CANVA FILE REF: \"{file_name}\" (ID: {file_id})]")
        else:
            # Account Director never reads Drive content — metadata only.
            # Content is fetched and processed by _process_drive_attachments.
            context_blocks.append(f"[DRIVE FILE REF: \"{file_name}\" (ID: {file_id})]")

    return "\n\n".join(context_blocks)


def _process_drive_attachments(
    session_id: str,
    drive_refs: list[dict],
    current_state: dict,
) -> dict:
    """
    For each explicitly @ mentioned Drive file, calls process_drive_document — a single
    Claude call on raw PDF bytes that returns both brief field extraction and full document
    content in one pass. No intermediate text extraction; no information loss.

    Always does both — the file is treated as brand/reference context AND a potential brief source.
    Never overwrites a field that already has a value in current_state.

    Returns:
        {
            "brief_updates":     {field: value, ...},  # campaign fields to write to state
            "drive_file_content": str | None,           # full extracted text for pipeline agents
            "drive_file_refs":    list,                 # echo back for state storage
            "coordinator_note":   str,                  # prepended to user message for Account Director
        }
    """
    existing_fields = {
        f: current_state[f]
        for f in CAMPAIGN_FIELDS
        if current_state.get(f)
    }

    all_content_blocks = []
    all_brief_updates = {}
    coordinator_notes = []

    for ref in drive_refs:
        file_id = ref["file_id"]
        file_name = ref.get("file_name", "Untitled")
        mime_type = ref.get("mime_type", "application/pdf")

        try:
            file_bytes = drive_download(session_id, file_id, mime_type)

            # Single call on raw PDF bytes — extracts brief fields AND full content simultaneously.
            # No intermediate text extraction step, so no information loss.
            result = process_drive_document(
                file_bytes=file_bytes,
                file_name=file_name,
                existing_fields=existing_fields,
            )

            if result["full_content"]:
                all_content_blocks.append(f"=== {file_name} ===\n{result['full_content']}")

            # Merge — never overwrite existing values
            for field, value in result.get("extracted", {}).items():
                if value and field not in existing_fields:
                    all_brief_updates[field] = value
                    existing_fields[field] = value  # prevent double-extraction across multiple files

            extracted = result.get("extracted", {})
            missing = result.get("missing", [])
            already_known = result.get("already_known", [])
            summary = result.get("summary", "")

            note_lines = [f'[DRIVE FILE — "{file_name}"]']
            note_lines.append(f"Summary: {summary}")
            if extracted:
                pairs = ", ".join(f'{k}="{v}"' for k, v in extracted.items())
                note_lines.append(f"Brief fields extracted: {pairs}")
            if already_known:
                note_lines.append(f"Already known (skipped): {', '.join(already_known)}")
            if missing:
                note_lines.append(f"Still missing: {', '.join(missing)}")
            note_lines.append(
                "→ The file content has been shared with the specialist team as reference. "
                "Confirm to the user what was extracted, and ask ONLY for the still-missing fields."
            )
            coordinator_notes.append("\n".join(note_lines))

        except Exception as e:
            print(f"[Drive] Failed to process '{file_name}': {e}")
            coordinator_notes.append(
                f'[DRIVE FILE — "{file_name}"]\n'
                f"Failed to read this file ({e}). "
                f"Tell the user it could not be processed and ask them to check the file or re-upload it."
            )

    return {
        "brief_updates": all_brief_updates,
        "drive_file_content": "\n\n".join(all_content_blocks) if all_content_blocks else None,
        "drive_file_refs": drive_refs,
        "coordinator_note": "\n\n".join(coordinator_notes),
    }


def extract_coverage_summary(performance_scores: str) -> dict | None:
    match = re.search(r"## Coverage Summary.*?\n\|.*?\n\|[-| ]+\n((?:\|.*\n?)+)", performance_scores, re.DOTALL)
    if not match:
        return None

    header_match = re.search(r"## Coverage Summary.*?\n(\|.*?\|)\n\|[-| ]+\n", performance_scores, re.DOTALL)
    if not header_match:
        return None

    headers = [h.strip() for h in header_match.group(1).split("|") if h.strip()]
    # headers[0] is "Area", rest are route names e.g. ["Route 1", "Route 2", "Route 3"]
    route_names = headers[1:]
    routes = {r: {} for r in route_names}

    for row_line in match.group(1).strip().splitlines():
        cells = [c.strip() for c in row_line.split("|") if c.strip()]
        if len(cells) < 2:
            continue
        area = cells[0]
        for i, route in enumerate(route_names):
            if i + 1 < len(cells):
                routes[route][area] = cells[i + 1]

    # Normalise keys to route1, route2, route3 ...
    result = {}
    for i, route in enumerate(route_names, start=1):
        key = f"route{i}"
        result[key] = routes[route]

    return result if result else None


def get_current_role(response: dict, prev_state: dict) -> str:
    """Determine which agent role should be attributed to the current response."""
    if response.get("final_routes") and not prev_state.get("final_routes"):
        return "ceo_agent"
    if response.get("performance_scores") and not prev_state.get("performance_scores"):
        return "digital_specialist"
    if response.get("creative_routes") and not prev_state.get("creative_routes"):
        return "creative_director"
    if response.get("ceo_combined_brief") and not prev_state.get("ceo_combined_brief"):
        return "ceo_agent"
    if response.get("media_plan") and not prev_state.get("media_plan"):
        return "media_planner"
    if response.get("strategy_brief") and not prev_state.get("strategy_brief"):
        return "brand_strategist"
    if response.get("ceo_direction") and not prev_state.get("ceo_direction"):
        return "ceo_agent"
    return "account_director"


async def _stream_invoke(
    coordinator, input_data, config, thread_id, websocket,
    log_label="Coordinator agent", role="account_director", stream_tokens=True,
):
    """
    Invoke coordinator via astream.
    stream_tokens=True  → stream pre-tool-call tokens as chunks; once the model
                          starts a tool call, streaming stops and the post-tool
                          summary is left for the outer loop to send as a single message.
    stream_tokens=False → silently collect state (pipeline phase).
    Returns (final_state, did_stream).
    did_stream=True only when content was fully streamed with no tool calls;
    did_stream=False lets the outer loop send last_msg.content as a single message.
    """
    streamed = False
    tool_called = False
    final_state = None

    async for stream_mode, data in coordinator.astream(
        input_data,
        config=config,
        stream_mode=["messages", "values"],
    ):
        if stream_mode == "messages" and stream_tokens and not tool_called:
            msg_chunk, _ = data

            # When the model starts a tool call, stop streaming.
            # The post-tool response (summary) will be sent as a single message.
            if getattr(msg_chunk, "tool_call_chunks", None) or getattr(msg_chunk, "tool_calls", None):
                tool_called = True
                if streamed:
                    await websocket.send_json({"type": "stream_end", "role": role, "thread_id": thread_id})
                    streamed = False
                continue

            content = getattr(msg_chunk, "content", "")
            if isinstance(content, list):
                content = "".join(
                    c.get("text", "") if isinstance(c, dict) else str(c) for c in content
                )
            if isinstance(content, str) and content:
                if not streamed:
                    await websocket.send_json({"type": "stream_start", "role": role, "thread_id": thread_id})
                    streamed = True
                await websocket.send_json({"type": "message", "role": role, "content": content, "thread_id": thread_id, "streaming": True})

        elif stream_mode == "values":
            final_state = data

    if streamed:
        await websocket.send_json({"type": "stream_end", "role": role, "thread_id": thread_id})

    log_token_usage(final_state, log_label)
    # did_stream=True only when we streamed content with no tool calls.
    # If a tool was called, the outer loop must send the post-tool summary as a single message.
    return final_state, streamed and not tool_called


async def handle_websocket(websocket: WebSocket):

    await websocket.accept()
    print("[Client connected]")

    thread_id = None

    # Serialised sender — safe to call from concurrent async contexts (parallel agents)
    _send_lock = asyncio.Lock()

    async def send_message(data: dict) -> None:
        async with _send_lock:
            await websocket.send_json(data)

    try:
        async with AsyncSqliteSaver.from_conn_string("demo.db") as checkpointer:
            coordinator = create_agent(
                model=gemini_model,
                tools=[
                    update_campaign_state,
                    mark_requirements_completed,
                    run_ceo_stage1,
                    run_brand_and_media,
                    run_ceo_stage2,
                    run_creative_and_digital,
                    run_ceo_stage3,
                    select_campaign_route,
                    generate_slides_content,
                ],
                state_schema=CampaignState,
                system_prompt=get_system_prompt(),
                checkpointer=checkpointer,
            )

            state = {"messages": []}
            
            pipeline_running = False

            while True:
                if pipeline_running:
                    # Pipeline is in progress — continue without waiting for user input
                    data_str = '{"text": "continue"}'
                else:
                    data_str = await websocket.receive_text()

                try:
                    payload = json.loads(data_str)
                except json.JSONDecodeError:
                    payload = {"text": data_str}

                # Handle heartbeat
                if payload.get("type") == "ping":
                    continue

                if payload.get("type") == "stop":
                    break

                # Handle explicit thread creation request
                if payload.get("type") == "create_thread":
                    if not thread_id:
                        thread_id = str(uuid.uuid4())
                        user_input = payload.get("text")
                        first_message = ""
                        print(f" ============> user_input: {user_input}")
                        if user_input:
                            if len(user_input) > 15 and user_input[15] == " ":
                                first_message = user_input[:15]
                                print(f" ============> first_message: {first_message}")
                            else:
                                # find next space after index 15
                                next_space_index = user_input.find(" ", 15)
                                if next_space_index == -1:
                                    first_message = user_input
                                    print(f" ============> first_message: {first_message}")
                                else:
                                    first_message = user_input[:next_space_index]
                                    print(f" ============> first_message: {first_message}")

                        session_id = payload.get("session_id") or str(uuid.uuid4())
                        create_thread(thread_id, first_message=first_message, session_id=session_id)
                        register(thread_id, send_message)
                        print(f"\n[Thread Created via event: {thread_id}]\n")

                    await websocket.send_json(
                        {"type": "thread_created", "thread_id": thread_id, "session_id": session_id}
                    )
                    continue

                incoming_thread_id = payload.get("thread_id")
                if incoming_thread_id and thread_id != incoming_thread_id:
                    thread_id = incoming_thread_id
                    register(thread_id, send_message)
                    # Restore in-memory state from checkpoint on reconnect so transition
                    # detectors and pre-flight guards know where the pipeline actually is.
                    _snapshot = await coordinator.aget_state({"configurable": {"thread_id": thread_id}})
                    if _snapshot and _snapshot.values:
                        state = dict(_snapshot.values)

                user_msg = payload.get("text") or payload.get("message")

                attachments = payload.get("attachments", [])
                canva_refs = []
                if attachments and thread_id:
                    attachment_context = _build_attachment_context(thread_id, attachments)
                    if attachment_context:
                        user_msg = f"{attachment_context}\n\n---\n{user_msg}"

                    canva_refs = [
                        {"file_id": att["file_id"], "file_name": att["file_name"]}
                        for att in attachments
                        if att.get("source") == "canva" and att.get("file_id")
                    ]

                    drive_refs = [
                        {
                            "file_id": att["file_id"],
                            "file_name": att.get("file_name", "Untitled"),
                            "mime_type": att.get("mime_type", "application/pdf"),
                        }
                        for att in attachments
                        if att.get("source", "google_drive") == "google_drive" and att.get("file_id")
                    ]
                    if drive_refs:
                        _session_id = get_thread_session_id(thread_id)
                        if _session_id:
                            _drive_names = ", ".join(f'"{r["file_name"]}"' for r in drive_refs)
                            await send_message({
                                "type": "processing_status",
                                "source": "drive",
                                "text": f"Reading {_drive_names} from Google Drive…",
                                "thread_id": thread_id,
                            })
                            drive_result = _process_drive_attachments(
                                _session_id, drive_refs, dict(state)
                            )
                            drive_state_update = {"drive_file_refs": drive_result["drive_file_refs"]}
                            if drive_result["drive_file_content"]:
                                drive_state_update["drive_file_content"] = drive_result["drive_file_content"]
                            drive_state_update.update(drive_result["brief_updates"])
                            await coordinator.aupdate_state(
                                {"configurable": {"thread_id": thread_id}},
                                drive_state_update,
                            )
                            if drive_result["coordinator_note"]:
                                user_msg = f"{drive_result['coordinator_note']}\n\n---\n{user_msg}"

                if canva_refs and thread_id:
                    await coordinator.aupdate_state(
                        {"configurable": {"thread_id": thread_id}},
                        {"canva_file_refs": canva_refs},
                    )

                # if payload.get("opening_message"):
                #     opening_msg = "What's your business name, and what do you sell or offer?"
                #     print(f"Bot: {opening_msg}\n")
                #     state["messages"].append(AIMessage(content=opening_msg))
                #     await websocket.send_json(
                #         {
                #             "type": "message",
                #             "role": "account_director",
                #             "content": opening_msg,
                #             "thread_id": thread_id,
                #         }
                #     )

                if not user_msg:
                    continue

                print(f"You: {user_msg}")

                attachment_meta = [
                    {k: att[k] for k in ("file_id", "file_name", "mime_type", "source") if k in att}
                    for att in attachments
                ]
                human_msg = HumanMessage(
                    content=user_msg,
                    additional_kwargs={"attachments": attachment_meta} if attachment_meta else {},
                )

                # Pre-flight guard: if brief is complete but MI hasn't run yet (e.g. the page
                # was reloaded between mark_requirements_completed and the first pipeline
                # ainvoke), run MI now — before the coordinator can chain into run_ceo_stage1.
                if state.get("brief_complete") and not state.get("market_intelligence_report"):
                    mi_report = await run_market_intelligence_core(thread_id, dict(state))
                    if mi_report:
                        await coordinator.aupdate_state(
                            {"configurable": {"thread_id": thread_id}},
                            {"market_intelligence_report": mi_report},
                        )
                        state = {**state, "market_intelligence_report": mi_report}

                # Stream tokens only during the conversational phase (before pipeline starts).
                # Once brief_complete is set the coordinator sends status summaries —
                # those are short and should arrive as single messages, not as chunks.
                in_pipeline = bool(state.get("brief_complete"))
                response, did_stream = await _stream_invoke(
                    coordinator,
                    {"messages": [human_msg]},
                    {"configurable": {"thread_id": thread_id}},
                    thread_id,
                    websocket,
                    stream_tokens=not in_pipeline,
                )

                # Send response and keep driving the pipeline until it reaches
                # a point that requires genuine user input (concept hub or done).
                while True:
                    # Detect the brief-completion transition and fire MI directly from Python.
                    # This runs once — when brief_complete flips from falsy to True — before
                    # the coordinator is ever asked to call run_ceo_stage1.
                    brief_just_completed = (
                        response.get("brief_complete") and not state.get("brief_complete")
                    )
                    # MI normally runs inside mark_requirements_completed and arrives
                    # already set in response. Only fire here as a fallback for old
                    # threads (pre-fix) where brief_complete=True but MI is absent.
                    if brief_just_completed and not response.get("market_intelligence_report"):
                        mi_report = await run_market_intelligence_core(thread_id, dict(response))
                        if mi_report:
                            await coordinator.aupdate_state(
                                {"configurable": {"thread_id": thread_id}},
                                {"market_intelligence_report": mi_report},
                            )

                    print("\n===== MESSAGE TRACE =====")
                    for msg in response["messages"]:
                        msg_role = msg.type if hasattr(msg, "type") else type(msg).__name__
                        print(f"{msg_role}: {msg.content}")
                    print("=========================\n")

                    current_role = get_current_role(response, state)

                    is_concept_hub = response.get("final_routes") and not state.get("final_routes")

                    # During pipeline, send the actual agent output (not the coordinator summary).
                    # Outside the pipeline, send the coordinator's reply as-is.
                    pipeline_field_sent = False
                    for field, role in AGENT_RESULT_FIELDS:
                        if response.get(field) and not state.get(field):
                            await websocket.send_json(
                                {
                                    "type": "internal_message",
                                    "role": role,
                                    "content": response[field],
                                    "thread_id": thread_id,
                                }
                            )
                            pipeline_field_sent = True


                    last_msg = response["messages"][-1]
                    if last_msg.content and not is_concept_hub and not pipeline_field_sent and not did_stream:
                        content = normalize_llm_output(last_msg.content)
                        await websocket.send_json(
                            {
                                "type": "message",
                                "role": current_role,
                                "content": content,
                                "thread_id": thread_id,
                            }
                        )

                    # Concept Hub — send each of the 3 routes as a separate message
                    if is_concept_hub:
                        print("\n===== CONCEPT HUB — 3 CAMPAIGN ROUTES =====\n")
                        print(response["final_routes"])
                        print("\n===========================================\n")

                        for route in parse_final_routes(response["final_routes"]):
                            await websocket.send_json(
                                {
                                    "type": "concept_hub",
                                    "role": "ceo_agent",
                                    "route_number": route["route_number"],
                                    "content": route["content"],
                                    "thread_id": thread_id,
                                }
                            )

                        coverage_summary = extract_coverage_summary(response["final_routes"])
                        if coverage_summary:
                            await websocket.send_json(
                                {
                                    "type": "coverage_summary",
                                    "role": "ceo_agent",
                                    "content": coverage_summary,
                                    "thread_id": thread_id,
                                }
                            )
                            update_thread(thread_id, coverage_summary=json.dumps(coverage_summary))

                    # Route selected — campaign approved
                    if response.get("selected_route") and not state.get("selected_route"):
                        print(f"\n===== ROUTE SELECTED: {response['selected_route']} =====\n")

                        await websocket.send_json(
                            {
                                "type": "route_selected",
                                "role": "account_director",
                                "content": response["selected_route"],
                                "approved_campaign": response.get("approved_campaign", ""),
                                "thread_id": thread_id,
                            }
                        )

                    # Slides content ready — send final markdown output
                    if response.get("slides_content") and not state.get("slides_content"):
                        print(f"\n===== SLIDES CONTENT READY =====\n")

                        await websocket.send_json(
                            {
                                "type": "slides_content",
                                "role": "presentation_strategist",
                                "content": response["slides_content"],
                                "thread_id": thread_id,
                                "chat_status": "DISABLED",
                            }
                        )

                    state = response

                    # Auto-continue while brief is complete but final routes not compiled yet,
                    # OR route is selected but slides haven't been generated yet.
                    should_continue = (
                        response.get("brief_complete") and not response.get("final_routes")
                    ) or (
                        response.get("selected_route") and not response.get("slides_content")
                    )

                    if should_continue:
                        response, did_stream = await _stream_invoke(
                            coordinator,
                            {"messages": [HumanMessage(content="continue")]},
                            {"configurable": {"thread_id": thread_id}},
                            thread_id,
                            websocket,
                            "Coordinator agent (auto-continue)",
                            stream_tokens=False,
                        )
                    else:
                        break

    except WebSocketDisconnect as e:
        if e.code in (1000, 1001):
            print(f"[Client disconnected cleanly, code={e.code}]")
        else:
            print(f"[Client disconnected unexpectedly, code={e.code}]")

    except Exception as e:
        print(f"Error handling websocket: {e}")
        import traceback

        traceback.print_exc()

    finally:
        if thread_id:
            unregister(thread_id)
        print("[Connection cleanup]")
