"""One-time import of existing usr/chats JSON history into the database.

chat.json stores each agent's History as a nested ``_cls``-tagged blob; the
importer walks it collecting ``_cls == "Message"`` entries. Fidelity is
best-effort (this is a backfill of the replica; the live sync is the
authoritative path going forward).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from helpers.print_style import PrintStyle

from usr.plugins.chat_history.helpers import db
from usr.plugins.chat_history.helpers.sync import _content_text, _tool_name


def _walk_messages(node: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(node, dict):
        if node.get("_cls") == "Message":
            out.append(node)
        for value in node.values():
            _walk_messages(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk_messages(item, out)


def _context_metadata(data: dict[str, Any]) -> dict[str, Any]:
    output_data = data.get("output_data") or {}
    context_data = data.get("data") or {}
    parent_id = str(
        output_data.get("parent_context_id")
        or context_data.get("parent_context_id")
        or context_data.get("_parallel_parent_context_id")
        or ""
    ).strip()
    is_subagent = bool(
        parent_id
        or output_data.get("parent_context_kind")
        or context_data.get("_parallel_worker_kind")
    )
    return {
        "context_id": str(data.get("id") or ""),
        "name": str(data.get("name") or ""),
        "type": str(data.get("type") or "user"),
        "profile": str(data.get("agent_profile") or ""),
        "parent_context_id": parent_id,
        "is_subagent": is_subagent,
        "created_at": _parse_ts(data.get("created_at")),
    }


def _upsert_context_metadata(metadata: dict[str, Any]) -> None:
    if not metadata["context_id"]:
        return
    conn = db.get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO contexts (
                id, name, type, agent_profile, parent_context_id,
                is_subagent, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                type = EXCLUDED.type,
                agent_profile = EXCLUDED.agent_profile,
                parent_context_id = EXCLUDED.parent_context_id,
                is_subagent = EXCLUDED.is_subagent
            """,
            (
                metadata["context_id"],
                metadata["name"],
                metadata["type"],
                metadata["profile"],
                metadata["parent_context_id"],
                metadata["is_subagent"],
                metadata["created_at"],
            ),
        )


def sync_persisted_context_metadata(chats_dir: Path | None = None) -> int:
    """Backfill context hierarchy/profile fields from stock chat snapshots."""
    if chats_dir is None:
        from helpers.files import get_abs_path

        chats_dir = Path(get_abs_path("usr", "chats"))
    updated = 0
    for chat_file in sorted(chats_dir.glob("*/chat.json")):
        try:
            data = json.loads(chat_file.read_text(encoding="utf-8"))
            metadata = _context_metadata(data)
            if not metadata["context_id"]:
                metadata["context_id"] = chat_file.parent.name
            _upsert_context_metadata(metadata)
            updated += 1
        except (OSError, ValueError):
            continue
        except Exception as exc:  # noqa: BLE001
            PrintStyle.debug(
                f"chat_history: metadata backfill skipped {chat_file.parent.name}: {exc}"
            )
    return updated


def import_json_chats(chats_dir: Path | None = None) -> dict[str, int]:
    imported_contexts = 0
    imported_messages = 0
    try:
        if chats_dir is None:
            from helpers.files import get_abs_path

            chats_dir = Path(get_abs_path("usr", "chats"))

        for chat_file in sorted(chats_dir.glob("*/chat.json")):
            try:
                data = json.loads(chat_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            metadata = _context_metadata(data)
            context_id = metadata["context_id"] or chat_file.parent.name
            metadata["context_id"] = context_id

            messages: list[dict[str, Any]] = []
            for agent in data.get("agents") or []:
                blob = agent.get("history")
                if isinstance(blob, str):
                    try:
                        blob = json.loads(blob)
                    except ValueError:
                        continue
                _walk_messages(blob, messages)

            conn = db.get_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM contexts WHERE id = %s", (context_id,))
                existed = cur.fetchone() is not None
            _upsert_context_metadata(metadata)
            imported_contexts += 0 if existed else 1
            if not messages:
                continue

            class _Shim:  # reuse sync_messages row shaping
                pass

            shim_msgs = [
                {
                    "id": str(m.get("id") or f"{context_id}-import-{index}"),
                    "ai": bool(m.get("ai")),
                    "content": m.get("content"),
                }
                for index, m in enumerate(messages)
                if _content_text(m.get("content")).strip()
            ]
            inserted = _insert_imported(context_id, shim_msgs)
            imported_messages += inserted
            # sync_messages uses a minimal context shim during import; restore
            # the exact persisted hierarchy/profile fields afterward.
            _upsert_context_metadata(metadata)
    except Exception as exc:  # noqa: BLE001
        PrintStyle.warning(f"chat_history: JSON import failed: {exc}")
    return {"contexts": imported_contexts, "messages": imported_messages}


def _insert_imported(context_id: str, messages: list[dict[str, Any]]) -> int:
    from usr.plugins.chat_history.helpers.sync import sync_messages

    class _Ctx:
        id = context_id
        name = ""
        type = None

    inserted = 0
    # Import in chunks; each chunk continues the seq counter.
    chunk = 200
    for start in range(0, len(messages), chunk):
        inserted += sync_messages(context_id, messages[start : start + chunk], context=_Ctx())
    return inserted


def _parse_ts(value: Any):
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def export_context(context_id: str, out_dir: Path | None = None) -> Path:
    """Escape hatch: dump a context's stored messages to readable JSONL."""
    if out_dir is None:
        from helpers.files import get_abs_path

        out_dir = Path(get_abs_path("usr", "exports", "chat_history"))
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = db.get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT seq, ai, tool_name, created_at, content_text
            FROM messages WHERE context_id = %s ORDER BY seq
            """,
            (context_id,),
        )
        rows = cur.fetchall()
    path = out_dir / f"{context_id}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for seq, ai, tool, created, text in rows:
            fh.write(
                json.dumps(
                    {
                        "seq": seq,
                        "ai": ai,
                        "tool": tool,
                        "created_at": str(created),
                        "text": text,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    return path
