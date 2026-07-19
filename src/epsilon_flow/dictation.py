"""Recording, local transcription, history, and delivery orchestration."""
from __future__ import annotations

import math
import shutil
import struct
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO, Callable

import requests

from .controller import DictationController
from .delivery import clean_transcript, deliver
from .history import TranscriptHistory
from .settings import AppSettings


PCM_METER_CHUNK_BYTES = 2048
PCM_FULL_SCALE = 32768.0
AudioLevelCallback = Callable[[float], None]


def audio_level_from_pcm(chunk: bytes) -> float | None:
    """Map little-endian signed 16-bit PCM from roughly -60..-12 dB into 0..1."""
    sample_count = len(chunk) // 2
    if sample_count == 0:
        return None
    samples = struct.unpack(f"<{sample_count}h", chunk[: sample_count * 2])
    rms = math.sqrt(sum(sample * sample for sample in samples) / sample_count)
    if rms <= 0:
        return 0.0
    decibels = 20.0 * math.log10(rms / PCM_FULL_SCALE)
    return max(0.0, min(1.0, (decibels + 60.0) / 48.0))


def read_audio_levels(stream: BinaryIO, callback: AudioLevelCallback) -> None:
    pending = b""
    while True:
        chunk = stream.read(PCM_METER_CHUNK_BYTES)
        if not chunk:
            break
        chunk = pending + chunk
        if len(chunk) % 2:
            pending = chunk[-1:]
            chunk = chunk[:-1]
        else:
            pending = b""
        level = audio_level_from_pcm(chunk)
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
        "-t", str(max_seconds), "-map", "0:a:0", "-ac", "1", "-ar", "16000", str(path),
    ]
    if on_audio_level is not None:
        # Duplicate the exact selected FFmpeg input into a live raw-PCM pipe.
        # Metadata output is buffered until capture ends on some FFmpeg builds,
        # whereas this stream gives the listener a level about every 64 ms.
        command.extend([
            "-t", str(max_seconds), "-map", "0:a:0", "-ac", "1", "-ar", "16000",
            "-f", "s16le", "pipe:1",
        ])
    recorder = subprocess.Popen(
        command,
        stdout=subprocess.PIPE if on_audio_level is not None else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        bufsize=0,
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
        error_bytes = recorder.stderr.read() if recorder.stderr else b""
        error = error_bytes.decode("utf-8", errors="replace").strip()
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
