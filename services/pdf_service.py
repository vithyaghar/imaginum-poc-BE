#!/usr/bin/env python
# -- coding:utf-8 --
###
#  Trinom Digital Pvt Ltd ("COMPANY") CONFIDENTIAL
#  Copyright (c) 2026 Trinom Digital Pvt Ltd, All rights reserved
#
#  File: \imaginum_POC_BE\services\pdf_service.py
#  Project: ps1
###

import base64
import json
import os

import anthropic

CAMPAIGN_FIELDS = [
    "business_name",
    "business_description",
    "campaign_goal",
    "target_audience",
    "key_message",
    "cta",
    "budget",
    "platforms",
    "geography",
    "language",
    "duration",
    "tone",
    "kpis",
    "restrictions",
]

FIELD_LABELS = {
    "business_name": "Business name",
    "business_description": "What the business does / sells",
    "campaign_goal": "Campaign goal",
    "target_audience": "Target audience",
    "key_message": "Key message",
    "cta": "Call to action (CTA)",
    "budget": "Total budget",
    "platforms": "Advertising platforms",
    "geography": "Geography / location",
    "language": "Language",
    "duration": "Campaign duration",
    "tone": "Tone / style of ads",
    "kpis": "KPIs / success metrics",
    "restrictions": "Restrictions / things to avoid",
}


def analyze_pdf_with_claude(file_bytes: bytes) -> dict:
    """
    Send the PDF directly to Claude (native document support) and extract
    campaign brief fields from it.

    Returns:
        {
            "extracted": { field: value, ... },   # fields found in the PDF
            "missing":   [ field, ... ],           # fields not found
            "summary":   str                       # human-readable summary for the client
        }
    """
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    field_list = "\n".join(f'- "{k}": {v}' for k, v in FIELD_LABELS.items())

    prompt = f"""You are an expert at extracting marketing campaign information from documents.

The client has uploaded a PDF as part of their campaign brief.

Your task:
1. Read the document carefully.
2. Extract values for as many of these campaign fields as you can find:
{field_list}

Rules:
- Only extract information that is clearly and explicitly stated in the document.
- Do NOT infer or guess values that are not present.
- If a field is not mentioned at all, leave it out of the extracted object.
- For "platforms", return a comma-separated string (e.g. "Instagram, Facebook, Google Search").
- For "budget", include the currency and amount as a string (e.g. "$50,000").
- Keep values concise but complete.

Respond ONLY with a valid JSON object in this exact format (no markdown, no explanation):
{{
  "extracted": {{
    "<field_name>": "<value>",
    ...
  }},
  "missing": ["<field_name>", ...],
  "summary": "<2-3 sentence plain-language summary of what was found and what still needs to be collected>"
}}"""

    pdf_b64 = base64.standard_b64encode(file_bytes).decode("utf-8")

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            }
        ],
    )

    raw = message.content[0].text.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            result = json.loads(raw.strip())
        else:
            raise

    result.setdefault("extracted", {})
    result.setdefault(
        "missing",
        [f for f in CAMPAIGN_FIELDS if f not in result["extracted"]],
    )
    result.setdefault("summary", "PDF processed.")

    return result


def process_drive_document(
    file_bytes: bytes,
    file_name: str,
    existing_fields: dict,
) -> dict:
    """
    Single Claude call on raw PDF bytes that returns both brief field extraction
    and full document content simultaneously. Replaces the two-step
    extract_document_content → analyze_drive_document flow.

    Reads the raw PDF natively (same quality as analyze_pdf_with_claude), so no
    information is lost through an intermediate text extraction step.

    Returns:
        {
            "extracted":     {field: value, ...},   # newly found brief fields only
            "missing":       [field, ...],           # not found AND not already known
            "already_known": [field, ...],           # skipped because already in state
            "summary":       str,                    # what the document contains
            "full_content":  str                     # comprehensive plain-text for pipeline agents
        }
    """
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    fields_to_find = {k: v for k, v in FIELD_LABELS.items() if k not in existing_fields}

    known_block = (
        "\n".join(f'  - {k}: "{v}"' for k, v in existing_fields.items())
        if existing_fields else "  (none yet)"
    )
    target_block = (
        "\n".join(f'  - "{k}": {v}' for k, v in fields_to_find.items())
        if fields_to_find else "  (all fields already collected)"
    )
    already_known_list = ", ".join(f'"{k}"' for k in existing_fields)

    prompt = f"""You are analyzing a document attached by a user in a marketing campaign planning system.
Document name: {file_name}

TASK 1 — CAMPAIGN BRIEF FIELD EXTRACTION:
Already collected (do NOT extract or overwrite these):
{known_block}

Extract ONLY the following fields, and ONLY if they are clearly and explicitly stated in the document.
Do NOT infer or guess. If a field is ambiguous, omit it — the Account Director will ask the user.

Fields to extract:
{target_block}

TASK 2 — FULL DOCUMENT CONTENT:
Return a comprehensive, structured plain-text version of the entire document.
Preserve all headings, data points, brand specifications, key findings, statistics, and notable details.
Be thorough — this content will be used as reference by specialist agents during campaign generation.

Respond ONLY with valid JSON (no markdown fences):
{{
  "extracted": {{}},
  "missing": [],
  "already_known": [{already_known_list}],
  "summary": "2-3 sentence plain-language description of what this document contains",
  "full_content": "comprehensive structured plain-text of the full document"
}}"""

    pdf_b64 = base64.standard_b64encode(file_bytes).decode("utf-8")

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=6000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            }
        ],
    )

    raw = message.content[0].text.strip()
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            result = json.loads(raw.strip())
        else:
            raise

    result.setdefault("extracted", {})
    result.setdefault(
        "missing",
        [f for f in fields_to_find if f not in result.get("extracted", {})],
    )
    result.setdefault("already_known", list(existing_fields.keys()))
    result.setdefault("summary", "Document processed.")
    result.setdefault("full_content", "")
    return result


