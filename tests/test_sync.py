"""Tests for sync row shaping and content projection (no DB needed)."""

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(PLUGIN_ROOT.parents[1]))  # usr.plugins package root


class ContentProjectionTests(unittest.TestCase):
    def setUp(self):
        from usr.plugins.chat_history.helpers import sync

        self.sync = sync

    def test_plain_string(self):
        self.assertEqual(self.sync._content_text("hello"), "hello")

    def test_user_message_dict(self):
        content = {"type": "message", "user_message": "do the thing"}
        self.assertEqual(self.sync._content_text(content), "do the thing")

    def test_tool_result_empty(self):
        content = {"type": "tool_result", "tool_name": "code_execution"}
        self.assertEqual(self.sync._content_text(content), "")
        self.assertEqual(self.sync._tool_name(content), "code_execution")

    def test_tool_name_from_native_calls(self):
        content = {
            "native_tool_calls": [
                {"function": {"name": "call_subordinate", "arguments": "{}"}}
            ]
        }
        self.assertEqual(self.sync._tool_name(content), "call_subordinate")

    def test_list_content_joined(self):
        self.assertEqual(self.sync._content_text(["a", "b"]), "a\nb")

    def test_framework_notification_is_a_background_tool(self):
        content = {
            "type": "framework_notification",
            "notification": "developer finished",
        }
        self.assertEqual(self.sync._content_text(content), "developer finished")
        self.assertEqual(self.sync._tool_name(content), "_background_notification")

    def test_walk_messages_finds_nested(self):
        from usr.plugins.chat_history.helpers.import_json import _walk_messages

        blob = {
            "_cls": "History",
            "topics": [
                {
                    "_cls": "Topic",
                    "messages": [
                        {"_cls": "Message", "ai": False, "content": "hi"},
                        {"_cls": "Message", "ai": True, "content": "hello"},
                    ],
                }
            ],
            "bulks": [],
        }
        out = []
        _walk_messages(blob, out)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["content"], "hi")


class SubagentMetadataTests(unittest.TestCase):
    def setUp(self):
        from usr.plugins.chat_history.helpers import sync

        self.sync = sync

    def test_upstream_output_data_marks_subordinate(self):
        context = SimpleNamespace(
            data={},
            output_data={
                "parent_context_id": "ctx-parent",
                "parent_context_kind": "subordinate",
            },
        )
        self.assertEqual(
            self.sync._context_subagent_fields(context),
            ("ctx-parent", True),
        )

    def test_root_context_is_not_subagent(self):
        context = SimpleNamespace(data={}, output_data={})
        self.assertEqual(self.sync._context_subagent_fields(context), ("", False))

    def test_persisted_output_data_projects_same_parent(self):
        from usr.plugins.chat_history.helpers.import_json import _context_metadata

        metadata = _context_metadata(
            {
                "id": "ctx-child",
                "name": "Research",
                "type": "user",
                "agent_profile": "researcher",
                "output_data": {
                    "parent_context_id": "ctx-main",
                    "parent_context_kind": "subordinate",
                },
            }
        )
        self.assertEqual(metadata["parent_context_id"], "ctx-main")
        self.assertTrue(metadata["is_subagent"])
        self.assertEqual(metadata["profile"], "researcher")


class PromptSchemaTests(unittest.TestCase):
    def test_tool_prompts_carry_schemas(self):
        from usr.plugins.llm_transport.helpers.responses_tools import _schema_from_prompt

        for name in ("chat_list", "chat_inspect", "chat_search"):
            path = (
                PLUGIN_ROOT
                / "prompts"
                / f"agent.system.tool.{name}.md"
            )
            schema = _schema_from_prompt(path.read_text(encoding="utf-8"))
            self.assertEqual(schema["type"], "object", name)
            self.assertIsInstance(schema.get("properties"), dict, name)
            self.assertTrue(len(schema["properties"]) >= 0, name)


if __name__ == "__main__":
    unittest.main()
