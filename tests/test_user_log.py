from types import SimpleNamespace

from helpers.log import Log
from usr.plugins.chat_history.helpers.user_log import repair_context_user_log


def _message(message_id, *, ai, content):
    return {"id": message_id, "ai": ai, "content": content}


def test_missing_user_logs_are_restored_in_history_order():
    messages = [
        _message("u1", ai=False, content="first"),
        _message("a1", ai=True, content="answer one"),
        _message("u2", ai=False, content="voice transcript"),
        _message("a2", ai=True, content="answer two"),
    ]
    log = Log()
    log.log(type="user", content="first", id="u1")
    log.log(type="response", content="answer one", id="a1")
    log.log(type="response", content="answer two", id="a2")
    context = SimpleNamespace(
        log=log,
        agent0=SimpleNamespace(history=SimpleNamespace(output=lambda: messages)),
    )

    assert repair_context_user_log(context) == 1
    assert [(item.type, item.id) for item in log.logs] == [
        ("user", "u1"),
        ("response", "a1"),
        ("user", "u2"),
        ("response", "a2"),
    ]
    assert repair_context_user_log(context) == 0


def test_tool_results_and_framework_records_are_not_user_logs():
    messages = [
        _message("tool", ai=False, content={"tool_name": "read_file", "tool_result": "x"}),
        _message(
            "notice",
            ai=False,
            content={"type": "framework_notification", "notification": "done"},
        ),
        _message("user", ai=False, content={"user_message": "hello", "attachments": ["/tmp/a.pdf"]}),
    ]
    log = Log()
    context = SimpleNamespace(
        log=log,
        agent0=SimpleNamespace(history=SimpleNamespace(output=lambda: messages)),
    )

    assert repair_context_user_log(context) == 1
    assert [(item.type, item.id, item.content) for item in log.logs] == [
        ("user", "user", "hello")
    ]
    assert dict(log.logs[0].kvps) == {"attachments": ["a.pdf"]}


def test_partial_context_without_agent0_is_skipped_not_raised():
    log = Log()
    log.log(type="user", content="hello", id="u1")

    class BrokenContext(SimpleNamespace):
        def get_agent(self):
            raise AttributeError("'AgentContext' object has no attribute 'agent0'")

    context = BrokenContext(log=log)  # no agent0 attribute

    assert repair_context_user_log(context) == 0
    assert [(item.type, item.id) for item in log.logs] == [("user", "u1")]
