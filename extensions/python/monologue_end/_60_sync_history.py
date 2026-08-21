"""Dual-write: mirror history into the DB after every monologue.

Fires for every agent (main, subagents, parallel workers) — that is the
point: all conversations land in the queryable store.
"""

import asyncio

from helpers.extension import Extension
from helpers.print_style import PrintStyle


class SyncHistory(Extension):
    async def execute(self, **kwargs):
        del kwargs
        try:
            from usr.plugins.chat_history.helpers.sync import sync_agent

            await asyncio.to_thread(sync_agent, self.agent)
            # Authoritative mode: exact-format snapshot after each monologue
            from usr.plugins.chat_history.helpers.authoritative import (
                capture_context,
                capture_enabled,
            )

            if capture_enabled():
                await asyncio.to_thread(capture_context, self.agent.context)
        except Exception as exc:  # noqa: BLE001
            PrintStyle.debug(f"chat_history: monologue sync skipped: {exc}")
