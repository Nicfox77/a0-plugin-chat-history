"""Wait for the compression strategy selected by chat_history.

The stock wait extension imports the bundled compressor directly, bypassing
the plugin override. This matching override keeps stock behavior everywhere
except the primary Continuous Mode chat, where it waits for partial-prefix
compaction instead.
"""

from agent import LoopData
from helpers.defer import DeferredTask
from helpers.extension import Extension

from usr.plugins.chat_history.extensions.python.message_loop_end._10_organize_history import (
    DATA_NAME_TASK,
    _continuous_primary,
    compress_history,
)

MAX_SYNC_COMPRESSION_PASSES = 64


class OrganizeHistoryWait(Extension):
    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        del loop_data, kwargs
        if not self.agent:
            return

        passes = 0
        while True:
            task: DeferredTask | None = self.agent.get_data(DATA_NAME_TASK)
            background_partial = bool(
                _continuous_primary(self.agent)
                and task
                and not task.is_ready()
            )
            if not self.agent.history.is_over_limit() and not background_partial:
                break

            passes += 1
            before_tokens = self.agent.history.get_tokens()

            if task:
                if not task.is_ready():
                    self.agent.context.log.set_progress("Compressing history...")
                compressed = bool(await task.result())
                self.agent.set_data(DATA_NAME_TASK, None)
            else:
                self.agent.context.log.set_progress("Compressing history...")
                compressed = await compress_history(self.agent)

            after_tokens = self.agent.history.get_tokens()
            if not compressed:
                # A Continuous Mode task can become unnecessary between its
                # threshold check and completion (for example after another
                # compactor wins the lock). A below-threshold no-op is not a
                # stalled compression and should not alarm the operator.
                still_required = self.agent.history.is_over_limit()
                if _continuous_primary(self.agent):
                    from usr.plugins.chat_history.helpers.rolling import should_compact

                    still_required = should_compact(self.agent)
                if still_required:
                    self._log_stalled(before_tokens, after_tokens)
                break
            if after_tokens >= before_tokens:
                self._log_stalled(before_tokens, after_tokens)
                break
            if passes >= MAX_SYNC_COMPRESSION_PASSES:
                self._log_stalled(before_tokens, after_tokens, max_passes=True)
                break

    def _log_stalled(
        self,
        before_tokens: int,
        after_tokens: int,
        max_passes: bool = False,
    ) -> None:
        detail = (
            f"History compression stopped after {MAX_SYNC_COMPRESSION_PASSES} passes"
            if max_passes
            else "History compression could not reduce the prompt history further"
        )
        self.agent.context.log.log(
            type="warning",
            heading="History compression stalled",
            content=f"{detail}. Tokens before: {before_tokens}; after: {after_tokens}.",
        )
