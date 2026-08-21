"""Continuous Mode: route stray main-chat user messages to the primary.

When Continuous Mode is on and the inbound message targets a non-primary
root USER context, this hook dispatches a redirect to the primary
context and short-circuits the stray's own turn via a flag picked up by the
monologue/start extension.

The stray context still receives the (empty) message so message.py's
``context.communicate(UserMessage(...))`` keeps running and the API responds
normally; the empty payload and the flag are enough for the short-circuit to
return the routing note instead of running an LLM monologue.
"""
from __future__ import annotations

from helpers.extension import Extension
from helpers.print_style import PrintStyle

from usr.plugins.chat_history.helpers.enforce import (
    REDIRECT_FLAG,
    should_redirect,
)


class EnforceSingleChat(Extension):
    async def execute(self, data: dict | None = None, **kwargs):
        if data is None:
            return
        agent = self.agent
        primary = should_redirect(agent)
        if primary is None:
            return

        context = getattr(agent, "context", None)
        if context is None:
            return

        original_message = data.get("message", "") or ""
        attachments = data.get("attachment_paths") or []
        context_data = getattr(context, "data", None)
        if not isinstance(context_data, dict):
            return

        try:
            from agent import UserMessage
        except Exception as exc:  # noqa: BLE001
            PrintStyle.warning(
                f"chat_history: redirect aborted, UserMessage import failed: {exc}"
            )
            return

        try:
            # communicate() starts and returns a DeferredTask. Upstream's
            # DeferredTask is intentionally not awaitable.
            primary.communicate(
                UserMessage(
                    message=original_message,
                    attachments=list(attachments),
                )
            )
        except Exception as exc:  # noqa: BLE001
            PrintStyle.warning(
                f"chat_history: primary redirect dispatch failed: {exc}"
            )
            return

        context_data[REDIRECT_FLAG] = True

        try:
            from agent import AgentContextLog

            log = getattr(context, "log", None)
            if log is not None and hasattr(log, "log"):
                log.log(
                    type="info",
                    heading="Continuous Mode",
                    content=(
                        "Message routed to the main chat — "
                        "Continuous Mode keeps a single main conversation."
                    ),
                )
        except Exception:
            pass

        data["message"] = ""
