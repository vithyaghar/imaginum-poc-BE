#!/usr/bin/env python
# -- coding:utf-8 --
###
# Filename: controllers/canva_auth_controller.py
# Author: Vithyaghar M
#
# Copyright (c) 2026 Trinom Digital
###

import os

from services.canva_service import (
    build_auth_url,
    disconnect,
    exchange_code_for_tokens,
    is_connected,
)


def get_canva_auth_url(session_id: str) -> str:
    return build_auth_url(session_id)


def handle_canva_callback(code: str, state: str) -> str:
    """
    Exchange the authorization code for tokens and return the frontend redirect URL.
    `state` carries the session_id, passed through the OAuth flow unchanged.
    """
    session_id = state
    exchange_code_for_tokens(code, session_id)
    frontend_url = os.getenv("FRONTEND_REDIRECT_URI", "http://localhost:3000")
    return f"{frontend_url}?status=canva_connected&session_id={session_id}"


def get_canva_connection_status(session_id: str) -> bool:
    return is_connected(session_id)


def disconnect_canva(session_id: str) -> None:
    disconnect(session_id)
