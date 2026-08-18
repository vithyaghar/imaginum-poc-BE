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
#  File: \business_commnunication\routes\thread_routes.py
#  Project: ps1
#  Created Date: Monday, March 9th 2026, 11:36:59 am
#  Author: Naveena J <naveena@codestax.ai>
#  -----
#  Last Modified:
#  Modified By:
#  -----
#  HISTORY:
#  Date         By  Comments
#  ---------------------------------------------------------------------------
###
from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel
from controllers.thread_controller import thread_controller
from controllers.pdf_controller import upload_pdf_controller


class UpdateTitleRequest(BaseModel):
    business_name: str

router = APIRouter(prefix="/threads", tags=["Threads"])


@router.get("")
async def get_threads():
    return await thread_controller.get_all_threads_controller()


@router.get("/{thread_id}")
async def get_thread(thread_id: str):
    return await thread_controller.get_thread_by_id_controller(thread_id)


@router.patch("/{thread_id}/title")
async def update_thread_title(thread_id: str, body: UpdateTitleRequest):
    return await thread_controller.update_thread_title_controller(thread_id, body.business_name)


@router.post("/{thread_id}/upload-pdf")
async def upload_pdf(thread_id: str, file: UploadFile = File(...)):
    """
    Upload a PDF for an existing thread.

    Extracts campaign information from the PDF, identifies which required fields
    are present and which are missing, pre-populates the LangGraph state, and
    returns a summary so the client knows what the assistant will ask next.
    """
    return await upload_pdf_controller(thread_id, file)
