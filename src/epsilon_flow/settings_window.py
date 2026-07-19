"""GTK3 settings window for all portable Epsilon Flow preferences."""
from __future__ import annotations

import subprocess
from dataclasses import asdict

from .integrations import bind_gnome_hotkey, set_autostart
from .settings import AppSettings, SettingsStore


def create_settings_window(store: SettingsStore):
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    current = store.load()
    window = Gtk.Window(title="Epsilon Flow Settings")
    window.set_default_size(620, 680)
    window.set_border_width(16)
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    window.add(root)

    grid = Gtk.Grid(column_spacing=14, row_spacing=10)
    root.pack_start(grid, True, True, 0)
    widgets: dict[str, object] = {}

    text_rows = [
        ("hotkey", "Custom hotkey"),
        ("microphone", "Microphone"),
        ("model", "Model name or path"),
        ("compute_type", "Compute type"),
        ("language", "Language"),
        ("service_url", "Local service URL"),
    ]
    row = 0
    for name, label_text in text_rows:
        label = Gtk.Label(label=label_text, xalign=0)
        entry = Gtk.Entry()
        entry.set_text(str(getattr(current, name)))
        grid.attach(label, 0, row, 1, 1)
        grid.attach(entry, 1, row, 1, 1)
        widgets[name] = entry
        row += 1

    for name, label_text, choices in (
        ("delivery_mode", "Delivery mode", ("copy", "paste", "type", "none")),
        ("device", "Device", ("auto", "cpu", "cuda")),
    ):
        combo = Gtk.ComboBoxText()
        for choice in choices:
            combo.append_text(choice)
        combo.set_active(choices.index(getattr(current, name)))
        grid.attach(Gtk.Label(label=label_text, xalign=0), 0, row, 1, 1)
        grid.attach(combo, 1, row, 1, 1)
        widgets[name] = combo
        row += 1

    for name, label_text in (("start_at_login", "Start at login"), ("history_enabled", "Save transcript history")):
        switch = Gtk.Switch()
        switch.set_active(getattr(current, name))
        grid.attach(Gtk.Label(label=label_text, xalign=0), 0, row, 1, 1)
        grid.attach(switch, 1, row, 1, 1)
        widgets[name] = switch
        row += 1

    history_limit = Gtk.SpinButton.new_with_range(1, 1000, 1)
    history_limit.set_value(current.history_limit)
    grid.attach(Gtk.Label(label="History limit", xalign=0), 0, row, 1, 1)
    grid.attach(history_limit, 1, row, 1, 1)
    widgets["history_limit"] = history_limit
    row += 1

    for name, label_text in (("initial_prompt", "Initial Prompt"), ("recognition_hints", "Recognition Hints")):
        view = Gtk.TextView()
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.get_buffer().set_text(getattr(current, name))
        scroller = Gtk.ScrolledWindow()
        scroller.set_min_content_height(90)
        scroller.add(view)
        grid.attach(Gtk.Label(label=label_text, xalign=0, yalign=0), 0, row, 1, 1)
        grid.attach(scroller, 1, row, 1, 1)
        widgets[name] = view
        row += 1

    actions = Gtk.ButtonBox(orientation=Gtk.Orientation.HORIZONTAL)
    actions.set_layout(Gtk.ButtonBoxStyle.END)
    cancel = Gtk.Button(label="Cancel")
    save = Gtk.Button(label="Save")
    actions.add(cancel)
    actions.add(save)
    root.pack_end(actions, False, False, 0)

    cancel.connect("clicked", lambda _button: window.destroy())

    def save_settings(_button) -> None:
        values = asdict(current)
        for name, widget in widgets.items():
            if isinstance(widget, Gtk.Entry):
                values[name] = widget.get_text().strip()
            elif isinstance(widget, Gtk.ComboBoxText):
                values[name] = widget.get_active_text()
            elif isinstance(widget, Gtk.Switch):
                values[name] = widget.get_active()
            elif isinstance(widget, Gtk.SpinButton):
                values[name] = widget.get_value_as_int()
            elif isinstance(widget, Gtk.TextView):
                buffer = widget.get_buffer()
                values[name] = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True).strip()
        settings = AppSettings.from_mapping(values)
        store.save(settings)
        set_autostart(settings.start_at_login)
        try:
            bind_gnome_hotkey(settings.hotkey)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            dialog = Gtk.MessageDialog(
                transient_for=window,
                modal=True,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text="Settings saved, but the GNOME hotkey could not be applied.",
            )
            dialog.format_secondary_text(str(exc))
            dialog.run()
            dialog.destroy()
        window.destroy()

    save.connect("clicked", save_settings)
    return window
