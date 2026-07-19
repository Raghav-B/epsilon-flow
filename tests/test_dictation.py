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
