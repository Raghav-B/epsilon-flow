import io
import subprocess
from types import SimpleNamespace

import pytest

from epsilon_flow import dictation
from epsilon_flow.settings import AppSettings


def test_audio_level_from_ffmpeg_metadata():
    assert dictation.audio_level_from_metadata("unrelated=value") is None
    assert dictation.audio_level_from_metadata(
        "lavfi.astats.Overall.RMS_level=-inf"
    ) == 0.0
    assert dictation.audio_level_from_metadata(
        "lavfi.astats.Overall.RMS_level=-36"
    ) == pytest.approx(0.5)
    assert dictation.audio_level_from_metadata(
        "lavfi.astats.Overall.RMS_level=-6"
    ) == 1.0


def test_read_audio_levels_ignores_other_metadata():
    levels = []
    stream = io.StringIO(
        "frame:0 pts:0\n"
        "lavfi.astats.Overall.RMS_level=-48\n"
        "lavfi.astats.Overall.Peak_level=-12\n"
    )

    dictation.read_audio_levels(stream, levels.append)

    assert levels == [pytest.approx(0.25)]


def test_record_audio_meters_the_same_ffmpeg_input(tmp_path, monkeypatch):
    captured = {}

    class FakeRecorder:
        stdout = io.StringIO("lavfi.astats.Overall.RMS_level=-36\n")
        stderr = io.StringIO("")
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
    assert captured["command"][captured["command"].index("-i") + 1] == "alsa_input.usb-headset"
    assert captured["command"][captured["command"].index("-af") + 1] == dictation.AUDIO_METER_FILTER
    assert captured["kwargs"]["stdout"] is subprocess.PIPE
    assert levels == [pytest.approx(0.5), 0.0]
