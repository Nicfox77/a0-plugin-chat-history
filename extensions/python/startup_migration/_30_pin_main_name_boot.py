"""Boot-time name assert: rename the primary chat to ``main_chat_name`` once
per startup if it is currently off. Sync mode (startup_migration runs sync).
Cheap — no DB, no LLM; only mutates the primary chat's name + its JSON
snapshot."""
from helpers.extension import Extension
from helpers.print_style import PrintStyle

from usr.plugins.chat_history.helpers import pin as pin_helper
from usr.plugins.chat_history.helpers import settings


class PinMainNameBoot(Extension):
    def execute(self, **kwargs):
        del kwargs
        try:
            if not settings.continuous_mode():
                return
            if not settings.lock_main_chat_name():
                return

            new_name = pin_helper.assert_primary_name()
            if new_name:
                PrintStyle.info(
                    f"chat_history: boot name lock applied ('{new_name}')"
                )
        except Exception as exc:  # noqa: BLE001
            PrintStyle.debug(f"chat_history: boot name lock skipped: {exc}")