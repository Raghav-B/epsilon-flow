import json
from types import SimpleNamespace

import requests

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


def test_service_status_reports_loaded_runtime(monkeypatch):
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {
            "ok": True,
            "model_loaded": True,
            "model": {
                "model": "turbo",
                "device": "cuda",
                "compute_type": "float16",
            },
        },
    )
    monkeypatch.setattr(settings_window.requests, "get", lambda *args, **kwargs: response)

    status = settings_window._transcription_service_status("http://127.0.0.1:8891", "ignored")

    assert status.summary == "Online"
    assert status.detail == "Whisper large-v3-turbo · CUDA / float16"


def test_service_status_reports_first_load_state(monkeypatch):
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"ok": True, "model_loaded": False, "model": None},
    )
    monkeypatch.setattr(settings_window.requests, "get", lambda *args, **kwargs: response)

    status = settings_window._transcription_service_status("http://192.168.1.20:8791", "turbo")

    assert status.summary == "Online"
    assert status.detail == "Whisper large-v3-turbo · loads on first dictation"


def test_service_status_handles_offline_backend(monkeypatch):
    def fail(*args, **kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(settings_window.requests, "get", fail)

    status = settings_window._transcription_service_status("http://127.0.0.1:8791", "turbo")

    assert status.summary == "Offline"
    assert "connection refused" in status.detail


def test_service_status_rejects_public_url_without_request(monkeypatch):
    requested = []
    monkeypatch.setattr(settings_window.requests, "get", lambda *args, **kwargs: requested.append(args))

    status = settings_window._transcription_service_status("https://example.com", "turbo")

    assert status.summary == "Invalid URL"
    assert "private IP" in status.detail
    assert requested == []


def test_service_status_rejects_non_flow_health_response(monkeypatch):
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"status": "up"},
    )
    monkeypatch.setattr(settings_window.requests, "get", lambda *args, **kwargs: response)

    status = settings_window._transcription_service_status("http://127.0.0.1:8791", "turbo")

    assert status.summary == "Invalid response"
    assert "ok=true" in status.detail
