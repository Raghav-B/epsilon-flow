"""Transcript cleanup and delivery selection without desktop UI dependencies."""
from __future__ import annotations

import shutil
import subprocess
import time


def clean_transcript(text: str) -> str:
    cleaned = " ".join(text.split()).strip()
    replacements = {
        "open claw": "OpenClaw",
        "Open Claw": "OpenClaw",
        "epsilon": "Epsilon",
        "codex": "Codex",
        "floramis": "Floramis",
        "raghav": "Raghav",
    }
    for source, replacement in replacements.items():
        cleaned = cleaned.replace(source, replacement)
    return cleaned


def deliver(text: str, mode: str) -> dict[str, str | None]:
    if mode == "none":
        return {"status": "not_requested", "backend": None}
    if mode in {"copy", "paste"}:
        backend = copy_to_clipboard(text)
        if mode == "paste":
            time.sleep(0.15)
            return {"status": "paste_sent", "backend": paste_clipboard()}
        return {"status": "copied", "backend": backend}
    if mode == "type":
        return {"status": "type_sent", "backend": type_text(text)}
    raise ValueError(f"invalid delivery mode: {mode}")


def copy_to_clipboard(text: str) -> str:
    commands = [
        ("wl-copy", ["wl-copy"]),
        ("xclip", ["xclip", "-selection", "clipboard"]),
        ("xsel", ["xsel", "--clipboard", "--input"]),
    ]
    for name, command in commands:
        if shutil.which(name):
            completed = subprocess.run(command, input=text, text=True, check=False)
            if completed.returncode == 0:
                return name
    raise RuntimeError("no clipboard tool found; install wl-clipboard, xclip, or xsel")


def paste_clipboard() -> str:
    commands = [
        ("ydotool", ["ydotool", "key", "ctrl+v"]),
        ("wtype", ["wtype", "-M", "ctrl", "v", "-m", "ctrl"]),
        ("xdotool", ["xdotool", "key", "ctrl+v"]),
    ]
    return _run_first(commands, "no virtual keyboard tool found for paste")


def type_text(text: str) -> str:
    commands = [
        ("wtype", ["wtype", text]),
        ("xdotool", ["xdotool", "type", "--clearmodifiers", "--", text]),
    ]
    return _run_first(commands, "no virtual keyboard tool found for typing")


def _run_first(commands: list[tuple[str, list[str]]], error: str) -> str:
    for name, command in commands:
        if shutil.which(name) and subprocess.run(command, check=False).returncode == 0:
            return name
    raise RuntimeError(error)
