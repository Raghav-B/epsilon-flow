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

from .backend_logic import ModelConfig, is_loopback_host, resolve_model_config


DEFAULT_MODEL = os.environ.get("EPSILON_FLOW_MODEL", "Systran/faster-whisper-large-v3-turbo")
DEFAULT_DEVICE = os.environ.get("EPSILON_FLOW_DEVICE", "auto")
DEFAULT_COMPUTE_TYPE = os.environ.get("EPSILON_FLOW_COMPUTE_TYPE", "default")


class LoadRequest(BaseModel):
    model: str | None = None
    device: str | None = None
    compute_type: str | None = None


class BackendState:
    def __init__(self) -> None:
        self.model: Any | None = None
        self.config: ModelConfig | None = None
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


async def load_model(request: LoadRequest | None = None) -> tuple[Any, ModelConfig]:
    requested = request or LoadRequest()
    config = resolve_model_config(
        requested.model or DEFAULT_MODEL,
        requested.device or DEFAULT_DEVICE,
        requested.compute_type or DEFAULT_COMPUTE_TYPE,
        cuda_available(),
    )
    async with state.lock:
        if state.model is not None and state.config == config:
            return state.model, config
        try:
            from faster_whisper import WhisperModel
            model = await asyncio.to_thread(
                WhisperModel,
                config.model,
                device=config.device,
                compute_type=config.compute_type,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"model load failed: {type(exc).__name__}: {exc}") from exc
        state.model = model
        state.config = config
        state.loaded_at = time.time()
        return model, config


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "model_loaded": state.model is not None, "model": state.config.__dict__ if state.config else None}


@app.get("/models/status")
async def model_status() -> dict[str, Any]:
    return await health()


@app.post("/admin/load")
async def admin_load(request: LoadRequest) -> dict[str, Any]:
    _model, config = await load_model(request)
    return {"ok": True, "model": config.__dict__, "loaded_at": state.loaded_at}


@app.post("/admin/unload")
async def admin_unload() -> dict[str, bool]:
    async with state.lock:
        state.model = None
        state.config = None
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
    try:
        audio_path.write_bytes(await file.read())
        whisper, config = await load_model(LoadRequest(model=model, device=device, compute_type=compute_type))
        kwargs: dict[str, Any] = {"vad_filter": True, "condition_on_previous_text": False}
        if language and language != "auto":
            kwargs["language"] = language
        if initial_prompt:
            kwargs["initial_prompt"] = initial_prompt
        segments_iter, info = await asyncio.to_thread(whisper.transcribe, str(audio_path), **kwargs)
        segments = await asyncio.to_thread(lambda: list(segments_iter))
        text = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
        return {
            "text": text,
            "language": info.language,
            "language_probability": info.language_probability,
            "device": config.device,
            "compute_type": config.compute_type,
            "model": config.model,
        }
    finally:
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
