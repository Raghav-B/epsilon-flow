import json

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


def test_controller_rejects_unknown_phase(tmp_path):
    controller = DictationController(tmp_path)
    assert controller.acquire()
    with pytest.raises(ValueError, match="invalid controller phase"):
        controller.set_phase("done")
    controller.release()
