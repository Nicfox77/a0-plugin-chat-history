"""Continuous Mode partial-prefix compaction for the primary chat.

The Agent Zero model preset owns the history threshold through ``ctx_history``.
When that budget is reached, Continuous Mode summarizes and evicts only the
oldest configured fraction of the full context window. The untouched recent
tail remains native history; the raw evicted segment and its summary are
archived in Postgres before history is rewritten.
"""

from __future__ import annotations

import threading
from typing import Any

from helpers.print_style import PrintStyle

from usr.plugins.chat_history.helpers import deck as deck_state
from usr.plugins.chat_history.helpers import settings

_COMPACT_LOCK = threading.Lock()
_COMPACTING = {"active": False}
SUMMARY_OUTPUT_RATIO = 0.05


def compaction_active() -> bool:
    return _COMPACTING["active"]


def _chat_model_config(agent: Any) -> dict[str, Any]:
    from plugins._model_config.helpers.model_config import get_chat_model_config

    config = get_chat_model_config(agent)
    return config if isinstance(config, dict) else {}


def context_length(agent: Any) -> int:
    return max(1, int(_chat_model_config(agent).get("ctx_length", 128000)))


def history_ratio(agent: Any) -> float:
    ratio = float(_chat_model_config(agent).get("ctx_history", 0.7))
    return max(0.05, min(ratio, 0.95))


def compaction_threshold(agent: Any) -> int:
    """Use Agent Zero's preset history budget as the sole trigger."""
    return int(context_length(agent) * history_ratio(agent))


def eviction_ratio() -> float:
    ratio = settings.float_setting("evict_ratio", "CH_EVICT_RATIO", 0.3)
    return max(0.05, min(ratio, 0.8))


def eviction_target(agent: Any) -> int:
    return int(context_length(agent) * min(eviction_ratio(), history_ratio(agent)))


def summary_output_budget(input_tokens: int) -> int:
    """Maximum summary output: five percent of the evicted native history."""
    return max(1, int(max(0, input_tokens) * SUMMARY_OUTPUT_RATIO))


def min_tokens() -> int:
    return max(500, settings.int_setting("min_tokens", "CH_MIN_TOKENS", 2000))


def _reasoning_text(output_items: list[Any]) -> str:
    parts: list[str] = []
    for raw in output_items:
        item = raw if isinstance(raw, dict) else getattr(raw, "data", None)
        if not isinstance(item, dict) or item.get("type") != "reasoning":
            continue
        summary = item.get("summary")
        if summary is None:
            continue
        for block in summary if isinstance(summary, list) else [summary]:
            if isinstance(block, dict):
                text = block.get("text") or block.get("reasoning")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
    return "".join(parts)


def message_tokens(message: Any) -> int:
    from helpers import tokens
    from helpers.history import output_text
    from helpers.llm_result import RESPONSE_METADATA_KEY

    visible = tokens.approximate_tokens(
        output_text([message], ai_label="assistant", human_label="user")
    )
    metadata = message.get("metadata") if isinstance(message, dict) else None
    responses = metadata.get(RESPONSE_METADATA_KEY) if isinstance(metadata, dict) else None
    output_items = responses.get("output_items") if isinstance(responses, dict) else None
    reasoning = (
        tokens.approximate_tokens(_reasoning_text(output_items))
        if isinstance(output_items, list)
        else 0
    )
    return visible + reasoning


def estimate_context_tokens(history_output: list[Any]) -> int:
    return sum(message_tokens(message) for message in history_output)


def should_compact(agent: Any) -> bool:
    try:
        estimate = estimate_context_tokens(list(agent.history.output()))
    except Exception:
        return False
    return estimate >= max(min_tokens(), compaction_threshold(agent))


def _is_user_boundary(message: Any) -> bool:
    if bool(message.get("ai") if isinstance(message, dict) else False):
        return False
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, dict):
        return True
    if content.get("type") in {"framework_notification", "tool_result"}:
        return False
    return not any(
        key in content
        for key in ("tool_name", "native_tool_name", "tool_result", "native_tool_calls")
    )