def analyze_drive_document(
    extracted_text: str,
    file_name: str,
    existing_fields: dict,
) -> dict:
    """
    Extract campaign brief fields from a Drive document's extracted text.
    Skips fields already present in existing_fields — will not overwrite known values.

    Returns:
        {
            "extracted":     {field: value, ...},   # newly found fields only
            "missing":       [field, ...],           # not found AND not already known
            "already_known": [field, ...],           # skipped because already in state
            "summary":       str                     # what the document contains
        }
    """
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    fields_to_find = {k: v for k, v in FIELD_LABELS.items() if k not in existing_fields}

    known_block = (
        "\n".join(f'  - {k}: "{v}"' for k, v in existing_fields.items())
        if existing_fields else "  (none yet)"
    )
    target_block = (
        "\n".join(f'  - "{k}": {v}' for k, v in fields_to_find.items())
        if fields_to_find else "  (all fields already collected)"
    )
    already_known_list = ", ".join(f'"{k}"' for k in existing_fields)

    prompt = f"""You are analyzing a document attached by a user in a marketing campaign planning system.

ALREADY COLLECTED — do NOT extract or overwrite these:
{known_block}

Your task: extract ONLY the following campaign fields from the document, and ONLY if they are clearly and explicitly stated.
Do NOT infer or guess. If a field is ambiguous or vague, omit it — the Account Director will ask the user.

Fields to extract:
{target_block}

Document name: {file_name}

Document content:
{extracted_text}

Respond ONLY with valid JSON (no markdown fences):
{{
  "extracted": {{}},
  "missing": [],
  "already_known": [{already_known_list}],
  "summary": "2-3 sentence plain-language description of what this document contains"
}}"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            result = json.loads(raw.strip())
        else:
            raise

    result.setdefault("extracted", {})
    result.setdefault(
        "missing",
        [f for f in fields_to_find if f not in result.get("extracted", {})],
    )
    result.setdefault("already_known", list(existing_fields.keys()))
    result.setdefault("summary", "Document processed.")
    return result


def extract_content_from_images(image_bytes_list: list, file_name: str) -> str:
    """
    Extract key content from page images using Claude Vision.

    Used as a fallback when a Canva design cannot be exported as PDF
    (e.g., PDFs imported/uploaded into Canva that the Export API rejects).
    Accepts any number of page images; all are sent in a single Claude call.
    """
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    content = []
    for img_bytes in image_bytes_list:
        media_type = "image/png" if img_bytes[:4] == b'\x89PNG' else "image/jpeg"
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.standard_b64encode(img_bytes).decode("utf-8"),
            },
        })

    content.append({
        "type": "text",
        "text": (
            f"The file name is: {file_name}\n\n"
            "These are page images from a brand design file. "
            "Extract and return the key content as structured plain text. "
            "Preserve headings, brand colors (hex/RGB values), fonts, tone guidelines, "
            "logo specifications, imagery guidelines, and all other brand-related information. "
            "Be thorough — this content will be used by specialist agents during campaign generation. "
            "Do not add commentary — just the structured content."
        ),
    })

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": content}],
    )
    return message.content[0].text.strip()


def extract_document_content(file_bytes: bytes, file_name: str) -> str:
    """
    Extract the key content from a document as plain text.

    Used when a user attaches a Drive file via @ mention — the returned
    string is injected into the message context for the coordinator to read.
    Unlike analyze_pdf_with_claude(), this is not campaign-field specific.
    """
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    pdf_b64 = base64.standard_b64encode(file_bytes).decode("utf-8")

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            f"The file name is: {file_name}\n\n"
                            "Extract and return the key content from this document as structured plain text. "
                            "Preserve headings, important values, lists, and any brand-related information. "
                            "Be thorough but concise. Do not add commentary or explanation — just the content."
                        ),
                    },
                ],
            }
        ],
    )

    return message.content[0].text.strip()