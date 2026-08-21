"""Dual-write sync: mirror live agent history into the database.

Idempotent: every message carries a stable id, so re-syncing a whole history
is a no-op for already-stored rows (ON CONFLICT DO NOTHING). Captures every
context — main chat, subagents, parallel workers, background tasks — because
the sync hook fires on every agent's monologue end.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from helpers.print_style import PrintStyle

from usr.plugins.chat_history.helpers import db

_sync_lock = threading.Lock()


def _content_text(content: Any) -> str:
    """Best-effort plain-text projection of a message content for FTS."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_content_text(item) for item in content if item is not None)
    if isinstance(content, dict):
        if content.get("type") == "tool_result":
            return ""
        if content.get("type") == "framework_notification":
            return str(content.get("notification") or "")
        for key in ("user_message", "message", "text", "raw_content", "preview"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return value
        tool = content.get("tool_name") or content.get("native_tool_name") or ""
        return f"[tool: {tool}]" if tool else ""
    return str(content)


def _tool_name(content: Any) -> str:
    if not isinstance(content, dict):
        return ""
    if content.get("type") == "framework_notification":
        return "_background_notification"
    for key in ("tool_name", "native_tool_name"):
        if content.get(key):
            return str(content[key])
    calls = content.get("native_tool_calls")
    if isinstance(calls, list) and calls:
        first = calls[0] if isinstance(calls[0], dict) else {}
        function = first.get("function") if isinstance(first, dict) else None
        if isinstance(function, dict) and function.get("name"):
            return str(function["name"])
    return ""


def sync_agent(agent: Any) -> int:
    """Upsert an agent's full history; returns the number of new rows."""
    context = getattr(agent, "context", None)
    context_id = str(getattr(context, "id", "") or "")
    if not context_id:
        return 0
    try:
        messages = list(agent.history.output())
    except Exception:
        return 0
    if not messages:
        return 0
    return sync_messages(context_id, messages, context=context, agent=agent)


def _context_subagent_fields(context: Any) -> tuple[str, bool]:
    """Return (parent_context_id, is_subagent) from a live context's data.

    The stock subordinate machinery (``tools/call_subordinate.py``) sets
    ``parent_context_id`` directly on the child's context data. The parallel
    job layer (``helpers/parallel_tools``) instead sets
    ``_parallel_parent_context_id`` and ``_parallel_worker_kind`` — both
    indicate this context is a child of another; treat the union as
    "is_subagent" for chat_list surfacing.
    """
    if context is None:
        return "", False
    data = getattr(context, "data", {}) or {}
    output_data = getattr(context, "output_data", {}) or {}
    parent = str(
        output_data.get("parent_context_id")
        or data.get("parent_context_id")
        or data.get("_parallel_parent_context_id")
        or ""
    ).strip()
    is_subagent = bool(
        output_data.get("parent_context_kind")
        or output_data.get("parent_context_id")
        or data.get("_parallel_worker_kind")
        or data.get("parent_context_id")
        or data.get("_parallel_parent_context_id")
    )
    return parent, is_subagent


def sync_messages(
    context_id: str,
    messages: list[Any],
    context: Any = None,
    agent: Any = None,
) -> int:
    """Store new messages of a context; returns inserted count."""
    if not messages:
        return 0
    with _sync_lock:
        try:
            conn = db.get_connection()
        except RuntimeError as exc:
            PrintStyle.warning(f"chat_history: sync skipped ({exc})")
            return 0

        context_name = str(getattr(context, "name", "") or "") if context else ""
        context_type = str(
            getattr(getattr(context, "type", None), "value", "") or ""
        ) if context else ""
        profile = str(
            getattr(getattr(agent, "config", None), "profile", "") or ""
        ) if agent else ""
        parent_id, is_subagent = _context_subagent_fields(context)

        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO contexts (id, name, type, agent_profile,
                                          parent_context_id, is_subagent, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        type = EXCLUDED.type,
                        agent_profile = EXCLUDED.agent_profile,
                        parent_context_id = EXCLUDED.parent_context_id,
                        is_subagent = EXCLUDED.is_subagent
                    """,
                    (
                        context_id,
                        context_name,
                        context_type,
                        profile,
                        parent_id,
                        is_subagent,
                    ),
                )
                cur.execute(
                    "SELECT coalesce(max(seq), 0) FROM messages WHERE context_id = %s",
                    (context_id,),
                )
                seq = cur.fetchone()[0]

                rows = []
                last_created = None
                for message in messages:
                    content = (
                        message.get("content")
                        if isinstance(message, dict)
                        else getattr(message, "content", None)
                    )
                    message_id = str(
                        (message.get("id") if isinstance(message, dict) else getattr(message, "id", ""))
                        or ""
                    )
                    if not message_id:
                        continue
                    ai = bool(
                        message.get("ai") if isinstance(message, dict) else getattr(message, "ai", False)
                    )
                    seq += 1
                    last_created = datetime.now(timezone.utc)
                    rows.append(
                        (
                            context_id,
                            message_id,
                            seq,
                            ai,
                            _tool_name(content),
                            last_created,
                            db.dumps(content if not isinstance(content, str) else content),
                            _content_text(content)[:20000],
                        )
                    )

                if not rows:
                    return 0

                inserted = 0
                for row in rows:
                    cur.execute(
                        """
                        INSERT INTO messages (context_id, message_id, seq, ai,
                                              tool_name, created_at, content, content_text)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (context_id, message_id) DO NOTHING
                        """,
                        row,
                    )
                    inserted += cur.rowcount

                if inserted:
                    cur.execute(
                        """
                        UPDATE contexts SET
                            msg_count = (SELECT count(*) FROM messages WHERE context_id = %s),
                            last_message_at = %s
                        WHERE id = %s
                        """,
                        (context_id, last_created, context_id),
                    )
            return inserted
        except Exception as exc:  # noqa: BLE001
            PrintStyle.warning(f"chat_history: sync failed: {exc}")
            return 0


def sync_all_live_contexts() -> int:
    """Sync every live AgentContext (job-loop safety net)."""
    total = 0
    try:
        from agent import AgentContext

        with AgentContext._contexts_lock:
            contexts = list(AgentContext._contexts.values())
        for context in contexts:
            agent = getattr(context, "agent0", None) or (
                context.get_agent() if hasattr(context, "get_agent") else None
            )
            if agent is None:
                continue
            total += sync_agent(agent)
    except Exception as exc:  # noqa: BLE001
        PrintStyle.debug(f"chat_history: live resync skipped: {exc}")
    return total
