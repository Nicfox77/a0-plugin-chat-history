"""Rolling summary deck, stored in the plugin's Postgres database.

Entries are the rolling-compaction summaries of the primary chat. The deck
lives outside the chat, so it survives chat resets, re-creation, and further
compaction. Each entry summarizes only the raw turns since the previous
entry — the chat is reseeded with a short pointer after each harvest, so
entries never summarize other entries.

Legacy import: deployments that ran the standalone continuous_chat plugin
have a deck at usr/state/continuous_chat/main.json; it is imported
idempotently on job ticks (see helpers/summaries.py).
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from typing import Any

from helpers.print_style import PrintStyle

from usr.plugins.chat_history.helpers import db
from usr.plugins.chat_history.helpers import settings

SEED_MARKER = "[continuous-chat-seed]"
DECK_ID = "main"
_WRITE_LOCK = threading.Lock()


def append_entry(summary: str, message_count: int, deck_id: str = DECK_ID) -> None:
    conn = db.get_connection()
    created = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO compaction_summaries (deck_id, created_at, message_count, summary)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (deck_id, created_at) DO NOTHING
            """,
            (deck_id, created, int(message_count), summary.strip()[:100000]),
        )
    db.set_meta("last_compact_at", created.isoformat())


def append_segment(
    *,
    context_id: str,
    messages: list[dict[str, Any]],
    summary: str,
    token_count: int,
    deck_id: str = DECK_ID,
) -> str:
    """Atomically archive an evicted raw segment and append its deck entry."""
    message_ids = [str(message.get("id") or "") for message in messages]
    identity = "\n".join([context_id, *message_ids])
    segment_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    created = datetime.now(timezone.utc)
    first_id = next((value for value in message_ids if value), "")
    last_id = next((value for value in reversed(message_ids) if value), "")
    raw_messages = json.dumps(messages, ensure_ascii=False, default=str)

    conn = db.get_connection()
    with _WRITE_LOCK, conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO compaction_segments (
                    id, deck_id, context_id, created_at, first_message_id,
                    last_message_id, message_count, token_count, messages, summary
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    segment_id,
                    deck_id,
                    context_id,
                    created,
                    first_id,
                    last_id,
                    len(messages),
                    int(token_count),
                    raw_messages,
                    summary.strip()[:100000],
                ),
            )
            cur.execute(
                """
                INSERT INTO compaction_summaries (
                    deck_id, created_at, message_count, summary, segment_id
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    deck_id,
                    created,
                    len(messages),
                    summary.strip()[:100000],
                    segment_id,
                ),
            )
    db.set_meta("last_compact_at", created.isoformat())
    return segment_id


def fetch_entries(deck_id: str = DECK_ID, limit: int | None = None) -> list[dict[str, Any]]:
    """Fetch deck entries oldest→newest.

    All entries by default: retention is manual (``trim_deck`` / the
    compact endpoint's trim action) so summaries survive model changes —
    switching to a larger-context model and raising the render budget must
    be able to bring older entries back. Rendering is bounded by the token
    budget (``render_entries``), not by storage.
    """
    conn = db.get_connection()
    sql = """
        SELECT created_at, message_count, summary
        FROM compaction_summaries
        WHERE deck_id = %s
        ORDER BY created_at DESC
        """
    params: list = [deck_id]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(max(1, limit))
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    entries = [
        {
            "created_at": row[0],
            "message_count": row[1],
            "summary": row[2] or "",
        }
        for row in reversed(rows)  # back to chronological order
    ]
    return entries


def trim_deck(keep: int, deck_id: str = DECK_ID) -> int:
    """Delete oldest entries beyond the newest ``keep`` (manual removal).

    Returns the number of deleted entries. ``keep=0`` clears the deck.
    """
    conn = db.get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM compaction_summaries
            WHERE deck_id = %s
              AND created_at NOT IN (
                  SELECT created_at FROM compaction_summaries
                  WHERE deck_id = %s
                  ORDER BY created_at DESC
                  LIMIT %s
              )
            """,
            (deck_id, deck_id, max(0, int(keep))),
        )
        return cur.rowcount


def entry_count(deck_id: str = DECK_ID) -> int:
    conn = db.get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM compaction_summaries WHERE deck_id = %s",
            (deck_id,),
        )
        return cur.fetchone()[0]


def render_entry_lines(
    entries: list[dict[str, Any]],
    budget: int | None = None,
    overhead_tokens: int = 0,
) -> list[str]:
    """Render entries oldest→newest as one string per entry (pure function).

    Newest entries render in full; when the budget is exceeded, remaining
    older entries collapse into a one-line count note (their content still
    lives in the table and long-term memory — raise deck_max_tokens after
    switching to a larger-context model to render more of them).
    ``overhead_tokens`` reserves budget for surrounding framing (the deck
    header in the joined rendering).
    """
    from helpers import tokens

    entries = [e for e in entries if str(e.get("summary") or "").strip()]
    if not entries:
        return []
    if budget is None:
        budget = settings.int_setting("deck_max_tokens", "CH_DECK_MAX_TOKENS", 2000)
    budget = max(300, budget)

    kept: list[str] = []
    omitted = 0
    used = int(overhead_tokens)
    for entry in reversed(entries):  # newest first for budget decisions
        summary = " ".join(str(entry["summary"]).split())
        created = str(entry.get("created_at") or "")[:10]
        line = f"[{created}] {summary}"
        cost = tokens.approximate_tokens(f"- {line}")
        if used + cost > budget and kept:
            omitted += 1
            continue
        kept.append(line)
        used += cost

    lines = list(reversed(kept))  # back to chronological order
    if omitted:
        lines.insert(
            0,
            f"(... {omitted} earlier summary blocks omitted; their content is in long-term memory)",
        )
    return lines


def render_entries(entries: list[dict[str, Any]], budget: int | None = None) -> str:
    """Render entries oldest→newest within a token budget (pure function)."""
    from helpers import tokens

    header = "Conversation summary deck (rolling compaction of this chat, oldest to newest):"
    lines = render_entry_lines(
        entries, budget, overhead_tokens=tokens.approximate_tokens(header)
    )
    if not lines:
        return ""
    return "\n".join([header, *[f"- {line}" for line in lines]])


def render_deck(deck_id: str = DECK_ID) -> str:
    try:
        return render_entries(fetch_entries(deck_id))
    except Exception as exc:  # noqa: BLE001
        PrintStyle.debug(f"chat_history: deck render skipped: {exc}")
        return ""


def reseed_message() -> str:
    """The short pointer message the chat is reseeded with after a harvest."""
    return (
        f"{SEED_MARKER} The earlier part of this conversation was compacted. "
        "Its full summary is available to you as the conversation summary "
        "deck in your context. Continue seamlessly; do not mention the "
        "compaction to the user unless asked."
    )


def strip_seed_transcript(text: str) -> str:
    """Drop any seed-marker preamble when feeding compaction input."""
    if SEED_MARKER in text:
        text = text.split(SEED_MARKER, 1)[-1]
    return text.strip()
