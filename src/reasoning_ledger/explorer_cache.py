"""Versioned, atomic bootstrap snapshot for the workbench explorer."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXPLORER_INDEX_VERSION = 1


class ExplorerIndexCache:
    """Persist the last canonical project index without making it canonical.

    The explorer renders this snapshot immediately at process start, then the
    normal ``/api/projects`` discovery replaces it. Invalid or future-version
    files are ignored so a cache problem can never prevent the workbench from
    opening.
    """

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> dict[str, Any] | None:
        with self._lock:
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                return None
        if not isinstance(value, dict) or value.get("version") != EXPLORER_INDEX_VERSION:
            return None
        projects = value.get("projects")
        generated_at = value.get("generated_at")
        if not isinstance(projects, list) or not isinstance(generated_at, str):
            return None
        if any(not isinstance(project, dict) for project in projects):
            return None
        return value

    def write(self, projects: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {
            "version": EXPLORER_INDEX_VERSION,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "projects": projects,
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary: Path | None = None
            try:
                handle, name = tempfile.mkstemp(
                    prefix=f".{self.path.name}.",
                    suffix=".tmp",
                    dir=self.path.parent,
                )
                temporary = Path(name)
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.path)
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()
        return payload

