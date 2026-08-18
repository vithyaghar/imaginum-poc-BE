#!/usr/bin/env python
# -- coding:utf-8 --
###
# Filename: routes/canva_routes.py
# Author: Vithyaghar M
#
# Copyright (c) 2026 Trinom Digital
###

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from controllers.canva_auth_controller import (
    disconnect_canva,
    get_canva_auth_url,
    get_canva_connection_status,
    handle_canva_callback,
)
from controllers.canva_controller import search_canva_designs_controller
from helper.response_handler import response_handler

router = APIRouter(prefix="/canva", tags=["Canva"])


@router.get("/auth/authorize")
def canva_authorize(session_id: str):
    """Return the Canva OAuth consent URL for the given session."""
    auth_url = get_canva_auth_url(session_id)
    return response_handler.success(
        message="Authorization URL generated",
        status_code=200,
        data={"auth_url": auth_url},
    )


@router.get("/auth/callback")
def canva_callback(
    state: str,
    code: str = None,
    error: str = None,
    error_description: str = None,
):
    """
    Canva redirects here after user consent.
    On success: exchanges code for tokens, redirects to frontend with status=canva_connected.
    On error: redirects to frontend with status=canva_error.
    """
    import os
    from urllib.parse import quote

    frontend_url = os.getenv("FRONTEND_REDIRECT_URI", "http://localhost:3000")

    if error:
        print(f"[Canva OAuth error] {error}: {error_description}")
        desc = quote(error_description or error)
        return RedirectResponse(url=f"{frontend_url}?status=canva_error&reason={desc}&session_id={state}")

    redirect_url = handle_canva_callback(code=code, state=state)
    return RedirectResponse(url=redirect_url)


@router.get("/auth/status")
def canva_status(session_id: str):
    """Check whether Canva is connected for this session."""
    connected = get_canva_connection_status(session_id)
    return response_handler.success(
        message="Status fetched",
        status_code=200,
        data={"connected": connected},
    )


@router.delete("/auth/disconnect")
def canva_disconnect(session_id: str):
    """Remove stored Canva tokens for this session."""
    disconnect_canva(session_id)
    return response_handler.success(
        message="Canva disconnected",
        status_code=200,
    )


@router.get("/search")
async def search_canva_designs(session_id: str, query: str = ""):
    """
    Search Canva designs by title for the @ mention dropdown.

    Returns { connected: false } when Canva is not connected for this session.
    Empty query returns 10 most recently modified designs.
    Each result includes source: "canva" so the client can distinguish from Drive files.
    """
    return await search_canva_designs_controller(session_id, query)
