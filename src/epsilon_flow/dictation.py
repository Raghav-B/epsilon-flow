"""Recording, local transcription, history, and delivery orchestration."""
from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, TextIO

import requests

from .controller import DictationController
from .delivery import clean_transcript, deliver
from .history import TranscriptHistory
from .settings import AppSettings


AUDIO_LEVEL_PREFIX = "lavfi.astats.Overall.RMS_level="
AUDIO_METER_FILTER = (
    "astats=metadata=1:reset=1,"
    "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-"
)
AudioLevelCallback = Callable[[float], None]


def audio_level_from_metadata(line: str) -> float | None:
    """Map FFmpeg RMS metadata from roughly -60..-12 dB into 0..1."""
    if not line.startswith(AUDIO_LEVEL_PREFIX):
        return None
    raw_value = line.removeprefix(AUDIO_LEVEL_PREFIX).strip()
    try:
        decibels = float(raw_value)
    except ValueError:
        return None
    if not math.isfinite(decibels):
        return 0.0
    return max(0.0, min(1.0, (decibels + 60.0) / 48.0))


def read_audio_levels(stream: TextIO, callback: AudioLevelCallback) -> None:
    for line in stream:
        level = audio_level_from_metadata(line.strip())
        if level is None:
            continue
        try:
            callback(level)
        except Exception:
            # Meter rendering must never interrupt or invalidate a recording.
            continue


def record_audio(
    path: Path,
    settings: AppSettings,
    controller: DictationController,
    max_seconds: int = 3600,
    on_audio_level: AudioLevelCallback | None = None,
) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to record audio")
    input_device = settings.microphone or "default"
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "pulse", "-i", input_device,
        "-t", str(max_seconds), "-ac", "1", "-ar", "16000",
    ]
    if on_audio_level is not None:
        # Derive the visual meter from the exact stream FFmpeg records. This
        # avoids a second microphone capture process, respects the selected
        # PulseAudio source, and works without an optional pw-record binary.
        command.extend(["-af", AUDIO_METER_FILTER])
    command.append(str(path))
    recorder = subprocess.Popen(
        command,
        stdout=subprocess.PIPE if on_audio_level is not None else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    meter_thread = None
    if on_audio_level is not None and recorder.stdout is not None:
        meter_thread = threading.Thread(
            target=read_audio_levels,
            args=(recorder.stdout, on_audio_level),
            daemon=True,
        )
        meter_thread.start()
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
    if meter_thread is not None:
        meter_thread.join(timeout=1)
    if on_audio_level is not None:
        try:
            on_audio_level(0.0)
        except Exception:
            pass
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


def run_dictation(
    settings: AppSettings,
    controller: DictationController,
    on_audio_level: AudioLevelCallback | None = None,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="epsilon-flow-") as directory:
        audio_path = Path(directory) / "dictation.wav"
        if not record_audio(audio_path, settings, controller, on_audio_level=on_audio_level):
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
