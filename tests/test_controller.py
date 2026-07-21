import json
import signal

import pytest

from epsilon_flow.controller import DictationController, DictationViewState


def test_controller_serializes_runs_and_tracks_phase(tmp_path):
    first = DictationController(tmp_path)
    second = DictationController(tmp_path)

    assert first.acquire()
    assert not second.acquire()
    first.set_phase("recording", microphone="default")
    status = json.loads(first.status_path.read_text())
    assert status["phase"] == "recording"
    assert status["microphone"] == "default"

    first.release()
    assert second.acquire()
    second.release()


def test_controller_installs_and_restores_signal_handlers(tmp_path, monkeypatch):
    controller = DictationController(tmp_path)
    previous_handlers = {signal.SIGUSR1: object(), signal.SIGUSR2: object()}
    installed = {}

    monkeypatch.setattr(signal, "getsignal", lambda number: previous_handlers[number])
    monkeypatch.setattr(signal, "signal", lambda number, handler: installed.__setitem__(number, handler))

    controller.install_signal_handlers()
    assert set(installed) == {signal.SIGUSR1, signal.SIGUSR2}
    installed[signal.SIGUSR1](signal.SIGUSR1, None)
    installed[signal.SIGUSR2](signal.SIGUSR2, None)
    assert controller.stop_requested
    assert controller.cancel_requested

    controller.restore_signal_handlers()
    assert installed == previous_handlers
    assert controller.previous_signal_handlers == {}


def test_controller_view_state_follows_phase_not_lock_lifetime(tmp_path):
    controller = DictationController(tmp_path)
    assert controller.view_state().mode == "idle"

    controller.handle = object()
    controller.phase = "starting"
    assert controller.view_state() == DictationViewState("recording", "Starting microphone…")

    controller.phase = "recording"
    assert controller.view_state() == DictationViewState("recording", "Recording")
    controller.stop_requested = True
    assert controller.view_state() == DictationViewState("busy", "Finishing recording…")
    controller.stop_requested = False
    controller.cancel_requested = True
    assert controller.view_state() == DictationViewState("busy", "Discarding recording…")

    # Request flags belong to capture. Once the worker advances, visible state
    # must describe the real processing phase rather than resurrect recording.
    controller.phase = "selecting_backend"
    assert controller.view_state() == DictationViewState("busy", "Selecting backend…")
    controller.phase = "transcribing"
    assert controller.view_state() == DictationViewState("busy", "Transcribing…")
    controller.phase = "delivering"
    assert controller.view_state() == DictationViewState("busy", "Delivering transcript…")

    controller.handle = None
    assert controller.view_state() == DictationViewState("idle", "Press to start")


def test_controller_rejects_unknown_phase(tmp_path):
    controller = DictationController(tmp_path)
    assert controller.acquire()
    with pytest.raises(ValueError, match="invalid controller phase"):
        controller.set_phase("done")
    controller.release()
