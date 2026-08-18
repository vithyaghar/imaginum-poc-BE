#!/usr/bin/env python
# -- coding:utf-8 --
###
# Filename: c:\Projects\imaginum\backend\services\table_service.py
# Path: c:\Projects\imaginum\backend\services
# Created Date: Monday, March 9th 2026, 11:13:44 am
# Author: Vithyaghar M
# 
# Copyright (c) 2026 Trinom Digital
###
#!/usr/bin/env python
# -- coding:utf-8 --

import sqlite3
import threading
import queue
import logging
from datetime import datetime

DB_PATH = "demo.db"

# Queue for DB operations
db_queue = queue.Queue()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("db_worker")


# ---------------------------------------------------------
# Initialize Database
# ---------------------------------------------------------
def init_db():

    conn = sqlite3.connect(DB_PATH)

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS threads (
            thread_id TEXT PRIMARY KEY,
            business_name TEXT,
            status TEXT,
            campaign_content TEXT,
            chat_status TEXT,
            created_at TEXT,
            updated_at TEXT,
            first_message TEXT,
            selected_route TEXT,
            approved_at TEXT
        )
        """
    )

    for col, definition in [
        ("first_message", "TEXT"),
        ("selected_route", "TEXT"),
        ("approved_at", "TEXT"),
        ("slides_content", "TEXT"),
        ("coverage_summary", "TEXT"),
        ("session_id", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE threads ADD COLUMN {col} {definition}")
        except Exception:
            pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS google_oauth_tokens (
            session_id    TEXT PRIMARY KEY,
            access_token  TEXT NOT NULL,
            refresh_token TEXT,
            token_expiry  TEXT,
            created_at    TEXT,
            updated_at    TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS connector_tokens (
            provider      TEXT NOT NULL,
            session_id    TEXT NOT NULL,
            access_token  TEXT NOT NULL,
            refresh_token TEXT,
            token_expiry  TEXT,
            created_at    TEXT,
            updated_at    TEXT,
            PRIMARY KEY (provider, session_id)
        )
    """)

    conn.commit()
    conn.close()


# ---------------------------------------------------------
# DB Worker
# ---------------------------------------------------------
def db_worker():

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")

    logger.info("DB Worker started")

    while True:
        task = db_queue.get()

        if task is None:
            logger.info("DB Worker shutting down")
            break

        query, values, result_queue = task

        try:
            cursor = conn.cursor()

            if values:
                cursor.execute(query, values)
            else:
                cursor.execute(query)

            conn.commit()

            # Return results for SELECT queries
            if result_queue:
                result_queue.put(cursor.fetchall())

        except sqlite3.DatabaseError as db_error:
            conn.rollback()

            logger.error(
                "Database error occurred.\nQuery: %s\nValues: %s\nError: %s",
                query,
                values,
                db_error,
            )

            if result_queue:
                result_queue.put(db_error)

        except Exception as e:
            conn.rollback()

            logger.error(
                "Unexpected DB worker error.\nQuery: %s\nValues: %s\nError: %s",
                query,
                values,
                e,
            )

            if result_queue:
                result_queue.put(e)

        finally:
            db_queue.task_done()


# Start worker thread
worker_thread = threading.Thread(target=db_worker, daemon=True)
worker_thread.start()


# ---------------------------------------------------------
# Execute Query
# ---------------------------------------------------------
def execute_db(query, values=None, fetch=False):

    result_queue = queue.Queue() if fetch else None

    db_queue.put((query, values, result_queue))

    if fetch:
        result = result_queue.get()

        if isinstance(result, Exception):
            raise result

        return result


# ---------------------------------------------------------
# Create Thread
# ---------------------------------------------------------
def create_thread(thread_id, business_name=None, first_message=None, session_id=None):

    now = datetime.now().isoformat()

    query = """
        INSERT INTO threads (
            thread_id,
            business_name,
            status,
            campaign_content,
            chat_status,
            created_at,
            updated_at,
            first_message,
            session_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    values = (
        thread_id,
        business_name,
        "GATHERING_REQUIREMENTS",
        None,
        "ACTIVE",
        now,
        now,
        first_message,
        session_id,
    )

    execute_db(query, values)


# ---------------------------------------------------------
# Update Thread
# ---------------------------------------------------------
def update_thread(thread_id, **fields):

    if not fields:
        return

    updates = []
    values = []

    for key, value in fields.items():
        updates.append(f"{key}=?")
        values.append(value)

    updates.append("updated_at=?")
    values.append(datetime.now().isoformat())
    values.append(thread_id)

    query = f"""
        UPDATE threads
        SET {",".join(updates)}
        WHERE thread_id=?
    """

    execute_db(query, tuple(values))


# ---------------------------------------------------------
# Get Thread
# ---------------------------------------------------------
def get_thread(thread_id):

    query = "SELECT * FROM threads WHERE thread_id=?"

    return execute_db(query, (thread_id,), fetch=True)


# ---------------------------------------------------------
# Get session_id for a thread (direct read, bypasses queue worker)
# ---------------------------------------------------------
def get_thread_session_id(thread_id: str) -> str | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT session_id FROM threads WHERE thread_id=?", (thread_id,)
        ).fetchone()
        return row["session_id"] if row else None
    finally:
        conn.close()


# ---------------------------------------------------------
# Save OAuth Tokens (upsert)
# ---------------------------------------------------------
def save_oauth_tokens(session_id, access_token, refresh_token, token_expiry):

    now = datetime.now().isoformat()

    query = """
        INSERT INTO google_oauth_tokens (
            session_id,
            access_token,
            refresh_token,
            token_expiry,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            access_token  = excluded.access_token,
            refresh_token = excluded.refresh_token,
            token_expiry  = excluded.token_expiry,
            updated_at    = excluded.updated_at
    """

    values = (session_id, access_token, refresh_token, token_expiry, now, now)

    execute_db(query, values)


# ---------------------------------------------------------
# Get OAuth Tokens
# ---------------------------------------------------------
def get_oauth_tokens(session_id):

    query = "SELECT * FROM google_oauth_tokens WHERE session_id=?"

    rows = execute_db(query, (session_id,), fetch=True)

    return rows[0] if rows else None


# ---------------------------------------------------------
# Delete OAuth Tokens
# ---------------------------------------------------------
def delete_oauth_tokens(session_id):

    query = "DELETE FROM google_oauth_tokens WHERE session_id=?"

    execute_db(query, (session_id,))


# ---------------------------------------------------------
# Generic Connector Token CRUD
# provider: arbitrary string key, e.g. "canva", "notion", "figma"
# ---------------------------------------------------------
def save_connector_tokens(provider, session_id, access_token, refresh_token, token_expiry):

    now = datetime.now().isoformat()

    query = """
        INSERT INTO connector_tokens (
            provider,
            session_id,
            access_token,
            refresh_token,
            token_expiry,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider, session_id) DO UPDATE SET
            access_token  = excluded.access_token,
            refresh_token = excluded.refresh_token,
            token_expiry  = excluded.token_expiry,
            updated_at    = excluded.updated_at
    """

    values = (provider, session_id, access_token, refresh_token, token_expiry, now, now)

    execute_db(query, values)


def get_connector_tokens(provider, session_id):

    query = "SELECT * FROM connector_tokens WHERE provider=? AND session_id=?"

    rows = execute_db(query, (provider, session_id), fetch=True)

    return rows[0] if rows else None


def delete_connector_tokens(provider, session_id):

    query = "DELETE FROM connector_tokens WHERE provider=? AND session_id=?"

    execute_db(query, (provider, session_id))
