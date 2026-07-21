"""Pure state and locking rules for one active dictation run."""
from __future__ import annotations

import fcntl
import json
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Callable, Literal, TextIO

from .settings import state_dir


ACTIVE_PHASES = {"starting", "recording", "selecting_backend", "transcribing", "delivering"}


@dataclass(frozen=True)
class DictationViewState:
    mode: Literal["idle", "recording", "busy"]
    title: str


class DictationController:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory or state_dir())
        self.lock_path = self.directory / "controller.lock"
        self.status_path = self.directory / "current.json"
        self.handle: TextIO | None = None
        self.phase: str | None = None
        self.stop_requested = False
        self.cancel_requested = False
        self.previous_signal_handlers: dict[int, signal.Handlers] = {}

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
        self.phase = phase
        payload = {"pid": os.getpid(), "phase": phase, "updated_at": time.time(), **metadata}
        temporary = self.status_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.status_path)

    def install_signal_handlers(self) -> None:
        if self.previous_signal_handlers:
            return

        def request_stop(_number: int, _frame: FrameType | None) -> None:
            self.stop_requested = True

        def request_cancel(_number: int, _frame: FrameType | None) -> None:
            self.cancel_requested = True

        handlers: dict[int, Callable[[int, FrameType | None], None]] = {
            signal.SIGUSR1: request_stop,
            signal.SIGUSR2: request_cancel,
        }
        for signal_number, handler in handlers.items():
            self.previous_signal_handlers[signal_number] = signal.getsignal(signal_number)
            signal.signal(signal_number, handler)

    def restore_signal_handlers(self) -> None:
        for signal_number, previous_handler in self.previous_signal_handlers.items():
            signal.signal(signal_number, previous_handler)
        self.previous_signal_handlers.clear()

    def view_state(self) -> DictationViewState:
        """Map controller phase into the one listener state users should see."""
        if self.handle is None or self.phase is None:
            return DictationViewState("idle", "Press to start")
        if self.phase in {"starting", "recording"}:
            if self.cancel_requested:
                return DictationViewState("busy", "Discarding recording…")
            if self.stop_requested:
                return DictationViewState("busy", "Finishing recording…")
            title = "Starting microphone…" if self.phase == "starting" else "Recording"
            return DictationViewState("recording", title)
        if self.phase == "selecting_backend":
            return DictationViewState("busy", "Selecting backend…")
        if self.phase == "transcribing":
            return DictationViewState("busy", "Transcribing…")
        return DictationViewState("busy", "Delivering transcript…")

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
        self.phase = None
