"""Reconcile canonical user history with the WebUI conversation log."""

from __future__ import annotations

import os
from typing import Any


def _message_value(message: Any, key: str, default: Any = None) -> Any:
    if isinstance(message, dict):
        return message.get(key, default)
    return getattr(message, key, default)


def _user_content(message: Any) -> tuple[str, list[str]] | None:
    if bool(_message_value(message, "ai", False)):
        return None
    content = _message_value(message, "content")
    if isinstance(content, str):
        text = content.strip()
        return (text, []) if text else None
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str) and block.strip():
                parts.append(block.strip())
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        text = "\n".join(parts)
        return (text, []) if text else None
    if not isinstance(content, dict):
        return None

    if content.get("type") in {
        "framework_notification",
        "history_summary",
        "tool_result",
    }:
        return None
    if any(
        key in content
        for key in (
            "tool_name",
            "native_tool_name",
            "tool_result",
            "tool_call_id",
            "native_tool_calls",
        )
    ):
        return None

    text = ""
    for key in ("user_intervention", "user_message", "message", "text"):
        value = content.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            break
    if not text:
        return None
    attachments = content.get("attachments")
    names = [
        os.path.basename(str(path))
        for path in attachments
        if str(path).strip()
    ] if isinstance(attachments, list) else []
    return text, names


def repair_context_user_log(context: Any) -> int:
    """Insert missing user log items in canonical history order.

    Agent history is the source of truth for model context. The WebUI log is a
    separate presentation stream, so an ingress path can accidentally persist
    the former without the latter. Reconciliation is ID-based and idempotent.
    """

    agent = getattr(context, "agent0", None)
    if agent is None and hasattr(context, "get_agent"):
        try:
            agent = context.get_agent()
        except Exception:
            # A failed chat restore can leave a partially initialized
            # context in the registry (no agent0); skip it rather than
            # crash the initialize_chats repair pass.
            agent = None
    log = getattr(context, "log", None)
    history = getattr(agent, "history", None)
    if log is None or history is None:
        return 0
    try:
        messages = list(history.output())
    except Exception:
        return 0

    history_positions: dict[str, int] = {}
    candidates: list[tuple[int, str, str, list[str]]] = []
    for position, message in enumerate(messages):
        message_id = str(_message_value(message, "id", "") or "")
        if not message_id:
            continue
        history_positions.setdefault(message_id, position)
        projected = _user_content(message)
        if projected is not None:
            candidates.append((position, message_id, projected[0], projected[1]))

    with log._lock:
        original = list(log.logs)
        existing_ids = {str(item.id) for item in original if item.id}
        try:
            progress_index = int(log.progress_no)
        except (TypeError, ValueError):
            progress_index = -1
        progress_item = (
            original[progress_index]
            if 0 <= progress_index < len(original)
            else None
        )

    missing = [item for item in candidates if item[1] not in existing_ids]
    if not missing:
        return 0

    created: list[tuple[int, Any]] = []
    for position, message_id, text, attachments in missing:
        item = log.log(
            type="user",
            heading="",
            content=text,
            kvps={"attachments": attachments},
            update_progress="none",
            id=message_id,
        )
        created.append((position, item))

    merged = list(original)
    for position, item in created:
        insert_at = len(merged)
        for index, existing in enumerate(merged):
            anchor = history_positions.get(str(existing.id or ""))
            if anchor is not None and anchor > position:
                insert_at = index
                break
        merged.insert(insert_at, item)

    created_ids = {id(item) for _, item in created}
    index = 0
    while index < len(merged):
        if id(merged[index]) not in created_ids:
            index += 1
            continue
        start = index
        while index < len(merged) and id(merged[index]) in created_ids:
            index += 1
        end = index
        previous = merged[start - 1].timestamp if start else 0.0
        following = merged[end].timestamp if end < len(merged) else 0.0
        count = end - start
        if previous and following and following > previous:
            step = (following - previous) / (count + 1)
            for offset in range(count):
                merged[start + offset].timestamp = previous + step * (offset + 1)
        elif following:
            for offset in range(count):
                merged[start + offset].timestamp = following - 0.001 * (count - offset)
        elif previous:
            for offset in range(count):
                merged[start + offset].timestamp = previous + 0.001 * (offset + 1)

    with log._lock:
        log.logs = merged
        for number, item in enumerate(log.logs):
            item.no = number
        log.updates = list(range(len(log.logs)))
        if progress_item in log.logs:
            log.progress_no = log.logs.index(progress_item)
    log._notify_state_monitor()
    return len(created)
