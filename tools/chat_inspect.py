from helpers.tool import Tool, Response


class ChatInspect(Tool):

    async def execute(self, context_id="", limit=20, offset=0, **kwargs):
        if not str(context_id).strip():
            return Response(message="context_id required (see chat_list).", break_loop=False)
        if limit in (None, ""):
            limit = 20
        limit = max(1, min(int(limit), 200))
        if offset in (None, ""):
            offset = 0
        offset = max(0, int(offset))

        import asyncio

        from usr.plugins.chat_history.helpers import db

        def query():
            conn = db.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT seq, ai, tool_name, content_text
                    FROM messages
                    WHERE context_id = %s
                    ORDER BY seq DESC
                    LIMIT %s OFFSET %s
                    """,
                    (str(context_id), limit, offset),
                )
                rows = cur.fetchall()
            return list(reversed(rows))

        try:
            rows = await asyncio.to_thread(query)
        except Exception as exc:
            return Response(message=f"chat_history unavailable: {exc}", break_loop=False)

        if not rows:
            return Response(
                message=f"No stored messages for context '{context_id}'"
                " (context may predate the plugin or not exist).",
                break_loop=False,
            )
        lines = [f"Tail of {context_id} (oldest→newest, offset {offset}):"]
        for seq, ai, tool, text in rows:
            speaker = "Assistant" if ai else ("Tool" if tool else "User")
            label = f"{speaker}/{tool}" if tool else speaker
            lines.append(f"[{seq}] {label}: {text[:500]}")
        return Response(message="\n".join(lines), break_loop=False)
