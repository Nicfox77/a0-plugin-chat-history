from helpers.tool import Tool, Response

from usr.plugins.chat_history.helpers import primary as _primary
from usr.plugins.chat_history.helpers import settings


def _shape_rows(rows, primary_id: str, pin_enabled: bool):
    """Pure row-shaping helper — extracted so unit tests can exercise it
    without booting the framework. Sorts primary to the top when pinning is
    enabled, then by last_message_at DESC. Returns the rendered message body.
    """
    live_map = rows.get("live", {})
    pin_main = pin_enabled and bool(primary_id)

    def sort_key(row):
        is_primary = bool(pin_main and row["context_id"] == primary_id)
        # Pin primary (sort 0), then subagents (sort 1), then everything else
        # (sort 2). Within a group, recency wins.
        bucket = 0 if is_primary else (1 if row.get("is_subagent") else 2)
        return (bucket, -(row.get("last_message_ts") or 0))

    ordered = sorted(rows["data"], key=sort_key)
    out = []
    for row in ordered:
        cid = row["context_id"]
        out.append(
            {
                "context_id": cid,
                "name": row["name"],
                "type": row["type"],
                "profile": row["profile"],
                "messages": row["messages"],
                "last_message_at": row["last_message_at"],
                "live": cid in live_map,
                "running": live_map.get(cid, False),
                "is_primary": cid == primary_id,
                "is_subagent": bool(row.get("is_subagent")),
                "parent_context_id": row.get("parent_context_id", "") or "",
            }
        )
    return out


class ChatList(Tool):

    async def execute(self, **kwargs):
        import asyncio

        from usr.plugins.chat_history.helpers import db

        pin_enabled = settings.continuous_mode() and settings.pin_main_chat()
        primary_id = _primary.resolve_primary_context_id() if pin_enabled else ""

        def query():
            conn = db.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.id, c.name, c.type, c.agent_profile,
                           c.msg_count, c.last_message_at,
                           c.parent_context_id, c.is_subagent
                    FROM contexts c
                    ORDER BY c.last_message_at DESC NULLS LAST
                    LIMIT 50
                    """
                )
                raw_rows = cur.fetchall()
            data = []
            for cid, name, ctype, profile, count, last, parent, sub in raw_rows:
                ts = 0
                if last is not None:
                    try:
                        ts = int(last.timestamp())
                    except Exception:
                        ts = 0
                data.append(
                    {
                        "context_id": cid,
                        "name": name or "",
                        "type": ctype or "",
                        "profile": profile or "",
                        "messages": count,
                        "last_message_at": str(last or ""),
                        "last_message_ts": ts,
                        "parent_context_id": parent or "",
                        "is_subagent": bool(sub),
                    }
                )
            live = set()
            try:
                from agent import AgentContext

                with AgentContext._contexts_lock:
                    live = {
                        str(ctx.id): bool(ctx.is_running())
                        for ctx in AgentContext._contexts.values()
                    }
            except Exception:
                pass
            return _shape_rows(
                {"data": data, "live": live}, primary_id, pin_enabled
            )

        try:
            rows = await asyncio.to_thread(query)
        except Exception as exc:
            return Response(message=f"chat_history unavailable: {exc}", break_loop=False)

        if not rows:
            return Response(message="No stored contexts yet.", break_loop=False)
        lines = []
        for row in rows:
            flags = " [live]" if row["live"] else ""
            running = " [RUNNING]" if row["running"] else ""
            primary = " [primary]" if row["is_primary"] else ""
            sub = " [subagent]" if row["is_subagent"] else ""
            parent = (
                f" parent={row['parent_context_id'][:10]}"
                if row["parent_context_id"]
                else ""
            )
            lines.append(
                f"- {row['context_id']} ({row['type']}/{row['profile'] or '-'}"
                f"{primary}{sub}): {row['messages']} msgs,"
                f" last {row['last_message_at'][:19]}{flags}{running}{parent}"
                f" — {row['name'][:60]}"
            )
        return Response(message="\n".join(lines), break_loop=False)