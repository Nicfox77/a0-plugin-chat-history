"""Contracts for suppressing the stock synthetic initial message."""

from pathlib import Path


OVERRIDE = (
    Path(__file__).resolve().parents[1]
    / "extensions/python/agent_init/_10_initial_message.py"
)


def test_override_shadows_the_stock_extension_by_filename():
    assert OVERRIDE.name == "_10_initial_message.py"
    assert OVERRIDE.is_file()


def test_override_does_not_add_history_or_log_messages():
    source = OVERRIDE.read_text(encoding="utf-8")

    assert "hist_add_ai_response" not in source
    assert ".log.log(" not in source
    assert "return None" in source
