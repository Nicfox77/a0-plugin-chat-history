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

        def read_prompt(name, **kwargs):
            if name == "compact.memory.sys.md":
                return "compact system"
            assert name == "compact.memory.msg.md", name
            return f"PREV:{kwargs.get('previous', '')}|CONV:{kwargs['conversation']}"

        agent = SimpleNamespace(read_prompt=read_prompt)
        messages = [_message("m1", ai=False, tokens=100, content="conversation")]

        with mock.patch(
            "plugins._chat_compaction.helpers.compactor._build_model",
            return_value=({}, model),
        ):
            result = await rolling._summarize_prefix(agent, messages, 100_000, "old memory")

        self.assertEqual(result, "summary")
        call = model.unified_call.await_args.kwargs
        self.assertEqual(call["max_tokens"], 5_000)
        self.assertIn("must not exceed 5000 tokens", call["system_message"])
        self.assertIn("PREV:old memory", call["user_message"])
        self.assertIn("CONV:user: conversation", call["user_message"])

    async def test_summary_without_previous_omits_it(self):
        from usr.plugins.chat_history.helpers import rolling

        model = SimpleNamespace(
            unified_call=mock.AsyncMock(return_value=("summary", ""))
        )
        agent = SimpleNamespace(
            read_prompt=lambda name, **kwargs: (
                "compact system" if name == "compact.memory.sys.md" else kwargs["conversation"]
            )
        )
        messages = [_message("m1", ai=False, tokens=100, content="conversation")]

        with mock.patch(
            "plugins._chat_compaction.helpers.compactor._build_model",
            return_value=({}, model),
        ):
            await rolling._summarize_prefix(agent, messages, 100_000)

        call = model.unified_call.await_args.kwargs
        self.assertEqual(call["user_message"], "user: conversation")


    async def test_summary_retries_once_on_empty_output(self):
        from usr.plugins.chat_history.helpers import rolling

        model = SimpleNamespace(
            unified_call=mock.AsyncMock(side_effect=[("", ""), ("summary", "")])
        )
        agent = SimpleNamespace(
            read_prompt=lambda name, **kwargs: "prompt"
        )
        messages = [_message("m1", ai=False, tokens=100, content="conversation")]

        with mock.patch(
            "plugins._chat_compaction.helpers.compactor._build_model",
            return_value=({}, model),
        ):
            result = await rolling._summarize_prefix(agent, messages, 100_000)

        self.assertEqual(result, "summary")
        self.assertEqual(model.unified_call.await_count, 2)

    async def test_summary_uses_utility_model(self):
        from usr.plugins.chat_history.helpers import rolling

        model = SimpleNamespace(
            unified_call=mock.AsyncMock(return_value=("summary", ""))
        )
        agent = SimpleNamespace(read_prompt=lambda name, **kwargs: "prompt")
        messages = [_message("m1", ai=False, tokens=100, content="conversation")]

        with mock.patch(
            "plugins._chat_compaction.helpers.compactor._build_model",
            return_value=({}, model),
        ) as build:
            await rolling._summarize_prefix(agent, messages, 100_000)

        self.assertIs(build.call_args.args[0], False)

    async def test_cycle_aborts_gracefully_on_empty_summary(self):
        from usr.plugins.chat_history.helpers import rolling

        agent0 = SimpleNamespace(history=SimpleNamespace(output=lambda: []))
        context = SimpleNamespace(id="ctx-1", agent0=agent0)
        prefix = [_message(f"m{i}", ai=i % 2 == 0, tokens=10, content=f"turn {i}")
                  for i in range(6)]

        with mock.patch.object(rolling, "select_eviction_prefix",
                               return_value=(prefix, prefix[:0], 1000)), \
             mock.patch.object(rolling, "eviction_target",
                               return_value=100), \
             mock.patch.object(rolling, "_summarize_prefix",
                               mock.AsyncMock(return_value="")), \
             mock.patch.object(rolling.deck_state, "append_segment",
                               mock.Mock(side_effect=AssertionError(
                                   "no segment must be written for an empty summary"))), \
             mock.patch.object(rolling, "_replace_history",
                               mock.Mock(side_effect=AssertionError(
                                   "history must not be replaced for an empty summary"))):
            self.assertFalse(await rolling._run_cycle(context))


