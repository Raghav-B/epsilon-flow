import io
import struct
import subprocess
from types import SimpleNamespace

import pytest

from epsilon_flow import dictation
from epsilon_flow.settings import AppSettings


def pcm_at_decibels(decibels: float, sample_count: int = 64) -> bytes:
    amplitude = round(dictation.PCM_FULL_SCALE * (10 ** (decibels / 20)))
    samples = [amplitude if index % 2 else -amplitude for index in range(sample_count)]
    return struct.pack(f"<{sample_count}h", *samples)


def test_audio_level_from_pcm():
    assert dictation.audio_level_from_pcm(b"") is None
    assert dictation.audio_level_from_pcm(struct.pack("<8h", *([0] * 8))) == 0.0
    assert dictation.audio_level_from_pcm(pcm_at_decibels(-36)) == pytest.approx(0.5, abs=0.01)
    assert dictation.audio_level_from_pcm(pcm_at_decibels(-6)) == 1.0


def test_read_audio_levels_streams_pcm_chunks():
    levels = []
    stream = io.BytesIO(pcm_at_decibels(-48, sample_count=1024))

    dictation.read_audio_levels(stream, levels.append)

    assert levels == [pytest.approx(0.25, abs=0.01)]


def test_record_audio_meters_the_same_ffmpeg_input(tmp_path, monkeypatch):
    captured = {}

    class FakeRecorder:
        stdout = io.BytesIO(pcm_at_decibels(-36))
        stderr = io.BytesIO(b"")
        returncode = 0

        @staticmethod
        def poll():
            return 0

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeRecorder()

    monkeypatch.setattr(dictation.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(dictation.subprocess, "Popen", fake_popen)
    controller = SimpleNamespace(
        stop_requested=False,
        cancel_requested=False,
        set_phase=lambda phase: captured.setdefault("phase", phase),
    )
    levels = []

    recorded = dictation.record_audio(
        tmp_path / "capture.wav",
        AppSettings(microphone="alsa_input.usb-headset"),
        controller,
        on_audio_level=levels.append,
    )

    assert recorded
    assert captured["phase"] == "recording"
    command = captured["command"]
    assert command[command.index("-i") + 1] == "alsa_input.usb-headset"
    assert command.count("-map") == 2
    assert command[-3:] == ["-f", "s16le", "pipe:1"]
    assert captured["kwargs"]["stdout"] is subprocess.PIPE
    assert captured["kwargs"]["bufsize"] == 0
    assert levels == [pytest.approx(0.5, abs=0.01), 0.0]


def test_transcribe_posts_directly_to_the_configured_service(tmp_path, monkeypatch):
    audio_path = tmp_path / "capture.wav"
    audio_path.write_bytes(b"wav")
    captured = {}

    class FakeResponse:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"text": "hello"}

    def fake_post(url, *, data, files, timeout):
        captured["url"] = url
        captured["data"] = data
        captured["filename"] = files["file"][0]
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(dictation.requests, "post", fake_post)
    settings = AppSettings(
        service_url="http://127.0.0.1:8891",
        initial_prompt="Okay, let us inspect this.",
        recognition_hints="Epsilon, CTranslate2",
    )

    result = dictation.transcribe(audio_path, settings)

    assert result == {"text": "hello"}
    assert captured["url"] == "http://127.0.0.1:8891/transcribe"
    assert captured["data"]["initial_prompt"] == (
        "Okay, let us inspect this.\n\nEpsilon, CTranslate2"
    )
    assert captured["filename"] == "capture.wav"
    assert captured["timeout"] == 600


def test_run_dictation_uses_one_configured_service_without_backend_selection(tmp_path, monkeypatch):
    captured = {"phases": []}

    def fake_record_audio(path, settings, controller, **kwargs):
        path.write_bytes(b"wav")
        return True

    def fake_transcribe(path, settings):
        captured["service_url"] = settings.service_url
        return {"text": "hello"}

    controller = SimpleNamespace(
        set_phase=lambda phase, **metadata: captured["phases"].append((phase, metadata)),
    )
    monkeypatch.setattr(dictation, "record_audio", fake_record_audio)
    monkeypatch.setattr(dictation, "transcribe", fake_transcribe)

    result = dictation.run_dictation(
        AppSettings(
            service_url="http://192.168.1.40:8791",
            history_enabled=False,
            delivery_mode="none",
        ),
        controller,
    )

    assert captured["service_url"] == "http://192.168.1.40:8791"
    assert captured["phases"] == [
        ("transcribing", {"service_url": "http://192.168.1.40:8791"}),
        ("delivering", {}),
    ]
    assert result["text"] == "hello"
