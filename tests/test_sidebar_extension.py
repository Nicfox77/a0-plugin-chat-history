"""Static contracts for the Continuous Mode sidebar extension."""

from pathlib import Path


EXTENSION = (
    Path(__file__).resolve().parents[1]
    / "extensions/webui/initFw_end/main-pin-v2.js"
)


def test_legacy_module_path_is_absent_to_force_cache_invalidation():
    legacy = EXTENSION.with_name("main-pin.js")

    assert not legacy.exists()


def _source() -> str:
    return EXTENSION.read_text(encoding="utf-8")


def test_sidebar_filters_non_primary_top_level_chats_but_keeps_children():
    source = _source()

    assert 'if (row.id === mainContextId) pinned.push(row);' in source
    assert 'else if (row.parent_context_id) rest.push(row);' in source
    assert 'return pinned.concat(rest);' in source


def test_new_chat_is_guarded_by_selecting_the_primary():
    source = _source()

    assert 'chatsStoreRef.newChat = async () => selectMain();' in source
    assert 'await chatsStoreRef.selectChat(mainContextId);' in source


def test_hidden_legacy_selection_is_repaired():
    source = _source()

    assert 'if (!selected) return;' in source
    assert 'if (isVisibleContext(selected)) return;' in source
    assert 'Promise.resolve(selectMain()).finally(() =>' in source


def test_dashboard_empty_selection_is_not_redirected_to_main():
    source = _source()

    empty_guard = source.index('if (!selected) return;')
    select_main = source.index('Promise.resolve(selectMain()).finally(() =>')
    assert empty_guard < select_main
