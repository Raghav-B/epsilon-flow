"""Validated, private settings for Epsilon Flow."""
from __future__ import annotations

import ipaddress
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from platformdirs import user_config_path, user_state_path


DELIVERY_MODES = {"copy", "paste", "type", "none"}
DEVICES = {"auto", "cpu", "cuda"}
FIXED_MODEL = "turbo"
LOCAL_SERVICE_URL = "http://127.0.0.1:8791"
LEGACY_LOCAL_FALLBACK_URL = "http://127.0.0.1:8794"
LEGACY_VM_TUNNEL_URL = "http://127.0.0.1:8891"
PRIVATE_SERVICE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)


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

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "AppSettings":
        known = {field.name for field in fields(cls)}
        values = {key: value for key, value in payload.items() if key in known}
        # Model choice is intentionally hidden in this release. Migrate stale
        # IDs from older settings files onto Faster-Whisper's supported alias.
        values["model"] = FIXED_MODEL
        # Older private builds selected the VM separately and kept the host
        # fallback URL in service_url. Preserve that user's working route by
        # migrating the selection to the tunnel endpoint they already used.
        if payload.get("compute_backend") == "vm":
            values["service_url"] = LEGACY_VM_TUNNEL_URL
        elif values.get("service_url") == LEGACY_LOCAL_FALLBACK_URL:
            values["service_url"] = LOCAL_SERVICE_URL
        if isinstance(values.get("service_url"), str):
            values["service_url"] = values["service_url"].strip().rstrip("/")
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
        validate_service_url(self.service_url)

    def prompt(self) -> str:
        initial_prompt = self.initial_prompt.strip()
        recognition_hints = self.recognition_hints.strip()
        parts = []
        if initial_prompt:
            parts.append(initial_prompt)
        if recognition_hints:
            parts.append(recognition_hints)
        return "\n\n".join(parts)


def validate_service_url(service_url: str) -> None:
    """Allow only local, private-LAN, or locally tunnelled HTTP services."""
    try:
        parsed = urlsplit(service_url)
    except ValueError as exc:
        raise ValueError("service URL must be a valid URL") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("service URL must be a complete http:// or https:// URL")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("service URL port must be a number between 1 and 65535") from exc
    if parsed.username or parsed.password:
        raise ValueError("service URL authentication is not supported yet")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("service URL must not include a path, query, or fragment")

    host = parsed.hostname.lower()
    if host == "localhost":
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("service URL host must be localhost or a private IP address") from exc
    if address.is_loopback or any(address in network for network in PRIVATE_SERVICE_NETWORKS):
        return
    raise ValueError("service URL host must be localhost or a private IP address")


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