class LogTrimTests(unittest.TestCase):
    def _log(self, entries):
        from types import SimpleNamespace

        items = [
            SimpleNamespace(no=i, type=t, id=id_, content=c)
            for i, (t, id_, c) in enumerate(entries)
        ]
        log = SimpleNamespace(
            logs=items,
            updates=[],
            log=lambda **kw: SimpleNamespace(
                no=0, type=kw.get("type"), id=kw.get("id"), content=kw.get("content")
            ),
        )
        # emulate Log.log(): append and return the item
        def _log_fn(**kw):
            item = SimpleNamespace(
                no=len(log.logs), type=kw.get("type"), id=kw.get("id"),
                heading=kw.get("heading"), content=kw.get("content"),
            )
            log.logs.append(item)
            return item

        log.log = _log_fn
        context = SimpleNamespace(log=log)
        return context

    def test_trims_to_first_retained_entry(self):
        from usr.plugins.chat_history.helpers import rolling

        context = self._log([
            ("user", "old-1", "old turn"),
            ("response", "old-2", "old reply"),
            ("tool", "", "tool work"),
            ("user", "keep-1", "kept turn"),
            ("response", "keep-2", "kept reply"),
        ])
        ok = rolling.trim_log_to_tail(
            context, [{"id": "keep-1"}, {"id": "keep-2"}]
        )
        self.assertTrue(ok)
        types = [item.type for item in context.log.logs]
        ids = [item.id for item in context.log.logs]
        self.assertEqual(types, ["info", "user", "response"])
        self.assertEqual(ids[1:], ["keep-1", "keep-2"])
        self.assertEqual([item.no for item in context.log.logs], [0, 1, 2])
        self.assertEqual(context.log.logs[0].heading, "Context compacted")

    def test_progress_cursor_reset_to_valid_index(self):
        from usr.plugins.chat_history.helpers import rolling

        context = self._log([
            ("user", "old-1", "old turn"),
            ("response", "old-2", "old reply"),
            ("user", "keep-1", "kept turn"),
            ("response", "keep-2", "kept reply"),
        ])
        context.log.progress_no = 3  # points at the final entry pre-trim
        context.log.progress = "busy"
        ok = rolling.trim_log_to_tail(
            context, [{"id": "keep-1"}, {"id": "keep-2"}]
        )
        self.assertTrue(ok)
        self.assertEqual(context.log.progress_no, -1)
        self.assertEqual(context.log.progress, "Waiting for input")
        self.assertLessEqual(context.log.progress_no, len(context.log.logs) - 1)

    def test_no_anchor_fails_open(self):
        from usr.plugins.chat_history.helpers import rolling

        context = self._log([
            ("user", "old-1", "old turn"),
            ("response", "old-2", "old reply"),
        ])
        before = list(context.log.logs)
        ok = rolling.trim_log_to_tail(context, [{"id": "missing"}])
        self.assertFalse(ok)
        self.assertEqual(context.log.logs, before)

    def test_empty_tail_ids_is_noop(self):
        from usr.plugins.chat_history.helpers import rolling

        context = self._log([("user", "u", "hi")])
        self.assertFalse(rolling.trim_log_to_tail(context, [{"id": ""}]))


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

    def test_continuous_mode_does_not_schedule_below_threshold(self):
        from usr.plugins.chat_history.helpers import rolling

        agent = SimpleNamespace(
            get_data=mock.Mock(return_value=None),
            set_data=mock.Mock(),
        )
        extension = self.organizer.OrganizeHistory(agent=agent)
        with (
            mock.patch.object(self.organizer, "_continuous_primary", return_value=True),
            mock.patch.object(rolling, "should_compact", return_value=False),
            mock.patch.object(self.organizer, "DeferredTask") as deferred,
        ):
            asyncio.run(extension.execute())

        deferred.assert_not_called()
        agent.set_data.assert_not_called()

    def test_continuous_mode_schedules_at_threshold(self):
        from usr.plugins.chat_history.helpers import rolling

        task = mock.Mock()
        agent = SimpleNamespace(
            get_data=mock.Mock(return_value=None),
            set_data=mock.Mock(),
        )
        extension = self.organizer.OrganizeHistory(agent=agent)
        with (
            mock.patch.object(self.organizer, "_continuous_primary", return_value=True),
            mock.patch.object(rolling, "should_compact", return_value=True),
            mock.patch.object(self.organizer, "DeferredTask", return_value=task),
        ):
            asyncio.run(extension.execute())

        task.start_task.assert_called_once_with(self.organizer.compress_history, agent)
        agent.set_data.assert_called_once_with(self.organizer.DATA_NAME_TASK, task)


