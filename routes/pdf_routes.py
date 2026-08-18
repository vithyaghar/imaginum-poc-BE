#!/usr/bin/env python
# -- coding:utf-8 --
###
#  Trinom Digital Pvt Ltd ("COMPANY") CONFIDENTIAL
#  Copyright (c) 2026 Trinom Digital Pvt Ltd, All rights reserved
#
#  File: \imaginum_POC_BE\routes\pdf_routes.py
#  Project: ps1
###

from fastapi import APIRouter, File, UploadFile

from controllers.pdf_controller import upload_pdf_controller

router = APIRouter(
    prefix="/api/threads",
    tags=["PDF Upload"],
)


@router.post("/{thread_id}/upload-pdf")
async def upload_pdf(thread_id: str, file: UploadFile = File(...)):
    """
    Upload a PDF for an existing thread.

    Extracts campaign information from the PDF, identifies which required fields
    are present and which are missing, pre-populates the LangGraph state, and
    returns a summary so the client knows what the assistant will ask next.
    """
    return await upload_pdf_controller(thread_id, file)