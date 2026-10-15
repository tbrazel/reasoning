"""Reusable, immutable case datasets for workflow inputs and evaluations."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..canonical import canonical_json


class DatasetError(ValueError):
    """A dataset definition or revision operation is invalid."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _safe_identifier(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or not value[0].isalnum()
        or any(not (character.isalnum() or character in "-_") for character in value)
    ):
        raise DatasetError(f"{label} must use only letters, numbers, '-' and '_'")
    return value


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def validate_dataset(dataset: Any) -> dict[str, Any]:
    """Validate the generic dataset payload embedded in a workflow or revision."""
    if not isinstance(dataset, dict):
        raise DatasetError("workflow dataset must be an object")
    name = dataset.get("name")
    if not isinstance(name, str) or not name.strip():
        raise DatasetError("dataset needs a name")
    raw_cases = dataset.get("cases")
    if not isinstance(raw_cases, list) or not 1 <= len(raw_cases) <= 1000:
        raise DatasetError("dataset needs between 1 and 1000 cases")
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_case in enumerate(raw_cases, start=1):
        if not isinstance(raw_case, dict):
            raise DatasetError(f"dataset case {index} must be an object")
        case_id = _safe_identifier(raw_case.get("case_id"), f"dataset case {index} id")
        if case_id in seen:
            raise DatasetError(f"duplicate dataset case id: {case_id}")
        seen.add(case_id)
        case_name = raw_case.get("name")
        if not isinstance(case_name, str) or not case_name.strip():
            raise DatasetError(f"dataset case {case_id} needs a name")
        inputs = raw_case.get("inputs")
        if not isinstance(inputs, dict):
            raise DatasetError(f"dataset case {case_id} inputs must be an object")
        try:
            json.dumps(inputs, ensure_ascii=False, allow_nan=False)
            if "expected" in raw_case:
                json.dumps(raw_case["expected"], ensure_ascii=False, allow_nan=False)
            if "metadata" in raw_case:
                json.dumps(raw_case["metadata"], ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise DatasetError(f"dataset case {case_id} must contain finite JSON values") from error
        case = {
            "case_id": case_id,
            "name": case_name.strip(),
            "inputs": json.loads(json.dumps(inputs, ensure_ascii=False)),
        }
        if "expected" in raw_case:
            case["expected"] = json.loads(json.dumps(raw_case["expected"], ensure_ascii=False))
        if isinstance(raw_case.get("metadata"), dict) and raw_case["metadata"]:
            case["metadata"] = json.loads(json.dumps(raw_case["metadata"], ensure_ascii=False))
        cases.append(case)
    return {
        "kind": "case_dataset_v1",
        "schema_version": 1,
        "name": name.strip(),
        "description": str(dataset.get("description") or ""),
        "cases": cases,
    }


class DatasetStore:
    """Project-local dataset heads over immutable, content-addressed revisions."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def id_for_name(name: str) -> str:
        base = "".join(character if character.isalnum() else "-" for character in name.lower())
        return "-".join(filter(None, base.split("-"))) or "dataset"

    def create(self, name: str, *, description: str = "", cases: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        base = self.id_for_name(name)
        dataset_id = base
        counter = 2
        while (self.root / dataset_id).exists():
            dataset_id = f"{base}-{counter}"
            counter += 1
        dataset = {
            "dataset_id": dataset_id,
            "name": name,
            "description": description,
            "cases": cases or [{"case_id": "case-1", "name": "Case 1", "inputs": {"input": ""}}],
        }
        return self.save(dataset)

    def save(self, dataset: dict[str, Any], *, expected_revision_id: str | None = None) -> dict[str, Any]:
        normalized = validate_dataset(dataset)
        dataset_id = _safe_identifier(dataset.get("dataset_id"), "dataset_id")
        directory = self.root / dataset_id
        head_path = directory / "head.json"
        parent_revision_id = None
        head: dict[str, Any] = {}
        if head_path.is_file():
            head = json.loads(head_path.read_text(encoding="utf-8"))
            parent_revision_id = head["revision_id"]
            if expected_revision_id is not None and expected_revision_id != parent_revision_id:
                raise DatasetError(
                    f"dataset changed since it was loaded: expected {expected_revision_id}, current {parent_revision_id}"
                )
        elif expected_revision_id is not None:
            raise DatasetError("cannot match a revision for a new dataset")
        content = {"dataset_id": dataset_id, **normalized}
        identity = {"parent_revision_id": parent_revision_id, **content}
        revision_id = "dsrev_" + hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()[:24]
        revision = {
            **content,
            "revision_id": revision_id,
            "parent_revision_id": parent_revision_id,
            "created_at": _now(),
        }
        revision_path = directory / "revisions" / f"{revision_id}.json"
        if not revision_path.exists():
            _atomic_json(revision_path, revision)
        _atomic_json(
            head_path,
            {
                "dataset_id": dataset_id,
                "revision_id": revision_id,
                "name": normalized["name"],
                "created_at": head.get("created_at") or _now(),
                "updated_at": _now(),
            },
        )
        return revision

    def get(self, dataset_id: str, revision_id: str | None = None) -> dict[str, Any]:
        dataset_id = _safe_identifier(dataset_id, "dataset_id")
        directory = self.root / dataset_id
        head_path = directory / "head.json"
        if not head_path.is_file():
            raise KeyError(f"unknown dataset: {dataset_id}")
        if revision_id is None:
            revision_id = json.loads(head_path.read_text(encoding="utf-8"))["revision_id"]
        revision_id = _safe_identifier(revision_id, "dataset revision_id")
        path = directory / "revisions" / f"{revision_id}.json"
        if not path.is_file():
            raise KeyError(f"unknown dataset revision: {dataset_id}/{revision_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self) -> list[dict[str, Any]]:
        heads = []
        for path in self.root.glob("*/head.json"):
            try:
                heads.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(heads, key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    def delete(self, dataset_id: str) -> dict[str, Any]:
        dataset_id = _safe_identifier(dataset_id, "dataset_id")
        directory = self.root / dataset_id
        head_path = directory / "head.json"
        if not head_path.is_file():
            raise KeyError(f"unknown dataset: {dataset_id}")
        head = json.loads(head_path.read_text(encoding="utf-8"))
        tombstone = {**head, "deleted_at": _now()}
        _atomic_json(directory / "deleted.json", tombstone)
        head_path.unlink()
        return tombstone
