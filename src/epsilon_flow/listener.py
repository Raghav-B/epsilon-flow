#!/usr/bin/env python3
"""Reusable tray-owned GTK listener with an in-place transcript recovery drawer."""
from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import threading
import time
from datetime import datetime
from typing import Callable

from .history import TranscriptHistory

os.environ.setdefault("GDK_BACKEND", "x11")

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango


LISTENER_WIDTH = 500
LISTENER_HEIGHT = 64
SNIPPET_DRAWER_MIN_HEIGHT = 520
SNIPPET_DRAWER_MAX_HEIGHT = 620
COPY_FEEDBACK_MS = 2000
LISTENER_BOTTOM_MARGIN = 120


CSS = """
window#listener_window {
  background: transparent;
}

#listener_shell, #snippet_drawer {
  background: rgba(18, 18, 22, 0.97);
  border: 1px solid rgba(255, 76, 108, 0.72);
}

#listener_shell {
  border-radius: 18px;
}

#snippet_drawer {
  border-radius: 14px;
  margin-bottom: 8px;
}

#title {
  color: #f5f5f7;
  font-size: 15px;
  font-weight: 700;
}

#subtitle, #snippet_time, #snippet_status, #empty {
  color: #b8bbc4;
  font-size: 11px;
}

#elapsed {
  color: #b8bbc4;
  font-size: 12px;
}

#copy_feedback {
  color: #ff8ca7;
  font-size: 11px;
  font-weight: 700;
}

#device_toast {
  background: rgba(30, 25, 20, 0.96);
  border: 1px solid rgba(255, 185, 95, 0.72);
  border-radius: 9px;
  margin-top: 6px;
  padding: 7px 10px;
}

#device_toast_label {
  color: #ffd59a;
  font-size: 12px;
  font-weight: 700;
}

#snippet_text {
  color: #f5f5f7;
  font-size: 12px;
}

#snippet_scroller, #snippet_scroller viewport, #snippet_list,
#snippet_list row, #empty_row, #snippet_row {
  background: rgba(20, 20, 25, 0.98);
  color: #f5f5f7;
}

#snippet_scroller {
  border: 1px solid rgba(74, 76, 87, 0.72);
  border-radius: 10px;
}

#snippet_row {
  border-bottom: 1px solid rgba(74, 76, 87, 0.46);
}

#snippet_row:hover {
  background: rgba(35, 35, 43, 0.98);
}

#snippet_row:active, #snippet_row:selected, #snippet_row:focus {
  background: rgba(28, 28, 34, 0.98);
  outline: none;
  box-shadow: none;
}

#recording_dot {
  background: #ff3f6e;
  border-radius: 5px;
  min-width: 10px;
  min-height: 10px;
}

#action_button, #danger_button, #stop_button {
  min-width: 34px;
  min-height: 34px;
  margin-top: 0;
  margin-bottom: 0;
  outline: none;
  box-shadow: none;
}

#action_button, #danger_button {
  background: transparent;
  border: 1px solid #4a4c57;
  border-radius: 8px;
  color: #d7d8de;
  padding: 5px;
}

#action_button:hover {
  background: rgba(255, 255, 255, 0.06);
}

#danger_button {
  color: #ff8ca7;
}

#danger_button:hover {
  background: rgba(255, 63, 110, 0.12);
  border-color: rgba(255, 63, 110, 0.65);
}

#stop_button {
  background: #e7335f;
  color: #ffffff;
  border: none;
  border-radius: 8px;
  padding: 5px;
}

#stop_button:hover {
  background: #ff3f6e;
}

#action_button:active, #action_button:focus, #action_button:checked,
#danger_button:active, #danger_button:focus, #danger_button:checked,
#stop_button:active, #stop_button:focus, #stop_button:checked {
  outline: none;
  box-shadow: none;
}

#action_button:active, #action_button:focus, #action_button:checked {
  background: transparent;
}

#danger_button:active, #danger_button:focus, #danger_button:checked {
  background: transparent;
  border-color: #4a4c57;
}

#stop_button:active, #stop_button:focus, #stop_button:checked {
  background: #d72a55;
}
"""


