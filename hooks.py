"""Plugin Hub lifecycle hooks for embedded Postgres dependencies."""

from __future__ import annotations

import importlib
import importlib.util
import shutil
import subprocess
import sys
import threading
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_REQUIREMENTS = _ROOT / "requirements.txt"
_DEPENDENCY_MODULES = ("pg0", "psycopg", "psycopg_pool")
_LOCK = threading.Lock()
_CHECKED = False


def _dependencies_available() -> bool:
    return all(importlib.util.find_spec(name) is not None for name in _DEPENDENCY_MODULES)


def _install_requirements() -> None:
    uv = shutil.which("uv")
    command = (
        [uv, "pip", "install", "--python", sys.executable, "-r", str(_REQUIREMENTS)]
        if uv
        else [sys.executable, "-m", "pip", "install", "-r", str(_REQUIREMENTS)]
    )
    subprocess.check_call(command, cwd=str(_ROOT))


def ensure_dependencies(raise_on_error: bool = True, force: bool = False) -> dict:
    """Restore framework dependencies after stock container recreation."""

    global _CHECKED
    if not force and _CHECKED and _dependencies_available():
        return {"ok": True, "installed": False}
    with _LOCK:
        if not force and _dependencies_available():
            _CHECKED = True
            return {"ok": True, "installed": False}
        try:
            _install_requirements()
            importlib.invalidate_caches()
            if not _dependencies_available():
                raise RuntimeError("required modules remain unavailable after installation")
            _CHECKED = True
            return {"ok": True, "installed": True, "requirements": str(_REQUIREMENTS)}
        except Exception as exc:
            if raise_on_error:
                raise RuntimeError(f"chat_history dependency installation failed: {exc}") from exc
            return {"ok": False, "error": str(exc)}


def install() -> dict:
    return ensure_dependencies(raise_on_error=True, force=True)


def pre_update() -> dict:
    return ensure_dependencies(raise_on_error=True, force=True)


def uninstall() -> dict:
    return {
        "ok": True,
        "message": (
            "Shared Python dependencies and the pg0 instance 'chat_history' were retained. "
            "Remove the database manually only after exporting any history you need."
        ),
    }
