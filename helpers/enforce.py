"""Continuous Mode single-chat enforcement decision helper.

Pure decision function: returns the live primary AgentContext when the given
agent's context should be redirected to it, or None when the inbound message
should run in place. Centralized here so it is unit-testable without booting
the framework and so the user_message_ui hook stays a thin caller.
"""
from __future__ import annotations

from typing import Any, Callable, Optional


REDIRECT_FLAG = "_continuous_mode_redirect"
REDIRECT_NOTE = (
    "Continuous Mode: your message was routed to the main chat. "
    "Switch to the main conversation to continue."
)


def _is_root_agent(agent: Any) -> bool:
    number = getattr(agent, "number", None)
    if number == 0:
        return True
    return getattr(agent, "agent_number", None) == 0


def _context_data(context: Any) -> dict:
    return getattr(context, "data", {}) or {}


def _is_user_context(context: Any) -> bool:
    try:
        from agent import AgentContextType
    except Exception:
        return False
    return getattr(context, "type", None) == AgentContextType.USER


def _is_parallel_worker(context: Any) -> bool:
    data = _context_data(context)
    output_data = getattr(context, "output_data", {}) or {}
    if data.get("_parallel_worker_kind"):
        return True
    if data.get("parent_context_id"):
        return True
    if output_data.get("parent_context_id"):
        return True
    return False


def _is_project_bound(context: Any) -> bool:
    try:
        from helpers import projects

        return bool(projects.get_context_project_name(context))
    except Exception:
        return False


def _resolve_primary():
    """Resolve the live primary AgentContext, or None when none exists.

    Order: canonical context id when live and valid, then another eligible
    root user context as recovery from deletion or incomplete startup state.
    """
    try:
        from agent import AgentContext, AgentContextType
    except Exception:
        return None

    try:
        from usr.plugins.chat_history.helpers.primary import (
            resolve_primary_context_id,
        )
    except Exception:
        resolve_primary_context_id = lambda: ""  # noqa: E731

    bound = resolve_primary_context_id()
    if bound:
        candidate = AgentContext.get(bound)
        if (
            candidate is not None
            and getattr(candidate, "type", None) == AgentContextType.USER
            and not _is_parallel_worker(candidate)
            # Project binding does not disqualify the pinned primary; see
            # chat_history.helpers.primary._is_root_user_context.
        ):
            return candidate

    contexts = (
        AgentContext.all()
        if hasattr(AgentContext, "all")
        else [AgentContext.first()]
    )
    for context in contexts:
        if context is None:
            continue
        if (
            getattr(context, "type", None) == AgentContextType.USER
            and not _is_parallel_worker(context)
            and not _is_project_bound(context)
        ):
            return context
    return None


def should_redirect(
    agent: Any,
    *,
    primary_resolver: Optional[Callable[[], Any]] = None,
) -> Any:
    """Return the live primary AgentContext when the agent's context should be
    routed to it; None when the inbound message should run in place.

    Exemptions (None returned):

    - ``settings.continuous_mode()`` is off (gate).
    - ``agent`` is None.
    - ``agent.number`` (or ``agent.agent_number``) is not 0 (subagents).
    - ``agent.context`` is missing or not USER-type.
    - the context carries ``_parallel_worker_kind`` or ``parent_context_id``.
    - the context is bound to a project (pipe-demo etc.).
    - the agent's context IS the primary itself.
    - no live primary exists.
    """
    if agent is None:
        return None
    try:
        from usr.plugins.chat_history.helpers import settings
    except Exception:
        return None
    if not settings.continuous_mode():
        return None
    if not _is_root_agent(agent):
        return None

    context = getattr(agent, "context", None)
    if context is None or not _is_user_context(context):
        return None

    if _is_parallel_worker(context):
        return None
    if _is_project_bound(context):
        return None

    resolver = primary_resolver or _resolve_primary
    primary = resolver()
    if primary is None:
        return None
    if str(getattr(primary, "id", "")) == str(getattr(context, "id", "")):
        return None
    return primary
