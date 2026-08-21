Read the recent message tail of any stored context — e.g. a running
developer subagent's conversation — without switching chats. Get context ids
from `chat_list`. Positive `offset` reaches further back.

Args:
- `context_id` (string, required): context to inspect.
- `limit` (integer, optional, default 20): messages to return.
- `offset` (integer, optional, default 0): skip N newest before tailing.

Input schema for tool_args:
~~~json
{"type": "object", "properties": {"context_id": {"type": "string", "description": "Context id from chat_list."}, "limit": {"type": "string", "description": "Messages to return (default 20, max 200)."}, "offset": {"type": "string", "description": "Skip N newest messages."}}, "required": ["context_id"], "additionalProperties": false}
~~~
