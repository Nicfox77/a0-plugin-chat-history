"""Primary-chat name lock + pin helpers (Continuous Mode).

Two related pieces:

- ``decide_rename`` — pure function: given the current name, target name,
  and lock flag, return the target when a rename should happen, else None.
  Unit-testable with no framework imports.
- ``assert_primary_name`` — live helper: resolve the primary chat and, if
  its name does not equal ``settings.main_chat_name()``, rename + persist.
  Idempotent and cheap; called from ``monologue_end`` and at boot.
"""
from __future__ import annotations

from typing import Optional


def decide_rename(
    current_name: str,
    target_name: str,
    lock_enabled: bool,
) -> Optional[str]:
    """Return ``target_name`` when the lock wants to rename, else None.

    Empty target is treated as no-op so a misconfigured setting cannot blank
    a chat. Comparison is exact (after strip) so the utility-model auto-rename
    still has free reign on chats that are NOT the primary.
    """
    if not lock_enabled:
        return None
    target = str(target_name or "").strip()
    if not target:
        return None
    if str(current_name or "").strip() == target:
        return None
    return target


def assert_primary_name(
    *,
    primary_getter=None,
    name_setter=None,
    continuous_mode=None,
    lock_enabled=None,
    target_name=None,
) -> Optional[str]:
    """Resolve the primary chat and rename it to ``target_name`` if needed.

    Pluggable: pass ``primary_getter`` to inject a fake ``enforce._resolve_primary``
    in tests; ``name_setter(context, new_name)`` performs the rename + persist
    (default uses ``persist_chat.save_tmp_chat`` + ``mark_dirty_all``). Returns
    the new name when a rename happened, else None.
    """
    from usr.plugins.chat_history.helpers import settings as _settings

    if continuous_mode is None:
        continuous_mode = _settings.continuous_mode()
    if lock_enabled is None:
        lock_enabled = _settings.lock_main_chat_name()
    if target_name is None:
        target_name = _settings.main_chat_name()

    if not continuous_mode or not lock_enabled:
        return None

    if primary_getter is None:
        from usr.plugins.chat_history.helpers.enforce import _resolve_primary

        primary_getter = _resolve_primary

    primary = primary_getter()
    if primary is None:
        return None

    current = str(getattr(primary, "name", "") or "")
    target = decide_rename(current, target_name, lock_enabled)
    if target is None:
        return None

    if name_setter is None:
        name_setter = _default_name_setter

    name_setter(primary, target)
    return target


def _default_name_setter(context, new_name: str) -> None:
    from helpers import persist_chat
    from helpers.state_monitor_integration import mark_dirty_all

    context.name = new_name
    try:
        if "parent_context_label" in context.output_data:
            context.output_data["parent_context_label"] = new_name
    except Exception:
        pass
    persist_chat.save_tmp_chat(context)
    mark_dirty_all(reason="plugins.chat_history.pin_main_name")