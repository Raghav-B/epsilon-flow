"""GTK3 tray, reusable listener, and transcript snippets surface."""
from __future__ import annotations

import shutil
import signal
import socket
import threading

from .controller import DictationController
from .dictation import run_dictation
from .history import TranscriptHistory
from .listener import DictationListener
from .settings import SettingsStore, state_dir
from .settings_window import create_settings_window


def main() -> int:
    import gi
    gi.require_version("AyatanaAppIndicator3", "0.1")
    gi.require_version("Gtk", "3.0")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator, GLib, Gtk

    store = SettingsStore()
    active: dict[str, DictationController | None] = {"controller": None}
    settings_surface: dict[str, object | None] = {"window": None}

    def stop() -> None:
        controller = active["controller"]
        if controller:
            controller.stop_requested = True

    def cancel() -> None:
        controller = active["controller"]
        if controller:
            controller.cancel_requested = True

    listener = DictationListener(lambda: start(), stop, cancel, TranscriptHistory(limit=store.load().history_limit))

    def start(_item=None) -> None:
        if active["controller"] is not None:
            listener.present()
            return
        controller = DictationController()
        if not controller.acquire():
            controller.signal_active_recording(signal.SIGUSR1)
            return
        controller.install_signal_handlers()
        active["controller"] = controller
        listener.present_for_start()

        def finish() -> bool:
            controller.restore_signal_handlers()
            active["controller"] = None
            return False

        def worker() -> None:
            try:
                result = run_dictation(store.load(), controller)
                GLib.idle_add(listener.hide)
                if result.get("cancelled"):
                    notify("Epsilon Flow", "Recording discarded")
                elif result.get("text"):
                    notify("Epsilon Flow", "Transcript ready")
                else:
                    notify("Epsilon Flow", "No speech detected")
            except Exception as exc:
                notify("Epsilon Flow failed", str(exc))
                GLib.idle_add(listener.hide)
            finally:
                controller.release()
                GLib.idle_add(finish)

        threading.Thread(target=worker, daemon=True).start()
        GLib.timeout_add(100, lambda: _sync_recording(listener, controller))

    def trigger() -> None:
        controller = active["controller"]
        if controller is None:
            start()
            return
        if controller.phase != "recording" or controller.stop_requested or controller.cancel_requested:
            listener.present()
            return
        controller.stop_requested = True
        listener.set_finishing("Finishing recording…")
        listener.hide()

    def open_settings(_item=None) -> None:
        window = settings_surface["window"]
        if window is None:
            window = create_settings_window(store)
            settings_surface["window"] = window
            window.connect("destroy", lambda _window: settings_surface.update(window=None))
        _show_settings(window)

    def notify(title: str, body: str) -> None:
        executable = shutil.which("notify-send")
        if not executable:
            return
        from subprocess import DEVNULL, run
        run([executable, title, body], stdout=DEVNULL, stderr=DEVNULL, check=False)

    socket_path = state_dir() / "tray.sock"
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    command_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    command_socket.bind(str(socket_path))
    command_socket.listen(2)

    def listen_for_commands() -> None:
        while True:
            try:
                connection, _address = command_socket.accept()
            except OSError:
                return
            with connection:
                command = connection.recv(32).decode("utf-8", errors="replace").strip()
            if command == "trigger":
                GLib.idle_add(trigger)

    threading.Thread(target=listen_for_commands, daemon=True).start()

    indicator = AppIndicator.Indicator.new(
        "epsilon-flow", "audio-input-microphone-symbolic", AppIndicator.IndicatorCategory.APPLICATION_STATUS
    )
    indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
    menu = Gtk.Menu()
    for label, callback in (
        ("Start Dictation", start),
        ("Open Transcript Snippets", lambda _item: listener.present_snippets()),
        ("Settings…", open_settings),
        ("Quit", lambda _item: Gtk.main_quit()),
    ):
        item = Gtk.MenuItem(label=label)
        item.connect("activate", callback)
        menu.append(item)
    menu.show_all()
    indicator.set_menu(menu)
    try:
        Gtk.main()
    finally:
        command_socket.close()
        socket_path.unlink(missing_ok=True)
    return 0


def _sync_recording(listener, controller: DictationController) -> bool:
    if controller.handle is None:
        return False
    listener.set_recording(True, "Recording")
    return True


def _show_settings(window) -> None:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    # AppIndicator activation does not always grant a new GTK window focus on
    # GNOME. Keep this short-lived settings surface above normal app windows so
    # it cannot silently open behind the workspace.
    window.set_keep_above(True)
    window.set_focus_on_map(True)
    window.show_all()
    window.realize()
    native_window = window.get_window()
    if native_window is not None:
        native_window.set_keep_above(True)
        native_window.raise_()
        native_window.focus(Gtk.get_current_event_time())
    window.present_with_time(Gtk.get_current_event_time())


if __name__ == "__main__":
    raise SystemExit(main())
