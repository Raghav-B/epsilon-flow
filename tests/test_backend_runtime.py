import asyncio
import io
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile

from epsilon_flow import backend
from epsilon_flow.backend_logic import ModelConfig


def reset_backend_state():
    backend.state.model = None
    backend.state.config = None
    backend.state.fallback = None
    backend.state.loaded_at = None


def test_auto_model_load_falls_back_to_cpu(monkeypatch):
    reset_backend_state()
    calls = []

    class WhisperModel:
        def __init__(self, _model, *, device, compute_type):
            calls.append((device, compute_type))
            if device == "cuda":
                raise RuntimeError("CUDA initialization failed")

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=WhisperModel))
    monkeypatch.setattr(backend, "cuda_available", lambda: True)

    _model, config, fallback = asyncio.run(backend.load_model(backend.LoadRequest(device="auto")))

    assert calls == [("cuda", "float16"), ("cpu", "int8")]
    assert config.device == "cpu"
    assert fallback == {
        "reason": "cuda_model_load_failed",
        "requested_device": "auto",
        "from_device": "cuda",
        "to_device": "cpu",
    }


def test_explicit_cuda_model_load_does_not_fallback(monkeypatch):
    reset_backend_state()
    calls = []

    class WhisperModel:
        def __init__(self, _model, *, device, compute_type):
            calls.append((device, compute_type))
            raise RuntimeError("CUDA initialization failed")

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=WhisperModel))
    monkeypatch.setattr(backend, "cuda_available", lambda: True)

    with pytest.raises(HTTPException, match="model load failed"):
        asyncio.run(backend.load_model(backend.LoadRequest(device="cuda")))
    assert calls == [("cuda", "float16")]


def test_upload_is_rejected_above_configured_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "MAX_UPLOAD_BYTES", 4)
    upload = UploadFile(io.BytesIO(b"12345"), filename="audio.wav", size=None)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(backend.write_bounded_upload(upload, tmp_path / "audio.wav"))
    assert caught.value.status_code == 413


def test_explicit_cuda_transcription_oom_does_not_fallback(monkeypatch):
    reset_backend_state()
    cuda_model = object()
    load_calls = []

    async def fake_load_model(_request, *, force_cpu=False, fallback_reason=None):
        load_calls.append((force_cpu, fallback_reason))
        return cuda_model, ModelConfig("small.en", "cuda", "float16"), None

    async def fake_run_transcription(_model, _path, _kwargs):
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(backend, "load_model", fake_load_model)
    monkeypatch.setattr(backend, "run_transcription", fake_run_transcription)
    upload = UploadFile(io.BytesIO(b"audio"), filename="audio.wav", size=5)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            backend.transcribe(
                file=upload,
                language="auto",
                initial_prompt="",
                model="small.en",
                device="cuda",
                compute_type="default",
            )
        )

    assert caught.value.status_code == 500
    assert load_calls == [(False, None)]


def test_auto_transcription_retries_cuda_oom_on_cpu(monkeypatch):
    reset_backend_state()
    cuda_model = object()
    cpu_model = object()
    load_calls = []
    transcription_calls = []

    async def fake_load_model(_request, *, force_cpu=False, fallback_reason=None):
        load_calls.append((force_cpu, fallback_reason))
        if force_cpu:
            return cpu_model, ModelConfig("small.en", "cpu", "int8"), backend.fallback_metadata("cuda_oom")
        return cuda_model, ModelConfig("small.en", "cuda", "float16"), None

    async def fake_run_transcription(model, _path, _kwargs):
        transcription_calls.append(model)
        if model is cuda_model:
            raise RuntimeError("CUDA out of memory")
        segment = SimpleNamespace(text=" recovered text ")
        info = SimpleNamespace(language="en", language_probability=0.99)
        return [segment], info

    monkeypatch.setattr(backend, "load_model", fake_load_model)
    monkeypatch.setattr(backend, "run_transcription", fake_run_transcription)
    upload = UploadFile(io.BytesIO(b"audio"), filename="audio.wav", size=5)

    result = asyncio.run(
        backend.transcribe(
            file=upload,
            language="auto",
            initial_prompt="",
            model="small.en",
            device="auto",
            compute_type="default",
        )
    )

    assert transcription_calls == [cuda_model, cpu_model]
    assert load_calls == [(False, None), (True, "cuda_oom")]
    assert result["text"] == "recovered text"
    assert result["device"] == "cpu"
    assert result["fallback"]["reason"] == "cuda_oom"
