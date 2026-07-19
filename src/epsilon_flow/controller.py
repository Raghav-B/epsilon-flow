"""Pure state and locking rules for one active dictation run."""
from __future__ import annotations

import fcntl
import json
import os
import signal
import time
from pathlib import Path
from typing import TextIO

from .settings import state_dir


ACTIVE_PHASES = {"starting", "recording", "transcribing", "delivering"}


class DictationController:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory or state_dir())
        self.lock_path = self.directory / "controller.lock"
        self.status_path = self.directory / "current.json"
        self.handle: TextIO | None = None
        self.stop_requested = False
        self.cancel_requested = False

    def acquire(self) -> bool:
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.handle = self.lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.close()
            self.handle = None
            return False
        self.set_phase("starting")
        return True

    def set_phase(self, phase: str, **metadata: object) -> None:
        if phase not in ACTIVE_PHASES:
            raise ValueError(f"invalid controller phase: {phase}")
        if self.handle is None:
            return
        payload = {"pid": os.getpid(), "phase": phase, "updated_at": time.time(), **metadata}
        temporary = self.status_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.status_path)

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGUSR1, lambda _number, _frame: setattr(self, "stop_requested", True))
        signal.signal(signal.SIGUSR2, lambda _number, _frame: setattr(self, "cancel_requested", True))

    def signal_active_recording(self, signal_number: int = signal.SIGUSR1) -> bool:
        try:
            status = json.loads(self.status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if status.get("phase") != "recording" or not isinstance(status.get("pid"), int):
            return False
        try:
            os.kill(status["pid"], signal_number)
        except (ProcessLookupError, PermissionError):
            return False
        return True

    def release(self) -> None:
        self.status_path.unlink(missing_ok=True)
        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None
