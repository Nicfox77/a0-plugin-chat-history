"""Continuous Mode: short-circuit a stray monologue flagged by the redirect hook.

Picks up the ``_continuous_mode_redirect`` context-data flag set by the
``user_message_ui`` hook and pre-empts the original ``Agent.monologue`` by
setting ``data["result"]`` on the @extensible decorator's mutable payload.
This is the same mechanism the bundled ``_error_retry`` plugin uses to
short-circuit the same function from its own monologue/start hook.
"""
from __future__ import annotations

from helpers.extension import Extension

from usr.plugins.chat_history.helpers.enforce import (
    REDIRECT_FLAG,
    REDIRECT_NOTE,
)


class ShortCircuitRedirect(Extension):
    async def execute(self, data: dict | None = None, **kwargs):
        if data is None:
            return
        agent = self.agent
        if agent is None:
            return
        context = getattr(agent, "context", None)
        if context is None:
            return
        ctx_data = getattr(context, "data", None)
        if not isinstance(ctx_data, dict):
            return
        if not ctx_data.get(REDIRECT_FLAG):
            return
        ctx_data.pop(REDIRECT_FLAG, None)
        data["result"] = REDIRECT_NOTE
