"""Kick off the one-time JSON import (deferred to the job loop — this hook
fires in SYNC mode and must not block startup on DB work)."""

from helpers.extension import Extension
from helpers.print_style import PrintStyle


class ScheduleJsonImport(Extension):
    def execute(self, **kwargs):
        del kwargs
        try:
            from helpers.plugins import call_plugin_hook

            result = call_plugin_hook(
                "chat_history", "ensure_dependencies", raise_on_error=False
            )
            if isinstance(result, dict) and not result.get("ok", True):
                raise RuntimeError(result.get("error") or "dependency installation failed")
            from usr.plugins.chat_history.helpers import db

            if db.get_meta("json_import_done"):
                return
            db.set_meta("json_import_pending", "1")
            PrintStyle.info("chat_history: JSON import pending (runs on first job tick)")
        except Exception as exc:  # noqa: BLE001
            PrintStyle.debug(f"chat_history: import flag not set: {exc}")
