"""Repair WebUI user turns after all persisted contexts have loaded."""

from helpers.extension import Extension
from helpers.print_style import PrintStyle


class RepairUserLog(Extension):
    def execute(self, **kwargs):
        del kwargs
        from agent import AgentContext
        from helpers.persist_chat import save_tmp_chat
        from usr.plugins.chat_history.helpers.authoritative import (
            capture_context,
            capture_enabled,
        )
        from usr.plugins.chat_history.helpers.user_log import repair_context_user_log

        with AgentContext._contexts_lock:
            contexts = list(AgentContext._contexts.values())
        repaired = 0
        for context in contexts:
            count = repair_context_user_log(context)
            if not count:
                continue
            repaired += count
            save_tmp_chat(context)
            if capture_enabled():
                capture_context(context)
        if repaired:
            PrintStyle.success(
                f"chat_history: restored {repaired} missing user transcript log item(s)"
            )
