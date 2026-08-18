import sqlite3

DB_PATH = "demo.db"


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create required tables if they don't already exist."""
    conn = get_db_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS threads (
                thread_id        TEXT PRIMARY KEY,
                business_name    TEXT,
                status           TEXT,
                campaign_content TEXT,
                chat_status      TEXT,
                created_at       TIMESTAMP,
                updated_at       TIMESTAMP,
                generated_count  INTEGER,
                is_ppt_generated BOOLEAN,
                ppt_filename     TEXT,
                first_message    TEXT,
                ppt_bytes        BLOB,
                approved_campaign TEXT,
                approved_at      TEXT,
                pdf_context      TEXT,
                slides_content   TEXT,
                selected_route   TEXT,
                coverage_summary TEXT,
                session_id       TEXT
            )
        """)
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
    finally:
        conn.close()