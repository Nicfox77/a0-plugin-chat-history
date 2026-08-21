"""Open (and optionally steer) a subagent context.

Surface area:
- Tail the subagent's recent messages (chat_inspect-style).
- Optionally deliver a user message via ``context.communicate(UserMessage(...))``
  (same mechanism the ``subagents`` plugin uses for steer).

Refuses to steer the primary chat — the primary is for the user only; subagent
steering lives here.
"""
from __future__ import annotations

from helpers.tool import Tool, Response


class ChatOpenSubagent(Tool):

    async def execute(self, context_id="", message="", limit=20, **kwargs):
        cid = str(context_id or "").strip()
        if not cid:
            return Response(
                message="context_id required (rows marked [subagent] in chat_list).",
                break_loop=False,
            )
        msg = str(message or "")
        try:
            limit_i = int(limit) if limit not in (None, "") else 20
        except (TypeError, ValueError):
            limit_i = 20
        limit_i = max(1, min(limit_i, 200))

        from usr.plugins.chat_history.helpers import db, primary

        primary_id = primary.resolve_primary_context_id() or ""

        def query():
            conn = db.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.id, c.name, c.type, c.agent_profile,
                           c.msg_count, c.last_message_at,
                           c.parent_context_id, c.is_subagent
                    FROM contexts c WHERE c.id = %s
                    """,
                    (cid,),
                )
                ctx_row = cur.fetchone()
                cur.execute(
                    """
                    SELECT seq, ai, tool_name, content_text
                    FROM messages
                    WHERE context_id = %s
                    ORDER BY seq DESC
                    LIMIT %s
                    """,
                    (cid, limit_i),
                )
                msg_rows = list(reversed(cur.fetchall()))
            return ctx_row, msg_rows

        import asyncio

        try:
            ctx_row, msg_rows = await asyncio.to_thread(query)
        except Exception as exc:
            return Response(
                message=f"chat_history unavailable: {exc}", break_loop=False
            )

        if not ctx_row:
            return Response(
                message=f"No stored context for '{cid}'", break_loop=False
            )

        (
            _id,
            name,
            ctype,
            profile,
            count,
            last,
            parent_id,
            is_subagent,
        ) = ctx_row
        is_primary = bool(primary_id and cid == primary_id)

        live = False
        running = False
        try:
            from agent import AgentContext

            ctx_obj = AgentContext.get(cid)
            if ctx_obj is not None:
                live = True
                running = bool(ctx_obj.is_running())
        except Exception:
            pass

        header = (
            f"Subagent {cid}\n"
            f"  name: {name or '(unnamed)'}\n"
            f"  profile: {profile or '-'}\n"
            f"  type: {ctype or '-'}\n"
            f"  parent_context_id: {parent_id or '-'}\n"
            f"  is_subagent: {bool(is_subagent)}\n"
            f"  is_primary: {is_primary}\n"
            f"  live: {live}  running: {running}\n"
            f"  messages: {count}  last_message_at: {str(last or '')[:19]}"
        )

        tail_lines = []
        if msg_rows:
            tail_lines.append("")
            tail_lines.append(f"--- tail (oldest→newest, last {len(msg_rows)}) ---")
            for seq, ai, tool, text in msg_rows:
                speaker = "Assistant" if ai else ("Tool" if tool else "User")
                label = f"{speaker}/{tool}" if tool else speaker
                tail_lines.append(f"[{seq}] {label}: {text[:500]}")

        if msg and not is_primary:
            try:
                from agent import AgentContext, UserMessage

                ctx_obj = AgentContext.get(cid)
                if ctx_obj is None:
                    return Response(
                        message=header
                        + "\nCannot steer: context is not live.",
                        break_loop=False,
                    )
                ctx_obj.communicate(UserMessage(message=msg))
                deliver = f"\n--- steer delivered: {msg[:120]} ---"
            except Exception as exc:
                return Response(
                    message=header + f"\nSteer failed: {exc}", break_loop=False
                )
            return Response(
                message=header + "".join(tail_lines) + deliver, break_loop=False
            )

        if msg and is_primary:
            tail_lines.append("")
            tail_lines.append(
                "Refused to steer the primary chat — use chat_inspect or"
                " the user message UI for the primary."
            )

        return Response(
            message=header + "\n".join(tail_lines), break_loop=False
        )