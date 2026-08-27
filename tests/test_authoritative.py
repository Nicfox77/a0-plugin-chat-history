"""Tests for authoritative-mode snapshot capture (no real DB required)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from usr.plugins.chat_history.helpers.authoritative import (
    _FILE_CACHE_LOCK,
    _FILE_MTIME_CACHE,
    capture_context,
)


class _FakeCursor:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.log.append((sql, params))


class _FakeConnection:
    def __init__(self):
        self.queries = []

    def cursor(self):
        return _FakeCursor(self.queries)


def _context(context_id="ctx-delta"):
    ctx = mock.MagicMock()
    ctx.id = context_id
    return ctx


class TranscriptDeltaTests(unittest.TestCase):
    def setUp(self):
        _FILE_MTIME_CACHE.pop("ctx-delta", None)
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        _FILE_MTIME_CACHE.pop("ctx-delta", None)
        self.tmp.cleanup()

    def _run_capture(self) -> list:
        conn = _FakeConnection()
        with mock.patch(
            "usr.plugins.chat_history.helpers.authoritative.db.get_connection",
            return_value=conn,
        ), mock.patch(
            "helpers.persist_chat.export_json_chat",
            return_value='{"id": "ctx-delta", "agents": [{"history": ""}]}',
        ), mock.patch(
            "helpers.persist_chat.get_chat_folder_path", return_value=str(self.dir)
        ):
            self.assertTrue(capture_context(_context()))
        return [
            (sql, params) for sql, params in conn.queries
            if "INSERT INTO message_files" in sql
        ]

    def _write(self, name, content):
        (self.dir / "messages").mkdir(exist_ok=True)
        target = self.dir / "messages" / name
        target.write_text(content, encoding="utf-8")
        return target

    def test_first_capture_upserts_all_files(self):
        self._write("1.txt", "one")
        self._write("2.txt", "two")
        inserts = self._run_capture()
        self.assertEqual(len(inserts), 2)

    def test_unchanged_files_are_skipped(self):
        self._write("1.txt", "one")
        self._write("2.txt", "two")
        self._run_capture()
        self._write("3.txt", "three")
        inserts = self._run_capture()
        self.assertEqual(len(inserts), 1)
        self.assertEqual(inserts[0][1][1], "3.txt")

    def test_modified_file_is_re_upserted(self):
        target = self._write("1.txt", "one")
        self._run_capture()
        os.utime(target, (target.stat().st_mtime + 10,) * 2)
        inserts = self._run_capture()
        self.assertEqual(len(inserts), 1)
        self.assertEqual(inserts[0][1][1], "1.txt")

    def test_cache_is_per_context(self):
        self._write("1.txt", "one")
        self._run_capture()
        with _FILE_CACHE_LOCK:
            self.assertIn("1.txt", _FILE_MTIME_CACHE.get("ctx-delta", {}))


class ShellSnapshotGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _capture_with_export(self, export_return):
        conn = _FakeConnection()
        with mock.patch(
            "usr.plugins.chat_history.helpers.authoritative.db.get_connection",
            return_value=conn,
        ), mock.patch(
            "helpers.persist_chat.export_json_chat", return_value=export_return
        ):
            return capture_context(_context()), conn.queries

    def test_empty_blob_refused(self):
        ok, queries = self._capture_with_export("{}")
        self.assertFalse(ok)
        self.assertEqual(queries, [])

    def test_blob_without_agents_refused(self):
        ok, queries = self._capture_with_export('{"id": "x"}')
        self.assertFalse(ok)
        self.assertEqual(queries, [])

    def test_valid_blob_accepted(self):
        ok, queries = self._capture_with_export(
            '{"id": "x", "agents": [{"history": ""}]}'
        )
        self.assertTrue(ok)
        self.assertTrue(any("context_snapshots" in sql for sql, _ in queries))


if __name__ == "__main__":
    unittest.main()
