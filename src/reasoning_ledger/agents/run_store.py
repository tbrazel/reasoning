"""Append-only run event log, artifacts, and resumable checkpoints."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import AgentEvent, AgentEventKind, AgentRunSpec, ArtifactRef, dataclass_dict


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


class RunStore:
    """Durable per-project records for executor-independent workflow runs."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def create(self, spec: AgentRunSpec, *, run_id: str | None = None) -> str:
        run_id = run_id or f"run_{uuid.uuid4().hex}"
        directory = self.root / run_id
        try:
            directory.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise ValueError(f"run already exists: {run_id}") from exc
        (directory / "artifacts").mkdir()
        (directory / "checkpoints").mkdir()
        _atomic_json(
            directory / "manifest.json",
            {
                "run_id": run_id,
                "status": "pending",
                "created_at": _now(),
                "updated_at": _now(),
                "spec": dataclass_dict(spec),
            },
        )
        (directory / "events.jsonl").touch()
        return run_id

    def manifest(self, run_id: str) -> dict[str, Any]:
        path = self.root / run_id / "manifest.json"
        if not path.is_file():
            raise KeyError(f"unknown run: {run_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def update_manifest(self, run_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            manifest = self.manifest(run_id)
            manifest.update(changes)
            manifest["updated_at"] = _now()
            _atomic_json(self.root / run_id / "manifest.json", manifest)
            return manifest

    def append_event(
        self,
        run_id: str,
        kind: AgentEventKind,
        data: dict[str, Any] | None = None,
    ) -> AgentEvent:
        with self._lock:
            path = self.root / run_id / "events.jsonl"
            if not path.is_file():
                raise KeyError(f"unknown run: {run_id}")
            sequence = 1
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                end = handle.tell()
                if end:
                    cursor = end - 1
                    while cursor > 0:
                        handle.seek(cursor)
                        if handle.read(1) == b"\n" and cursor < end - 1:
                            break
                        cursor -= 1
                    handle.seek(cursor + (1 if cursor else 0))
                    last = handle.readline().decode("utf-8").strip()
                    if last:
                        sequence = int(json.loads(last)["sequence"]) + 1
            event = AgentEvent(
                run_id=run_id,
                sequence=sequence,
                kind=kind,
                at=_now(),
                data=data or {},
            )
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return event

    def events(self, run_id: str, *, after: int = 0) -> list[dict[str, Any]]:
        path = self.root / run_id / "events.jsonl"
        if not path.is_file():
            raise KeyError(f"unknown run: {run_id}")
        return [
            event
            for line in path.read_text(encoding="utf-8").splitlines()
            if line and (event := json.loads(line))["sequence"] > after
        ]

    def checkpoint(self, run_id: str, state: dict[str, Any]) -> str:
        checkpoint_id = f"chk_{uuid.uuid4().hex}"
        _atomic_json(
            self.root / run_id / "checkpoints" / f"{checkpoint_id}.json",
            {"checkpoint_id": checkpoint_id, "created_at": _now(), "state": state},
        )
        self.append_event(
            run_id,
            AgentEventKind.CHECKPOINT_CREATED,
            {"checkpoint_id": checkpoint_id},
        )
        return checkpoint_id

    def load_checkpoint(self, run_id: str, checkpoint_id: str) -> dict[str, Any]:
        path = self.root / run_id / "checkpoints" / f"{checkpoint_id}.json"
        if not path.is_file():
            raise KeyError(f"unknown checkpoint: {checkpoint_id}")
        return json.loads(path.read_text(encoding="utf-8"))["state"]

    def add_artifact(
        self,
        run_id: str,
        source: Path | str,
        *,
        kind: str,
        media_type: str | None = None,
        name: str | None = None,
    ) -> ArtifactRef:
        source = Path(source)
        if not source.is_file():
            raise ValueError(f"artifact source is not a file: {source}")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        safe_name = Path(name or source.name).name
        artifact_id = f"art_{digest[:20]}"
        target = self.root / run_id / "artifacts" / f"{artifact_id}-{safe_name}"
        if not target.exists():
            shutil.copyfile(source, target)
        artifact = ArtifactRef(
            artifact_id=artifact_id,
            kind=kind,
            path=str(target),
            media_type=media_type,
            sha256=digest,
        )
        self.append_event(
            run_id,
            AgentEventKind.ARTIFACT_CREATED,
            dataclass_dict(artifact),
        )
        return artifact
