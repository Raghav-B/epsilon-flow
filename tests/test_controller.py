import json
import signal

import pytest

from epsilon_flow.controller import DictationController


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


def test_controller_rejects_unknown_phase(tmp_path):
    controller = DictationController(tmp_path)
    assert controller.acquire()
    with pytest.raises(ValueError, match="invalid controller phase"):
        controller.set_phase("done")
    controller.release()
