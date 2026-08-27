"""Durable primary-chat identity for Continuous Mode.

The primary chat is owned by chat_history, not by any transport. Its context
id is persisted under ``usr/state/chat_history`` and survives restarts. A
Telegram binding is used only to migrate installations that predate this
state file; live root user contexts are the final fallback. Agent profile is
deliberately not part of primary identity, so changing the selected profile
does not break Continuous Mode.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path


_STATE_LOCK = threading.RLock()
_STATE_VERSION = 1


def _state_path() -> Path:
    from helpers.files import get_abs_path

    return Path(get_abs_path("usr", "state", "chat_history", "primary.json"))


def _chat_path(context_id: str) -> Path:
    from helpers.files import get_abs_path

    return Path(get_abs_path("usr", "chats", context_id, "chat.json"))


def _read_state() -> str:
    try:
        payload = json.loads(_state_path().read_text(encoding="utf-8"))
        return str(payload.get("context_id") or "").strip()
    except Exception:
        return ""


def set_primary_context_id(context_id: str) -> str:
    """Persist the canonical context id atomically and return it."""
    context_id = str(context_id or "").strip()
    if not context_id:
        return ""
    with _STATE_LOCK:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp.{os.getpid()}.{threading.get_ident()}")
        tmp.write_text(
            json.dumps(
                {"version": _STATE_VERSION, "context_id": context_id},
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    return context_id


def _telegram_context_ids() -> list[str]:
    try:
        from helpers.files import get_abs_path

        path = Path(
            get_abs_path("usr", "plugins", "_telegram_integration", "state.json")
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        chats = payload.get("chats") or {}
        if not isinstance(chats, dict):
            return []
        return list(dict.fromkeys(str(value) for value in chats.values() if value))
    except Exception:
        return []


def _is_root_user_context(context) -> bool:
    if context is None:
        return False
    try:
        from agent import AgentContextType

        if getattr(context, "type", None) != AgentContextType.USER:
            return False
    except Exception:
        return False
    data = getattr(context, "data", {}) or {}
    output_data = getattr(context, "output_data", {}) or {}
    if data.get("parent_context_id") or output_data.get("parent_context_id"):
        return False
    # NOTE: project binding deliberately does NOT disqualify the primary.
    # A main chat activated into a project (e.g. via the projects tool) is
    # still the main chat; rejecting it orphaned the primary and spawned a
    # duplicate empty "Main" after restarts (incident 2026-08-23).
    return True


def _is_persisted_root_user(context_id: str) -> bool:
    try:
        payload = json.loads(_chat_path(context_id).read_text(encoding="utf-8"))
    except Exception:
        return False
    if payload.get("type") != "user":
        return False
    data = payload.get("data") or {}
    output_data = payload.get("output_data") or {}
    if data.get("parent_context_id") or output_data.get("parent_context_id"):
        return False
    # Project binding does not disqualify the persisted primary (see
    # _is_root_user_context).
    return True


def _valid_context_id(context_id: str) -> bool:
    if not context_id:
        return False
    try:
        from agent import AgentContext

        live = AgentContext.get(context_id)
    except Exception:
        live = None
    if live is not None:
        return _is_root_user_context(live)
    return _is_persisted_root_user(context_id)


def _live_candidates() -> list:
    try:
        from agent import AgentContext

        return [ctx for ctx in AgentContext.all() if _is_root_user_context(ctx)]
    except Exception:
        return []


def resolve_primary_context_id() -> str:
    """Return the canonical root user context, claiming one if necessary."""
    with _STATE_LOCK:
        stored = _read_state()
        if _valid_context_id(stored):
            return stored

        for context_id in _telegram_context_ids():
            if _valid_context_id(context_id):
                return set_primary_context_id(context_id)

        candidates = _live_candidates()
        if not candidates:
            return ""

        try:
            from usr.plugins.chat_history.helpers import settings

            target_name = settings.main_chat_name()
        except Exception:
            target_name = "main"
        named = [
            ctx
            for ctx in candidates
            if str(getattr(ctx, "name", "") or "").strip() == target_name
        ]
        chosen = (named or candidates)[0]
        return set_primary_context_id(str(chosen.id))


def resolve_primary_context():
    """Return the live canonical context, or None before chats are loaded."""
    context_id = resolve_primary_context_id()
    if not context_id:
        return None
    try:
        from agent import AgentContext

        context = AgentContext.get(context_id)
        return context if _is_root_user_context(context) else None
    except Exception:
        return None


def hydrate_primary_context():
    """Materialize the persisted primary into the live registry if needed.

    After a service restart the stock chat loader may not have registered
    the primary yet (deferred load, idle unload, partial boot). Transport
    handlers call this before falling back to spawning a fresh context, so
    a durable pin always wins over a new empty chat.
    """
    try:
        from agent import AgentContext
    except Exception:
        return None
    context_id = resolve_primary_context_id()
    if not context_id:
        return None
    live = AgentContext.get(context_id)
    if live is not None:
        return live if _is_root_user_context(live) else None
    if not _valid_context_id(context_id):
        return None
    try:
        from helpers import persist_chat

        data = json.loads(_chat_path(context_id).read_text(encoding="utf-8"))
        context = persist_chat._deserialize_context(data)
        persist_chat.mark_chat_saved(context)
        return context
    except Exception:
        return None


def is_primary_context(context) -> bool:
    """True when ``context`` is the durable canonical primary chat."""
    if context is None:
        return False
    primary_id = resolve_primary_context_id()
    return bool(primary_id) and str(getattr(context, "id", "")) == primary_id
