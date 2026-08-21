"""Tests for durable Continuous Mode primary identity."""

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from usr.plugins.chat_history.helpers import primary


def test_live_primary_validity_is_profile_neutral(monkeypatch):
    context = SimpleNamespace(
        id="ctx-main",
        type="USER",
        config=SimpleNamespace(profile="developer"),
        data={},
        output_data={},
    )
    fake_agent = SimpleNamespace(
        AgentContext=SimpleNamespace(get=lambda _id: context),
        AgentContextType=SimpleNamespace(USER="USER"),
    )
    monkeypatch.setitem(__import__("sys").modules, "agent", fake_agent)

    assert primary._valid_context_id("ctx-main") is True


def test_persisted_primary_validity_ignores_profile(tmp_path: Path):
    chat = tmp_path / "chat.json"
    chat.write_text(
        '{"type":"user","agent_profile":"agent0","data":{},"output_data":{}}',
        encoding="utf-8",
    )
    with mock.patch.object(primary, "_chat_path", return_value=chat):
        assert primary._is_persisted_root_user("ctx-main") is True


def test_stored_primary_wins_without_rewriting():
    with (
        mock.patch.object(primary, "_read_state", return_value="ctx-stored"),
        mock.patch.object(primary, "_valid_context_id", return_value=True),
        mock.patch.object(primary, "set_primary_context_id") as setter,
    ):
        assert primary.resolve_primary_context_id() == "ctx-stored"
    setter.assert_not_called()


def test_telegram_binding_migrates_into_canonical_state():
    calls = []
    with (
        mock.patch.object(primary, "_read_state", return_value=""),
        mock.patch.object(primary, "_telegram_context_ids", return_value=["ctx-tg"]),
        mock.patch.object(
            primary,
            "_valid_context_id",
            side_effect=lambda value: value == "ctx-tg",
        ),
        mock.patch.object(
            primary,
            "set_primary_context_id",
            side_effect=lambda value: calls.append(value) or value,
        ),
    ):
        assert primary.resolve_primary_context_id() == "ctx-tg"
    assert calls == ["ctx-tg"]


def test_named_live_context_is_preferred_when_claiming_primary():
    other = SimpleNamespace(id="ctx-other", name="Other")
    named = SimpleNamespace(id="ctx-main", name="Main")
    with (
        mock.patch.object(primary, "_read_state", return_value=""),
        mock.patch.object(primary, "_telegram_context_ids", return_value=[]),
        mock.patch.object(primary, "_live_candidates", return_value=[other, named]),
        mock.patch.object(
            primary, "set_primary_context_id", side_effect=lambda value: value
        ) as setter,
    ):
        assert primary.resolve_primary_context_id() == "ctx-main"
    setter.assert_called_once_with("ctx-main")


def test_set_primary_context_id_is_atomic(tmp_path: Path):
    state_path = tmp_path / "state" / "primary.json"
    with mock.patch.object(primary, "_state_path", return_value=state_path):
        assert primary.set_primary_context_id("ctx-main") == "ctx-main"
        assert primary._read_state() == "ctx-main"
        assert list(state_path.parent.glob("*.tmp.*")) == []
