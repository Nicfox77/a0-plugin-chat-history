from helpers.tool import Tool, Response


class ChatSearch(Tool):

    async def execute(self, query="", context_id="", mode="text", days=0, limit=8, **kwargs):
        query = str(query or "").strip()
        if not query:
            return Response(message="query required.", break_loop=False)
        if limit in (None, ""):
            limit = 8
        limit = max(1, min(int(limit), 25))
        if days in (None, ""):
            days = 0
        days = max(0, int(days))
        mode = str(mode or "text").strip().lower()

        import asyncio

        from usr.plugins.chat_history.helpers import db

        def summary_hits(limit: int) -> list[dict]:
            conn = db.get_connection()
            params: list = [query]
            where = "tsv @@ websearch_to_tsquery('english', %s)"
            if days:
                where += " AND created_at > now() - (%s || ' days')::interval"
                params.append(str(days))
            params.append(limit)
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT deck_id, created_at, left(summary, 400),
                           ts_rank(tsv, websearch_to_tsquery('english', %s)) AS rank
                    FROM compaction_summaries
                    WHERE {where}
                    ORDER BY rank DESC, created_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                rows = cur.fetchall()
            return [
                {
                    "context_id": f"summary-deck:{r[0]}",
                    "ai": True,
                    "text": r[2],
                    "created_at": str(r[1] or "")[:19],
                    "rank": round(float(r[3]), 4),
                }
                for r in rows
            ]

        def fts():
            conn = db.get_connection()
            params: list = [query]
            where = "tsv @@ websearch_to_tsquery('english', %s)"
            if context_id:
                where += " AND m.context_id = %s"
                params.append(str(context_id))
            if days:
                where += " AND m.created_at > now() - (%s || ' days')::interval"
                params.append(str(days))
            params.append(limit)
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT m.context_id, m.ai, m.tool_name,
                           left(m.content_text, 400) AS text, m.created_at,
                           ts_rank(m.tsv, websearch_to_tsquery('english', %s)) AS rank
                    FROM messages m
                    WHERE {where}
                    ORDER BY rank DESC, m.created_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                rows = cur.fetchall()
            return [
                {
                    "context_id": r[0],
                    "ai": r[1],
                    "tool": r[2],
                    "text": r[3],
                    "created_at": str(r[4] or "")[:19],
                    "rank": round(float(r[5]), 4),
                }
                for r in rows
            ] + summary_hits(limit)

        try:
            if mode == "semantic":
                from usr.plugins.chat_history.helpers.embed import semantic_search

                results = await asyncio.to_thread(semantic_search, query, limit)
                if not results:
                    results = await asyncio.to_thread(fts)
            else:
                results = await asyncio.to_thread(fts)
        except Exception as exc:
            return Response(message=f"chat_history unavailable: {exc}", break_loop=False)

        if not results:
            return Response(message="No matching messages found.", break_loop=False)
        lines = []
        for row in results:
            speaker = "Assistant" if row.get("ai") else "User"
            score = row.get("rank") or row.get("similarity") or 0
            lines.append(
                f"- [{row['context_id']} | {row['created_at'][:19]} | {speaker}"
                f" | score {score}] {row['text'][:300]}"
            )
        return Response(message="\n".join(lines), break_loop=False)