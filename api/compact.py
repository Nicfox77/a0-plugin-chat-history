"""Operator endpoint for Continuous Mode rolling compaction.

POST /api/plugins/chat_history/compact   {"context_id": "..."}  (optional;
defaults to the resolved primary chat) — run one compaction cycle now,
regardless of the token threshold. Returns deck stats.

GET same path — deck stats only, no compaction.

Action payloads (POST body):
    (action omitted) — run a single rolling cycle on the resolved primary
        chat.
    action="trim", keep=N — manual deck maintenance; keeps the newest N
        entries (default 20), returns the deletion count and fresh stats.
    action="full" (or "full-archive") — archive the ENTIRE native history
        into the summary deck in boundary-aligned chunks; chat history is
        cleared, continuity is retained via the deck (injected every turn
        regardless of history size).

Loopback-only. 409s when Continuous Mode is off.
"""

from helpers.api import ApiHandler, Request, Response
from helpers.print_style import PrintStyle


class ChatHistoryCompact(ApiHandler):
    @classmethod
    def requires_auth(cls) -> bool:
        return False

    @classmethod
    def requires_csrf(cls) -> bool:
        return False

    @classmethod
    def requires_loopback(cls) -> bool:
        return True

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET", "POST"]

    async def process(self, input: dict, request: Request) -> dict | Response:
        from usr.plugins.chat_history.helpers import db, settings
        from usr.plugins.chat_history.helpers.deck import entry_count

        def stats(context_id: str) -> dict:
            return {
                "context_id": context_id,
                "continuous_mode": settings.continuous_mode(),
                "deck_entries": entry_count(),
                "last_compact_at": db.get_meta("last_compact_at"),
            }

        if not settings.continuous_mode():
            return {
                "ok": False,
                "message": "Continuous Mode is off; enable it in the chat_history settings.",
            }

        # Manual deck maintenance: keep every summary forever by default;
        # trimming is an explicit operator action.
        if str(input.get("action") or "").strip().lower() == "trim":
            from usr.plugins.chat_history.helpers.deck import trim_deck

            keep = input.get("keep", 20)
            try:
                keep = max(0, int(keep))
            except (TypeError, ValueError):
                return {"ok": False, "message": "keep must be an integer."}
            deleted = trim_deck(keep)
            return {
                "ok": True,
                "message": f"Trimmed {deleted} deck entries (kept newest {keep}).",
                "stats": stats(""),
            }

        action = str(input.get("action") or "").strip().lower()

        from usr.plugins.chat_history.helpers.primary import resolve_primary_context_id

        context_id = str(input.get("context_id") or "").strip() or resolve_primary_context_id()
        if not context_id:
            return {"ok": False, "message": "No primary chat bound and no context_id given."}

        if request.method == "GET":
            return {"ok": True, "stats": stats(context_id)}

        from agent import AgentContext

        context = AgentContext.get(context_id)
        if context is None:
            return {"ok": False, "message": f"Context '{context_id}' not found (live contexts only)."}
        if context.is_running():
            return {"ok": False, "message": "Context is running; retry when idle."}

        if action == "full-archive" or action == "full":
            from usr.plugins.chat_history.helpers.rolling import run_full_compaction

            try:
                import asyncio

                archived = await asyncio.wait_for(
                    run_full_compaction(context), timeout=1800
                )
            except Exception as exc:
                PrintStyle.error(f"chat_history: full archive failed: {exc}")
                return {"ok": False, "message": str(exc)}
            return {
                "ok": bool(archived),
                "archived": archived,
                "message": (
                    f"Archived {archived} segment(s); history cleared."
                    if archived
                    else "Nothing archived (empty summary or no history)."
                ),
                "stats": stats(context_id),
            }

        from usr.plugins.chat_history.helpers.rolling import run_rolling_compaction

        try:
            import asyncio

            compacted = await asyncio.wait_for(run_rolling_compaction(context), timeout=900)
        except Exception as exc:
            PrintStyle.error(f"chat_history: manual compaction failed: {exc}")
            return {"ok": False, "message": str(exc)}

        return {"ok": bool(compacted), "stats": stats(context_id)}
