"""Validated, private settings for Epsilon Flow."""
from __future__ import annotations

import getpass
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from platformdirs import user_config_path, user_state_path

from .vm_backend import BackendName, VmBackendConfig


DELIVERY_MODES = {"copy", "paste", "type", "none"}
DEVICES = {"auto", "cpu", "cuda"}
COMPUTE_BACKENDS = {"local", "vm"}
FIXED_MODEL = "turbo"
LOCAL_SERVICE_URL = "http://127.0.0.1:8794"
LEGACY_ROUTER_URL = "http://127.0.0.1:8791"


@dataclass
class AppSettings:
    hotkey: str = "<Ctrl><Shift>F9"
    start_at_login: bool = True
    delivery_mode: str = "copy"
    history_enabled: bool = True
    history_limit: int = 30
    microphone: str = "default"
    model: str = FIXED_MODEL
    device: str = "auto"
    compute_type: str = "default"
    language: str = "auto"
    initial_prompt: str = ""
    recognition_hints: str = ""
    service_url: str = LOCAL_SERVICE_URL
    compute_backend: BackendName = "local"

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "AppSettings":
        known = {field.name for field in fields(cls)}
        values = {key: value for key, value in payload.items() if key in known}
        # Model choice is intentionally hidden in this release. Migrate stale
        # IDs from older settings files onto Faster-Whisper's supported alias.
        values["model"] = FIXED_MODEL
        # Flow now owns a direct VM tunnel. Do not use the old global router as
        # its local fallback because that router can be configured back to VM.
        if values.get("service_url") == LEGACY_ROUTER_URL:
            values["service_url"] = LOCAL_SERVICE_URL
        settings = cls(**values)
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.delivery_mode not in DELIVERY_MODES:
            raise ValueError(f"invalid delivery mode: {self.delivery_mode}")
        if self.device not in DEVICES:
            raise ValueError(f"invalid device: {self.device}")
        if self.compute_backend not in COMPUTE_BACKENDS:
            raise ValueError(f"invalid compute backend: {self.compute_backend}")
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


def _integer_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def vm_backend_config(config_dir: Path | None = None, state_directory: Path | None = None) -> VmBackendConfig:
    """Build the private VM route used only when the VM backend is requested.

    Host mode remains the default. These machine defaults match the current
    generic GPU VM: QEMU exposes guest SSH on host ``127.0.0.1:2222`` and the
    Flow guest backend is reached through a host tunnel on ``127.0.0.1:8891``.
    Environment overrides keep the public package usable on other machines
    without changing ordinary local dictation behavior.
    """
    root = Path(config_dir or user_config_path("epsilon-flow", appauthor=False))
    runtime_state_dir = Path(state_directory or state_dir())
    return VmBackendConfig(
        ssh_host=os.environ.get("EPSILON_FLOW_VM_SSH_HOST", "127.0.0.1"),
        ssh_user=os.environ.get("EPSILON_FLOW_VM_SSH_USER", getpass.getuser()),
        ssh_port=_integer_env("EPSILON_FLOW_VM_SSH_PORT", 2222),
        known_hosts_path=Path(os.environ.get("EPSILON_FLOW_VM_KNOWN_HOSTS", root / "vm_known_hosts")).expanduser(),
        state_path=runtime_state_dir / "vm-tunnel.json",
        local_host="127.0.0.1",
        local_port=_integer_env("EPSILON_FLOW_VM_TUNNEL_PORT", 8891),
        guest_host="127.0.0.1",
        guest_port=_integer_env("EPSILON_FLOW_VM_GUEST_PORT", 8791),
    )
