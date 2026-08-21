"""Status and loopback maintenance endpoint for chat-history state."""

import asyncio

from helpers.api import ApiHandler, Request, Response


class ChatHistoryStatus(ApiHandler):
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
        from usr.plugins.chat_history.helpers import db, pin, primary, settings
        from usr.plugins.chat_history.helpers.authoritative import (
            capture_context,
            capture_enabled,
            snapshot_stats,
        )
        from usr.plugins.chat_history.helpers.deck import entry_count

        action = str(input.get("action") or "status").strip().lower()
        if action not in {"status", "enforce_primary_name"}:
            return {
                "ok": False,
                "error": (
                    "Unknown action. Use 'status' or "
                    "'enforce_primary_name'."
                ),
            }

        renamed = None
        if action == "enforce_primary_name":
            renamed = pin.assert_primary_name()

        primary_id = primary.resolve_primary_context_id()
        primary_context = None
        try:
            from agent import AgentContext

            primary_context = AgentContext.get(primary_id) if primary_id else None
        except Exception:
            primary_context = None

        if action == "enforce_primary_name" and primary_context is not None:
            from usr.plugins.chat_history.helpers.sync import sync_agent

            await asyncio.to_thread(sync_agent, primary_context.agent0)
            if capture_enabled():
                await asyncio.to_thread(capture_context, primary_context)

        status = db.stats()
        status["storage_mode"] = settings.storage_mode()
        status.update(snapshot_stats())
        status["continuous_mode"] = settings.continuous_mode()
        status["primary_context_id"] = primary_id or None
        status["primary_live"] = bool(primary_context)
        status["primary_name"] = (
            str(getattr(primary_context, "name", "") or "") or None
        )
        status["primary_target_name"] = settings.main_chat_name()
        status["primary_renamed"] = renamed
        status["deck_entries"] = entry_count()
        status["last_compact_at"] = db.get_meta("last_compact_at")
        if primary_context is not None:
            from usr.plugins.chat_history.helpers import rolling

            primary_agent = primary_context.agent0
            status["compaction_policy"] = {
                "context_length": rolling.context_length(primary_agent),
                "history_ratio": rolling.history_ratio(primary_agent),
                "trigger_tokens": rolling.compaction_threshold(primary_agent),
                "evict_ratio": rolling.eviction_ratio(),
                "evict_tokens": rolling.eviction_target(primary_agent),
                "summary_output_ratio": rolling.SUMMARY_OUTPUT_RATIO,
                "summary_output_tokens": rolling.summary_output_budget(
                    rolling.eviction_target(primary_agent)
                ),
            }
        status["import_done"] = db.get_meta("json_import_done") == "1"
        status["import_pending"] = db.get_meta("json_import_pending") == "1"
        status["pgvector"] = db.vector_column_ready()
        status["boot_error"] = db.boot_error()
        return status