class CompressionWaitTests(unittest.IsolatedAsyncioTestCase):
    async def test_below_threshold_noop_does_not_log_stalled_warning(self):
        from usr.plugins.chat_history.extensions.python.message_loop_prompts_before import (
            _90_organize_history_wait as waiter,
        )
        from usr.plugins.chat_history.helpers import rolling

        task = SimpleNamespace(
            is_ready=lambda: False,
            result=mock.AsyncMock(return_value=False),
        )
        history = SimpleNamespace(
            is_over_limit=mock.Mock(return_value=False),
            get_tokens=mock.Mock(return_value=35_000),
        )
        log = SimpleNamespace(set_progress=mock.Mock(), log=mock.Mock())
        agent = SimpleNamespace(
            history=history,
            context=SimpleNamespace(log=log),
            get_data=mock.Mock(return_value=task),
            set_data=mock.Mock(),
        )
        extension = waiter.OrganizeHistoryWait(agent=agent)
        with (
            mock.patch.object(waiter, "_continuous_primary", return_value=True),
            mock.patch.object(rolling, "should_compact", return_value=False),
        ):
            await extension.execute()

        task.result.assert_awaited_once_with()
        agent.set_data.assert_called_once_with(waiter.DATA_NAME_TASK, None)
        log.log.assert_not_called()


class FullPrefixSelectionTests(unittest.TestCase):
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

    def test_small_history_evicts_whole(self):
        messages = [
            _message(f"m{i + 1}", ai=False, tokens=10)
            for i in range(3)
        ]
        prefix, tail, tokens = self.rolling.select_full_prefix(messages, 1000)
        self.assertEqual([message["id"] for message in prefix], ["m1", "m2", "m3"])
        self.assertEqual(tail, [])
        self.assertEqual(tokens, 30)

    def test_empty_history_returns_empty(self):
        prefix, tail, tokens = self.rolling.select_full_prefix([], 1000)
        self.assertEqual(prefix, [])
        self.assertEqual(tail, [])
        self.assertEqual(tokens, 0)

    def test_two_messages_evicted_whole(self):
        messages = [
            _message("m1", ai=False, tokens=10),
            _message("m2", ai=True, tokens=10),
        ]
        prefix, tail, tokens = self.rolling.select_full_prefix(messages, 1000)
        self.assertEqual([message["id"] for message in prefix], ["m1", "m2"])
        self.assertEqual(tail, [])
        self.assertEqual(tokens, 20)

    def test_large_history_chunks_to_boundary(self):
        # 8 alternating user/ai messages, 100 tokens each, chunk 250.
        messages = [
            _message(f"m{i + 1}", ai=i % 2 == 1, tokens=100)
            for i in range(8)
        ]
        prefix, tail, tokens = self.rolling.select_full_prefix(messages, 250)
        # First call: walking history[:-1] reaches 250 at consumed index 3
        # (300 tokens); user boundaries in range(1, 8) are at indexes 2, 4, 6
        # (every other message is a real user turn here). First boundary
        # >= desired(3) is index 4 → prefix[:4], tail[4:].
        self.assertEqual([message["id"] for message in prefix], ["m1", "m2", "m3", "m4"])
        self.assertEqual([message["id"] for message in tail], ["m5", "m6", "m7", "m8"])
        self.assertEqual(tokens, 400)
        self.assertEqual(tokens, sum(message["test_tokens"] for message in prefix))

        # Repeatedly calling on the remaining tail must eventually drain it
        # to an empty prefix.
        remaining = list(tail)
        drained = 0
        while remaining:
            next_prefix, next_tail, _ = self.rolling.select_full_prefix(remaining, 250)
            if not next_prefix:
                break
            drained += 1
            self.assertEqual(
                next_prefix + next_tail,
                remaining,
                "select_full_prefix must partition the input",
            )
            remaining = list(next_tail)
        self.assertEqual(remaining, [])

    def test_no_user_boundaries_falls_back_to_evict_all(self):
        # All messages are AI replies after a synthetic first boundary — no
        # real user turns after index 0 — yet total exceeds the chunk budget.
        messages = [
            _message("m1", ai=False, tokens=10),
            _message("m2", ai=True, tokens=200),
            _message("m3", ai=True, tokens=200),
            _message("m4", ai=True, tokens=200),
        ]
        prefix, tail, tokens = self.rolling.select_full_prefix(messages, 250)
        self.assertEqual([message["id"] for message in prefix], ["m1", "m2", "m3", "m4"])
        self.assertEqual(tail, [])
        self.assertEqual(tokens, 610)


