"""Regression tests for web/API redirects into the primary chat."""

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, mock

from usr.plugins.chat_history.helpers.enforce import REDIRECT_FLAG
from usr.plugins.chat_history.extensions.python.user_message_ui import (
    _10_enforce_single_chat as redirect_extension,
)


class RedirectExtensionTests(IsolatedAsyncioTestCase):
    async def test_communicate_deferred_task_is_not_awaited(self):
        delivered = []
        deferred = object()
        primary = SimpleNamespace(
            communicate=lambda message: delivered.append(message) or deferred
        )
        context = SimpleNamespace(data={}, log=None)
        agent = SimpleNamespace(context=context)
        data = {"message": "keep this", "attachment_paths": ["one.txt"]}

        extension = redirect_extension.EnforceSingleChat(agent=agent)
        with mock.patch.object(
            redirect_extension, "should_redirect", return_value=primary
        ):
            await extension.execute(data=data)

        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0].message, "keep this")
        self.assertEqual(delivered[0].attachments, ["one.txt"])
        self.assertEqual(data["message"], "")
        self.assertTrue(context.data[REDIRECT_FLAG])

    async def test_failed_delivery_does_not_discard_original_message(self):
        def fail(_message):
            raise RuntimeError("delivery failed")

        primary = SimpleNamespace(communicate=fail)
        context = SimpleNamespace(data={}, log=None)
        agent = SimpleNamespace(context=context)
        data = {"message": "do not lose me", "attachment_paths": []}

        extension = redirect_extension.EnforceSingleChat(agent=agent)
        with mock.patch.object(
            redirect_extension, "should_redirect", return_value=primary
        ):
            await extension.execute(data=data)

        self.assertEqual(data["message"], "do not lose me")
        self.assertNotIn(REDIRECT_FLAG, context.data)

    async def test_missing_context_data_does_not_deliver_or_discard(self):
        delivered = []
        primary = SimpleNamespace(communicate=lambda message: delivered.append(message))
        agent = SimpleNamespace(context=SimpleNamespace(data=None, log=None))
        data = {"message": "keep local", "attachment_paths": []}

        extension = redirect_extension.EnforceSingleChat(agent=agent)
        with mock.patch.object(
            redirect_extension, "should_redirect", return_value=primary
        ):
            await extension.execute(data=data)

        self.assertEqual(delivered, [])
        self.assertEqual(data["message"], "keep local")
