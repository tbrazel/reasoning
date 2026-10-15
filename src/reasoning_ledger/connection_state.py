"""Non-secret, workbench-local readiness state for provider connections."""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

CONNECTION_STATE_VERSION = 1


class ConnectionStateStore:
    """Persist validation and compatibility results, never credentials."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"version": CONNECTION_STATE_VERSION, "connections": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {"version": CONNECTION_STATE_VERSION, "connections": {}}
        if (
            not isinstance(payload, dict)
            or payload.get("version") != CONNECTION_STATE_VERSION
            or not isinstance(payload.get("connections"), dict)
        ):
            return {"version": CONNECTION_STATE_VERSION, "connections": {}}
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()

    def get(self, connection_id: str) -> dict[str, Any]:
        with self._lock:
            value = self._load()["connections"].get(connection_id, {})
            return dict(value) if isinstance(value, dict) else {}

    def update(self, connection_id: str, **values: Any) -> dict[str, Any]:
        with self._lock:
            payload = self._load()
            current = payload["connections"].get(connection_id, {})
            state = dict(current) if isinstance(current, dict) else {}
            state.update(values)
            payload["connections"][connection_id] = state
            self._write(payload)
            return dict(state)

    def clear(self, connection_id: str) -> None:
        with self._lock:
            payload = self._load()
            payload["connections"].pop(connection_id, None)
            self._write(payload)