class TrimLogFullTests(unittest.TestCase):
    def _log(self, entries):
        from types import SimpleNamespace

        items = [
            SimpleNamespace(no=i, type=t, id=id_, content=c)
            for i, (t, id_, c) in enumerate(entries)
        ]
        log = SimpleNamespace(
            logs=items,
            updates=[],
            progress="busy",
            progress_no=5,
            log=lambda **kw: SimpleNamespace(
                no=0, type=kw.get("type"), id=kw.get("id"), content=kw.get("content")
            ),
        )

        def _log_fn(**kw):
            item = SimpleNamespace(
                no=len(log.logs), type=kw.get("type"), id=kw.get("id"),
                heading=kw.get("heading"), content=kw.get("content"),
            )
            log.logs.append(item)
            return item

        log.log = _log_fn
        context = SimpleNamespace(log=log)
        return context

    def test_trims_to_single_banner(self):
        from usr.plugins.chat_history.helpers import rolling

        context = self._log([
            ("user", "u1", "old turn"),
            ("response", "r1", "old reply"),
            ("user", "u2", "another turn"),
        ])
        ok = rolling.trim_log_full(context)
        self.assertTrue(ok)
        self.assertEqual(len(context.log.logs), 1)
        banner = context.log.logs[0]
        self.assertEqual(banner.type, "info")
        self.assertEqual(banner.heading, "Context compacted")
        self.assertEqual(banner.no, 0)
        self.assertEqual(context.log.progress, "Waiting for input")
        self.assertEqual(context.log.progress_no, -1)
        self.assertEqual(context.log.updates, [])

    def test_empty_log_still_writes_banner(self):
        from usr.plugins.chat_history.helpers import rolling

        context = self._log([])
        self.assertTrue(rolling.trim_log_full(context))
        self.assertEqual(len(context.log.logs), 1)
        self.assertEqual(context.log.logs[0].heading, "Context compacted")

    def test_returns_false_when_log_missing(self):
        from usr.plugins.chat_history.helpers import rolling

        self.assertFalse(rolling.trim_log_full(SimpleNamespace(log=None)))


class FullCompactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_run_archives_until_empty(self):
        from usr.plugins.chat_history.helpers import rolling

        # One cycle empties a 2-message history (small history → evict all).
        messages = [
            _message("m1", ai=False, tokens=10),
            _message("m2", ai=True, tokens=10),
        ]
        # Shared mutable backing list — the mocked _replace_history shrinks
        # it in place so the next cycle's output() sees the new history.
        state = list(messages)
        agent = SimpleNamespace(
            history=SimpleNamespace(output=lambda: list(state))
        )
        context = SimpleNamespace(id="ctx-full", agent0=agent)
        append_mock = mock.Mock(return_value="seg-1")

        def fake_replace(_agent, tail):
            state.clear()
            state.extend(tail)

        with (
            mock.patch.object(rolling, "eviction_target", return_value=150_000),
            mock.patch.object(
                rolling.deck_state, "fetch_entries", return_value=[{"summary": "old"}]
            ),
            mock.patch.object(
                rolling,
                "_summarize_prefix",
                mock.AsyncMock(return_value="sum-1"),
            ) as summarize,
            mock.patch.object(rolling.deck_state, "append_segment", append_mock),
            mock.patch.object(
                rolling, "_replace_history", side_effect=fake_replace
            ) as replace,
            mock.patch.object(rolling, "trim_log_to_tail") as trim_tail,
            mock.patch.object(rolling, "trim_log_full") as trim_full,
            mock.patch.object(rolling, "_persist_pruned_history"),
            mock.patch.object(
                rolling,
                "_publish_hindsight_snapshot",
                mock.AsyncMock(),
            ) as publish,
        ):
            result = await rolling.run_full_compaction(context)

        self.assertEqual(result, 1)
        summarize.assert_awaited_once()
        # previous_summary is the 4th positional arg of _summarize_prefix.
        self.assertEqual(summarize.await_args.args[3], "old")
        self.assertGreater(summarize.await_args.args[2], 0)
        self.assertEqual(
            [message["id"] for message in summarize.await_args.args[1]], ["m1", "m2"]
        )
        append_mock.assert_called_once()
        append_kwargs = append_mock.call_args.kwargs
        self.assertEqual(append_kwargs["context_id"], "ctx-full")
        # token_count comes from estimate_context_tokens; the _message
        # factory's `tokens` field is only honoured when message_tokens is
        # patched, which it isn't in this class.
        self.assertGreater(append_kwargs["token_count"], 0)
        self.assertEqual(
            [message["id"] for message in append_kwargs["messages"]], ["m1", "m2"]
        )
        # Whole history was archived → no retained tail → empty replace + trim_full.
        replace.assert_called_once_with(agent, [])
        trim_full.assert_called_once_with(context)
        trim_tail.assert_not_called()
        publish.assert_awaited_once_with(agent)

    async def test_full_run_chunks_large_history(self):
        from usr.plugins.chat_history.helpers import rolling

        # Fake history whose output() shrinks as chunks are replaced.
        remaining = [
            _message(f"m{i + 1}", ai=i % 2 == 1, tokens=100)
            for i in range(8)
        ]
        state = list(remaining)
        agent = SimpleNamespace(history=SimpleNamespace(output=lambda: list(state)))
        context = SimpleNamespace(id="ctx-chunks", agent0=agent)

        # First call: chunk 4. Second call on the remaining 4 → evict all
        # (no aligned boundary left). Third would never run because the
        # loop body sees an empty history on the next iteration.
        chunk_results = iter([
            (remaining[:4], remaining[4:], 400),
            (remaining[4:], [], 400),
        ])

        def fake_replace(_agent, tail):
            state.clear()
            state.extend(tail)

        # fetch_entries must reflect what was archived so far — first call
        # returns the prior "old" summary, second returns "s1" appended by
        # the first cycle. This is how the continuation voice chains.
        fetch_returns = iter([
            [{"summary": "old"}],
            [{"summary": "s1"}],
        ])

        with (
            mock.patch.object(rolling, "eviction_target", return_value=150_000),
            mock.patch.object(
                rolling.deck_state, "fetch_entries",
                side_effect=lambda *a, **kw: next(fetch_returns),
            ),
            mock.patch.object(
                rolling, "select_full_prefix", side_effect=lambda hist, _chunk: next(chunk_results)
            ),
            mock.patch.object(
                rolling, "_summarize_prefix",
                mock.AsyncMock(side_effect=["s1", "s2"]),
            ) as summarize,
            mock.patch.object(rolling.deck_state, "append_segment",
                              mock.Mock(side_effect=["seg-1", "seg-2"])) as append_mock,
            mock.patch.object(
                rolling, "_replace_history", side_effect=fake_replace
            ) as replace,
            mock.patch.object(rolling, "trim_log_to_tail") as trim_tail,
            mock.patch.object(rolling, "trim_log_full") as trim_full,
            mock.patch.object(rolling, "_persist_pruned_history"),
            mock.patch.object(
                rolling, "_publish_hindsight_snapshot", mock.AsyncMock()
            ) as publish,
        ):
            result = await rolling.run_full_compaction(context)

        self.assertEqual(result, 2)
        self.assertEqual(summarize.await_count, 2)
        # previous_summary is the 4th positional arg of _summarize_prefix.
        self.assertEqual(summarize.await_args_list[0].args[3], "old")
        self.assertEqual(summarize.await_args_list[1].args[3], "s1")
        self.assertEqual(append_mock.call_count, 2)
        # First cycle had a retained tail → trim_log_to_tail; second had
        # none → trim_log_full.
        trim_tail.assert_called_once()
        trim_full.assert_called_once()
        # History ends empty (the last replace got an empty list).
        last_replace_args = replace.call_args_list[-1].args
        self.assertEqual(last_replace_args[1], [])
        publish.assert_awaited_once_with(agent)

    async def test_full_run_aborts_on_empty_summary(self):
        from usr.plugins.chat_history.helpers import rolling

        messages = [
            _message("m1", ai=False, tokens=100),
            _message("m2", ai=True, tokens=100),
            _message("m3", ai=False, tokens=100),
            _message("m4", ai=True, tokens=100),
            _message("m5", ai=False, tokens=100),
        ]
        agent = SimpleNamespace(history=SimpleNamespace(output=lambda: list(messages)))
        context = SimpleNamespace(id="ctx-empty", agent0=agent)

        with (
            mock.patch.object(rolling, "eviction_target", return_value=150_000),
            mock.patch.object(rolling.deck_state, "fetch_entries", return_value=[]),
            mock.patch.object(
                rolling, "select_full_prefix",
                return_value=(messages[:3], messages[3:], 300),
            ),
            mock.patch.object(
                rolling, "_summarize_prefix", mock.AsyncMock(return_value="")
            ),
            mock.patch.object(rolling.deck_state, "append_segment") as append_mock,
            mock.patch.object(rolling, "_replace_history") as replace,
            mock.patch.object(rolling, "_persist_pruned_history"),
            mock.patch.object(rolling, "_publish_hindsight_snapshot", mock.AsyncMock()) as publish,
        ):
            result = await rolling.run_full_compaction(context)

        self.assertEqual(result, 0)
        append_mock.assert_not_called()
        replace.assert_not_called()
        publish.assert_not_awaited()

    async def test_full_run_skips_when_already_active(self):
        from usr.plugins.chat_history.helpers import rolling

        # No agent0 needed — the active guard short-circuits before any
        # history is read. Force the flag back to False afterwards so
        # subsequent tests in the same process aren't poisoned.
        rolling._COMPACTING["active"] = True
        try:
            with (
                mock.patch.object(rolling.deck_state, "append_segment") as append_mock,
                mock.patch.object(rolling, "_publish_hindsight_snapshot", mock.AsyncMock()) as publish,
            ):
                result = await rolling.run_full_compaction(SimpleNamespace(id="x"))
            self.assertEqual(result, 0)
            append_mock.assert_not_called()
            publish.assert_not_awaited()
        finally:
            rolling._COMPACTING["active"] = False


if __name__ == "__main__":
    unittest.main()
