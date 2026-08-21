from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock


def _message(message_id: str, *, ai: bool, tokens: int, content=None) -> dict:
    return {
        "id": message_id,
        "ai": ai,
        "content": content if content is not None else message_id,
        "test_tokens": tokens,
        "metadata": {},
        "sequence": int(message_id.removeprefix("m")),
    }


class PolicyTests(unittest.TestCase):
    def setUp(self):
        from usr.plugins.chat_history.helpers import rolling

        self.rolling = rolling

    def test_trigger_uses_agent_zero_ctx_history(self):
        with mock.patch.object(
            self.rolling,
            "_chat_model_config",
            return_value={"ctx_length": 500_000, "ctx_history": 0.65},
        ):
            self.assertEqual(self.rolling.compaction_threshold(object()), 325_000)

    def test_eviction_target_is_separate_fraction(self):
        with (
            mock.patch.object(
                self.rolling,
                "_chat_model_config",
                return_value={"ctx_length": 500_000, "ctx_history": 0.65},
            ),
            mock.patch.object(self.rolling, "eviction_ratio", return_value=0.3),
        ):
            self.assertEqual(self.rolling.eviction_target(object()), 150_000)

    def test_summary_output_budget_is_five_percent(self):
        self.assertEqual(self.rolling.summary_output_budget(100_000), 5_000)
        self.assertEqual(self.rolling.summary_output_budget(150_000), 7_500)


class SummaryGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_call_enforces_ratio_budget(self):
        from usr.plugins.chat_history.helpers import rolling

        model = SimpleNamespace(
            unified_call=mock.AsyncMock(return_value=("summary", ""))
        )
        agent = SimpleNamespace(
            read_prompt=lambda name, **kwargs: (
                "compact system" if name == "compact.sys.md" else kwargs["conversation"]
            )
        )
        messages = [_message("m1", ai=False, tokens=100, content="conversation")]

        with mock.patch(
            "plugins._chat_compaction.helpers.compactor._build_model",
            return_value=({}, model),
        ):
            result = await rolling._summarize_prefix(agent, messages, 100_000)

        self.assertEqual(result, "summary")
        call = model.unified_call.await_args.kwargs
        self.assertEqual(call["max_tokens"], 5_000)
        self.assertIn("must not exceed 5000 tokens", call["system_message"])


class PrefixSelectionTests(unittest.TestCase):
    def setUp(self):
        from usr.plugins.chat_history.helpers import rolling

        self.rolling = rolling
        self.token_patch = mock.patch.object(
            rolling,
            "message_tokens",
            side_effect=lambda message: message["test_tokens"],
        )
        self.token_patch.start()
        self.addCleanup(self.token_patch.stop)

    def test_cutoff_moves_to_next_real_user_turn(self):
        messages = [
            _message("m1", ai=False, tokens=100),
            _message("m2", ai=True, tokens=100),
            _message(
                "m3",
                ai=False,
                tokens=100,
                content={"tool_name": "shell", "tool_result": "done"},
            ),
            _message("m4", ai=True, tokens=100),
            _message("m5", ai=False, tokens=100),
            _message("m6", ai=True, tokens=100),
        ]

        prefix, tail, token_count = self.rolling.select_eviction_prefix(messages, 250)

        self.assertEqual([message["id"] for message in prefix], ["m1", "m2", "m3", "m4"])
        self.assertEqual([message["id"] for message in tail], ["m5", "m6"])
        self.assertEqual(token_count, 400)

    def test_no_boundary_leaves_history_unchanged(self):
        messages = [
            _message("m1", ai=True, tokens=100),
            _message("m2", ai=True, tokens=100),
            _message("m3", ai=True, tokens=100),
        ]

        prefix, tail, token_count = self.rolling.select_eviction_prefix(messages, 50)

        self.assertEqual(prefix, [])
        self.assertIs(tail, messages)
        self.assertEqual(token_count, 0)

    def test_rebuild_preserves_tail_ids_and_sequences(self):
        agent = SimpleNamespace(history=None, last_user_message=None)
        tail = [
            _message("m7", ai=False, tokens=100),
            _message("m8", ai=True, tokens=100),
        ]

        self.rolling._replace_history(agent, tail)

        messages = agent.history.all_messages()
        self.assertEqual([message.id for message in messages], ["m7", "m8"])
        self.assertEqual([message.sequence for message in messages], [7, 8])
        self.assertEqual(agent.history.counter, 8)
        self.assertEqual(agent.last_user_message.id, "m7")


class SchemaTests(unittest.TestCase):
    def test_raw_compaction_segments_are_durable(self):
        from usr.plugins.chat_history.helpers import db

        self.assertIn("CREATE TABLE IF NOT EXISTS compaction_segments", db.SCHEMA_SQL)
        self.assertIn("messages         JSONB", db.SCHEMA_SQL)


class StrategySwitchTests(unittest.TestCase):
    def setUp(self):
        from usr.plugins.chat_history.extensions.python.message_loop_end import (
            _10_organize_history as organizer,
        )

        self.organizer = organizer

    def test_continuous_primary_uses_partial_compactor(self):
        from usr.plugins.chat_history.helpers import rolling

        agent = SimpleNamespace(context=object())
        with (
            mock.patch.object(self.organizer, "_continuous_primary", return_value=True),
            mock.patch.object(rolling, "should_compact", return_value=True),
            mock.patch.object(
                rolling,
                "run_rolling_compaction",
                new=mock.AsyncMock(return_value=True),
            ) as compact,
        ):
            result = asyncio.run(self.organizer.compress_history(agent))

        self.assertTrue(result)
        compact.assert_awaited_once_with(agent.context)

    def test_continuous_mode_off_uses_stock_history_compression(self):
        history = SimpleNamespace(compress=mock.AsyncMock(return_value=True))
        agent = SimpleNamespace(history=history)
        with (
            mock.patch.object(self.organizer, "_continuous_primary", return_value=False),
            mock.patch.object(self.organizer, "clear_responses_provider_state"),
        ):
            result = asyncio.run(self.organizer.compress_history(agent))

        self.assertTrue(result)
        history.compress.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
