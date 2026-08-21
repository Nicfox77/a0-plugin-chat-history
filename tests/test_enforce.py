"""Unit tests for the Continuous Mode single-chat redirect decision helper."""
from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT.parents[1]))


SETTINGS_PATH = "usr.plugins.chat_history.helpers.settings"
ENFORCE_PATH = "usr.plugins.chat_history.helpers.enforce"
PRIMARY_PATH = "usr.plugins.chat_history.helpers.primary"


def _load_enforce():
    return importlib.import_module(ENFORCE_PATH)


def _agent(
    cid: str = "ctx-stray",
    *,
    number: int = 0,
    profile: str = "agent0",
    context_type=None,
    data: dict | None = None,
    output_data: dict | None = None,
) -> SimpleNamespace:
    if context_type is None:
        try:
            from agent import AgentContextType

            context_type = AgentContextType.USER
        except Exception:
            context_type = "user"
    return SimpleNamespace(
        number=number,
        agent_number=number,
        config=SimpleNamespace(profile=profile),
        context=SimpleNamespace(
            id=cid,
            type=context_type,
            data=data if data is not None else {},
            output_data=output_data if output_data is not None else {},
        ),
    )


def _continuous_mode(value: bool):
    return mock.patch(f"{SETTINGS_PATH}.continuous_mode", lambda: value)


def _resolver(returning):
    return mock.patch(f"{ENFORCE_PATH}._resolve_primary", lambda: returning)


class ExemptionTests(unittest.TestCase):
    """Every documented exemption must short-circuit to None."""

    def setUp(self):
        self.enforce = _load_enforce()

    def test_continuous_mode_off_returns_none(self):
        agent = _agent()
        with _continuous_mode(False), _resolver(SimpleNamespace(id="ctx-pri")):
            self.assertIsNone(self.enforce.should_redirect(agent))

    def test_agent_none_returns_none(self):
        with _continuous_mode(True):
            self.assertIsNone(self.enforce.should_redirect(None))

    def test_non_root_agent_returns_none(self):
        agent = _agent(number=1)
        with _continuous_mode(True), _resolver(SimpleNamespace(id="ctx-pri")):
            self.assertIsNone(self.enforce.should_redirect(agent))

    def test_non_user_context_returns_none(self):
        agent = _agent(context_type="solve")
        with _continuous_mode(True), _resolver(SimpleNamespace(id="ctx-pri")):
            self.assertIsNone(self.enforce.should_redirect(agent))

    def test_profile_does_not_exempt_a_stray_root_chat(self):
        agent = _agent(profile="developer")
        primary = SimpleNamespace(id="ctx-pri")
        with _continuous_mode(True), _resolver(primary):
            self.assertIs(self.enforce.should_redirect(agent), primary)

    def test_parallel_worker_kind_returns_none(self):
        agent = _agent(data={"_parallel_worker_kind": "tool"})
        with _continuous_mode(True), _resolver(SimpleNamespace(id="ctx-pri")):
            self.assertIsNone(self.enforce.should_redirect(agent))

    def test_parent_context_id_returns_none(self):
        agent = _agent(data={"parent_context_id": "ctx-parent"})
        with _continuous_mode(True), _resolver(SimpleNamespace(id="ctx-pri")):
            self.assertIsNone(self.enforce.should_redirect(agent))

    def test_output_parent_context_id_returns_none(self):
        agent = _agent(output_data={"parent_context_id": "ctx-parent"})
        with _continuous_mode(True), _resolver(SimpleNamespace(id="ctx-pri")):
            self.assertIsNone(self.enforce.should_redirect(agent))

    def test_project_bound_context_returns_none(self):
        agent = _agent()
        import helpers.projects as real_projects

        def fake_resolve(context):
            return "pipe-demo"

        with _continuous_mode(True), _resolver(
            SimpleNamespace(id="ctx-pri")
        ), mock.patch.object(real_projects, "get_context_project_name", fake_resolve):
            self.assertIsNone(self.enforce.should_redirect(agent))

    def test_primary_context_returns_none(self):
        primary = SimpleNamespace(id="ctx-stray")
        agent = _agent(cid="ctx-stray")
        with _continuous_mode(True), _resolver(primary):
            self.assertIsNone(self.enforce.should_redirect(agent))

    def test_no_live_primary_returns_none(self):
        agent = _agent()
        with _continuous_mode(True), _resolver(None):
            self.assertIsNone(self.enforce.should_redirect(agent))

    def test_context_missing_returns_none(self):
        agent = SimpleNamespace(
            number=0, agent_number=0, config=SimpleNamespace(profile="agent0")
        )
        with _continuous_mode(True):
            self.assertIsNone(self.enforce.should_redirect(agent))


