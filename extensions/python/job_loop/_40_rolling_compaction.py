"""Continuous Mode trigger: compact the primary chat when over budget.

Runs on the framework job loop (background — never blocks a turn). Only the
primary chat is managed, and only while it is idle. Inactive entirely when
Continuous Mode is off.
"""

from helpers.extension import Extension
from helpers.print_style import PrintStyle

from usr.plugins.chat_history.helpers import settings
from usr.plugins.chat_history.helpers.rolling import (
    compaction_active,
    run_rolling_compaction,
    should_compact,
)


class RollingCompaction(Extension):
    async def execute(self, data: dict | None = None, **kwargs):
        del data, kwargs
        if not settings.continuous_mode() or compaction_active():
            return
        try:
            from agent import AgentContext, AgentContextType

            from usr.plugins.chat_history.helpers.primary import (
                resolve_primary_context_id,
            )

            context_id = resolve_primary_context_id()
            if not context_id:
                return
            context = AgentContext.get(context_id)
            if context is None:
                return
            if getattr(context, "type", None) != AgentContextType.USER:
                return
            if context.is_running():
                return
            agent = context.get_agent()
            if agent is None or getattr(agent, "number", 0) != 0:
                return
            if not should_compact(agent):
                return

            PrintStyle.info(
                "chat_history: primary chat over budget — starting rolling compaction"
            )
            await run_rolling_compaction(context)
        except Exception as exc:
            PrintStyle.warning(f"chat_history: rolling compaction skipped: {exc}")
