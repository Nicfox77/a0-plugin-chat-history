"""Deck rendering tests (pure function, no DB required)."""

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

BASE = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)


def entries(count: int, size: int = 400) -> list[dict]:
    return [
        {
            "created_at": BASE + timedelta(minutes=i),
            "message_count": 5,
            "summary": f"Summary number {i} " + "x" * size,
        }
        for i in range(count)
    ]


class StorageModeTests(unittest.TestCase):
    def test_default_is_replica(self):
        from usr.plugins.chat_history.helpers import settings

        with mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("CH_STORAGE_MODE", None)
            with mock.patch.object(
                settings, "_plugin_config", lambda: {}
            ):
                self.assertEqual(settings.storage_mode(), "replica")

    def test_invalid_falls_back_to_replica(self):
        from usr.plugins.chat_history.helpers import settings

        with mock.patch.dict("os.environ", {"CH_STORAGE_MODE": "bogus"}):
            self.assertEqual(settings.storage_mode(), "replica")

    def test_authoritative_accepted(self):
        from usr.plugins.chat_history.helpers import settings

        with mock.patch.dict("os.environ", {"CH_STORAGE_MODE": "authoritative"}):
            self.assertEqual(settings.storage_mode(), "authoritative")


class RenderEntriesTests(unittest.TestCase):
    def _render(self, items, budget=None):
        from usr.plugins.chat_history.helpers.deck import render_entries

        if budget is None:
            return render_entries(items)
        return render_entries(items, budget)

    def test_chronological_order(self):
        rendered = self._render(entries(3, size=50))
        first = rendered.index("Summary number 0")
        last = rendered.index("Summary number 2")
        self.assertLess(first, last)

    def test_empty_renders_empty(self):
        self.assertEqual(self._render([]), "")

    def test_budget_drops_oldest_with_note(self):
        # Renderer clamps budget to >= 300; entries are ~100 tokens each.
        rendered = self._render(entries(5), budget=350)
        self.assertIn("omitted", rendered)
        self.assertNotIn("Summary number 0", rendered)
        self.assertIn("Summary number 4", rendered)  # newest kept in full

    def test_seed_transcript_strip(self):
        from usr.plugins.chat_history.helpers.deck import (
            SEED_MARKER,
            strip_seed_transcript,
        )

        text = f"{SEED_MARKER} The earlier part... Continue seamlessly.\nUser: hello"
        stripped = strip_seed_transcript(text)
        self.assertNotIn(SEED_MARKER, stripped)
        self.assertTrue(stripped.startswith("The earlier part"))


if __name__ == "__main__":
    unittest.main()
