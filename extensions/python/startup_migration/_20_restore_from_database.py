"""Authoritative mode: materialize DB snapshots to files BEFORE the stock
chat loader runs. The startup_migration hook fires synchronously in
initialize(), while initialize_chats() is a deferred task started later —
so files written here are seen by the stock loader. No shadowing."""

from helpers.extension import Extension
from helpers.print_style import PrintStyle


class RestoreFromDatabase(Extension):
    def execute(self, **kwargs):
        del kwargs
        try:
            from usr.plugins.chat_history.helpers import settings

            if settings.storage_mode() != "authoritative":
                return
            from usr.plugins.chat_history.helpers.authoritative import (
                restore_stale_files,
            )

            restore_stale_files()
        except Exception as exc:  # noqa: BLE001
            PrintStyle.warning(f"chat_history: boot restore skipped: {exc}")
