"""Legacy deck import + summary search support.

The standalone continuous_chat plugin (predecessor of Continuous Mode)
stored its deck at usr/state/continuous_chat/main.json. Entries are
imported idempotently into the compaction_summaries table on job ticks;
absent file or malformed content is a silent no-op.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from helpers.print_style import PrintStyle

from usr.plugins.chat_history.helpers import db


def deck_path() -> Path:
    from helpers.files import get_abs_path

    return Path(get_abs_path("usr", "state", "continuous_chat", "main.json"))


def load_deck_entries(path: Path | None = None) -> list[dict]:
    if path is None:
        path = deck_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("entries")
        return entries if isinstance(entries, list) else []
    except (OSError, ValueError):
        return []


def sync_deck_summaries(deck_id: str = "main") -> int:
    """Upsert deck entries; returns the number of newly stored entries."""
    entries = load_deck_entries()
    if not entries:
        return 0
    inserted = 0
    try:
        conn = db.get_connection()
        with conn.cursor() as cur:
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                created = _parse_ts(entry.get("created_at"))
                summary = str(entry.get("summary") or "").strip()
                if not summary:
                    continue
                cur.execute(
                    """
                    INSERT INTO compaction_summaries
                        (deck_id, created_at, message_count, summary)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (deck_id, created_at) DO NOTHING
                    """,
                    (
                        deck_id,
                        created,
                        int(entry.get("message_count") or 0),
                        summary[:100000],
                    ),
                )
                inserted += cur.rowcount
    except Exception as exc:  # noqa: BLE001
        PrintStyle.debug(f"chat_history: deck sync skipped: {exc}")
        return 0
    return inserted


def _parse_ts(value) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
