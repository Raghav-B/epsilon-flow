"""Command-line control surface for Epsilon Flow."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import shutil
import socket
import subprocess
import sys

import requests

from .controller import DictationController
from .integrations import bind_gnome_hotkey, set_autostart
from .settings import SettingsStore, state_dir


def trigger() -> int:
    socket_path = state_dir() / "tray.sock"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(socket_path))
            client.sendall(b"trigger\n")
        return 0
    except OSError:
        controller = DictationController()
        if controller.signal_active_recording():
            return 0
        print("epsilon-flow: tray is not running; start epsilon-flow-tray", file=sys.stderr)
        return 1


def set_service_url(service_url: str, store: SettingsStore | None = None) -> int:
    """Update the transcription endpoint through Flow's validated settings boundary."""
    settings_store = store or SettingsStore()
    current = settings_store.load()
    updated = replace(current, service_url=service_url.strip().rstrip("/"))
    settings_store.save(updated)
    print(updated.service_url)
    return 0


def doctor() -> int:
    settings = SettingsStore().load()
    checks = {
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "clipboard": any(shutil.which(name) for name in ("wl-copy", "xclip", "xsel")),
        "virtual_keyboard": any(shutil.which(name) for name in ("ydotool", "wtype", "xdotool")),
        "service": False,
    }
    try:
        checks["service"] = requests.get(f"{settings.service_url.rstrip('/')}/health", timeout=2).ok
    except requests.RequestException:
        pass
    for name, healthy in checks.items():
        print(f"{'OK' if healthy else 'MISSING'}  {name}")
    return 0 if all(checks[name] for name in ("ffmpeg", "clipboard", "service")) else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="epsilon-flow")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("trigger", help="start or stop dictation through the tray")
    subparsers.add_parser("tray", help="run the GTK tray")
    subparsers.add_parser("settings", help="open GTK settings")
    subparsers.add_parser("doctor", help="check desktop and service dependencies")
    subparsers.add_parser("show-settings", help="print settings JSON")
    service_url_parser = subparsers.add_parser(
        "set-service-url",
        help="set the local or private transcription service endpoint",
    )
    service_url_parser.add_argument("service_url")
    apply_parser = subparsers.add_parser("apply-integrations", help="apply saved hotkey and autostart settings")
    apply_parser.add_argument("--no-hotkey", action="store_true")
    args = parser.parse_args()

    if args.command == "trigger":
        return trigger()
    if args.command == "tray":
        from .tray import main as tray_main
        return tray_main()
    if args.command == "settings":
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        from .settings_window import create_settings_window
        window = create_settings_window(SettingsStore())
        window.connect("destroy", lambda _window: Gtk.main_quit())
        window.show_all()
        Gtk.main()
        return 0
    if args.command == "doctor":
        return doctor()
    if args.command == "show-settings":
        from dataclasses import asdict
        print(json.dumps(asdict(SettingsStore().load()), indent=2))
        return 0
    if args.command == "set-service-url":
        return set_service_url(args.service_url)
    if args.command == "apply-integrations":
        settings = SettingsStore().load()
        set_autostart(settings.start_at_login)
        if not args.no_hotkey:
            bind_gnome_hotkey(settings.hotkey)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
