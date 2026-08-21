"""GET /api/plugins/chat_history/pinned — return the resolved primary chat id.

Browser-facing: a logged-in web UI reads this on initial load AND on every
chat-list mutation to pin the main chat row to the top. Default auth so the
browser session can call it; NOT loopback (the web UI is not on 127.0.0.1).

Returns ``{"context_id": ..., "name": ..., "continuous_mode": ...}`` when a
primary resolves; ``{"context_id": null, ...}`` otherwise. Gate is
``continuous_mode() and pin_main_chat()`` — both must be on (consistent with
``chat_list`` ordering).
"""
from helpers.api import ApiHandler, Request, Response

from usr.plugins.chat_history.helpers import primary as _primary
from usr.plugins.chat_history.helpers import settings


class ChatHistoryPinned(ApiHandler):
    @classmethod
    def requires_auth(cls) -> bool:
        return True

    @classmethod
    def requires_csrf(cls) -> bool:
        return True

    @classmethod
    def requires_loopback(cls) -> bool:
        return False

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET"]

    async def process(self, input: dict, request: Request) -> dict | Response:
        continuous = settings.continuous_mode()
        pin = settings.pin_main_chat()
        if not (continuous and pin):
            return {
                "ok": False,
                "message": "Continuous Mode and Pin Primary Chat must both be on.",
                "continuous_mode": continuous,
                "pin_main_chat": pin,
                "context_id": None,
                "name": None,
            }

        context_id = _primary.resolve_primary_context_id() or None
        name = None
        if context_id:
            try:
                from agent import AgentContext

                ctx = AgentContext.get(context_id)
                if ctx is not None:
                    name = str(getattr(ctx, "name", "") or "") or None
            except Exception:
                pass
            if name is None:
                try:
                    from helpers.files import get_abs_path

                    chat_json = get_abs_path("usr", "chats", context_id, "chat.json")
                    import json
                    import os

                    if os.path.isfile(chat_json):
                        with open(chat_json, encoding="utf-8") as fh:
                            data = json.load(fh)
                        name = (data.get("name") or "") or None
                except Exception:
                    pass

        return {
            "ok": True,
            "continuous_mode": continuous,
            "pin_main_chat": pin,
            "context_id": context_id,
            "name": name,
        }