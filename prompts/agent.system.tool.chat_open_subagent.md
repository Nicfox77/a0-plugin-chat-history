Open and steer a subagent context (not the primary chat). Returns the
subagent's profile, live state, parent context id, and the tail of its
conversation. If `message` is provided, it is delivered to the subagent
via `context.communicate(UserMessage(...))` (same path as the subagents
plugin's steer). The primary chat is never steered here.

Args:
- `context_id` (string, required): subagent context id (from chat_list;
  rows marked [subagent]).
- `message` (string, optional): when non-empty, deliver this as a user
  message to the subagent (steer). Empty = inspect only.
- `limit` (integer, optional, default 20): tail length when inspecting.

Input schema for tool_args:
~~~json
{"type": "object", "properties": {"context_id": {"type": "string", "description": "Subagent context id from chat_list."}, "message": {"type": "string", "description": "Optional steer message; empty = inspect only."}, "limit": {"type": "string", "description": "Messages to return when inspecting (default 20, max 200)."}}, "required": ["context_id"], "additionalProperties": false}
~~~
