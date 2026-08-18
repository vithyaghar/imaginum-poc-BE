import os
import uuid

from google_auth_oauthlib.flow import Flow

from services.table_service import save_oauth_tokens, get_oauth_tokens, delete_oauth_tokens

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def _build_flow() -> Flow:
    client_config = {
        "web": {
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [os.getenv("GOOGLE_REDIRECT_URI")],
        }
    }
    return Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=os.getenv("GOOGLE_REDIRECT_URI"),
    )


def generate_session_id() -> str:
    return str(uuid.uuid4())


def get_google_auth_url(session_id: str) -> str:
    flow = _build_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=session_id,
    )
    return auth_url


def handle_google_callback(code: str, state: str) -> str:
    """
    Exchange the authorization code for tokens, persist them, and return
    the frontend redirect URL.
    """
    session_id = state

    flow = _build_flow()
    flow.fetch_token(code=code)

    creds = flow.credentials
    expiry_str = creds.expiry.isoformat() if creds.expiry else None

    save_oauth_tokens(
        session_id=session_id,
        access_token=creds.token,
        refresh_token=creds.refresh_token,
        token_expiry=expiry_str,
    )

    frontend_url = os.getenv("FRONTEND_REDIRECT_URI", "http://localhost:3000")
    return f"{frontend_url}?status=connected&session_id={session_id}"


def get_connection_status(session_id: str) -> bool:
    return get_oauth_tokens(session_id) is not None


def disconnect(session_id: str) -> None:
    delete_oauth_tokens(session_id)
