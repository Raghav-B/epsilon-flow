"""Optional GNOME hotkey and XDG autostart integration."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from platformdirs import user_config_path


HOTKEY_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/epsilon-flow/"
SCHEMA = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"


def set_autostart(enabled: bool) -> Path:
    directory = Path(user_config_path("autostart", appauthor=False))
    path = directory / "epsilon-flow.desktop"
    if not enabled:
        path.unlink(missing_ok=True)
        return path
    executable = shutil.which("epsilon-flow-tray") or "epsilon-flow-tray"
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\nType=Application\nName=Epsilon Flow\n"
        f"Exec={executable}\nX-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )
    return path


def bind_gnome_hotkey(accelerator: str) -> None:
    if not shutil.which("gsettings"):
        raise RuntimeError("gsettings is required for GNOME hotkey integration")
    executable = shutil.which("epsilon-flow") or "epsilon-flow"
    base_schema = "org.gnome.settings-daemon.plugins.media-keys"
    completed = subprocess.run(["gsettings", "get", base_schema, "custom-keybindings"], text=True, capture_output=True, check=True)
    existing = completed.stdout.strip()
    paths = [] if existing == "@as []" else [part.strip(" '") for part in existing.strip("[]\n ").split(",") if part.strip()]
    if HOTKEY_PATH not in paths:
        paths.append(HOTKEY_PATH)
    serialized = "[" + ", ".join(repr(path) for path in paths) + "]"
    subprocess.run(["gsettings", "set", base_schema, "custom-keybindings", serialized], check=True)
    schema_path = f"{SCHEMA}:{HOTKEY_PATH}"
    subprocess.run(["gsettings", "set", schema_path, "name", "Epsilon Flow"], check=True)
    subprocess.run(["gsettings", "set", schema_path, "command", f"{executable} trigger"], check=True)
    subprocess.run(["gsettings", "set", schema_path, "binding", accelerator], check=True)
