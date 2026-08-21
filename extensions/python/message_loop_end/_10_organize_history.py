"""Select stock or Continuous Mode history compression at runtime.

This filename intentionally overrides Agent Zero's stock extension. With
Continuous Mode disabled (or outside the primary root chat), behavior is the
upstream implementation. The primary Continuous Mode chat instead runs the
partial-prefix compactor.
"""

from agent import LoopData
from helpers.defer import DeferredTask, THREAD_BACKGROUND
from helpers.extension import Extension
from helpers.history import clear_responses_provider_state

DATA_NAME_TASK = "_organize_history_task"


def _continuous_primary(agent) -> bool:
    try:
        from usr.plugins.chat_history.helpers import settings
        from usr.plugins.chat_history.helpers.primary import is_primary_context

        return (
            settings.continuous_mode()
            and getattr(agent, "number", 0) == 0
            and is_primary_context(getattr(agent, "context", None))
        )
    except Exception:
        return False


async def compress_history(agent) -> bool:
    if _continuous_primary(agent):
        from usr.plugins.chat_history.helpers.rolling import (
            run_rolling_compaction,
            should_compact,
        )

        if not should_compact(agent):
            return False
        return bool(await run_rolling_compaction(agent.context))

    compressed = bool(await agent.history.compress())
    if compressed:
        clear_responses_provider_state(agent)
    return compressed


class OrganizeHistory(Extension):
    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        del loop_data, kwargs
        if not self.agent:
            return

        task: DeferredTask | None = self.agent.get_data(DATA_NAME_TASK)
        if task and not task.is_ready():
            return

        task = DeferredTask(thread_name=THREAD_BACKGROUND)
        task.start_task(compress_history, self.agent)
        self.agent.set_data(DATA_NAME_TASK, task)
