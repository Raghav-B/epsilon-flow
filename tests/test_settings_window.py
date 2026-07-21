import json
from types import SimpleNamespace

import requests

from epsilon_flow.settings import AppSettings
from epsilon_flow import settings_window


def test_microphone_choices_use_descriptions_and_hide_monitor_sources(monkeypatch):
    payload = [
        {
            "name": "alsa_input.usb-headset",
            "properties": {"device.description": "USB Headset"},
        },
        {
            "name": "alsa_output.pci.monitor",
            "properties": {"device.description": "Monitor of Speakers"},
        },
    ]
    monkeypatch.setattr(settings_window.shutil, "which", lambda name: "/usr/bin/pactl" if name == "pactl" else None)
    monkeypatch.setattr(
        settings_window.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(payload)),
    )

    assert settings_window._microphone_choices() == [
        ("default", "System default"),
        ("alsa_input.usb-headset", "USB Headset"),
    ]


def test_model_status_reports_loaded_runtime(monkeypatch):
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {
            "model_loaded": True,
            "model": {
                "model": "turbo",
                "device": "cpu",
                "compute_type": "int8",
            },
        },
    )
    monkeypatch.setattr(settings_window.requests, "get", lambda *args, **kwargs: response)

    assert settings_window._model_status_text("http://127.0.0.1:8791", "ignored") == (
        "Whisper large-v3-turbo · CPU / int8"
    )


def test_model_status_handles_offline_backend(monkeypatch):
    def fail(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(settings_window.requests, "get", fail)

    assert settings_window._model_status_text("http://127.0.0.1:8791", "model") == (
        "Whisper large-v3-turbo · backend offline"
    )


def test_compute_backend_status_reports_requested_and_active_with_failure(tmp_path, monkeypatch):
    current = tmp_path / "current.json"
    current.write_text(json.dumps({
        "transcription_backend": {
            "requested_backend": "vm",
            "active_backend": "local",
            "vm_status": {"failure": {"code": "gpu_busy"}},
        }
    }))
    monkeypatch.setattr(settings_window, "state_dir", lambda: tmp_path)

    assert settings_window._compute_backend_status_text(AppSettings(compute_backend="vm")) == (
        "Requested VM GPU; active Host · gpu_busy"
    )
