Full-text search across ALL stored conversations (main chat, subagents,
background jobs). Use to find past discussions, decisions, or to check what
a subagent was told/did. `mode` "semantic" adds embedding similarity when
available; default "text" is lexical and always available.

Args:
- `query` (string, required): search text.
- `context_id` (string, optional): restrict to one context.
- `mode` (string, optional): "text" (default) or "semantic".
- `days` (integer, optional): only messages newer than N days (0 = all).
- `limit` (integer, optional, default 8).

Input schema for tool_args:
~~~json
{"type": "object", "properties": {"query": {"type": "string", "description": "Search text."}, "context_id": {"type": "string", "description": "Optional context filter."}, "mode": {"type": "string", "enum": ["text", "semantic"], "description": "Search mode."}, "days": {"type": "string", "description": "Time window in days (0 = all)."}, "limit": {"type": "string", "description": "Max results (default 8, max 25)."}}, "required": ["query"], "additionalProperties": false}
~~~