class RedirectPositiveTests(unittest.TestCase):
    def setUp(self):
        self.enforce = _load_enforce()
        self.primary = SimpleNamespace(id="ctx-primary")

    def test_stray_root_context_redirects(self):
        agent = _agent(cid="ctx-stray")
        with _continuous_mode(True), _resolver(self.primary):
            result = self.enforce.should_redirect(agent)
        self.assertIs(result, self.primary)

    def test_custom_primary_resolver_is_used(self):
        agent = _agent(cid="ctx-stray")
        sentinel = SimpleNamespace(id="ctx-sentinel")

        def resolver():
            return sentinel

        with _continuous_mode(True):
            result = self.enforce.should_redirect(agent, primary_resolver=resolver)
        self.assertIs(result, sentinel)


class ResolvePrimaryTests(unittest.TestCase):
    """Direct exercise of the binding-vs-first fallback."""

    def setUp(self):
        self.enforce = _load_enforce()

    def test_bound_id_resolves_after_profile_change(self):
        candidate = SimpleNamespace(
            id="ctx-bound",
            type="USER",
            config=SimpleNamespace(profile="developer"),
            data={},
            output_data={},
        )
        fake_ctx_mod = SimpleNamespace(
            AgentContext=SimpleNamespace(get=lambda _id: candidate, first=lambda: None),
            AgentContextType=SimpleNamespace(USER="USER"),
        )
        fake_primary_mod = SimpleNamespace(resolve_primary_context_id=lambda: "ctx-bound")
        with mock.patch.dict(
            sys.modules,
            {"agent": fake_ctx_mod, PRIMARY_PATH: fake_primary_mod},
        ):
            self.assertIs(self.enforce._resolve_primary(), candidate)

    def test_bound_id_with_non_user_context_falls_back_to_first(self):
        bound = SimpleNamespace(
            id="ctx-bound",
            type="SOLVE",
            config=SimpleNamespace(profile="agent0"),
            data={},
            output_data={},
        )
        first = SimpleNamespace(
            id="ctx-first",
            type="USER",
            config=SimpleNamespace(profile="agent0"),
            data={},
            output_data={},
        )
        fake_ctx_mod = SimpleNamespace(
            AgentContext=SimpleNamespace(get=lambda _id: bound, first=lambda: first),
            AgentContextType=SimpleNamespace(USER="USER"),
        )
        fake_primary_mod = SimpleNamespace(resolve_primary_context_id=lambda: "ctx-bound")
        with mock.patch.dict(
            sys.modules,
            {"agent": fake_ctx_mod, PRIMARY_PATH: fake_primary_mod},
        ):
            self.assertIs(self.enforce._resolve_primary(), first)

    def test_first_fallback_accepts_any_profile(self):
        first = SimpleNamespace(
            id="ctx-first",
            type="USER",
            config=SimpleNamespace(profile="developer"),
            data={},
            output_data={},
        )
        fake_ctx_mod = SimpleNamespace(
            AgentContext=SimpleNamespace(get=lambda _id: None, first=lambda: first),
            AgentContextType=SimpleNamespace(USER="USER"),
        )
        fake_primary_mod = SimpleNamespace(resolve_primary_context_id=lambda: "")
        with mock.patch.dict(
            sys.modules,
            {"agent": fake_ctx_mod, PRIMARY_PATH: fake_primary_mod},
        ):
            self.assertIs(self.enforce._resolve_primary(), first)


if __name__ == "__main__":
    unittest.main()
