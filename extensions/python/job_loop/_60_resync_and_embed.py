"""Periodic safety net: resync live contexts and run one embedding pass."""

import asyncio
import os

from helpers.extension import Extension
from helpers.print_style import PrintStyle

_TICK = {"count": 0}
_METADATA_SYNCED = {"done": False}


def _interval() -> int:
    """Run every N job-loop ticks (each tick is roughly a minute)."""
    try:
        return max(1, int(os.environ.get("CH_JOB_INTERVAL_TICKS", "5")))
    except ValueError:
        return 5


class ResyncAndEmbed(Extension):
    async def execute(self, **kwargs):
        del kwargs
        try:
            from usr.plugins.chat_history.helpers.pin import assert_primary_name

            await asyncio.to_thread(assert_primary_name)
        except Exception as exc:  # noqa: BLE001
            PrintStyle.debug(f"chat_history: idle name lock skipped: {exc}")

        if not _METADATA_SYNCED["done"]:
            try:
                from usr.plugins.chat_history.helpers.import_json import (
                    sync_persisted_context_metadata,
                )

                await asyncio.to_thread(sync_persisted_context_metadata)
                _METADATA_SYNCED["done"] = True
            except Exception as exc:  # noqa: BLE001
                PrintStyle.debug(
                    f"chat_history: metadata backfill tick skipped: {exc}"
                )
        try:
            from usr.plugins.chat_history.helpers import db

            if db.get_meta("json_import_pending") and not db.get_meta("json_import_done"):
                from usr.plugins.chat_history.helpers.import_json import import_json_chats

                result = await asyncio.to_thread(import_json_chats)
                db.set_meta("json_import_done", "1")
                db.set_meta("json_import_pending", "0")
                PrintStyle.success(
                    f"chat_history: JSON import done "
                    f"({result['contexts']} contexts, {result['messages']} messages)"
                )
        except Exception as exc:  # noqa: BLE001
            PrintStyle.debug(f"chat_history: import tick skipped: {exc}")

        _TICK["count"] += 1
        if (_TICK["count"] % _interval()) != 0:
            return
        try:
            from usr.plugins.chat_history.helpers import embed
            from usr.plugins.chat_history.helpers.summaries import sync_deck_summaries
            from usr.plugins.chat_history.helpers.sync import sync_all_live_contexts

            inserted = await asyncio.to_thread(sync_all_live_contexts)
            result = await asyncio.to_thread(embed.embedding_tick)
            summaries = await asyncio.to_thread(sync_deck_summaries)

            # Authoritative mode: snapshot live contexts + reconcile deletions
            from usr.plugins.chat_history.helpers.authoritative import (
                capture_enabled,
                reconcile_deletions,
            )

            if capture_enabled():
                await asyncio.to_thread(_capture_all_live)
                await asyncio.to_thread(reconcile_deletions)

            if inserted or result.get("embedded") or summaries:
                PrintStyle.debug(
                    f"chat_history: resynced {inserted} new messages,"
                    f" {summaries} deck summaries,"
                    f" embedded {result.get('embedded', 0)}"
                )
            if result.get("error"):
                PrintStyle.debug(f"chat_history: {result['error']}")
        except Exception as exc:  # noqa: BLE001
            PrintStyle.debug(f"chat_history: job tick skipped: {exc}")


def _capture_all_live() -> int:
    """Snapshot every live context (authoritative-mode job tick)."""
    from usr.plugins.chat_history.helpers.authoritative import capture_context

    count = 0
    try:
        from agent import AgentContext

        with AgentContext._contexts_lock:
            contexts = list(AgentContext._contexts.values())
        for context in contexts:
            if capture_context(context):
                count += 1
    except Exception as exc:  # noqa: BLE001
        PrintStyle.debug(f"chat_history: live capture skipped: {exc}")
    return count
