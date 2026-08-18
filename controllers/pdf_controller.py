#!/usr/bin/env python
# -- coding:utf-8 --
###
#  Trinom Digital Pvt Ltd ("COMPANY") CONFIDENTIAL
#  Copyright (c) 2026 Trinom Digital Pvt Ltd, All rights reserved
#
#  File: \imaginum_POC_BE\controllers\pdf_controller.py
#  Project: ps1
###

import json

from fastapi import HTTPException, UploadFile
from langchain.messages import HumanMessage
from langchain.agents import create_agent
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from business_communication import (
    CampaignState,
    update_campaign_state,
    mark_requirements_completed,
    run_ceo_stage1,
    run_brand_strategist,
    run_media_planner,
    run_ceo_stage2,
    run_creative_director,
    run_digital_specialist,
    run_ceo_stage3,
    select_campaign_route,
    SYSTEM_PROMPT,
    normalize_llm_output,
    gemini_model,
)
from services.pdf_service import analyze_pdf_with_claude
from services.table_service import get_thread, update_thread


MAX_PDF_SIZE_MB = 10
MAX_PDF_SIZE_BYTES = MAX_PDF_SIZE_MB * 1024 * 1024


async def upload_pdf_controller(thread_id: str, file: UploadFile):
    # Validate thread exists
    rows = get_thread(thread_id)
    if not rows:
        raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found.")

    thread = rows[0]
    thread_status = thread[2] if isinstance(thread, (list, tuple)) else thread["status"]
    if thread_status != "GATHERING_REQUIREMENTS":
        raise HTTPException(
            status_code=400,
            detail="PDF upload is only allowed while the thread is still gathering requirements.",
        )

    # Validate file type
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    file_bytes = await file.read()
    if len(file_bytes) > MAX_PDF_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"PDF exceeds the {MAX_PDF_SIZE_MB} MB size limit.",
        )

    # Step 1: Send PDF directly to Claude — it reads the document natively
    try:
        analysis = analyze_pdf_with_claude(file_bytes)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Claude analysis failed: {str(e)}"
        )

    extracted: dict = analysis.get("extracted", {})
    missing: list = analysis.get("missing", [])
    summary: str = analysis.get("summary", "")

    # Step 3: Persist pdf_context in the threads table for reference
    update_thread(thread_id, pdf_context=json.dumps(extracted))

    assistant_message = None

    # Step 4: Inject extracted fields directly into LangGraph checkpoint via aupdate_state.
    # This bypasses the LLM tool-call path entirely — Gemini Flash Lite was unreliably
    # omitting fields (e.g. language) when calling update_campaign_state with 13 params.
    # After the state write, invoke the coordinator only to generate the user-facing message.
    if extracted:
        config = {"configurable": {"thread_id": thread_id}}

        async with AsyncSqliteSaver.from_conn_string("demo.db") as checkpointer:
            coordinator = create_agent(
                model=gemini_model,
                tools=[
                    update_campaign_state,
                    mark_requirements_completed,
                    run_ceo_stage1,
                    run_brand_strategist,
                    run_media_planner,
                    run_ceo_stage2,
                    run_creative_director,
                    run_digital_specialist,
                    run_ceo_stage3,
                    select_campaign_route,
                ],
                state_schema=CampaignState,
                system_prompt=SYSTEM_PROMPT,
                checkpointer=checkpointer,
            )

            # Write all extracted fields straight into the checkpoint — guaranteed complete.
            await coordinator.aupdate_state(config, extracted)

            # Replicate update_campaign_state side-effect: persist business_name to threads table.
            if "business_name" in extracted:
                update_thread(thread_id, business_name=extracted["business_name"])

            # Now ask the coordinator only to generate the greeting/follow-up question.
            # State is already populated — explicitly tell it NOT to call update_campaign_state.
            if missing:
                missing_labels = ", ".join(f.replace("_", " ") for f in missing)
                field_summary = "\n".join(
                    f"- {k.replace('_', ' ').title()}: {v}"
                    for k, v in extracted.items()
                )
                prompt = (
                    f"[PDF UPLOAD — SYSTEM CONTEXT]\n"
                    f"The client has uploaded a PDF. All extracted fields have already been saved "
                    f"to state — do NOT call update_campaign_state.\n"
                    f"Here are the extracted values:\n{field_summary}\n\n"
                    f"The following field(s) still need to be collected from the client: {missing_labels}.\n\n"
                    f"Greet the client warmly, confirm what was found (using the actual values above, "
                    f"not placeholders), and ask ONLY for the missing field(s) listed above."
                )
            else:
                field_summary = "\n".join(
                    f"- {k.replace('_', ' ').title()}: {v}"
                    for k, v in extracted.items()
                )
                prompt = (
                    f"[PDF UPLOAD — SYSTEM CONTEXT]\n"
                    f"The client has uploaded a PDF. All required campaign fields have been extracted "
                    f"and saved — do NOT call update_campaign_state.\n\n"
                    f"Here are the extracted values:\n{field_summary}\n\n"
                    f"Greet the client warmly, confirm everything was found, present a brief summary "
                    f"using the actual values above (not placeholders), "
                    f"and ask: 'Does everything look right? Reply yes, looks good to proceed.'"
                )

            pdf_response = await coordinator.ainvoke(
                {
                    "messages": [
                        HumanMessage(
                            content=prompt,
                            additional_kwargs={
                                "attachments": [
                                    {
                                        "file_name": file.filename,
                                        "mime_type": "application/pdf",
                                        "source": "upload",
                                    }
                                ]
                            },
                        )
                    ]
                },
                config=config,
            )

        for msg in reversed(pdf_response["messages"]):
            if hasattr(msg, "type") and msg.type == "ai" and msg.content:
                text = normalize_llm_output(msg.content).strip()
                if text:
                    assistant_message = text
                    break

    return {
        "status": "ok",
        "thread_id": thread_id,
        "extracted_fields": extracted,
        "missing_fields": missing,
        "summary": summary,
        "assistant_message": assistant_message,
        "message": (
            f"PDF processed successfully. "
            f"Found {len(extracted)} of 14 required fields. "
            f"{'The assistant will now ask for the remaining details.' if missing else 'All fields collected — proceed to confirm the brief.'}"
        ),
    }