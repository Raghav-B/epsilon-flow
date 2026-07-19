"""Private, bounded transcript recovery history."""
from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from .settings import state_dir


class TranscriptHistory:
    def __init__(self, directory: Path | None = None, limit: int = 30) -> None:
        if limit <= 0:
            raise ValueError("history limit must be greater than zero")
        self.directory = Path(directory or state_dir())
        self.path = self.directory / "history.json"
        self.limit = limit

    def load(self) -> list[dict[str, Any]]:
        self._prepare()
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [entry for entry in payload if isinstance(entry, dict)] if isinstance(payload, list) else []

    def add(self, text: str, **metadata: Any) -> dict[str, Any]:
        entry = {"id": uuid.uuid4().hex, "created_at": time.time(), "text": text, **metadata}
        self.write([entry, *self.load()])
        return entry

    def update(self, entry_id: str, **changes: Any) -> dict[str, Any] | None:
        entries = self.load()
        updated = None
        for entry in entries:
            if entry.get("id") == entry_id:
                entry.update(changes)
                updated = entry
                break
        if updated:
            self.write(entries)
        return updated

    def clear(self) -> None:
        self.write([])

    def write(self, entries: list[dict[str, Any]]) -> None:
        self._prepare()
        descriptor, temporary = tempfile.mkstemp(dir=self.directory, prefix="history-", suffix=".json")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(entries[: self.limit], handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _prepare(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
