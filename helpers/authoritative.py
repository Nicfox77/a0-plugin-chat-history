"""Authoritative storage: exact-format snapshots and boot materialization.

storage_mode "authoritative" makes the database the system of record:

- **Save side**: after each monologue (and on job ticks), the context is
  serialized with the stock serializer (``export_json_chat``) and stored as
  a JSONB snapshot — the exact blob ``chat.json`` contains, lossless
  (history objects, log entries with colors, metadata). The per-message
  transcript files (``messages/*.txt``) are captured too.
- **Restore side**: at startup (before the stock chat loader runs), any
  context whose files are missing or older than its snapshot is
  materialized back to ``usr/chats/<id>/`` — so the stock loader, the web
  UI transcript view, and everything else read normal files. The DB never
  sits in the live path; files become a derived cache that can always be
  rebuilt from the DB.
- **Deletion**: a snapshot whose files are gone, whose context is not
  live, and whose saved_at is older than the grace period is treated as
  user-deleted (stock remove_chat) and dropped from the DB.

No framework module is shadowed: stock persistence code writes the files
both ways; authoritative mode only adds DB capture and boot-time
materialization.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from helpers.print_style import PrintStyle

from usr.plugins.chat_history.helpers import db
from usr.plugins.chat_history.helpers import settings

GRACE = timedelta(minutes=15)


def capture_context(context) -> bool:
    """Serialize a live context and upsert its snapshot + message files."""
    context_id = str(getattr(context, "id", "") or "")
    if not context_id:
        return False
    try:
        from helpers.persist_chat import export_json_chat, get_chat_folder_path

        blob_text = export_json_chat(context)
        blob = json.loads(blob_text)
        conn = db.get_connection()

        def count_messages(node: Any) -> int:
            if isinstance(node, dict):
                if node.get("_cls") == "Message":
                    return 1
                return sum(count_messages(v) for v in node.values())
            if isinstance(node, list):
                return sum(count_messages(v) for v in node)
            return 0

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO context_snapshots (context_id, name, type, blob, msg_count, saved_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (context_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    type = EXCLUDED.type,
                    blob = EXCLUDED.blob,
                    msg_count = EXCLUDED.msg_count,
                    saved_at = now()
                """,
                (
                    context_id,
                    str(blob.get("name") or ""),
                    str(blob.get("type") or ""),
                    json.dumps(blob, ensure_ascii=False),
                    count_messages(blob),
                ),
            )

        # Capture per-message transcript files (source of the UI transcript)
        folder = Path(get_chat_folder_path(context_id))
        messages_dir = folder / "messages"
        if messages_dir.is_dir():
            for file in messages_dir.glob("*.txt"):
                try:
                    content = file.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO message_files (context_id, filename, content, updated_at)
                        VALUES (%s, %s, %s, now())
                        ON CONFLICT (context_id, filename) DO UPDATE SET
                            content = EXCLUDED.content, updated_at = now()
                        """,
                        (context_id, file.name, content),
                    )
        return True
    except Exception as exc:  # noqa: BLE001
        PrintStyle.warning(f"chat_history: snapshot capture failed: {exc}")
        return False


def _chats_dir() -> Path:
    from helpers.files import get_abs_path

    return Path(get_abs_path("usr", "chats"))


def restore_stale_files() -> dict[str, int]:
    """Materialize DB snapshots whose files are missing or stale.

    Must run before the stock chat loader (startup_migration hook fires
    before the deferred initialize_chats task).
    """
    restored = {"contexts": 0, "files": 0}
    try:
        conn = db.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT context_id, blob, saved_at FROM context_snapshots"
            )
            rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        PrintStyle.warning(f"chat_history: restore query failed: {exc}")
        return restored

    chats = _chats_dir()
    for context_id, blob_text, saved_at in rows:
        try:
            folder = chats / context_id
            chat_file = folder / "chat.json"
            blob = json.dumps(blob_text, ensure_ascii=False)
            is_stale = True
            if chat_file.is_file():
                mtime = datetime.fromtimestamp(chat_file.stat().st_mtime, tz=timezone.utc)
                is_stale = mtime < saved_at

            if is_stale:
                folder.mkdir(parents=True, exist_ok=True)
                chat_file.write_text(blob, encoding="utf-8")
                restored["contexts"] += 1

            # Always refresh transcript files if the folder lacks them
            messages_dir = folder / "messages"
            if not messages_dir.is_dir():
                try:
                    with db.get_connection().cursor() as cur:
                        cur.execute(
                            "SELECT filename, content FROM message_files WHERE context_id = %s",
                            (context_id,),
                        )
                    files = cur.fetchall()
                except Exception:
                    files = []
                if files:
                    messages_dir.mkdir(parents=True, exist_ok=True)
                    for filename, content in files:
                        target = messages_dir / filename
                        target.write_text(str(content), encoding="utf-8")
                        restored["files"] += 1
        except Exception as exc:  # noqa: BLE001
            PrintStyle.debug(f"chat_history: restore failed for {context_id}: {exc}")
    if restored["contexts"] or restored["files"]:
        PrintStyle.success(
            f"chat_history: materialized {restored['contexts']} chats,"
            f" {restored['files']} transcript files from DB snapshots"
        )
    return restored


def reconcile_deletions() -> int:
    """Drop snapshots for chats deleted while we weren't looking.

    A snapshot whose chat folder is gone and whose context is not live is
    presumed deleted by the user (stock remove_chat deletes the folder).
    The grace period avoids racing a brand-new context before first save.
    """
    deleted = 0
    try:
        conn = db.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT context_id, saved_at FROM context_snapshots"
            )
            rows = cur.fetchall()
        if not rows:
            return 0

        live = set()
        try:
            from agent import AgentContext

            with AgentContext._contexts_lock:
                live = {str(ctx.id) for ctx in AgentContext._contexts.values()}
        except Exception:
            pass

        chats = _chats_dir()
        cutoff = datetime.now(timezone.utc) - GRACE
        for context_id, saved_at in rows:
            if context_id in live:
                continue
            if (chats / context_id).exists():
                continue
            if saved_at.tzinfo is None:
                saved_at = saved_at.replace(tzinfo=timezone.utc)
            if saved_at >= cutoff:
                continue
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM context_snapshots WHERE context_id = %s", (context_id,)
                )
                cur.execute(
                    "DELETE FROM message_files WHERE context_id = %s", (context_id,)
                )
                cur.execute(
                    "DELETE FROM messages WHERE context_id = %s"
                    " AND context_id NOT IN (SELECT id FROM contexts WHERE id = %s)",
                    (context_id, context_id),
                )
            deleted += 1
    except Exception as exc:  # noqa: BLE001
        PrintStyle.debug(f"chat_history: deletion reconcile skipped: {exc}")
    return deleted


def snapshot_stats() -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        conn = db.get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT count(*), coalesce(max(saved_at), NULL) FROM context_snapshots")
            count, newest = cur.fetchone()
            out["snapshots"] = count
            out["newest_snapshot_at"] = str(newest or "")
    except Exception as exc:  # noqa: BLE001
        out["snapshots_error"] = str(exc)
    return out


def capture_enabled() -> bool:
    return settings.storage_mode() == "authoritative"
