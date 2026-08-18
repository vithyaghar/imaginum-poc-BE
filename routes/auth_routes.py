from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from controllers.auth_controller import (
    generate_session_id,
    get_google_auth_url,
    handle_google_callback,
    get_connection_status,
    disconnect,
)
from helper.response_handler import response_handler

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.get("/session")
def get_session():
    """Generate a new session_id. The frontend stores this in localStorage."""
    session_id = generate_session_id()
    return response_handler.success(
        message="Session created",
        status_code=200,
        data={"session_id": session_id},
    )


@router.get("/google/authorize")
def google_authorize(session_id: str):
    """Return the Google OAuth consent URL for the given session."""
    auth_url = get_google_auth_url(session_id)
    return response_handler.success(
        message="Authorization URL generated",
        status_code=200,
        data={"auth_url": auth_url},
    )


@router.get("/google/callback")
def google_callback(code: str, state: str):
    """
    Google redirects here after user consent.
    Exchanges the code for tokens, stores them, then redirects to the frontend.
    """
    redirect_url = handle_google_callback(code=code, state=state)
    return RedirectResponse(url=redirect_url)


@router.get("/google/status")
def google_status(session_id: str):
    """Check whether Google Drive is connected for this session."""
    connected = get_connection_status(session_id)
    return response_handler.success(
        message="Status fetched",
        status_code=200,
        data={"connected": connected},
    )


@router.delete("/google/disconnect")
def google_disconnect(session_id: str):
    """Remove stored Drive tokens for this session."""
    disconnect(session_id)
    return response_handler.success(
        message="Google Drive disconnected",
        status_code=200,
    )
