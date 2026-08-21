"""Run a pg0 instance from Agent Zero's persistent ``usr`` directory.

Stock Agent Zero runs the UI as root and only persists ``usr``. PostgreSQL
refuses to run as root, while pg0 normally stores data below ``HOME``. This
adapter makes both behaviors explicit so plugins do not depend on a custom
container user or home-directory layout.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from helpers import files


@dataclass(frozen=True)
class Pg0Info:
    running: bool
    uri: str = ""
    data_dir: str = ""


class PersistentPg0:
    def __init__(
        self,
        name: str,
        *,
        config: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.usr_dir = Path(files.get_abs_path("usr")).resolve()
        self.pg0_dir = self.usr_dir / ".pg0"
        self.data_dir = self.pg0_dir / "instances" / name / "data"
        self.config = dict(config or {})

    def get_or_start(self) -> Pg0Info:
        self._prepare_storage()
        lock_path = self.pg0_dir / ".plugin-start.lock"
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            info = self._info()
            if info.running and info.uri:
                return info

            args = [
                "start",
                "--name",
                self.name,
                "--data-dir",
                str(self.data_dir),
            ]
            for key, value in self.config.items():
                args.extend(["-c", f"{key}={value}"])
            self._run(*args)
            info = self._info()
            if not info.running or not info.uri:
                raise RuntimeError(f"pg0 instance {self.name!r} did not start")
            return info

    def _info(self) -> Pg0Info:
        result = self._run("info", "--name", self.name, "-o", "json", check=False)
        try:
            payload: dict[str, Any] = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            payload = {}
        return Pg0Info(
            running=bool(payload.get("running")),
            uri=str(payload.get("uri") or ""),
            data_dir=str(payload.get("data_dir") or self.data_dir),
        )

    def _prepare_storage(self) -> None:
        self.pg0_dir.mkdir(parents=True, exist_ok=True)
        if os.geteuid() != 0:
            return

        owner = self.pg0_dir.stat()
        if owner.st_uid != 0:
            return

        # Stock Agent Zero restores archives as root. Move the complete pg0
        # tree to a stable unprivileged identity before PostgreSQL touches it.
        for root, dirs, names in os.walk(self.pg0_dir):
            for name in [*dirs, *names]:
                os.chown(Path(root) / name, 65534, 65534, follow_symlinks=False)
        os.chown(self.pg0_dir, 65534, 65534, follow_symlinks=False)

    def _runner_prefix(self) -> list[str]:
        if os.geteuid() != 0:
            return []
        owner = self.pg0_dir.stat()
        setpriv = shutil.which("setpriv")
        if not setpriv:
            raise RuntimeError(
                "Agent Zero is running as root but setpriv is unavailable; "
                "pg0 requires an unprivileged process"
            )
        return [
            setpriv,
            f"--reuid={owner.st_uid}",
            f"--regid={owner.st_gid}",
            "--clear-groups",
        ]

    @staticmethod
    def _binary() -> str:
        import pg0

        bundled = Path(pg0.__file__).resolve().parent / "bin" / (
            "pg0.exe" if os.name == "nt" else "pg0"
        )
        if bundled.is_file():
            return str(bundled)
        binary = shutil.which("pg0")
        if binary:
            return binary
        raise RuntimeError("pg0 binary is unavailable; reinstall pg0-embedded")

    def _run(
        self,
        *args: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(self.usr_dir)
        command = [*self._runner_prefix(), self._binary(), *args]
        result = subprocess.run(command, capture_output=True, text=True, env=env)
        if check and result.returncode:
            message = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(message or f"pg0 exited with status {result.returncode}")
        return result
