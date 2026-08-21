"""Plugin settings: env var > config.json (web UI) > default_config.yaml."""

from __future__ import annotations

import os


def _plugin_config() -> dict:
    try:
        from helpers import plugins

        cfg = plugins.get_plugin_config("chat_history")
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def setting(key: str, env: str, default: str) -> str:
    value = os.environ.get(env)
    if value is not None and value.strip():
        return value.strip()
    cfg = _plugin_config().get(key)
    if cfg is not None and str(cfg).strip():
        return str(cfg).strip()
    return default


def bool_setting(key: str, env: str, default: bool) -> bool:
    return setting(key, env, "1" if default else "0").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "",
    }


def float_setting(key: str, env: str, default: float) -> float:
    try:
        return float(setting(key, env, str(default)))
    except ValueError:
        return default


def int_setting(key: str, env: str, default: int) -> int:
    try:
        return int(setting(key, env, str(default)))
    except ValueError:
        return default


def replica_enabled() -> bool:
    return bool_setting("enabled", "CH_ENABLED", True)


def embed_enabled() -> bool:
    return bool_setting("embed_enabled", "CH_EMBED_ENABLED", True)


def storage_mode() -> str:
    mode = setting("storage_mode", "CH_STORAGE_MODE", "replica").strip().lower()
    return mode if mode in {"replica", "authoritative"} else "replica"


def continuous_mode() -> bool:
    return bool_setting("continuous_mode", "CH_CONTINUOUS_MODE", False)


def deck_injection_enabled() -> bool:
    return bool_setting("deck_injection", "CH_DECK_INJECTION", True)


def main_chat_name() -> str:
    """Canonical name for the primary chat (Continuous Mode only).

    Defaults to ``"Main"``. Whitespace is stripped; empty values fall back to
    the default so the name-lock sweep never produces a blank rename.
    """
    raw = setting("main_chat_name", "CH_MAIN_CHAT_NAME", "Main").strip()
    return raw or "Main"


def lock_main_chat_name() -> bool:
    """When True (default), the primary chat's name is force-set to
    ``main_chat_name`` on every monologue end so the utility-model auto-rename
    cannot rename it away. Gated on ``continuous_mode()`` at call sites — this
    function only reports the raw setting.
    """
    return bool_setting("lock_main_chat_name", "CH_LOCK_MAIN_CHAT_NAME", True)


def pin_main_chat() -> bool:
    """When True (default), the primary chat is sorted to the top of
    ``chat_list`` and exposed via ``/api/plugins/chat_history/pinned``. Raw
    setting only — call sites gate on ``continuous_mode()`` too.
    """
    return bool_setting("pin_main_chat", "CH_PIN_MAIN_CHAT", True)