class DictationListener(Gtk.Window):
    """A single hidden window that the tray presents for every dictation run."""

    def __init__(
        self,
        on_start: Callable[[], None],
        on_stop: Callable[[], None],
        on_cancel: Callable[[], None],
        history: TranscriptHistory | None = None,
    ) -> None:
        super().__init__(title="Epsilon Flow")
        self.set_name("listener_window")
        self.on_start = on_start
        self.on_stop = on_stop
        self.on_cancel = on_cancel
        self.history = history or TranscriptHistory()
        self.recording = False
        self.recording_started = 0.0
        self.limit_warning_visible = False
        self.audio_level = 0.0
        self.last_audio_at = 0.0
        self.meter_phase = 0
        self.meter_process: subprocess.Popen[bytes] | None = None
        self.meter_thread: threading.Thread | None = None
        self.copy_feedback_source_id: int | None = None
        self.last_allocation_size: tuple[int, int] | None = None
        self.active_device: str | None = None
        self.fallback_reason: str | None = None
        self.set_decorated(False)
        self.set_keep_above(True)
        # Notification windows float well, but many WMs refuse to focus them.
        # Utility keeps Escape handling available while keep_above owns stacking.
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_position(Gtk.WindowPosition.NONE)
        self.set_resizable(False)
        self.set_default_size(LISTENER_WIDTH, LISTENER_HEIGHT)
        self.set_border_width(0)
        self.set_app_paintable(True)
        self.set_accept_focus(True)
        self.set_focus_on_map(True)
        self.set_can_focus(True)
        self.connect("delete-event", self.on_delete)
        self.connect("key-press-event", self.on_key_press)
        self.connect("map-event", self.on_map)
        self.connect("realize", self.on_realize)
        self.connect("size-allocate", self.on_size_allocate)
        self.connect("hide", self.on_hide)
        self.connect("destroy", self.on_destroy)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_screen(
            screen,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(root)

        self.drawer_revealer = Gtk.Revealer()
        self.drawer_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self.drawer_revealer.set_transition_duration(180)
        self.drawer_revealer.connect("notify::reveal-child", self.on_drawer_reveal_changed)
        self.drawer_revealer.connect("notify::child-revealed", self.on_drawer_reveal_changed)
        root.pack_start(self.drawer_revealer, False, False, 0)

        drawer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        drawer.set_name("snippet_drawer")
        drawer.set_margin_top(0)
        drawer.set_margin_start(0)
        drawer.set_margin_end(0)
        drawer.set_margin_bottom(0)
        self.drawer_revealer.add(drawer)

        drawer_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        drawer_header.set_margin_top(10)
        drawer_header.set_margin_start(12)
        drawer_header.set_margin_end(8)
        drawer.pack_start(drawer_header, False, False, 0)
        drawer_title = Gtk.Label(label="Recent snippets · local only")
        drawer_title.set_name("subtitle")
        drawer_title.set_xalign(0)
        drawer_header.pack_start(drawer_title, True, True, 0)
        self.copy_feedback = Gtk.Label(label="")
        self.copy_feedback.set_name("copy_feedback")
        self.copy_feedback.set_no_show_all(True)
        self.copy_feedback.hide()
        drawer_header.pack_start(self.copy_feedback, False, False, 0)
        clear_button = self.icon_button(
            self.first_available_icon("user-trash-symbolic", "edit-delete-symbolic", "edit-clear-symbolic"),
            "Clear snippets",
            "Clear all saved local snippets",
            name="danger_button",
        )
        clear_button.connect("clicked", self.clear_snippets)
        drawer_header.pack_end(clear_button, False, False, 0)

        self.snippet_list = Gtk.ListBox()
        self.snippet_list.set_name("snippet_list")
        self.snippet_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.snippet_list.set_activate_on_single_click(True)
        self.snippet_list.connect("row-activated", self.toggle_snippet_row)
        scroller = Gtk.ScrolledWindow()
        scroller.set_name("snippet_scroller")
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(SNIPPET_DRAWER_MIN_HEIGHT)
        scroller.set_max_content_height(SNIPPET_DRAWER_MAX_HEIGHT)
        scroller.set_margin_start(8)
        scroller.set_margin_end(8)
        scroller.set_margin_bottom(8)
        scroller.add(self.snippet_list)
        drawer.pack_start(scroller, True, True, 0)

        shell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        shell.set_name("listener_shell")
        shell.set_margin_top(0)
        shell.set_margin_bottom(0)
        shell.set_margin_start(0)
        shell.set_margin_end(0)
        shell.set_size_request(LISTENER_WIDTH, LISTENER_HEIGHT)
        root.pack_start(shell, False, False, 0)

        self.recording_dot = Gtk.Box()
        self.recording_dot.set_name("recording_dot")
        self.recording_dot.set_valign(Gtk.Align.CENTER)
        self.recording_dot.set_margin_start(16)
        shell.pack_start(self.recording_dot, False, False, 0)

        labels = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        labels.set_valign(Gtk.Align.CENTER)
        shell.pack_start(labels, True, True, 0)
        self.title_label = Gtk.Label(label="Ready")
        self.title_label.set_name("title")
        self.title_label.set_xalign(0)
        labels.pack_start(self.title_label, False, False, 0)
        self.elapsed = Gtk.Label(label="00:00 / 1:00:00")
        self.elapsed.set_name("elapsed")
        self.elapsed.set_margin_end(2)
        labels.pack_start(self.elapsed, False, False, 0)

        self.meter = Gtk.DrawingArea()
        self.meter.set_size_request(92, 26)
        self.meter.set_halign(Gtk.Align.CENTER)
        self.meter.set_valign(Gtk.Align.CENTER)
        self.meter.connect("draw", self.draw_meter)
        shell.pack_start(self.meter, False, False, 0)

        self.snippets_button = self.icon_button(
            "view-list-symbolic",
            "Snippets",
            "Show recent local transcript snippets",
        )
        self.snippets_button.connect("clicked", self.toggle_drawer)
        shell.pack_end(self.snippets_button, False, False, 14)

        self.start_button = self.icon_button(
            "media-playback-start-symbolic",
            "Start dictation",
            "Start recording a new dictation",
            name="stop_button",
        )
        self.start_button.connect("clicked", self.start_recording)
        shell.pack_end(self.start_button, False, False, 0)

        self.cancel_button = self.icon_button(
            self.first_available_icon("window-close-symbolic", "edit-delete-symbolic", "process-stop-symbolic"),
            "Cancel",
            "Discard this recording without transcription or delivery",
            name="danger_button",
        )
        self.cancel_button.connect("clicked", self.cancel_recording)
        shell.pack_end(self.cancel_button, False, False, 0)

        self.stop_button = self.icon_button(
            "media-playback-stop-symbolic",
            "Stop",
            "Stop recording and transcribe the captured audio",
            name="stop_button",
        )
        self.stop_button.connect("clicked", self.stop_recording)
        shell.pack_end(self.stop_button, False, False, 0)

        self.device_toast_revealer = Gtk.Revealer()
        self.device_toast_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.device_toast_revealer.set_transition_duration(140)
        root.pack_start(self.device_toast_revealer, False, False, 0)

        device_toast = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        device_toast.set_name("device_toast")
        device_toast.set_halign(Gtk.Align.CENTER)
        device_toast.set_valign(Gtk.Align.CENTER)
        warning_icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic", Gtk.IconSize.MENU)
        warning_icon.set_pixel_size(15)
        device_toast.pack_start(warning_icon, False, False, 0)
        self.device_toast_label = Gtk.Label(label="")
        self.device_toast_label.set_name("device_toast_label")
        self.device_toast_label.set_xalign(0)
        device_toast.pack_start(self.device_toast_label, False, False, 0)
        self.device_toast_revealer.add(device_toast)

        self.set_idle("Ready")
        self.set_device_status(None, None)
        GLib.timeout_add(50, self.animate_meter)
        GLib.timeout_add(250, self.update_elapsed)

    def present(self) -> None:
        super().present()
        self.force_above()
        self.schedule_centering()
        self.schedule_focus()

    @staticmethod
    def icon_button(icon: str, label: str, tooltip: str, name: str = "action_button") -> Gtk.Button:
        button = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
        button.set_name(name)
        button.set_tooltip_text(tooltip)
        button.set_always_show_image(True)
        button.set_size_request(34, 34)
        button.set_halign(Gtk.Align.CENTER)
        button.set_valign(Gtk.Align.CENTER)
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_focus_on_click(False)
        button.get_accessible().set_name(label)
        return button

    @staticmethod
    def first_available_icon(*icons: str) -> str:
        theme = Gtk.IconTheme.get_default()
        if theme is None:
            return icons[-1]

        for icon in icons:
            if theme.has_icon(icon):
                return icon
        return icons[-1]

    def present_for_start(self) -> None:
        self.drawer_revealer.set_reveal_child(False)
        self.recording_started = time.monotonic()
        self.limit_warning_visible = False
        self.elapsed.set_text("00:00 / 1:00:00")
        self.set_recording(True, "Starting microphone…")
        self.set_device_status(self.active_device, self.fallback_reason)
        self.show_all()
        self.drawer_revealer.set_reveal_child(False)
        self.apply_mode_controls()
        self.force_above()
        self.present()
        self.schedule_centering()
        self.schedule_focus()

    def present_snippets(self) -> None:
        self.refresh_snippets()
        if not self.recording:
            self.set_idle("Transcript snippets")
        self.show_all()
        self.drawer_revealer.set_reveal_child(True)
        self.apply_mode_controls()
        self.force_above()
        self.present()
        self.schedule_centering()
        self.schedule_focus()
        self.schedule_snippets_reveal()

    def schedule_snippets_reveal(self) -> None:
        GLib.idle_add(self.ensure_snippets_revealed)
        for delay_ms in (40, 140, 260):
            GLib.timeout_add(delay_ms, self.ensure_snippets_revealed)

    def ensure_snippets_revealed(self) -> bool:
        self.drawer_revealer.set_reveal_child(True)
        self.force_above()
        self.schedule_centering()
        return False

    def on_map(self, _widget: Gtk.Widget, _event: Gdk.Event) -> bool:
        self.force_above()
        self.schedule_centering()
        self.schedule_focus()
        if self.recording:
            self.start_audio_meter()
        return False

    def on_realize(self, _widget: Gtk.Widget) -> None:
        self.force_above()
        self.schedule_centering()
        self.schedule_focus()

    def on_size_allocate(self, _widget: Gtk.Widget, _allocation: Gdk.Rectangle) -> None:
        allocation_size = (_allocation.width, _allocation.height)
        if self.get_visible() and allocation_size != self.last_allocation_size:
            self.last_allocation_size = allocation_size
            self.schedule_centering()

    def force_above(self) -> None:
        self.set_keep_above(True)
        self.stick()
        window = self.get_window()
        if window is not None:
            window.set_keep_above(True)

    def schedule_focus(self) -> None:
        GLib.idle_add(self.grab_keyboard_focus)
        for delay_ms in (40, 120, 260):
            GLib.timeout_add(delay_ms, self.grab_keyboard_focus)

    def grab_keyboard_focus(self) -> bool:
        self.set_accept_focus(True)
        self.grab_focus()
        window = self.get_window()
        if window is not None:
            timestamp = Gtk.get_current_event_time() or Gdk.CURRENT_TIME
            window.focus(timestamp)
        return False

    def schedule_centering(self) -> None:
        for delay_ms in (0, 40, 120, 260):
            if delay_ms == 0:
                GLib.idle_add(self.move_to_lower_center)
            else:
                GLib.timeout_add(delay_ms, self.move_to_lower_center)

    def move_to_lower_center(self) -> bool:
        display = Gdk.Display.get_default()
        if display is None:
            return False

        monitor = display.get_primary_monitor() or display.get_monitor(0)
        if monitor is None:
            return False

        workarea = monitor.get_workarea()
        width, height = self.get_size()
        if width <= 1 or height <= 1:
            requisition = self.get_preferred_size()[1]
            width = max(width, requisition.width, LISTENER_WIDTH)
            height = max(height, requisition.height, LISTENER_HEIGHT)
        x = workarea.x + max(0, (workarea.width - width) // 2)
        y = workarea.y + max(0, workarea.height - height - LISTENER_BOTTOM_MARGIN)
        self.move(x, y)
        self.force_above()
        return False

    def set_recording(self, recording: bool, title: str) -> None:
        self.recording = recording
        self.title_label.set_text(title)
        self.apply_mode_controls()
        self.refresh_device_toast()
        if recording and self.get_visible():
            self.start_audio_meter()
        if not recording:
            self.stop_audio_meter()

    def set_idle(self, title: str = "Ready") -> None:
        self.recording_started = 0.0
        self.elapsed.set_text("")
        self.set_recording(False, title)

    def apply_mode_controls(self) -> None:
        self.recording_dot.set_visible(self.recording)
        self.elapsed.set_visible(self.recording)
        self.meter.set_visible(self.recording)
        self.stop_button.set_visible(self.recording)
        self.cancel_button.set_visible(self.recording)
        self.start_button.set_visible(not self.recording)

    def start_recording(self, _button: Gtk.Button) -> None:
        if not self.recording:
            self.on_start()

    def set_device_status(self, active_device: str | None, fallback_reason: str | None = None) -> None:
        self.active_device = active_device
        self.fallback_reason = fallback_reason
        self.refresh_device_toast()

    def refresh_device_toast(self) -> None:
        message = ""
        if self.fallback_reason == "cuda_oom":
            message = "CUDA memory full; retrying on CPU"
        elif self.fallback_reason:
            message = "CUDA unavailable; using CPU"
        elif self.active_device == "cpu":
            message = "CPU transcription active"

        self.device_toast_label.set_text(message)
        # Keep the listener compact when idle; surface device risk only while this run is visible.
        self.device_toast_revealer.set_reveal_child(bool(message and self.get_visible()))

    def update_elapsed(self) -> bool:
        if self.recording_started <= 0:
            return True

        elapsed_seconds = int(time.monotonic() - self.recording_started)
        self.elapsed.set_text(f"{self.format_duration(elapsed_seconds)} / 1:00:00")
        if self.recording and not self.limit_warning_visible and elapsed_seconds >= 2700:
            self.limit_warning_visible = True
        return True

    @staticmethod
    def format_duration(seconds: int) -> str:
        hours, remainder = divmod(max(0, seconds), 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def set_finishing(self, subtitle: str) -> None:
        self.set_recording(False, subtitle)

    def stop_recording(self, _button: Gtk.Button) -> None:
        if not self.recording:
            return
        self.set_finishing("Finishing recording…")
        self.hide()
        self.on_stop()

    def cancel_recording(self, _button: Gtk.Button) -> None:
        if not self.recording:
            return
        self.set_finishing("Discarding recording…")
        self.hide()
        self.on_cancel()

    def toggle_drawer(self, _button: Gtk.Button) -> None:
        reveal = not self.drawer_revealer.get_reveal_child()
        if reveal:
            self.refresh_snippets()
        self.drawer_revealer.set_reveal_child(reveal)

    def on_drawer_reveal_changed(self, _revealer: Gtk.Revealer, _param) -> None:
        # Drawer height changes after the slide animation; recenter both before and after it.
        self.schedule_centering()

    def refresh_snippets(self) -> None:
        for row in self.snippet_list.get_children():
            self.snippet_list.remove(row)

        entries = self.history.load()
        if not entries:
            row = Gtk.ListBoxRow()
            row.set_name("empty_row")
            row.set_selectable(False)
            row.set_activatable(False)
            empty = Gtk.Label(label="No saved transcripts yet")
            empty.set_name("empty")
            empty.set_margin_top(10)
            empty.set_margin_bottom(10)
            row.add(empty)
            self.snippet_list.add(row)
        else:
            for entry in entries:
                self.snippet_list.add(self.build_snippet_row(entry))
        self.snippet_list.show_all()

    def build_snippet_row(self, entry: dict) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.set_name("snippet_row")
        row.set_selectable(False)
        row.set_activatable(True)
        row.preview_label = None
        row.expanded_label = None
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        content.set_margin_top(7)
        content.set_margin_bottom(7)
        content.set_margin_start(8)
        content.set_margin_end(4)
        row.add(content)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        content.pack_start(header, False, False, 0)
        created_at = datetime.fromtimestamp(entry.get("created_at", 0)).strftime("%b %-d · %H:%M")
        timestamp = Gtk.Label(label=created_at)
        timestamp.set_name("snippet_time")
        timestamp.set_xalign(0)
        header.pack_start(timestamp, True, True, 0)
        copy_button = self.icon_button("edit-copy-symbolic", "Copy snippet", "Copy transcript to clipboard")
        copy_button.connect("clicked", self.copy_snippet, entry.get("text", ""))
        header.pack_end(copy_button, False, False, 0)

        preview = Gtk.Label(label=entry.get("text", ""))
        preview.set_name("snippet_text")
        preview.set_xalign(0)
        preview.set_ellipsize(Pango.EllipsizeMode.END)
        preview.set_single_line_mode(True)
        content.pack_start(preview, False, False, 0)

        expanded = Gtk.Label(label=entry.get("text", ""))
        expanded.set_name("snippet_text")
        expanded.set_xalign(0)
        expanded.set_line_wrap(True)
        expanded.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        expanded.set_selectable(True)
        expanded.set_no_show_all(True)
        expanded.hide()
        content.pack_start(expanded, False, False, 0)
        row.preview_label = preview
        row.expanded_label = expanded
        return row

    def toggle_snippet_row(self, _list_box: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        expanded = getattr(row, "expanded_label", None)
        if expanded is None:
            return
        preview = getattr(row, "preview_label", None)
        should_expand = not expanded.get_visible()
        expanded.set_visible(should_expand)
        if preview is not None:
            preview.set_visible(not should_expand)

    def copy_snippet(self, _button: Gtk.Button, text: str) -> None:
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)
        self.show_copy_feedback()

    def show_copy_feedback(self) -> None:
        if self.copy_feedback_source_id is not None:
            GLib.source_remove(self.copy_feedback_source_id)
            self.copy_feedback_source_id = None

        self.copy_feedback.set_text("Copied!")
        self.copy_feedback.show()
        self.copy_feedback_source_id = GLib.timeout_add(
            COPY_FEEDBACK_MS,
            self.clear_copy_feedback,
        )

    def clear_copy_feedback(self) -> bool:
        self.copy_feedback.set_text("")
        self.copy_feedback.hide()
        self.copy_feedback_source_id = None
        return False

    def clear_snippets(self, _button: Gtk.Button) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text="Clear all local dictation snippets?",
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Clear", Gtk.ResponseType.ACCEPT)
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.ACCEPT:
            return
        self.history.clear()
        self.refresh_snippets()

    def start_audio_meter(self) -> None:
        if self.meter_process is not None and self.meter_process.poll() is None:
            return

        recorder = shutil.which("pw-record")
        if not recorder:
            return

        command = [
            recorder,
            "--media-category",
            "Capture",
            "--rate",
            "16000",
            "--channels",
            "1",
            "--format",
            "s16",
            "-",
        ]
        try:
            self.meter_process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            self.meter_process = None
            return

        self.meter_thread = threading.Thread(target=self.read_audio_meter, daemon=True)
        self.meter_thread.start()

    def read_audio_meter(self) -> None:
        process = self.meter_process
        if process is None or process.stdout is None:
            return

        while True:
            chunk = process.stdout.read(2048)
            if not chunk:
                break
            sample_count = len(chunk) // 2
            if sample_count == 0:
                continue
            samples = struct.unpack(f"<{sample_count}h", chunk[: sample_count * 2])
            rms = math.sqrt(sum(sample * sample for sample in samples) / sample_count)
            self.audio_level = min(1.0, rms / 1700.0)
            self.last_audio_at = time.monotonic()

    def animate_meter(self) -> bool:
        self.meter_phase += 1
        if time.monotonic() - self.last_audio_at > 0.4:
            self.audio_level *= 0.86
        self.meter.queue_draw()
        return True

    def draw_meter(self, widget: Gtk.DrawingArea, context) -> bool:
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        bar_width = 4
        gap = 4
        bar_count = 10
        total_width = bar_count * bar_width + (bar_count - 1) * gap
        start_x = (width - total_width) / 2
        center_y = height / 2

        for index in range(bar_count):
            wave = (math.sin((self.meter_phase * 0.26) + index * 0.85) + 1.0) / 2.0
            level = max(0.08, min(1.0, self.audio_level * 0.86 + wave * self.audio_level * 0.35))
            bar_height = 4 + level * 20
            x = start_x + index * (bar_width + gap)
            y = center_y - bar_height / 2
            context.set_source_rgba(1.0, 0.24, 0.43, 0.35 + level * 0.55)
            context.rectangle(x, y, bar_width, bar_height)
            context.fill()
        return False

    def stop_audio_meter(self) -> None:
        process = self.meter_process
        thread = self.meter_thread
        self.meter_process = None
        self.meter_thread = None
        if process is None:
            return

        # The meter is only a visual cue; never leave its capture helper alive after hide/destroy.
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.5)
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.2)

    def on_hide(self, _widget: Gtk.Widget) -> None:
        self.device_toast_revealer.set_reveal_child(False)
        self.stop_audio_meter()

    def on_destroy(self, _widget: Gtk.Widget) -> None:
        if self.copy_feedback_source_id is not None:
            GLib.source_remove(self.copy_feedback_source_id)
            self.copy_feedback_source_id = None
        self.stop_audio_meter()

    def on_delete(self, _widget: Gtk.Window, _event: Gdk.Event) -> bool:
        # Closing the listener should not quit its tray controller or discard an active recording.
        self.hide()
        return True

    def on_key_press(self, _widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        if event.keyval != Gdk.KEY_Escape:
            return False
        if self.recording:
            self.stop_recording(self.stop_button)
        else:
            self.hide()
        return True
