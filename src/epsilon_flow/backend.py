"""Local-only FastAPI service backed by faster-whisper."""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from .backend_logic import ModelConfig, is_cuda_oom, is_loopback_host, resolve_model_config
from .settings import FIXED_MODEL


DEFAULT_MODEL = os.environ.get("EPSILON_FLOW_MODEL", FIXED_MODEL)
DEFAULT_DEVICE = os.environ.get("EPSILON_FLOW_DEVICE", "auto")
DEFAULT_COMPUTE_TYPE = os.environ.get("EPSILON_FLOW_COMPUTE_TYPE", "default")
MAX_UPLOAD_BYTES = int(os.environ.get("EPSILON_FLOW_MAX_UPLOAD_BYTES", str(100 * 1024 * 1024)))
UPLOAD_CHUNK_BYTES = 1024 * 1024


class LoadRequest(BaseModel):
    model: str | None = None
    device: str | None = None
    compute_type: str | None = None


class BackendState:
    def __init__(self) -> None:
        self.model: Any | None = None
        self.config: ModelConfig | None = None
        self.fallback: dict[str, str] | None = None
        self.lock = asyncio.Lock()
        self.loaded_at: float | None = None


app = FastAPI(title="Epsilon Flow Local Transcription", version="0.1.0")
state = BackendState()


def cuda_available() -> bool:
    try:
        result = subprocess.run(["nvidia-smi", "-L"], capture_output=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def fallback_metadata(reason: str) -> dict[str, str]:
    return {
        "reason": reason,
        "requested_device": "auto",
        "from_device": "cuda",
        "to_device": "cpu",
    }


async def load_model(
    request: LoadRequest | None = None,
    *,
    force_cpu: bool = False,
    fallback_reason: str | None = None,
) -> tuple[Any, ModelConfig, dict[str, str] | None]:
    requested = request or LoadRequest()
    requested_device = requested.device or DEFAULT_DEVICE
    effective_device = "cpu" if force_cpu else requested_device
    config = resolve_model_config(
        requested.model or DEFAULT_MODEL,
        effective_device,
        requested.compute_type or DEFAULT_COMPUTE_TYPE,
        cuda_available(),
    )
    is_auto_cuda = requested_device == "auto" and config.device == "cuda"

    async with state.lock:
        if state.model is not None and state.config == config:
            return state.model, config, state.fallback

        # Keep an Auto fallback stable until unload instead of retrying a broken
        # GPU model for every dictation request.
        cpu_config = resolve_model_config(
            config.model,
            "cpu",
            requested.compute_type or DEFAULT_COMPUTE_TYPE,
            cuda_available=False,
        )
        if is_auto_cuda and state.model is not None and state.config == cpu_config and state.fallback:
            return state.model, state.config, state.fallback

        try:
            from faster_whisper import WhisperModel
            model = await asyncio.to_thread(
                WhisperModel,
                config.model,
                device=config.device,
                compute_type=config.compute_type,
            )
        except Exception as exc:
            if not is_auto_cuda:
                raise HTTPException(
                    status_code=500,
                    detail=f"model load failed: {type(exc).__name__}: {exc}",
                ) from exc

            # Auto mode promises a usable backend. A CUDA runtime can be visible
            # while still being unable to load this model, so retry on CPU.
            config = cpu_config
            try:
                model = await asyncio.to_thread(
                    WhisperModel,
                    config.model,
                    device=config.device,
                    compute_type=config.compute_type,
                )
            except Exception as cpu_exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"model load failed after CUDA-to-CPU fallback: {type(cpu_exc).__name__}: {cpu_exc}",
                ) from cpu_exc
            fallback_reason = "cuda_model_load_failed"

        state.model = model
        state.config = config
        state.fallback = fallback_metadata(fallback_reason) if fallback_reason else None
        state.loaded_at = time.time()
        return model, config, state.fallback


async def write_bounded_upload(file: UploadFile, path: Path) -> int:
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"audio upload exceeds {MAX_UPLOAD_BYTES} bytes")

    total = 0
    with path.open("wb") as handle:
        while chunk := await file.read(UPLOAD_CHUNK_BYTES):
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail=f"audio upload exceeds {MAX_UPLOAD_BYTES} bytes")
            handle.write(chunk)
    return total


async def run_transcription(whisper: Any, audio_path: Path, kwargs: dict[str, Any]) -> tuple[list[Any], Any]:
    segments_iter, info = await asyncio.to_thread(whisper.transcribe, str(audio_path), **kwargs)
    segments = await asyncio.to_thread(lambda: list(segments_iter))
    return segments, info


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "model_loaded": state.model is not None,
        "model": state.config.__dict__ if state.config else None,
        "fallback": state.fallback,
    }


@app.get("/models/status")
async def model_status() -> dict[str, Any]:
    return await health()


@app.post("/admin/load")
async def admin_load(request: LoadRequest) -> dict[str, Any]:
    _model, config, fallback = await load_model(request)
    return {"ok": True, "model": config.__dict__, "fallback": fallback, "loaded_at": state.loaded_at}


@app.post("/admin/unload")
async def admin_unload() -> dict[str, bool]:
    async with state.lock:
        state.model = None
        state.config = None
        state.fallback = None
        state.loaded_at = None
    return {"ok": True}


@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: str = Form("auto"),
    initial_prompt: str = Form(""),
    model: str | None = Form(None),
    device: str | None = Form(None),
    compute_type: str | None = Form(None),
) -> dict[str, Any]:
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    descriptor, temporary = tempfile.mkstemp(prefix="epsilon-flow-", suffix=suffix)
    os.close(descriptor)
    audio_path = Path(temporary)
    request = LoadRequest(model=model, device=device, compute_type=compute_type)
    requested_device = device or DEFAULT_DEVICE
    try:
        await write_bounded_upload(file, audio_path)
        whisper, config, fallback = await load_model(request)
        kwargs: dict[str, Any] = {"vad_filter": True, "condition_on_previous_text": False}
        if language and language != "auto":
            kwargs["language"] = language
        if initial_prompt:
            kwargs["initial_prompt"] = initial_prompt
        try:
            segments, info = await run_transcription(whisper, audio_path, kwargs)
        except Exception as exc:
            if requested_device != "auto" or config.device != "cuda" or not is_cuda_oom(exc):
                raise HTTPException(
                    status_code=500,
                    detail=f"transcription failed: {type(exc).__name__}: {exc}",
                ) from exc

            # CUDA can run out of memory after a successful model load. Auto
            # mode retries the complete transcription once on a fresh CPU model.
            whisper, config, fallback = await load_model(
                request,
                force_cpu=True,
                fallback_reason="cuda_oom",
            )
            try:
                segments, info = await run_transcription(whisper, audio_path, kwargs)
            except Exception as cpu_exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"transcription failed after CUDA-to-CPU fallback: {type(cpu_exc).__name__}: {cpu_exc}",
                ) from cpu_exc

        text = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
        return {
            "text": text,
            "language": info.language,
            "language_probability": info.language_probability,
            "device": config.device,
            "compute_type": config.compute_type,
            "model": config.model,
            "fallback": fallback,
        }
    finally:
        await file.close()
        audio_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the localhost Epsilon Flow transcription service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8791)
    args = parser.parse_args()
    if not is_loopback_host(args.host):
        parser.error("--host must be a loopback address")
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
