"""Unit tests for the primary-chat name-lock sweep.

Covers the pure decision helper (``decide_rename``) and the live glue
(``assert_primary_name``) with all I/O pluggable.
"""
from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT.parents[1]))

PIN_PATH = "usr.plugins.chat_history.helpers.pin"
SETTINGS_PATH = "usr.plugins.chat_history.helpers.settings"


def _load_pin():
    return importlib.import_module(PIN_PATH)


class DecideRenameTests(unittest.TestCase):
    def setUp(self):
        self.pin = _load_pin()

    def test_lock_disabled_no_rename(self):
        self.assertIsNone(self.pin.decide_rename("anything", "main", False))

    def test_same_name_no_rename(self):
        self.assertIsNone(self.pin.decide_rename("main", "main", True))

    def test_same_name_stripped_no_rename(self):
        self.assertIsNone(self.pin.decide_rename("  main  ", "main", True))

    def test_drift_returns_target(self):
        self.assertEqual(
            self.pin.decide_rename("Old session name", "main", True), "main"
        )

    def test_empty_target_no_rename(self):
        self.assertIsNone(self.pin.decide_rename("anything", "", True))
        self.assertIsNone(self.pin.decide_rename("anything", "   ", True))

    def test_empty_current_returns_target(self):
        # empty current name (uninitialized chat) should be filled in
        self.assertEqual(self.pin.decide_rename("", "main", True), "main")

    def test_whitespace_only_current_treated_as_empty(self):
        self.assertEqual(self.pin.decide_rename("   ", "main", True), "main")


class AssertPrimaryNameTests(unittest.TestCase):
    """Pluggable seams — no framework imports, no DB."""

    def setUp(self):
        self.pin = _load_pin()

    def _setup(self, *, continuous, lock, primary=None, current_name="main", target):
        calls = []
        context = SimpleNamespace(id="ctx-primary", name=current_name)
        resolved = context if primary is None else primary

        def primary_getter():
            return resolved

        def name_setter(ctx, new_name):
            calls.append((ctx.id, new_name))
            ctx.name = new_name

        return context, primary_getter, name_setter, calls

    def test_continuous_off_no_rename(self):
        context, pg, ns, calls = self._setup(
            continuous=False,
            lock=True,
            primary=SimpleNamespace(id="ctx", name="Old"),
            current_name="Old",
            target="main",
        )
        result = self.pin.assert_primary_name(
            primary_getter=pg,
            name_setter=ns,
            continuous_mode=False,
            lock_enabled=True,
            target_name="main",
        )
        self.assertIsNone(result)
        self.assertEqual(calls, [])

    def test_lock_off_no_rename(self):
        _, pg, ns, calls = self._setup(
            continuous=True,
            lock=False,
            primary=SimpleNamespace(id="ctx", name="Old"),
            current_name="Old",
            target="main",
        )
        result = self.pin.assert_primary_name(
            primary_getter=pg,
            name_setter=ns,
            continuous_mode=True,
            lock_enabled=False,
            target_name="main",
        )
        self.assertIsNone(result)
        self.assertEqual(calls, [])

    def test_no_live_primary_no_rename(self):
        calls = []
        ns = lambda ctx, name: calls.append((ctx.id, name))  # noqa: E731
        result = self.pin.assert_primary_name(
            primary_getter=lambda: None,
            name_setter=ns,
            continuous_mode=True,
            lock_enabled=True,
            target_name="main",
        )
        self.assertIsNone(result)
        self.assertEqual(calls, [])

    def test_drift_calls_setter_and_returns_target(self):
        context, pg, ns, calls = self._setup(
            continuous=True,
            lock=True,
            primary=None,
            current_name="Old utility-model name",
            target="main",
        )
        result = self.pin.assert_primary_name(
            primary_getter=pg,
            name_setter=ns,
            continuous_mode=True,
            lock_enabled=True,
            target_name="main",
        )
        self.assertEqual(result, "main")
        self.assertEqual(calls, [("ctx-primary", "main")])
        self.assertEqual(context.name, "main")

    def test_already_correct_no_setter_call(self):
        context, pg, ns, calls = self._setup(
            continuous=True,
            lock=True,
            primary=None,
            current_name="main",
            target="main",
        )
        result = self.pin.assert_primary_name(
            primary_getter=pg,
            name_setter=ns,
            continuous_mode=True,
            lock_enabled=True,
            target_name="main",
        )
        self.assertIsNone(result)
        self.assertEqual(calls, [])


class SettingsDefaultsTests(unittest.TestCase):
    def test_main_chat_name_default(self):
        from usr.plugins.chat_history.helpers import settings

        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            for var in ("CH_MAIN_CHAT_NAME",):
                os.environ.pop(var, None)
            with mock.patch.object(settings, "_plugin_config", lambda: {}):
                self.assertEqual(settings.main_chat_name(), "Main")

    def test_lock_main_chat_name_default_true(self):
        from usr.plugins.chat_history.helpers import settings

        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("CH_LOCK_MAIN_CHAT_NAME", None)
            with mock.patch.object(settings, "_plugin_config", lambda: {}):
                self.assertTrue(settings.lock_main_chat_name())

    def test_pin_main_chat_default_true(self):
        from usr.plugins.chat_history.helpers import settings

        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("CH_PIN_MAIN_CHAT", None)
            with mock.patch.object(settings, "_plugin_config", lambda: {}):
                self.assertTrue(settings.pin_main_chat())


if __name__ == "__main__":
    unittest.main()
