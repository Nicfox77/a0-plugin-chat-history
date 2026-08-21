"""Embedded Postgres (pg0) bootstrap, schema, and connection handling.

Owns a dedicated pg0 instance named ``chat_history`` (separate from any
Hindsight instance) below Agent Zero's persistent ``usr/.pg0`` directory.
Auto-starts on first use, runs migrations idempotently, and exposes a
thread-safe connection factory. All SQL used by the plugin lives here or in
sync/embed helpers, parameterized through psycopg — never string-formatted.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from helpers.print_style import PrintStyle
from usr.plugins.chat_history.helpers.pg0_runtime import PersistentPg0

INSTANCE_NAME = "chat_history"
SCHEMA_VERSION = "1"

_lock = threading.RLock()
_conn = None  # psycopg connection, autocommit
_boot_error: str | None = None

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS contexts (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL DEFAULT '',
    type          TEXT NOT NULL DEFAULT 'user',
    agent_profile TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ,
    last_message_at TIMESTAMPTZ,
    msg_count     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    context_id  TEXT NOT NULL REFERENCES contexts(id) ON DELETE CASCADE,
    message_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    ai          BOOLEAN NOT NULL DEFAULT FALSE,
    tool_name   TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ,
    content     JSONB NOT NULL DEFAULT '{}'::jsonb,
    content_text TEXT NOT NULL DEFAULT '',
    tsv         TSVECTOR GENERATED ALWAYS AS (
                    to_tsvector('english', coalesce(content_text, ''))
                ) STORED,
    PRIMARY KEY (context_id, message_id)
);

CREATE INDEX IF NOT EXISTS messages_context_seq_idx ON messages (context_id, seq);
CREATE INDEX IF NOT EXISTS messages_tsv_idx ON messages USING GIN (tsv);

CREATE TABLE IF NOT EXISTS embeddings (
    context_id  TEXT NOT NULL,
    message_id  TEXT NOT NULL,
    model       TEXT NOT NULL,
    embedded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (context_id, message_id),
    FOREIGN KEY (context_id, message_id)
        REFERENCES messages (context_id, message_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS compaction_summaries (
    deck_id      TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    summary      TEXT NOT NULL DEFAULT '',
    tsv          TSVECTOR GENERATED ALWAYS AS (
                    to_tsvector('english', coalesce(summary, ''))
                ) STORED,
    PRIMARY KEY (deck_id, created_at)
);

CREATE INDEX IF NOT EXISTS compaction_summaries_tsv_idx
    ON compaction_summaries USING GIN (tsv);

CREATE TABLE IF NOT EXISTS compaction_segments (
    id               TEXT PRIMARY KEY,
    deck_id          TEXT NOT NULL,
    context_id       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    first_message_id TEXT NOT NULL DEFAULT '',
    last_message_id  TEXT NOT NULL DEFAULT '',
    message_count    INTEGER NOT NULL DEFAULT 0,
    token_count      INTEGER NOT NULL DEFAULT 0,
    messages         JSONB NOT NULL DEFAULT '[]'::jsonb,
    summary          TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS compaction_segments_context_created_idx
    ON compaction_segments (context_id, created_at);

ALTER TABLE compaction_summaries
    ADD COLUMN IF NOT EXISTS segment_id TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS compaction_summaries_segment_idx
    ON compaction_summaries (segment_id) WHERE segment_id <> '';

CREATE TABLE IF NOT EXISTS context_snapshots (
    context_id  TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    type        TEXT NOT NULL DEFAULT '',
    blob        JSONB NOT NULL,
    msg_count   INTEGER NOT NULL DEFAULT 0,
    saved_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS message_files (
    context_id  TEXT NOT NULL,
    filename    TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (context_id, filename)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

ALTER TABLE contexts
    ADD COLUMN IF NOT EXISTS parent_context_id TEXT NOT NULL DEFAULT '';
ALTER TABLE contexts
    ADD COLUMN IF NOT EXISTS is_subagent      BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS contexts_parent_idx
    ON contexts (parent_context_id) WHERE parent_context_id <> '';
CREATE INDEX IF NOT EXISTS contexts_subagent_idx
    ON contexts (is_subagent) WHERE is_subagent;
"""


def get_connection():
    """Return the shared autocommit connection, booting pg0 if needed.

    Raises RuntimeError on boot failure (caller decides how fatal that is —
    the plugin is additive and must never break a running agent).
    """
    global _conn, _boot_error
    if _conn is not None:
        try:
            with _conn.cursor():
                pass
            return _conn
        except Exception:
            _conn = None
    with _lock:
        if _conn is not None:
            return _conn
        try:
            import psycopg

            info = PersistentPg0(name=INSTANCE_NAME).get_or_start()
            conn = psycopg.connect(info.uri, autocommit=True)
            conn.execute("CREATE TABLE IF NOT EXISTS chat_history_reserved (ok boolean)")
            # SCHEMA_SQL is fully IF NOT EXISTS — safe (and required) to run
            # on every boot so schema additions reach already-initialized DBs.
            conn.execute(SCHEMA_SQL)
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', %s)"
                " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (SCHEMA_VERSION,),
            )
            _conn = conn
            _boot_error = None
            return conn
        except Exception as exc:  # noqa: BLE001 — degrade, never crash turns
            _boot_error = str(exc)
            raise RuntimeError(f"chat_history DB unavailable: {exc}") from exc


def boot_error() -> str | None:
    return _boot_error


def get_meta(key: str) -> str | None:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM meta WHERE key = %s", (key,))
        row = cur.fetchone()
        return row[0] if row else None


def set_meta(key: str, value: str) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (%s, %s)"
        " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (key, value),
    )


def vector_column_ready() -> bool:
    """True when pgvector is available (embeddings table upgraded)."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_available_extensions"
                " WHERE name = 'vector')"
            )
            if not cur.fetchone()[0]:
                return False
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(
                "ALTER TABLE embeddings"
                " ADD COLUMN IF NOT EXISTS vec vector"
            )
            return True
    except Exception:
        return False


def healthy() -> bool:
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            return True
    except Exception:
        return False


def stats() -> dict[str, Any]:
    out: dict[str, Any] = {"instance": INSTANCE_NAME}
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM contexts")
            out["contexts"] = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM messages")
            out["messages"] = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM embeddings")
            out["embeddings"] = cur.fetchone()[0]
            out["embedding_model"] = get_meta("embedding_model")
    except Exception as exc:
        out["error"] = str(exc)
    return out


def rows_to_dicts(rows: list[tuple], columns: list[str]) -> list[dict[str, Any]]:
    return [dict(zip(columns, row)) for row in rows]


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
