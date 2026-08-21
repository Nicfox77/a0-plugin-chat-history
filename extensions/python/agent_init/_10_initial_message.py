"""Suppress Agent Zero's synthetic greeting for Continuous Mode chats.

The filename intentionally shadows the stock ``agent_init`` extension. A new
chat should begin with the first real user message, not an invented assistant
turn that is then persisted as conversation history.
"""

from helpers.extension import Extension


class InitialMessage(Extension):
    def execute(self, **kwargs):
        return None
