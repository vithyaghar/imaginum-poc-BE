#!/usr/bin/env python
# -- coding:utf-8 --
###
# Filename: c:\Projects\imaginum\backend\services\ppt_anthropic_service.py
# Path: c:\Projects\imaginum\backend\services
# Created Date: Wednesday, March 18th 2026, 11:12:06 am
# Author: Vithyaghar M
#
# Copyright (c) 2026 Trinom Digital
###

import anthropic
import os
import base64
from dotenv import load_dotenv
from services.table_service import update_thread

load_dotenv()


async def generate_presentation_with_claude(
    markdown_content: str,
    output_path: str = None,
    return_base64: bool = False,
    thread_id: str = None,
):
    """
    Generate PPT using Claude pptx skill.
    """

    prompt = f"""
You are an expert presentation designer. Create a professional PowerPoint presentation (.pptx file) using the pptx tool.

CONTENT RULES:
- Preserve all key details: numbers, strategies, KPIs, budgets, and timelines.
- Do not aggressively summarize. Expand where needed for clarity.
- Remove any stray whitespace, special characters, or formatting artifacts from the input before use.

SLIDE STRUCTURE:
- Slide 1: Title slide (title + subtitle).
- Total slides: 8 to 12 (add more only if content demands it).
- Group related ideas into logical sections, each as its own slide.
- Maintain clear narrative progression throughout.

SLIDE CONTENT FORMAT:
- Each slide must have a clear, specific title.
- Use bullet points: 3 to 6 per slide, each 1 to 2 lines max.
- Preserve key phrases, metrics, and numbers in bullets.
- Avoid long paragraphs; prefer structured, scannable content.

DESIGN GUIDELINES:
- Choose a bold, topic-appropriate color palette (avoid generic blue).
- Use dark backgrounds for the title and final slide; light backgrounds for content slides.
- Vary slide layouts: two-column, icon rows, stat callouts, and grid cards.
- Every slide must include at least one visual element: icon, shape, chart, or image placeholder.
- Font pairing: use a strong header font (e.g., Calibri Bold or Georgia) with a clean body font (e.g., Calibri 14-16pt).
- No accent lines under titles.

OUTPUT:
- Save the generated file as: presentation.pptx
- The deliverable must be a .pptx file — not raw text or JSON.

INPUT DOCUMENT:
{markdown_content.strip()}
"""

    # Check for cached bytes FIRST — skip API call entirely if found
    cached_bytes = None
    if thread_id:
        try:
            from services.table_service import get_thread

            row = get_thread(thread_id)
            if row and row[0]:
                columns = [
                    "thread_id",
                    "business_name",
                    "status",
                    "campaign_content",
                    "chat_status",
                    "created_at",
                    "updated_at",
                    "generated_count",
                    "is_ppt_generated",
                    "ppt_filename",
                    "first_message",
                    "ppt_bytes",
                ]
                thread_data = dict(zip(columns, row[0]))
                cached_bytes = thread_data.get("ppt_bytes")
                if cached_bytes:
                    print("[PPT] Found cached bytes in DB — skipping API call entirely")
        except Exception as e:
            print(f"[PPT] Could not read cached bytes: {e}")

    if cached_bytes:
        print("===============> Using Cached Bytes — no API call needed")
        file_bytes = cached_bytes
        # Jump straight to saving
        if output_path:
            try:
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(file_bytes)
                print("===============> File Saved from Cache")
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Failed to save cached file to {output_path}: {str(e)}",
                    "output_path": None,
                    "base64": None,
                }

        if thread_id:
            update_thread(
                thread_id,
                ppt_bytes=None,
                status="COMPLETED",
                chat_status="DISABLED",
                is_ppt_generated=True,
                ppt_filename=os.path.basename(output_path) if output_path else None,
            )
            print("[PPT] Cache used — cleared bytes and marked COMPLETED")

        b64 = None
        if return_base64:
            b64 = base64.b64encode(file_bytes).decode()

        return {
            "success": True,
            "slide_count": None,
            "output_path": output_path,
            "base64": b64,
            "error": None,
        }

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return {
            "success": False,
            "error": "ANTHROPIC_API_KEY is not set in environment",
            "output_path": None,
            "base64": None,
        }

    client = anthropic.Anthropic(api_key=api_key)
    print("===============> Client Activated")

    try:
        response = client.beta.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=16000,
            betas=["skills-2025-10-02", "code-execution-2025-08-25"],
            container={
                "skills": [
                    {"type": "anthropic", "skill_id": "pptx", "version": "latest"}
                ]
            },
            tools=[{"type": "code_execution_20250825", "name": "code_execution"}],
            messages=[{"role": "user", "content": prompt}],
        )
        print("===============> Response Generated")
    except Exception as e:
        print(f"===============> Error ============ \n {e}")
        return {
            "success": False,
            "error": f"Anthropic API error: {str(e)}",
            "output_path": None,
            "base64": None,
        }

    # Extract file blocks — only blocks with a file_ prefixed ID or explicit file_id attribute
    # file_blocks = []
    # print(f"===============> Extracting File Blocks \n {response}")
    # for block in response.content:
    #     block_type = getattr(block, "type", "")

    #     # Direct document/file block types
    #     if block_type in ("document", "files_api_file", "tool_result_document"):
    #         file_blocks.append(block)
    #         continue

    #     # Check for file_id attribute explicitly (not generic .id)
    #     file_id_attr = getattr(block, "file_id", None)
    #     if file_id_attr and str(file_id_attr).startswith("file_"):
    #         file_blocks.append(block)
    #         continue

    #     # Check .id but ONLY if it starts with file_ prefix
    #     id_attr = getattr(block, "id", None)
    #     if id_attr and str(id_attr).startswith("file_"):
    #         file_blocks.append(block)
    #         continue

    # if not file_blocks:
    #     print(
    #         f"[PPT] No file block found. Block types in response: "
    #         f"{[getattr(b, 'type', type(b).__name__) for b in response.content]}"
    #     )
    #     return {
    #         "success": False,
    #         "error": "No file found in response content",
    #         "output_path": None,
    #         "base64": None,
    #     }

    # # Always log full block details to identify correct file ID field
    # print("===============> Full response content blocks:")
    # for i, block in enumerate(response.content):
    #     print(f"  Block {i}: type={getattr(block, 'type', type(block).__name__)}")
    #     for attr in ["id", "file_id", "name", "media_type", "url", "input", "output"]:
    #         val = getattr(block, attr, None)
    #         if val is not None:
    #             print(f"    .{attr} = {str(val)[:120]}")
    #     # Also print raw dict if available
    #     if hasattr(block, "__dict__"):
    #         print(f"    __dict__ keys: {list(block.__dict__.keys())}")
    #     if hasattr(block, "model_dump"):
    #         try:
    #             print(f"    model_dump: {block.model_dump()}")
    #         except Exception:
    #             pass

    # if not file_blocks:
    #     return {
    #         "success": False,
    #         "error": "No file found in response content",
    #         "output_path": None,
    #         "base64": None,
    #     }

    # print("===============> File Blocks Found")
    # ppt_block = file_blocks[0]

    # # Prefer explicit file_id attribute, fallback to id only if file_ prefixed
    # file_id = getattr(ppt_block, "file_id", None)
    # if not file_id:
    #     id_attr = getattr(ppt_block, "id", None)
    #     if id_attr and str(id_attr).startswith("file_"):
    #         file_id = id_attr

    # if not file_id:
    #     print(
    #         f"[PPT] File block found but no valid file_ ID: {ppt_block.model_dump() if hasattr(ppt_block, 'model_dump') else ppt_block}"
    #     )
    #     return {
    #         "success": False,
    #         "error": "File block found but has no valid file ID",
    #         "output_path": None,
    #         "base64": None,
    #     }

    # print(f"===============> File ID: {file_id}")
    # -----------------------------------------------------------------------------------------------------------------------------------------------------
    # Extract file_id — search nested bash_code_execution_output blocks
    file_id = None

    for block in response.content:
        block_type = getattr(block, "type", "")

        # Direct file_ prefixed id on the block itself
        for attr in ["file_id", "id"]:
            val = getattr(block, attr, None)
            if val and str(val).startswith("file_"):
                file_id = val
                break

        if file_id:
            break

        # Nested: bash_code_execution_tool_result → content list → bash_code_execution_output
        if block_type == "bash_code_execution_tool_result":
            nested_content = getattr(block, "content", None)
            if isinstance(nested_content, list):
                for nested_block in nested_content:
                    nested_file_id = getattr(nested_block, "file_id", None)
                    if nested_file_id and str(nested_file_id).startswith("file_"):
                        file_id = nested_file_id
                        break
            elif nested_content is not None:
                # Sometimes content is a single object, not a list
                nested_file_id = getattr(nested_content, "file_id", None)
                if nested_file_id and str(nested_file_id).startswith("file_"):
                    file_id = nested_file_id

        if file_id:
            break

    if not file_id:
        print(
            f"[PPT] No file_ ID found anywhere in response. Block types: "
            f"{[getattr(b, 'type', type(b).__name__) for b in response.content]}"
        )
        return {
            "success": False,
            "error": "No file found in response content",
            "output_path": None,
            "base64": None,
        }

    print(f"===============> File ID found: {file_id}")

    try:
        print("===============> Downloading File")
        file_bytes = client.beta.files.download(file_id).read()
        print("===============> File Downloaded")
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to download generated file: {str(e)}",
            "output_path": None,
            "base64": None,
        }

    # Cache raw bytes in DB immediately after download, before saving to disk
    if thread_id:
        try:
            print("===============> Caching Bytes")
            update_thread(thread_id, ppt_bytes=file_bytes)
            print("===============> [PPT] AI response bytes cached in DB")
        except Exception as e:
            print(f"===============> [PPT] Warning: Could not cache bytes in DB: {e}")

    # Save to the caller-specified output path
    if output_path:
        print("===============> Saving File")
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(file_bytes)
            print("===============> File Saved")
        except Exception as e:
            print("===============> Error Saving File")
            return {
                "success": False,
                "error": f"Failed to save file to {output_path}: {str(e)}",
                "output_path": None,
                "base64": None,
            }

    # Clear cached bytes from DB after successful save to disk
    if thread_id:
        try:
            update_thread(
                thread_id,
                ppt_bytes=None,
                status="COMPLETED",
                chat_status="DISABLED",
                is_ppt_generated=True,
                ppt_filename=os.path.basename(output_path) if output_path else None,
            )
            print("[PPT] File saved to disk — cleared cached bytes from DB")
        except Exception as e:
            print(f"[PPT] Warning: Could not update thread after save: {e}")

    b64 = None
    if return_base64:
        print("===============> Encoding File")
        b64 = base64.b64encode(file_bytes).decode()
        print("===============> File Encoded")

    return {
        "success": True,
        "slide_count": None,
        "output_path": output_path,
        "base64": b64,
        "error": None,
    }
