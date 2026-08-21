"""Continuous Mode: inject the conversation summary deck into primary-chat
prompts. Inactive when Continuous Mode is off."""

from agent import LoopData
from helpers.extension import Extension
from helpers.print_style import PrintStyle

_PARALLEL_WORKER_KIND_KEY = "_parallel_worker_kind"


class SummaryDeckInjection(Extension):
    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        try:
            from usr.plugins.chat_history.helpers import settings
            from usr.plugins.chat_history.helpers.primary import is_primary_context

            if not settings.continuous_mode() or not settings.deck_injection_enabled():
                return
            agent = self.agent
            if agent is None or getattr(agent, "number", 0) != 0:
                return
            context = getattr(agent, "context", None)
            if not is_primary_context(context):
                return
            getter = getattr(context, "get_data", None)
            if callable(getter) and getter(_PARALLEL_WORKER_KIND_KEY):
                return

            # Once per monologue: deck context is turn context, not loop context
            if str(loop_data.extras_persistent.get("summary_deck") or "").strip():
                return

            from usr.plugins.chat_history.helpers.deck import render_deck

            rendered = render_deck("main")
            if rendered.strip():
                loop_data.extras_persistent["summary_deck"] = rendered.strip()
        except Exception as exc:
            PrintStyle.warning(f"chat_history: deck injection skipped: {exc}")
