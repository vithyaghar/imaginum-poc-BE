import os
from datetime import datetime, timezone

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io

from services.table_service import get_oauth_tokens, save_oauth_tokens

BRAND_SEARCH_TERMS = ["brand", "guideline", "style guide", "style sheet", "brand book"]

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def _build_credentials(token_row) -> Credentials:
    """Build a Credentials object from a stored token row."""
    creds = Credentials(
        token=token_row["access_token"],
        refresh_token=token_row["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        scopes=DRIVE_SCOPES,
    )
    if token_row["token_expiry"]:
        creds.expiry = datetime.fromisoformat(token_row["token_expiry"])
    return creds


def get_drive_service(session_id: str):
    """
    Build an authenticated Drive API resource for the given session.
    Refreshes the access token if expired and persists the new token.
    Raises ValueError if no tokens are stored for this session.
    """
    token_row = get_oauth_tokens(session_id)
    if not token_row:
        raise ValueError(f"No Google Drive tokens found for session '{session_id}'")

    creds = _build_credentials(token_row)

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        expiry_str = creds.expiry.isoformat() if creds.expiry else None
        save_oauth_tokens(session_id, creds.token, creds.refresh_token, expiry_str)

    return build("drive", "v3", credentials=creds, cache_discovery=False)


def is_connected(session_id: str) -> bool:
    """Return True if Drive tokens exist for this session."""
    return get_oauth_tokens(session_id) is not None


def search_brand_files(session_id: str) -> list[dict]:
    """
    Search the connected Drive for files likely to be brand guidelines.
    Returns a list of {id, name, mimeType, modifiedTime} dicts.
    """
    service = get_drive_service(session_id)

    name_clauses = " or ".join(
        f"name contains '{term}'" for term in BRAND_SEARCH_TERMS
    )
    query = (
        f"({name_clauses})"
        " and trashed = false"
        " and (mimeType = 'application/pdf'"
        "   or mimeType = 'application/vnd.google-apps.document'"
        "   or mimeType = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')"
    )

    results = (
        service.files()
        .list(
            q=query,
            fields="files(id, name, mimeType, modifiedTime)",
            orderBy="modifiedTime desc",
            pageSize=20,
        )
        .execute()
    )

    return results.get("files", [])


def search_files(session_id: str, query: str) -> dict:
    """
    Search Drive files by name for the @ mention dropdown.

    Returns {"connected": False, "files": []} when no tokens exist for the session.
    Empty query returns the 10 most recently modified supported files.
    Non-empty query filters by name containing the query string.
    """
    token_row = get_oauth_tokens(session_id)
    if not token_row:
        return {"connected": False, "files": []}

    service = get_drive_service(session_id)

    type_filter = (
        "(mimeType = 'application/pdf'"
        " or mimeType = 'application/vnd.google-apps.document'"
        " or mimeType = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')"
    )

    if query.strip():
        safe_query = query.replace("'", "\\'")
        drive_query = f"name contains '{safe_query}' and trashed = false and {type_filter}"
    else:
        drive_query = f"trashed = false and {type_filter}"

    results = (
        service.files()
        .list(
            q=drive_query,
            fields="files(id, name, mimeType, modifiedTime)",
            orderBy="modifiedTime desc",
            pageSize=10,
        )
        .execute()
    )

    raw = results.get("files", [])
    files = [
        {
            "file_id": f["id"],
            "file_name": f["name"],
            "mime_type": f["mimeType"],
            "modified_time": f.get("modifiedTime"),
        }
        for f in raw
    ]
    return {"connected": True, "files": files}


def download_file_as_bytes(session_id: str, file_id: str, mime_type: str) -> bytes:
    """
    Download a Drive file and return its content as bytes.

    - Google Docs are exported as PDF.
    - PDF and Word files are downloaded directly.

    The returned bytes can be passed directly into analyze_pdf_with_claude()
    in services/pdf_service.py.
    """
    service = get_drive_service(session_id)
    buffer = io.BytesIO()

    if mime_type == "application/vnd.google-apps.document":
        request = service.files().export_media(
            fileId=file_id, mimeType="application/pdf"
        )
    else:
        request = service.files().get_media(fileId=file_id)

    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    return buffer.getvalue()
