"""Static contracts for the Continuous Mode sidebar extension."""

from pathlib import Path


EXTENSION = (
    Path(__file__).resolve().parents[1]
    / "extensions/webui/initFw_end/main-pin-v3.js"
)


def test_legacy_module_path_is_absent_to_force_cache_invalidation():
    for name in ("main-pin.js", "main-pin-v2.js"):
        assert not EXTENSION.with_name(name).exists()


def _source() -> str:
    return EXTENSION.read_text(encoding="utf-8")


def test_sidebar_filters_non_primary_top_level_chats_but_keeps_children():
    source = _source()

    assert 'if (row.id === mainContextId) pinned.push(row);' in source
    assert 'else if (row.parent_context_id) rest.push(row);' in source
    assert 'return pinned.concat(rest);' in source


def test_new_chat_selects_primary_or_bootstraps_the_first_main_chat():
    source = _source()

    assert 'if (mainContextId) return selectMain();' in source
    assert 'const createdContextId = await stockNewChat(...args);' in source
    assert 'await refreshPinned(10);' in source
    assert 'return mainContextId || createdContextId;' in source
    assert 'await chatsStoreRef.selectChat(mainContextId);' in source


def test_empty_store_still_installs_the_create_guard_before_pin_resolution():
    source = _source()

    resolve_stores = source.index(
        'await Promise.all([resolveSidebarStore(), resolveChatsStore()]);'
    )
    install_guard = source.index('installSingleChatCreateGuard();', resolve_stores)
    refresh_pin = source.index('await refreshPinned();', install_guard)
    assert resolve_stores < install_guard < refresh_pin


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
