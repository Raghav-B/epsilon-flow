"""Recording, local transcription, history, and delivery orchestration."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import requests

from .controller import DictationController
from .delivery import clean_transcript, deliver
from .history import TranscriptHistory
from .settings import AppSettings


def record_audio(path: Path, settings: AppSettings, controller: DictationController, max_seconds: int = 3600) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to record audio")
    input_device = settings.microphone or "default"
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "pulse", "-i", input_device,
        "-t", str(max_seconds), "-ac", "1", "-ar", "16000", str(path),
    ]
    recorder = subprocess.Popen(command, stderr=subprocess.PIPE, text=True)
    controller.set_phase("recording")
    while recorder.poll() is None and not controller.stop_requested and not controller.cancel_requested:
        time.sleep(0.08)
    if recorder.poll() is None:
        recorder.terminate()
        try:
            recorder.wait(timeout=5)
        except subprocess.TimeoutExpired:
            recorder.kill()
            recorder.wait(timeout=5)
    if recorder.returncode not in (0, 255, -15):
        error = recorder.stderr.read().strip() if recorder.stderr else ""
        raise RuntimeError(f"microphone recording failed: {error}")
    return not controller.cancel_requested


def transcribe(path: Path, settings: AppSettings) -> dict[str, Any]:
    fields = {
        "language": settings.language,
        "initial_prompt": settings.prompt(),
        "model": settings.model,
        "device": settings.device,
        "compute_type": settings.compute_type,
    }
    with path.open("rb") as handle:
        response = requests.post(
            f"{settings.service_url.rstrip('/')}/transcribe",
            data=fields,
            files={"file": (path.name, handle, "audio/wav")},
            timeout=600,
        )
    response.raise_for_status()
    return response.json()


def run_dictation(settings: AppSettings, controller: DictationController) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="epsilon-flow-") as directory:
        audio_path = Path(directory) / "dictation.wav"
        if not record_audio(audio_path, settings, controller):
            return {"cancelled": True, "text": ""}
        controller.set_phase("transcribing")
        result = transcribe(audio_path, settings)

    transcript = clean_transcript(result.get("text", ""))
    result["text"] = transcript
    if not transcript:
        return result

    entry = None
    history = TranscriptHistory(limit=settings.history_limit)
    if settings.history_enabled:
        entry = history.add(transcript, delivery_status="pending")

    controller.set_phase("delivering")
    try:
        delivery = deliver(transcript, settings.delivery_mode)
    except RuntimeError as exc:
        delivery = {"status": "delivery_failed", "backend": None, "error": str(exc)}
    result["delivery"] = delivery
    if entry:
        history.update(entry["id"], delivery_status=delivery["status"], delivery_backend=delivery.get("backend"))
    return result
