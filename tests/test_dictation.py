import io
import struct
import subprocess
from types import SimpleNamespace

import pytest

from epsilon_flow import dictation
from epsilon_flow.settings import AppSettings
from epsilon_flow.vm_backend import BackendSelection, VmBackendFailure, VmBackendProgress, VmBackendStatus


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


def test_transcription_route_uses_verified_vm_tunnel():
    status = VmBackendStatus(
        ready=True,
        progress=(VmBackendProgress("ready", "ready"),),
        tunnel_url="http://127.0.0.1:8891",
    )
    selection = BackendSelection(requested_backend="vm", active_backend="vm", vm_status=status)

    assert dictation.transcription_service_url(AppSettings(), selection) == "http://127.0.0.1:8891"


def test_run_dictation_falls_back_to_local_and_surfaces_vm_failure(tmp_path, monkeypatch):
    captured = {"phases": [], "backend_callbacks": []}
    failure = VmBackendFailure(
        "host_key_verification_failed",
        "VM SSH host key did not match.",
        {"known_hosts_path": str(tmp_path / "vm_known_hosts")},
        retryable=False,
    )
    status = VmBackendStatus(
        ready=False,
        progress=(VmBackendProgress("ssh_probe", "Probing VM SSH."),),
        failure=failure,
    )
    selection = BackendSelection(requested_backend="vm", active_backend="local", vm_status=status)

    def fake_record_audio(path, settings, controller, **kwargs):
        path.write_bytes(b"wav")
        return True

    def fake_resolve(settings):
        return selection, settings.service_url

    def fake_transcribe(path, settings, service_url=None):
        captured["service_url"] = service_url
        return {"text": "hello"}

    controller = SimpleNamespace(
        set_phase=lambda phase, **metadata: captured["phases"].append((phase, metadata)),
    )
    monkeypatch.setattr(dictation, "record_audio", fake_record_audio)
    monkeypatch.setattr(dictation, "resolve_transcription_backend", fake_resolve)
    monkeypatch.setattr(dictation, "transcribe", fake_transcribe)

    result = dictation.run_dictation(
        AppSettings(compute_backend="vm", history_enabled=False, delivery_mode="none"),
        controller,
        on_backend_selection=captured["backend_callbacks"].append,
    )

    assert captured["service_url"] == "http://127.0.0.1:8794"
    assert captured["phases"][0] == ("selecting_backend", {"requested_backend": "vm"})
    transcribing_metadata = captured["phases"][1][1]
    assert transcribing_metadata["transcription_backend"]["requested_backend"] == "vm"
    assert transcribing_metadata["transcription_backend"]["active_backend"] == "local"
    assert transcribing_metadata["transcription_backend"]["vm_status"]["failure"]["code"] == (
        "host_key_verification_failed"
    )
    assert captured["backend_callbacks"] == [transcribing_metadata["transcription_backend"]]
    assert result["text"] == "hello"
    assert result["transcription_backend"] == transcribing_metadata["transcription_backend"]


def test_resolve_backend_reports_unavailable_when_vm_and_direct_local_routes_fail(monkeypatch):
    failure = VmBackendFailure("gpu_busy", "GPU is busy.", retryable=True)
    selection = BackendSelection(
        requested_backend="vm",
        active_backend="local",
        vm_status=VmBackendStatus(ready=False, progress=(), failure=failure),
    )

    monkeypatch.setattr(dictation, "select_backend", lambda *args, **kwargs: selection)
    monkeypatch.setattr(dictation, "local_service_is_ready", lambda url: False)

    resolved, service_url = dictation.resolve_transcription_backend(AppSettings(compute_backend="vm"))

    assert resolved.active_backend == "unavailable"
    assert service_url is None


def test_run_dictation_returns_clear_error_when_no_backend_is_available(tmp_path, monkeypatch):
    selection = BackendSelection(
        requested_backend="vm",
        active_backend="unavailable",
        vm_status=VmBackendStatus(ready=False, progress=()),
    )

    def fake_record_audio(path, settings, controller, **kwargs):
        path.write_bytes(b"wav")
        return True

    monkeypatch.setattr(dictation, "record_audio", fake_record_audio)
    monkeypatch.setattr(dictation, "resolve_transcription_backend", lambda settings: (selection, None))

    controller = SimpleNamespace(set_phase=lambda *args, **kwargs: None)
    result = dictation.run_dictation(AppSettings(compute_backend="vm"), controller)

    assert result["transcription_backend"]["active_backend"] == "unavailable"
    assert "local fallback is not running" in result["error"]
