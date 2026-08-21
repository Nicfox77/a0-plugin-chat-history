"""Background embedding of stored messages via the framework embedding model.

Uses whatever embedding provider the user configured once in Agent Zero's
model settings (``get_embedding_model_config``) — no plugin-level provider
configuration. The model fingerprint is tracked in ``meta``; changing the
configured model clears stale vectors and re-embeds automatically on
subsequent ticks.
"""

from __future__ import annotations

import os
from typing import Any

from helpers.print_style import PrintStyle

from usr.plugins.chat_history.helpers import db


def _enabled() -> bool:
    raw = os.environ.get("CH_EMBED_ENABLED", "1")
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _batch() -> int:
    try:
        return max(1, min(64, int(os.environ.get("CH_EMBED_BATCH", "16"))))
    except ValueError:
        return 16


def _max_chars() -> int:
    try:
        return max(200, int(os.environ.get("CH_EMBED_MAX_CHARS", "4000")))
    except ValueError:
        return 4000


def _resolve_model() -> tuple[str, str] | None:
    """(provider, name) of the framework embedding preset, if configured."""
    try:
        from plugins._model_config.helpers.model_config import get_embedding_model_config

        cfg = get_embedding_model_config()
        provider = str(cfg.get("provider") or "").strip()
        name = str(cfg.get("name") or "").strip()
        if provider and name:
            return provider, name
    except Exception as exc:
        PrintStyle.debug(f"chat_history: embedding config unavailable: {exc}")
    return None


def _fingerprint(provider: str, name: str) -> str:
    return f"{provider}/{name}"


def embedding_tick() -> dict[str, Any]:
    """One background embedding pass. Returns a small status dict."""
    result: dict[str, Any] = {"embedded": 0}
    if not _enabled():
        result["disabled"] = True
        return result

    resolved = _resolve_model()
    if resolved is None:
        result["no_model"] = True
        return result
    provider, name = resolved
    fingerprint = _fingerprint(provider, name)

    try:
        if not db.vector_column_ready():
            result["no_pgvector"] = True
            return result
    except RuntimeError as exc:
        result["error"] = str(exc)
        return result

    stored = db.get_meta("embedding_model")
    if stored and stored != fingerprint:
        # Model changed: old vectors are incompatible; clear and re-embed.
        conn = db.get_connection()
        conn.execute("DELETE FROM embeddings")
        db.set_meta("embedding_model", fingerprint)
        PrintStyle.info(
            f"chat_history: embedding model changed ({stored} -> {fingerprint});"
            " re-embedding will restart"
        )
    elif not stored:
        db.set_meta("embedding_model", fingerprint)

    batch = _batch()
    limit = _max_chars()
    conn = db.get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.context_id, m.message_id, m.content_text
            FROM messages m
            LEFT JOIN embeddings e
                ON e.context_id = m.context_id AND e.message_id = m.message_id
            WHERE e.message_id IS NULL AND length(m.content_text) > 0
            ORDER BY m.created_at DESC
            LIMIT %s
            """,
            (batch,),
        )
        pending = cur.fetchall()

    if not pending:
        return result

    texts = [row[2][:limit] for row in pending]
    try:
        import models

        wrapper = models.get_embedding_model(provider, name)
        vectors = wrapper.embed_documents(texts)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"embedding call failed: {exc}"
        return result

    with conn.cursor() as cur:
        for (context_id, message_id, _text), vector in zip(pending, vectors):
            if not vector:
                continue
            vec_literal = "[" + ",".join(repr(float(v)) for v in vector) + "]"
            cur.execute(
                """
                INSERT INTO embeddings (context_id, message_id, model, vec)
                VALUES (%s, %s, %s, %s::vector)
                ON CONFLICT (context_id, message_id) DO UPDATE
                    SET model = EXCLUDED.model, vec = EXCLUDED.vec,
                        embedded_at = now()
                """,
                (context_id, message_id, fingerprint, vec_literal),
            )
            result["embedded"] += 1
    return result


def semantic_search(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Vector similarity search using the configured embedding model."""
    resolved = _resolve_model()
    if resolved is None:
        return []
    provider, name = resolved
    try:
        import models

        wrapper = models.get_embedding_model(provider, name)
        query_vec = wrapper.embed_query(query)
    except Exception as exc:  # noqa: BLE001
        PrintStyle.debug(f"chat_history: query embedding failed: {exc}")
        return []
    if not query_vec:
        return []
    vec_literal = "[" + ",".join(repr(float(v)) for v in query_vec) + "]"
    conn = db.get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.context_id, m.message_id, m.ai, m.content_text,
                   m.created_at, 1 - (e.vec <=> %s::vector) AS similarity
            FROM embeddings e
            JOIN messages m
                ON m.context_id = e.context_id AND m.message_id = e.message_id
            ORDER BY e.vec <=> %s::vector
            LIMIT %s
            """,
            (vec_literal, vec_literal, limit),
        )
        rows = cur.fetchall()
    return db.rows_to_dicts(
        rows,
        ["context_id", "message_id", "ai", "content_text", "created_at", "similarity"],
    )
