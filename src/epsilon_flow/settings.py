"""Validated, private settings for Epsilon Flow."""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from platformdirs import user_config_path, user_state_path


DELIVERY_MODES = {"copy", "paste", "type", "none"}
DEVICES = {"auto", "cpu", "cuda"}


@dataclass
class AppSettings:
    hotkey: str = "<Ctrl><Shift>F9"
    start_at_login: bool = True
    delivery_mode: str = "copy"
    history_enabled: bool = True
    history_limit: int = 30
    microphone: str = "default"
    model: str = "deepdml/faster-whisper-large-v3-turbo-ct2"
    device: str = "auto"
    compute_type: str = "default"
    language: str = "auto"
    initial_prompt: str = ""
    recognition_hints: str = ""
    service_url: str = "http://127.0.0.1:8791"

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "AppSettings":
        known = {field.name for field in fields(cls)}
        values = {key: value for key, value in payload.items() if key in known}
        settings = cls(**values)
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.delivery_mode not in DELIVERY_MODES:
            raise ValueError(f"invalid delivery mode: {self.delivery_mode}")
        if self.device not in DEVICES:
            raise ValueError(f"invalid device: {self.device}")
        if not 1 <= self.history_limit <= 1000:
            raise ValueError("history limit must be between 1 and 1000")
        if not self.service_url.startswith("http://127.0.0.1:") and not self.service_url.startswith("http://localhost:"):
            raise ValueError("service URL must use localhost")

    def prompt(self) -> str:
        initial_prompt = self.initial_prompt.strip()
        recognition_hints = self.recognition_hints.strip()
        parts = []
        if initial_prompt:
            parts.append(initial_prompt)
        if recognition_hints:
            parts.append(f"Recognition hints: {recognition_hints}")
        return "\n\n".join(parts)


class SettingsStore:
    def __init__(self, config_dir: Path | None = None) -> None:
        self.config_dir = Path(config_dir or user_config_path("epsilon-flow", appauthor=False))
        self.path = self.config_dir / "settings.json"

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("settings root is not an object")
            return AppSettings.from_mapping(payload)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            preserved = self.path.with_name(f"settings.corrupt-{int(time.time())}.json")
            try:
                self.path.replace(preserved)
            except OSError:
                pass
            return AppSettings()

    def save(self, settings: AppSettings) -> None:
        settings.validate()
        self.config_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.config_dir, 0o700)
        descriptor, temporary = tempfile.mkstemp(dir=self.config_dir, prefix="settings-", suffix=".json")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(asdict(settings), handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def state_dir() -> Path:
    override = os.environ.get("EPSILON_FLOW_STATE_DIR")
    return Path(override).expanduser() if override else Path(user_state_path("epsilon-flow", appauthor=False))
