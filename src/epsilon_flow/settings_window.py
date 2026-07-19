"""GTK3 settings window for portable Epsilon Flow preferences."""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from dataclasses import asdict

import requests

from .integrations import bind_gnome_hotkey, set_autostart
from .settings import AppSettings, SettingsStore


COMPUTE_TYPES = (
    ("default", "Automatic (recommended)"),
    ("int8", "INT8 · lowest memory"),
    ("float16", "Float16 · CUDA"),
    ("int8_float16", "INT8 + Float16 · CUDA"),
)
LANGUAGES = (
    ("auto", "Auto-detect"),
    ("en", "English"),
    ("zh", "Chinese"),
    ("ms", "Malay"),
    ("ta", "Tamil"),
    ("hi", "Hindi"),
    ("id", "Indonesian"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("fr", "French"),
    ("de", "German"),
    ("es", "Spanish"),
    ("pt", "Portuguese"),
    ("it", "Italian"),
    ("nl", "Dutch"),
    ("ru", "Russian"),
    ("ar", "Arabic"),
    ("th", "Thai"),
    ("vi", "Vietnamese"),
    ("tr", "Turkish"),
)
DELIVERY_MODES = (
    ("copy", "Copy to clipboard"),
    ("paste", "Paste into active app"),
    ("type", "Type into active app"),
    ("none", "Do not insert automatically"),
)
DEVICES = (
    ("auto", "Automatic · CUDA then CPU fallback"),
    ("cpu", "CPU"),
    ("cuda", "CUDA · NVIDIA GPU"),
)
MODIFIER_KEYS = {
    "Alt_L", "Alt_R", "Control_L", "Control_R", "Hyper_L", "Hyper_R",
    "Meta_L", "Meta_R", "Shift_L", "Shift_R", "Super_L", "Super_R",
}


def _microphone_choices() -> list[tuple[str, str]]:
    choices = [("default", "System default")]
    pactl = shutil.which("pactl")
    if not pactl:
        return choices

    try:
        completed = subprocess.run(
            [pactl, "--format=json", "list", "sources"],
            text=True,
            capture_output=True,
            check=True,
            timeout=3,
        )
        sources = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return choices

    seen = {"default"}
    for source in sources:
        name = str(source.get("name", "")).strip()
        if not name or name in seen or name.endswith(".monitor"):
            continue
        properties = source.get("properties") or {}
        description = str(properties.get("device.description") or source.get("description") or name).strip()
        choices.append((name, description))
        seen.add(name)
    return choices


def _combo_box(Gtk, choices: tuple[tuple[str, str], ...] | list[tuple[str, str]], current: str):
    combo = Gtk.ComboBoxText()
    known = set()
    for value, label in choices:
        combo.append(value, label)
        known.add(value)
    if current not in known:
        combo.append(current, f"Current: {current}")
    combo.set_active_id(current)
    combo.set_hexpand(True)
    combo.set_halign(Gtk.Align.FILL)
    return combo


def _field_label(Gtk, title: str, description: str):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    box.set_valign(Gtk.Align.START)
    title_label = Gtk.Label(label=title, xalign=0)
    title_label.get_style_context().add_class("heading")
    description_label = Gtk.Label(label=description, xalign=0, yalign=0)
    description_label.set_line_wrap(True)
    description_label.set_max_width_chars(38)
    description_label.get_style_context().add_class("dim-label")
    box.pack_start(title_label, False, False, 0)
    box.pack_start(description_label, False, False, 0)
    return box


def _model_status_text(service_url: str, configured_model: str) -> str:
    friendly = "Whisper large-v3-turbo"
    try:
        response = requests.get(f"{service_url.rstrip('/')}/models/status", timeout=2)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return f"{friendly} · backend offline"

    config = payload.get("model")
    if not payload.get("model_loaded") or not isinstance(config, dict):
        return f"{friendly} · loads on first dictation"
    device = str(config.get("device", "unknown")).upper()
    compute_type = str(config.get("compute_type", "default"))
    model = str(config.get("model") or configured_model)
    model_name = friendly if model in {"turbo", "large-v3-turbo"} or "large-v3-turbo" in model else model
    return f"{model_name} · {device} / {compute_type}"


def create_settings_window(store: SettingsStore):
    import gi

    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gdk, GLib, Gtk, Pango

    current = store.load()
    window = Gtk.Window(title="Epsilon Flow Settings")
    window.set_name("settings_window")
    window.set_default_size(820, 720)
    window.set_resizable(True)

    # The recording listener uses a transparent top-level window. Keep this
    # ordinary settings surface opaque when both share the tray process.
    provider = Gtk.CssProvider()
    provider.load_from_data(
        b"window#settings_window { background-color: @theme_bg_color; }"
        b"#model_status { font-weight: 600; }"
    )
    window.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    window.add(root)

    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    root.pack_start(scroller, True, True, 0)

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    content.set_margin_top(16)
    content.set_margin_bottom(28)
    content.set_margin_start(18)
    content.set_margin_end(18)
    scroller.add(content)

    grid = Gtk.Grid(column_spacing=28, row_spacing=11)
    grid.set_hexpand(True)
    grid.set_halign(Gtk.Align.FILL)
    content.pack_start(grid, True, True, 0)
    widgets: dict[str, object] = {}
    row = 0

    def attach(title: str, description: str, control) -> None:
        nonlocal row
        control.set_hexpand(True)
        if not isinstance(control, Gtk.Switch):
            control.set_halign(Gtk.Align.FILL)
        if isinstance(control, Gtk.ScrolledWindow):
            control.set_valign(Gtk.Align.FILL)
        else:
            control.set_valign(Gtk.Align.CENTER)
        grid.attach(_field_label(Gtk, title, description), 0, row, 1, 1)
        grid.attach(control, 1, row, 1, 1)
        row += 1

    hotkey = Gtk.Button()
    hotkey.set_can_focus(True)
    hotkey.set_image(Gtk.Image.new_from_icon_name("input-keyboard-symbolic", Gtk.IconSize.BUTTON))
    hotkey.set_always_show_image(True)
    hotkey.set_tooltip_text("Click here, then press the shortcut you want to use")
    hotkey.epsilon_accelerator = current.hotkey
    parsed_key, parsed_modifiers = Gtk.accelerator_parse(current.hotkey)
    hotkey.epsilon_display = Gtk.accelerator_get_label(parsed_key, parsed_modifiers) if parsed_key else current.hotkey
    hotkey.set_label(hotkey.epsilon_display)

    def begin_hotkey_capture(_button) -> None:
        hotkey.set_label("Press shortcut…")
        hotkey.grab_focus()

    def capture_hotkey(_button, event) -> bool:
        key_name = Gdk.keyval_name(event.keyval) or ""
        if key_name in MODIFIER_KEYS:
            return True
        if key_name == "Escape":
            hotkey.set_label(hotkey.epsilon_display)
            window.set_focus(None)
            return True
        modifiers = event.state & Gtk.accelerator_get_default_mod_mask()
        if not Gtk.accelerator_valid(event.keyval, modifiers):
            hotkey.set_label("Press a different shortcut")
            return True
        accelerator = Gtk.accelerator_name(event.keyval, modifiers)
        hotkey.epsilon_accelerator = accelerator
        hotkey.epsilon_display = Gtk.accelerator_get_label(event.keyval, modifiers)
        hotkey.set_label(hotkey.epsilon_display)
        return True

    def finish_hotkey_capture(_button, _event) -> bool:
        hotkey.set_label(hotkey.epsilon_display)
        return False

    hotkey.connect("clicked", begin_hotkey_capture)
    hotkey.connect("key-press-event", capture_hotkey)
    hotkey.connect("focus-out-event", finish_hotkey_capture)
    attach(
        "Custom hotkey",
        "Click the field, then press the keys you want. Pressing it again while recording stops and transcribes.",
        hotkey,
    )
    widgets["hotkey"] = hotkey

    microphone = _combo_box(Gtk, _microphone_choices(), current.microphone)
    attach(
        "Microphone",
        "Audio input used for dictation. System default follows your current GNOME/PipeWire selection.",
        microphone,
    )
    widgets["microphone"] = microphone

    compute_type = _combo_box(Gtk, COMPUTE_TYPES, current.compute_type)
    attach(
        "Compute type",
        "Automatic uses Float16 on CUDA and INT8 on CPU. Override only for memory or compatibility needs.",
        compute_type,
    )
    widgets["compute_type"] = compute_type

    language = _combo_box(Gtk, LANGUAGES, current.language)
    attach(
        "Language",
        "Auto-detect works for mixed use. Choosing a known language can improve speed and recognition consistency.",
        language,
    )
    widgets["language"] = language

    model_status = Gtk.Label(label="Checking backend…", xalign=0)
    model_status.set_name("model_status")
    model_status.set_selectable(True)
    model_status.set_ellipsize(Pango.EllipsizeMode.END)
    attach(
        "Model status",
        "The transcription model is fixed for this release. Status updates after the first dictation loads it.",
        model_status,
    )

    service_url = Gtk.Entry()
    service_url.set_text(current.service_url)
    attach(
        "Local service URL",
        "Loopback endpoint used by the tray. Keep this on localhost unless you deliberately run another local backend.",
        service_url,
    )
    widgets["service_url"] = service_url

    delivery = _combo_box(Gtk, DELIVERY_MODES, current.delivery_mode)
    attach(
        "Delivery mode",
        "Choose what happens after transcription. Clipboard copy is the safest default on Wayland.",
        delivery,
    )
    widgets["delivery_mode"] = delivery

    device = _combo_box(Gtk, DEVICES, current.device)
    attach(
        "Device",
        "Automatic tries NVIDIA CUDA first and falls back to CPU. Explicit CUDA reports GPU failures instead.",
        device,
    )
    widgets["device"] = device

    for name, title, description in (
        ("start_at_login", "Start at login", "Run the background tray automatically with your GNOME session."),
        ("history_enabled", "Save transcript history", "Keep a bounded local snippets list for recovering recent dictations."),
    ):
        switch = Gtk.Switch()
        switch.set_active(getattr(current, name))
        switch.set_halign(Gtk.Align.START)
        switch.set_hexpand(False)
        attach(title, description, switch)
        widgets[name] = switch

    history_limit = Gtk.SpinButton.new_with_range(1, 1000, 1)
    history_limit.set_value(current.history_limit)
    attach(
        "History limit",
        "Maximum number of local transcript snippets retained when history is enabled.",
        history_limit,
    )
    widgets["history_limit"] = history_limit

    for name, title, description, placeholder in (
        (
            "initial_prompt",
            "Initial prompt",
            "Briefly describe the conversation context or writing style. This biases decoding; it is not a guaranteed instruction.",
            "Example: Technical discussion about robotics, Python, and local AI tools.",
        ),
        (
            "recognition_hints",
            "Recognition hints",
            "Add likely names and specialist terms, separated by commas. Hints bias spelling; they are not a guaranteed dictionary.",
            "Example: Epsilon, OpenClaw, Floramis, faster-whisper",
        ),
    ):
        view = Gtk.TextView()
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.set_left_margin(8)
        view.set_right_margin(8)
        view.set_top_margin(7)
        view.set_bottom_margin(7)
        view.get_buffer().set_text(getattr(current, name))
        view.set_tooltip_text(placeholder)
        field_scroller = Gtk.ScrolledWindow()
        field_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        field_scroller.set_min_content_height(90)
        field_scroller.add(view)
        attach(title, description + " " + placeholder, field_scroller)
        widgets[name] = view

    separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    root.pack_start(separator, False, False, 0)
    actions = Gtk.ButtonBox(orientation=Gtk.Orientation.HORIZONTAL)
    actions.set_layout(Gtk.ButtonBoxStyle.END)
    actions.set_margin_top(12)
    actions.set_margin_bottom(12)
    actions.set_margin_start(18)
    actions.set_margin_end(18)
    cancel = Gtk.Button(label="Cancel")
    save = Gtk.Button(label="Save")
    save.get_style_context().add_class("suggested-action")
    actions.add(cancel)
    actions.add(save)
    root.pack_end(actions, False, False, 0)

    cancel.connect("clicked", lambda _button: window.destroy())

    def refresh_model_status() -> None:
        status = _model_status_text(current.service_url, current.model)
        GLib.idle_add(model_status.set_text, status)

    threading.Thread(target=refresh_model_status, daemon=True).start()

    def save_settings(_button) -> None:
        values = asdict(current)
        for name, widget in widgets.items():
            if name == "hotkey":
                values[name] = widget.epsilon_accelerator
            # Gtk.SpinButton subclasses Gtk.Entry, so read its numeric value
            # before the generic text-entry branch.
            elif isinstance(widget, Gtk.SpinButton):
                values[name] = widget.get_value_as_int()
            elif isinstance(widget, Gtk.Entry):
                values[name] = widget.get_text().strip()
            elif isinstance(widget, Gtk.ComboBoxText):
                values[name] = widget.get_active_id()
            elif isinstance(widget, Gtk.Switch):
                values[name] = widget.get_active()
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
