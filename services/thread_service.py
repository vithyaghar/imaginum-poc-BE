#!/usr/bin/env python
# -- coding:utf-8 --
###
#  Trinom Digital Pvt Ltd ("COMPANY") CONFIDENTIAL
#  Copyright (c) 2026 Trinom Digital Pvt Ltd, All rights reserved
#
#  NOTICE: All information contained herein is, and remains the property
#  of COMPANY. The intellectual and technical concepts contained herein are
#  proprietary to COMPANY and may be protected by law.
#
#  File: \business_commnunication\services\thread_service.py
#  Project: ps1
#  Created Date: Monday, March 9th 2026, 11:34:46 am
#  Author: Naveena J <naveena@codestax.ai>
#  -----
#  Last Modified:
#  Modified By:
#  -----
#  HISTORY:
#  Date         By  Comments
#  ---------------------------------------------------------------------------
###
import json
from database.database import get_db_connection
from langgraph.checkpoint.sqlite import SqliteSaver
from business_communication import parse_final_routes

ROLE_LIST = {
    "account_director": "Account Director",
    "ceo_agent": "CEO",
    "brand_strategist": "Brand Strategist",
    "media_planner": "Media Planner",
    "creative_director": "Creative Director",
    "digital_specialist": "Digital Specialist",
    "presentation_strategist": "Presentation Strategist",
    "user": "User",
}

AGENT_NAME_TO_ROLE_KEY = {
    "account_manager_requirements_gathering": "account_director",
    "account_director": "account_director",
    "ceo_agent": "ceo_agent",
    "brand_strategist": "brand_strategist",
    "media_planner": "media_planner",
    "creative_director": "creative_director",
    "digital_specialist": "digital_specialist",
}

# Maps tool name → list of (ws_type, role_key, state_field) entries to emit.
# Parallel tools emit one entry per agent they ran.
# run_ceo_stage3 / select_campaign_route / generate_slides_content are handled inline.
TOOL_TO_MESSAGES = {
    "run_ceo_stage1": [
        ("internal_message", "ceo_agent", "ceo_direction"),
    ],
    "run_brand_strategist": [
        ("internal_message", "brand_strategist", "strategy_brief"),
    ],
    "run_media_planner": [
        ("internal_message", "media_planner", "media_plan"),
    ],
    "run_brand_and_media": [
        ("internal_message", "brand_strategist", "strategy_brief"),
        ("internal_message", "media_planner", "media_plan"),
    ],
    "run_ceo_stage2": [
        ("internal_message", "ceo_agent", "ceo_combined_brief"),
    ],
    "run_creative_director": [
        ("internal_message", "creative_director", "creative_routes"),
    ],
    "run_digital_specialist": [
        ("internal_message", "digital_specialist", "performance_scores"),
    ],
    "run_creative_and_digital": [
        ("internal_message", "creative_director", "creative_routes"),
        ("internal_message", "digital_specialist", "performance_scores"),
    ],
}


