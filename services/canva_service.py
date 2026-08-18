#!/usr/bin/env python
# -- coding:utf-8 --
###
# Filename: services/canva_service.py
# Author: Vithyaghar M
#
# Copyright (c) 2026 Trinom Digital
###

import hashlib
import os
import secrets
import time
from base64 import urlsafe_b64encode
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

import requests

from services.table_service import (
    save_connector_tokens,
    get_connector_tokens,
    delete_connector_tokens,
)

PROVIDER = "canva"

CANVA_AUTH_URL = "https://www.canva.com/api/oauth/authorize"
CANVA_TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"
CANVA_API_BASE = "https://api.canva.com/rest/v1"
CANVA_SCOPES = "design:meta:read design:content:read asset:read profile:read"

# In-memory PKCE store: session_id → code_verifier.
# Populated on auth-URL generation, consumed on callback.
# Acceptable for a single-process server; a Redis/DB store would be needed for
# multi-process deployments or servers that restart mid-auth-flow.
_pkce_store: dict[str, str] = {}

_EXPORT_POLL_INTERVAL = 2.0   # seconds between export-status polls
_EXPORT_MAX_POLLS = 20        # maximum polls (~40 s total)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _generate_pkce() -> tuple[str, str]:
    """Return a (code_verifier, code_challenge) PKCE pair (S256)."""
    code_verifier = secrets.token_urlsafe(96)  # 128-char base64url string
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def _bearer(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


def _refresh_access_token(session_id: str, refresh_token: str) -> str:
    """Call the Canva token endpoint with grant_type=refresh_token, persist, and return new token."""
    resp = requests.post(
        CANVA_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": os.getenv("CANVA_CLIENT_ID"),
            "client_secret": os.getenv("CANVA_CLIENT_SECRET"),
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    data = resp.json()
    new_access = data["access_token"]
    new_refresh = data.get("refresh_token", refresh_token)
    expires_in = data.get("expires_in", 3600)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    save_connector_tokens(PROVIDER, session_id, new_access, new_refresh, expiry.isoformat())
    return new_access


def _get_valid_token(session_id: str) -> str:
    """Return a non-expired access token for this session, refreshing if needed."""
    token_row = get_connector_tokens(PROVIDER, session_id)
    if not token_row:
        raise ValueError(f"No Canva tokens found for session '{session_id}'")

    expiry_str = token_row["token_expiry"]
    if expiry_str:
        expiry = datetime.fromisoformat(expiry_str)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= expiry - timedelta(seconds=60):
            return _refresh_access_token(session_id, token_row["refresh_token"])

    return token_row["access_token"]


def _epoch_to_iso(epoch: int | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Auth — called by canva_auth_controller
# ---------------------------------------------------------------------------

def build_auth_url(session_id: str) -> str:
    """Generate the Canva OAuth consent URL for the given session."""
    code_verifier, code_challenge = _generate_pkce()
    _pkce_store[session_id] = code_verifier
    params = {
        "response_type": "code",
        "client_id": os.getenv("CANVA_CLIENT_ID"),
        "redirect_uri": os.getenv("CANVA_REDIRECT_URI"),
        "scope": CANVA_SCOPES,
        "state": session_id,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{CANVA_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_tokens(code: str, session_id: str) -> None:
    """Exchange an authorization code for tokens and persist them."""
    code_verifier = _pkce_store.pop(session_id, None)
    if not code_verifier:
        raise ValueError(f"No PKCE verifier found for session '{session_id}' — auth may have expired")

    resp = requests.post(
        CANVA_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": os.getenv("CANVA_REDIRECT_URI"),
            "code_verifier": code_verifier,
            "client_id": os.getenv("CANVA_CLIENT_ID"),
            "client_secret": os.getenv("CANVA_CLIENT_SECRET"),
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    data = resp.json()

    access_token = data["access_token"]
    refresh_token = data.get("refresh_token")
    expires_in = data.get("expires_in", 3600)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    save_connector_tokens(PROVIDER, session_id, access_token, refresh_token, expiry.isoformat())


def is_connected(session_id: str) -> bool:
    """Return True if Canva tokens exist for this session."""
    return get_connector_tokens(PROVIDER, session_id) is not None


def disconnect(session_id: str) -> None:
    """Remove stored Canva tokens for this session."""
    delete_connector_tokens(PROVIDER, session_id)


# ---------------------------------------------------------------------------
# Design search — powers the @ mention dropdown
# ---------------------------------------------------------------------------

def search_designs(session_id: str, query: str) -> dict:
    """
    Search the connected Canva account for designs by title.

    Returns {"connected": False, "files": []} when no tokens exist.
    Empty query returns the 10 most recently modified designs.
    Non-empty query filters by title.

    Each file entry includes a "source": "canva" field so the FE and
    _build_attachment_context can route the download to the right service.
    """
    if not is_connected(session_id):
        return {"connected": False, "files": []}

    access_token = _get_valid_token(session_id)
    params: dict = {"limit": 10, "ownership": "any"}
    if query.strip():
        params["query"] = query.strip()

    resp = requests.get(
        f"{CANVA_API_BASE}/designs",
        params=params,
        headers=_bearer(access_token),
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])

    files = [
        {
            "file_id": item["id"],
            "file_name": item.get("title") or "Untitled Design",
            "mime_type": "application/vnd.canva.design",
            "modified_time": _epoch_to_iso(item.get("updated_at")),
            "thumbnail_url": item.get("thumbnail", {}).get("url"),
            "source": PROVIDER,
        }
        for item in items
    ]
    return {"connected": True, "files": files}


# ---------------------------------------------------------------------------
# Design download — used by websocket attachment handler
# ---------------------------------------------------------------------------

def _poll_export_job(access_token: str, job_id: str) -> list:
    """Poll GET /exports/{job_id} until success/failure and return the list of download URLs."""
    for _ in range(_EXPORT_MAX_POLLS):
        time.sleep(_EXPORT_POLL_INTERVAL)
        poll = requests.get(
            f"{CANVA_API_BASE}/exports/{job_id}",
            headers=_bearer(access_token),
        )
        poll.raise_for_status()
        job_data = poll.json().get("job", {})
        status = job_data.get("status")

        if status == "success":
            # urls is a direct property of job (one entry per page for PNG, one entry for PDF)
            urls = job_data.get("urls", [])
            if not urls:
                raise RuntimeError("Canva export succeeded but returned no download URLs")
            return urls

        if status == "failed":
            raise RuntimeError(f"Canva export job failed: {job_data}")

    raise TimeoutError(f"Canva export job '{job_id}' did not complete within the allowed time")


def _trigger_export(access_token: str, design_id: str, format_type: str) -> list:
    """
    Start a Canva export job via POST /exports and return all download URL(s).

    Correct Canva API: the design_id goes in the request BODY, not the URL path.
    Endpoint: POST https://api.canva.com/rest/v1/exports
    Scope required: design:content:read

    For PDF: returns one URL (all pages in one file).
    For PNG: returns one URL per page, sorted by page order.

    Raises requests.HTTPError on non-2xx responses.
    """
    resp = requests.post(
        f"{CANVA_API_BASE}/exports",
        json={"design_id": design_id, "format": {"type": format_type}},
        headers={**_bearer(access_token), "Content-Type": "application/json"},
    )
    if not resp.ok:
        print(f"[Canva] Export {format_type.upper()} failed for design '{design_id}' "
              f"— HTTP {resp.status_code}: {resp.text}")
    resp.raise_for_status()
    job = resp.json().get("job", {})
    job_id = job.get("id")
    if not job_id:
        raise RuntimeError("Canva export did not return a job ID")
    return _poll_export_job(access_token, job_id)


def _download_thumbnail(access_token: str, design_id: str) -> bytes:
    """
    Last-resort fallback: fetch the design thumbnail image (page 1) via GET /designs/{id}.
    Returns raw image bytes.
    """
    resp = requests.get(
        f"{CANVA_API_BASE}/designs/{design_id}",
        headers=_bearer(access_token),
    )
    resp.raise_for_status()
    thumbnail_url = resp.json().get("design", {}).get("thumbnail", {}).get("url")
    if not thumbnail_url:
        raise ValueError(f"No thumbnail URL returned for Canva design '{design_id}'")
    img_resp = requests.get(thumbnail_url)
    img_resp.raise_for_status()
    return img_resp.content


def download_design_as_bytes(session_id: str, design_id: str) -> bytes:
    """
    Export a Canva design as PDF and return its content as bytes.

    Triggers an async export job, polls until it completes, then downloads
    the resulting file. The returned bytes can be passed directly to
    extract_document_content() in services/pdf_service.py.

    Raises RuntimeError on export failure, TimeoutError if the job exceeds
    _EXPORT_MAX_POLLS * _EXPORT_POLL_INTERVAL seconds.
    """
    access_token = _get_valid_token(session_id)
    urls = _trigger_export(access_token, design_id, "pdf")
    file_resp = requests.get(urls[0])
    file_resp.raise_for_status()
    return file_resp.content


def _log_extracted_content(file_name: str, method: str, content: str) -> None:
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"[Canva] Content extracted from '{file_name}' via {method}")
    print(sep)
    print(content)
    print(f"{sep}\n")


def fetch_design_text_content(session_id: str, design_id: str, file_name: str) -> str:
    """
    Fetch a Canva design's text content using the best available method:

    1. PDF export  → Claude document extraction (native designs).
    2. PNG export  → Claude Vision extraction (some imported designs support image export).
    3. Thumbnail   → Claude Vision extraction of page-1 image (universal fallback for
                     designs that were imported/uploaded and cannot be re-exported via
                     the Canva Export API).

    The Canva Export API returns 404 for designs whose source was an uploaded file
    (e.g. a PDF imported into Canva) because Canva does not re-serve the original
    bytes through the export endpoint — authentication is irrelevant.  The PNG export
    and thumbnail paths work around this restriction by reading the design as images.
    """
    from services.pdf_service import extract_document_content, extract_content_from_images

    access_token = _get_valid_token(session_id)

    # --- 1. Try PDF export (works for native Canva designs) ---
    try:
        urls = _trigger_export(access_token, design_id, "pdf")
        file_resp = requests.get(urls[0])
        file_resp.raise_for_status()
        print(f"[Canva] PDF export succeeded for '{file_name}'")
        content = extract_document_content(file_resp.content, file_name)
        _log_extracted_content(file_name, "PDF export", content)
        return content
    except (requests.HTTPError, RuntimeError, TimeoutError) as e:
        status = getattr(getattr(e, "response", None), "status_code", "N/A")
        print(f"[Canva] PDF export not available for '{file_name}' (HTTP {status}: {e}), trying PNG export...")

    # --- 2. Try PNG export (returns one URL per page; works for most Canva designs) ---
    try:
        urls = _trigger_export(access_token, design_id, "png")
        image_bytes_list = []
        for url in urls:
            img_resp = requests.get(url)
            img_resp.raise_for_status()
            image_bytes_list.append(img_resp.content)
        print(f"[Canva] PNG export succeeded for '{file_name}' ({len(image_bytes_list)} page(s))")
        content = extract_content_from_images(image_bytes_list, file_name)
        _log_extracted_content(file_name, f"PNG export ({len(image_bytes_list)} pages)", content)
        return content
    except (requests.HTTPError, RuntimeError, TimeoutError) as e:
        status = getattr(getattr(e, "response", None), "status_code", "N/A")
        print(f"[Canva] PNG export not available for '{file_name}' (HTTP {status}: {e}), falling back to thumbnail...")

    # --- 3. Thumbnail fallback (page 1 only — universal for all design types) ---
    img_bytes = _download_thumbnail(access_token, design_id)
    print(f"[Canva] Using thumbnail fallback for '{file_name}' (page 1 only)")
    content = extract_content_from_images([img_bytes], file_name)
    _log_extracted_content(file_name, "thumbnail (page 1 only)", content)
    return content


# ---------------------------------------------------------------------------
# Brand guidelines content validation
# ---------------------------------------------------------------------------

_BLANK_CONTENT_INDICATORS = (
    "blank/white",
    "no visible text",
    "no additional content was visible",
    "available information is from the file name only",
    "no text content",
    "appears to be blank",
    "no content could be extracted",
)


def _is_meaningful_content(content: str) -> bool:
    """Return False if the extracted text is blank, too short, or only filename metadata."""
    if not content or len(content.strip()) < 200:
        return False
    lower = content.lower()
    return not any(indicator in lower for indicator in _BLANK_CONTENT_INDICATORS)


# ---------------------------------------------------------------------------
# Brand guidelines discovery — kept for reference; no longer called by pipeline
# ---------------------------------------------------------------------------

BRAND_GUIDELINE_KEYWORDS = {
    "brand", "guideline", "style", "identity", "logo", "visual",
    "tone", "kit", "palette", "typography", "design system",
    "brand book", "brand guide", "colour", "color", "font", "asset",
}


def discover_brand_relevant_designs(session_id: str) -> list[dict]:
    """
    Scan available Canva design names and return those that look like
    brand guidelines, style guides, or visual identity documents.

    Uses keyword matching on file names only — no file content is read here.
    Returns a list of {file_id, file_name} dicts.
    """
    result = search_designs(session_id, query="")
    if not result.get("connected"):
        return []

    relevant = []
    for item in result.get("files", []):
        name_lower = item["file_name"].lower()
        if any(kw in name_lower for kw in BRAND_GUIDELINE_KEYWORDS):
            relevant.append({
                "file_id": item["file_id"],
                "file_name": item["file_name"],
            })

    return relevant


def fetch_brand_guidelines_content(session_id: str, file_refs: list[dict]) -> str:
    """
    Download each file in file_refs from Canva and extract its text content.

    Called only from run_ceo_stage1 — the single fetch point for brand guidelines.
    Returns a combined string ready to be appended to an agent prompt.
    Silently skips files that fail to download.
    """
    print(f"\n[Canva] Fetching content for {len(file_refs)} file(s): "
          f"{[r.get('file_name', 'Untitled') for r in file_refs]}")

    blocks = []
    for ref in file_refs:
        file_id = ref.get("file_id")
        file_name = ref.get("file_name", "Untitled")
        try:
            content = fetch_design_text_content(session_id, file_id, file_name)
            if not _is_meaningful_content(content):
                print(f"[Canva] Skipping '{file_name}' — extracted content is blank or contains no usable text")
                continue
            blocks.append(f"=== {file_name} ===\n{content}")
        except Exception as e:
            print(f"[Canva] Failed to fetch brand guideline '{file_name}': {e}")

    combined = "\n\n".join(blocks)
    if combined:
        sep = "=" * 60
        print(f"\n{sep}")
        print(f"[Canva] COMBINED BRAND GUIDELINES ({len(blocks)} file(s) successfully read)")
        print(sep)
        print(combined)
        print(f"{sep}\n")
    else:
        print("[Canva] No brand guideline content could be extracted from any file.")

    return combined
