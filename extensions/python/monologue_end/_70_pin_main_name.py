"""Continuous Mode primary-chat name lock (self-healing sweep).

Runs after every monologue (with an idle safety sweep in the periodic job
loop). If Continuous Mode is on and the lock is enabled the primary chat's
name is forced to ``settings.main_chat_name()`` so the utility-model
auto-rename (bundled ``_chat_naming`` plugin) cannot drift it away. Cheap;
no-op when the name already matches.

Logs a one-line info entry in the primary chat's log under the heading
"Continuous Mode" so the rename is visible to the user (does not leak a
no-op every turn — only fires on a real rename).
"""
from __future__ import annotations

from helpers.extension import Extension
from helpers.print_style import PrintStyle

from usr.plugins.chat_history.helpers import pin as pin_helper
from usr.plugins.chat_history.helpers import settings


class PinMainName(Extension):
    async def execute(self, **kwargs):
        del kwargs
        try:
            if not settings.continuous_mode():
                return
            if not settings.lock_main_chat_name():
                return

            new_name = pin_helper.assert_primary_name()
            if new_name is None:
                return

            agent = getattr(self, "agent", None)
            context = getattr(agent, "context", None) if agent else None
            log = getattr(context, "log", None) if context else None
            if log is not None and hasattr(log, "log"):
                try:
                    log.log(
                        type="info",
                        heading="Continuous Mode",
                        content=(
                            "Primary chat renamed to "
                            f"'{new_name}' by the name-lock sweep."
                        ),
                    )
                except Exception:
                    pass
            PrintStyle.info(
                f"chat_history: primary chat renamed to '{new_name}'"
            )
        except Exception as exc:  # noqa: BLE001
            PrintStyle.debug(f"chat_history: name lock sweep skipped: {exc}")