class ThreadService:
    def __init__(self):
        self.conn = get_db_connection()
    
    def normalize_llm_content(self, content):
        if isinstance(content, list):
            return " ".join(
                item.get("text", "") if isinstance(item, dict) else str(item)
                for item in content
            )
        if isinstance(content, str):
            return content
        return str(content)

    def update_thread_title(self, thread_id: str, business_name: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute("SELECT thread_id FROM threads WHERE thread_id = ?", (thread_id,))
        if not cursor.fetchone():
            return False
        from services.table_service import update_thread
        update_thread(thread_id, business_name=business_name)
        return True

    def get_all_threads(self):

        cursor = self.conn.cursor()

        cursor.execute("""
            SELECT
                thread_id,
                business_name,
                status,
                chat_status,
                campaign_content,
                created_at,
                updated_at,
                first_message
            FROM threads
            ORDER BY updated_at DESC
        """)

        rows = cursor.fetchall()

        threads = []

        for row in rows:
            threads.append(
                {
                    "thread_id": row["thread_id"],
                    "business_name": row["business_name"],
                    "status": row["status"],
                    "chat_status": row["chat_status"],
                    "created_at": row["created_at"],
                    "campaign_content": row["campaign_content"],
                    "updated_at": row["updated_at"],
                    "first_message": row["first_message"],
                }
            )

        return threads

    def get_thread_by_id(self, thread_id: str):

        cursor = self.conn.cursor()

        cursor.execute(
            """
            SELECT
                thread_id,
                business_name,
                status,
                chat_status,
                campaign_content,
                created_at,
                updated_at,
                is_ppt_generated,
                ppt_filename,
                slides_content,
                coverage_summary
            FROM threads
            WHERE thread_id = ?
        """,
            (thread_id,),
        )

        row = cursor.fetchone()

        if not row:
            return None

        raw_coverage = row["coverage_summary"]
        try:
            coverage_summary = json.loads(raw_coverage) if raw_coverage else None
        except (ValueError, TypeError):
            coverage_summary = None

        thread = {
            "thread_id": row["thread_id"],
            "business_name": row["business_name"],
            "status": row["status"],
            "chat_status": row["chat_status"],
            "campaign_content": row["campaign_content"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "is_ppt_generated": bool(row["slides_content"]),
            "slides_content": row["slides_content"],
            "coverage_summary": coverage_summary,
        }

        chat_history = []

        with SqliteSaver.from_conn_string("demo.db") as checkpointer:
            checkpoint = checkpointer.get({"configurable": {"thread_id": thread_id}})

            if checkpoint:
                channel_values = checkpoint["channel_values"]
                messages = channel_values.get("messages", [])

                seen_tools = set()
                pipeline_started = False

                for msg in messages:
                    if msg.type == "tool":
                        tool_name = getattr(msg, "name", None)

                        if tool_name == "mark_requirements_completed":
                            pipeline_started = True

                        if tool_name in seen_tools:
                            continue
                        seen_tools.add(tool_name)

                        if tool_name in ("update_campaign_state", "mark_requirements_completed"):
                            continue

                        # --- Concept Hub: 3 route cards ---
                        if tool_name == "run_ceo_stage3":
                            final_routes_content = channel_values.get("final_routes")
                            if final_routes_content:
                                for route in parse_final_routes(final_routes_content):
                                    chat_history.append({
                                        "type": "concept_hub",
                                        "role": "ceo_agent",
                                        "role_label": ROLE_LIST["ceo_agent"],
                                        "content": route["content"],
                                        "route_number": route["route_number"],
                                    })
                            continue

                        # --- Route selected / slides: returned as top-level thread fields, not in chat history ---
                        if tool_name in ("select_campaign_route", "generate_slides_content"):
                            continue

                        # --- Generic agent outputs (single or parallel tools) ---
                        mappings = TOOL_TO_MESSAGES.get(tool_name)
                        if mappings:
                            for msg_type, role_key, field in mappings:
                                field_content = channel_values.get(field)
                                if field_content:
                                    chat_history.append({
                                        "type": msg_type,
                                        "role": role_key,
                                        "role_label": ROLE_LIST.get(role_key, role_key),
                                        "content": self.normalize_llm_content(field_content),
                                    })
                        continue

                    # --- Human and AI messages (brief collection phase only) ---
                    if pipeline_started:
                        continue

                    content = self.normalize_llm_content(msg.content)
                    msg_attachments = (msg.additional_kwargs or {}).get("attachments") or []

                    # Skip messages that have neither text nor attachments
                    if not content.strip() and not msg_attachments:
                        continue

                    if msg.type == "human":
                        # [PDF UPLOAD — SYSTEM CONTEXT] prompts are internal coordinator directives.
                        # The same HumanMessage carries the attachment metadata in additional_kwargs,
                        # so emit an attachment-only entry (no system-prompt text) when present.
                        if content.startswith("[PDF UPLOAD"):
                            if msg_attachments:
                                chat_history.append({
                                    "type": "message",
                                    "role": "user",
                                    "role_label": "User",
                                    "content": "",
                                    "attachments": msg_attachments,
                                })
                            continue
                        human_entry = {
                            "type": "message",
                            "role": "user",
                            "role_label": "User",
                            "content": content,
                        }
                        if msg_attachments:
                            human_entry["attachments"] = msg_attachments
                        chat_history.append(human_entry)
                    elif msg.type == "ai":
                        agent_name = (
                            getattr(msg, "name", None)
                            or msg.additional_kwargs.get("agent")
                            or "account_manager_requirements_gathering"
                        )
                        role = AGENT_NAME_TO_ROLE_KEY.get(agent_name, "account_director")
                        chat_history.append({
                            "type": "message",
                            "role": role,
                            "role_label": ROLE_LIST.get(role, "Account Director"),
                            "content": content,
                        })

        return {"thread": thread, "chat_history": chat_history}
