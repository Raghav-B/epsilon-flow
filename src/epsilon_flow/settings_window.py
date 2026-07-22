"""GTK3 settings window for portable Epsilon Flow preferences."""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from dataclasses import asdict, dataclass
from datetime import datetime

import requests

from .integrations import bind_gnome_hotkey, set_autostart
from .settings import AppSettings, SettingsStore, validate_service_url


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


@dataclass(frozen=True)
class TranscriptionServiceStatus:
    summary: str
    detail: str


def _transcription_service_status(service_url: str, configured_model: str) -> TranscriptionServiceStatus:
    try:
        validate_service_url(service_url)
    except ValueError as exc:
        return TranscriptionServiceStatus("Invalid URL", str(exc))

    try:
        response = requests.get(f"{service_url.rstrip('/')}/health", timeout=2)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return TranscriptionServiceStatus("Offline", str(exc))
    except ValueError:
        return TranscriptionServiceStatus("Invalid response", "The health endpoint did not return JSON.")

    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return TranscriptionServiceStatus("Invalid response", "The health endpoint did not report ok=true.")

    friendly = "Whisper large-v3-turbo"
    config = payload.get("model")
    if not payload.get("model_loaded") or not isinstance(config, dict):
        return TranscriptionServiceStatus("Online", f"{friendly} · loads on first dictation")

    device = str(config.get("device", "unknown")).upper()
    compute_type = str(config.get("compute_type", "default"))
    model = str(config.get("model") or configured_model)
    model_name = friendly if model in {"turbo", "large-v3-turbo"} or "large-v3-turbo" in model else model
    return TranscriptionServiceStatus("Online", f"{model_name} · {device} / {compute_type}")


def create_settings_window(store: SettingsStore):
    import gi

    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gdk, GLib, Gtk

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
        b"#service_status { font-weight: 600; }"
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

    service_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    endpoint_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    service_url = Gtk.Entry()
    service_url.set_text(current.service_url)
    service_url.set_placeholder_text("http://127.0.0.1:8791")
    endpoint_row.pack_start(service_url, True, True, 0)
    refresh_service = Gtk.Button(label="Refresh")
    refresh_service.set_image(Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON))
    refresh_service.set_always_show_image(True)
    endpoint_row.pack_end(refresh_service, False, False, 0)
    service_box.pack_start(endpoint_row, False, False, 0)

    service_status = Gtk.Label(label="Checking…", xalign=0)
    service_status.set_name("service_status")
    service_status.set_selectable(True)
    service_box.pack_start(service_status, False, False, 0)
    service_detail = Gtk.Label(label="", xalign=0, yalign=0)
    service_detail.set_line_wrap(True)
    service_detail.set_max_width_chars(70)
    service_detail.set_selectable(True)
    service_detail.get_style_context().add_class("dim-label")
    service_box.pack_start(service_detail, False, False, 0)
    service_checked = Gtk.Label(label="Not checked yet", xalign=0)
    service_checked.get_style_context().add_class("dim-label")
    service_box.pack_start(service_checked, False, False, 0)
    attach(
        "Transcription service",
        "HTTP endpoint used for status and transcription. Use localhost, a private LAN IP, or a local SSH tunnel.",
        service_box,
    )
    widgets["service_url"] = service_url

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

    def text_field(value: str, placeholder: str):
        view = Gtk.TextView()
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.set_left_margin(8)
        view.set_right_margin(8)
        view.set_top_margin(7)
        view.set_bottom_margin(7)
        view.get_buffer().set_text(value)
        view.set_tooltip_text(placeholder)
        field_scroller = Gtk.ScrolledWindow()
        field_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        field_scroller.set_shadow_type(Gtk.ShadowType.IN)
        field_scroller.set_min_content_height(90)
        field_scroller.add(view)
        return view, field_scroller

    names_view, names_scroller = text_field(
        current.recognition_hints,
        "Example: Epsilon, OpenClaw, CTranslate2, Faster-Whisper",
    )
    attach(
        "Names and terms",
        "Exact names and specialist terms, separated by commas. They bias spelling but are not a guaranteed dictionary.",
        names_scroller,
    )
    widgets["recognition_hints"] = names_view

    style_view, style_scroller = text_field(
        current.initial_prompt,
        "Example: Okay, let’s inspect this carefully. The backend is healthy, but the status label is stale.",
    )
    style_expander = Gtk.Expander(label="Add a style example")
    style_expander.add(style_scroller)
    attach(
        "Style example · Advanced",
        "Leave empty unless transcripts repeatedly use the wrong casing, punctuation, or prose style. Use natural transcript text—not instructions or vocabulary.",
        style_expander,
    )
    widgets["initial_prompt"] = style_view

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

    refresh_generation = 0

    def refresh_service_status(_button=None) -> None:
        nonlocal refresh_generation
        endpoint = service_url.get_text().strip().rstrip("/")
        refresh_generation += 1
        generation = refresh_generation
        refresh_service.set_sensitive(False)
        service_status.set_text("Checking…")
        service_detail.set_text(endpoint or "Enter a service URL.")
        service_checked.set_text("")

        def worker() -> None:
            status = _transcription_service_status(endpoint, current.model)
            checked_at = datetime.now().strftime("%H:%M:%S")

            def apply_status() -> bool:
                # A slower response from an older endpoint must not overwrite a
                # newer manual refresh after the URL field has changed.
                if generation != refresh_generation:
                    return False
                service_status.set_text(status.summary)
                service_detail.set_text(status.detail)
                service_checked.set_text(f"Last checked {checked_at}")
                refresh_service.set_sensitive(True)
                return False

            GLib.idle_add(apply_status)

        threading.Thread(target=worker, daemon=True).start()

    refresh_service.connect("clicked", refresh_service_status)
    refresh_service_status()

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
        try:
            settings = AppSettings.from_mapping(values)
        except ValueError as exc:
            dialog = Gtk.MessageDialog(
                transient_for=window,
                modal=True,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text="Check the settings before saving.",
            )
            dialog.format_secondary_text(str(exc))
            dialog.run()
            dialog.destroy()
            return

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