def select_eviction_prefix(
    history_output: list[dict[str, Any]],
    target_tokens: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Split at a user-turn boundary after reaching the eviction target."""
    if len(history_output) < 3 or target_tokens <= 0:
        return [], history_output, 0

    running = 0
    desired = 0
    for index, message in enumerate(history_output[:-2], start=1):
        running += message_tokens(message)
        if running >= target_tokens:
            desired = index
            break
    if not desired:
        return [], history_output, 0

    boundaries = [
        index
        for index in range(1, len(history_output) - 1)
        if _is_user_boundary(history_output[index])
    ]
    cutoff = next((index for index in boundaries if index >= desired), 0)
    if not cutoff and boundaries:
        cutoff = boundaries[-1]
    if cutoff <= 0:
        return [], history_output, 0

    prefix = history_output[:cutoff]
    tail = history_output[cutoff:]
    return prefix, tail, estimate_context_tokens(prefix)


async def run_rolling_compaction(context: Any) -> bool:
    if _COMPACTING["active"]:
        return False
    with _COMPACT_LOCK:
        if _COMPACTING["active"]:
            return False
        _COMPACTING["active"] = True
    try:
        return await _run_cycle(context)
    finally:
        _COMPACTING["active"] = False


async def _run_cycle(context: Any) -> bool:
    agent = getattr(context, "agent0", None) or context.get_agent()
    history_output = list(agent.history.output())
    prefix, tail, prefix_tokens = select_eviction_prefix(
        history_output,
        eviction_target(agent),
    )
    if not prefix:
        PrintStyle.warning(
            "chat_history: rolling compaction could not find a safe user-turn boundary"
        )
        return False

    from usr.plugins.chat_history.helpers.sync import sync_agent

    sync_agent(agent)
    summary = await _summarize_prefix(agent, prefix, prefix_tokens)
    if not summary.strip():
        raise ValueError("Rolling compaction produced an empty summary")

    segment_id = deck_state.append_segment(
        context_id=str(context.id),
        messages=prefix,
        summary=summary,
        token_count=prefix_tokens,
    )
    _replace_history(agent, tail)
    _persist_pruned_history(agent, context)
    PrintStyle.success(
        "chat_history: archived segment "
        f"{segment_id[:12]} ({len(prefix)} messages, ~{prefix_tokens} tokens)"
    )
    return True


async def _summarize_prefix(
    agent: Any,
    messages: list[dict[str, Any]],
    input_tokens: int,
) -> str:
    from helpers.history import output_text
    from plugins._chat_compaction.helpers.compactor import _build_model

    conversation = output_text(messages, ai_label="assistant", human_label="user")
    if not conversation.strip():
        return ""
    output_budget = summary_output_budget(input_tokens)
    _, model = _build_model(True, None, agent)
    summary, _ = await model.unified_call(
        system_message=(
            agent.read_prompt("compact.sys.md")
            + "\n\n"
            + f"The final summary must not exceed {output_budget} tokens. "
            + "Prioritize information required to continue the conversation correctly."
        ),
        user_message=agent.read_prompt("compact.msg.md", conversation=conversation),
        max_tokens=output_budget,
    )
    return str(summary or "").strip()


def _replace_history(agent: Any, tail: list[dict[str, Any]]) -> None:
    from helpers.history import History

    history = History(agent=agent)
    last_user = None
    max_sequence = 0
    for output in tail:
        if _is_user_boundary(output):
            history.new_topic()
        sequence = int(output.get("sequence") or 0)
        message = history.add_message(
            ai=bool(output.get("ai")),
            content=output.get("content"),
            id=str(output.get("id") or ""),
            metadata=output.get("metadata") or {},
        )
        if sequence:
            message.sequence = sequence
            max_sequence = max(max_sequence, sequence)
        if _is_user_boundary(output):
            last_user = message
    history.counter = max(max_sequence, history.counter)
    agent.history = history
    agent.last_user_message = last_user


def _persist_pruned_history(agent: Any, context: Any) -> None:
    from agent import Agent
    from helpers.history import clear_responses_provider_state
    from helpers.persist_chat import save_tmp_chat
    from helpers.state_monitor_integration import mark_dirty_all

    clear_responses_provider_state(agent)
    agent.data.pop(Agent.DATA_NAME_CTX_WINDOW, None)
    save_tmp_chat(context)
    mark_dirty_all(reason="plugins.chat_history.partial_compaction")

    try:
        from usr.plugins.chat_history.helpers.authoritative import (
            capture_context,
            capture_enabled,
        )

        if capture_enabled():
            capture_context(context)
    except Exception as exc:  # noqa: BLE001
        PrintStyle.warning(f"chat_history: compacted snapshot capture failed: {exc}")
