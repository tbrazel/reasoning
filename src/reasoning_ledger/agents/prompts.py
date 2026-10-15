"""Immutable, content-addressed prompt revisions with exact rendering."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ..canonical import canonical_json

_SLOT = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*}}")
_ID = re.compile(r"[^a-z0-9]+")
PROMPT_KINDS = ("system", "user", "context", "evaluator")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _slug(value: str) -> str:
    return _ID.sub("-", value.lower()).strip("-") or "prompt"


def _render_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class PromptRevision:
    prompt_id: str
    revision_id: str
    parent_revision_id: str | None
    name: str
    kind: str
    description: str
    template: str
    variable_schema: dict[str, Any] = field(default_factory=dict)
    slots: tuple[str, ...] = ()
    created_at: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> PromptRevision:
        return cls(**{**value, "slots": tuple(value.get("slots", ()))})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def render(self, variables: dict[str, Any] | None = None) -> str:
        supplied = variables or {}
        missing = [slot for slot in self.slots if slot not in supplied]
        if missing:
            raise ValueError(f"missing prompt variables: {', '.join(missing)}")
        if self.variable_schema:
            Draft202012Validator(self.variable_schema).validate(supplied)
        return _SLOT.sub(lambda match: _render_value(supplied[match.group(1)]), self.template)


class PromptRegistry:
    """A prompt library whose revisions never change in place."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _directory(self, prompt_id: str, *, active: bool = True) -> Path:
        directory = self.root / prompt_id
        if not (directory / "revisions").is_dir() or (
            active and not (directory / "head.json").is_file()
        ):
            raise KeyError(f"unknown prompt: {prompt_id}")
        return directory

    def _next_id(self, name: str) -> str:
        base = _slug(name)
        candidate = base
        counter = 2
        while (self.root / candidate).exists():
            candidate = f"{base}-{counter}"
            counter += 1
        return candidate

    def create(
        self,
        *,
        name: str,
        kind: str,
        template: str,
        description: str = "",
        variable_schema: dict[str, Any] | None = None,
        prompt_id: str | None = None,
    ) -> PromptRevision:
        prompt_id = prompt_id or self._next_id(name)
        if not name.strip():
            raise ValueError("prompt name is required")
        if kind not in PROMPT_KINDS:
            raise ValueError(f"prompt kind must be one of {', '.join(PROMPT_KINDS)}")
        if (self.root / prompt_id).exists():
            raise ValueError(f"prompt already exists: {prompt_id}")
        directory = self.root / prompt_id
        (directory / "revisions").mkdir(parents=True)
        return self._write_revision(
            prompt_id=prompt_id,
            parent_revision_id=None,
            name=name.strip(),
            kind=kind,
            template=template,
            description=description,
            variable_schema=variable_schema or {},
        )

    def revise(
        self,
        prompt_id: str,
        *,
        template: str,
        name: str | None = None,
        kind: str | None = None,
        description: str | None = None,
        variable_schema: dict[str, Any] | None = None,
        parent_revision_id: str | None = None,
    ) -> PromptRevision:
        current = self.get(prompt_id, parent_revision_id)
        next_kind = kind or current.kind
        if next_kind not in PROMPT_KINDS:
            raise ValueError(f"prompt kind must be one of {', '.join(PROMPT_KINDS)}")
        return self._write_revision(
            prompt_id=prompt_id,
            parent_revision_id=current.revision_id,
            name=(name or current.name).strip(),
            kind=next_kind,
            template=template,
            description=current.description if description is None else description,
            variable_schema=(
                current.variable_schema if variable_schema is None else variable_schema
            ),
        )

    def _write_revision(
        self,
        *,
        prompt_id: str,
        parent_revision_id: str | None,
        name: str,
        kind: str,
        template: str,
        description: str,
        variable_schema: dict[str, Any],
    ) -> PromptRevision:
        Draft202012Validator.check_schema(variable_schema)
        slots = tuple(dict.fromkeys(_SLOT.findall(template)))
        identity = {
            "prompt_id": prompt_id,
            "parent_revision_id": parent_revision_id,
            "name": name,
            "kind": kind,
            "description": description,
            "template": template,
            "variable_schema": variable_schema,
            "slots": slots,
        }
        revision_id = "prmrev_" + hashlib.sha256(
            canonical_json(identity).encode("utf-8")
        ).hexdigest()[:24]
        revision = PromptRevision(
            **identity,
            revision_id=revision_id,
            created_at=_now(),
        )
        directory = self.root / prompt_id
        revision_path = directory / "revisions" / f"{revision_id}.json"
        if not revision_path.exists():
            _atomic_json(revision_path, revision.to_dict())
        _atomic_json(
            directory / "head.json",
            {
                "prompt_id": prompt_id,
                "revision_id": revision_id,
                "name": name,
                "kind": kind,
                "updated_at": _now(),
            },
        )
        return revision

    def get(
        self, prompt_id: str, revision_id: str | None = None
    ) -> PromptRevision:
        # Archived prompts remain resolvable by pinned revision so historical
        # workflow revisions and run manifests never become invalid.
        directory = self._directory(prompt_id, active=revision_id is None)
        if revision_id is None:
            revision_id = json.loads(
                (directory / "head.json").read_text(encoding="utf-8")
            )["revision_id"]
        path = directory / "revisions" / f"{revision_id}.json"
        if not path.is_file():
            raise KeyError(f"unknown prompt revision: {prompt_id}/{revision_id}")
        return PromptRevision.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def revisions(self, prompt_id: str) -> list[PromptRevision]:
        directory = self._directory(prompt_id)
        values = [
            PromptRevision.from_dict(json.loads(path.read_text(encoding="utf-8")))
            for path in (directory / "revisions").glob("*.json")
        ]
        return sorted(values, key=lambda revision: revision.created_at)

    def list(self) -> list[dict[str, Any]]:
        heads = []
        for path in self.root.glob("*/head.json"):
            heads.append(json.loads(path.read_text(encoding="utf-8")))
        return sorted(heads, key=lambda value: (value["name"].lower(), value["prompt_id"]))

    def delete(self, prompt_id: str) -> dict[str, Any]:
        """Archive a prompt head while preserving every immutable revision."""
        directory = self._directory(prompt_id)
        head_path = directory / "head.json"
        head = json.loads(head_path.read_text(encoding="utf-8"))
        tombstone = {**head, "deleted_at": _now()}
        _atomic_json(directory / "deleted.json", tombstone)
        head_path.unlink()
        return tombstone

    def render(
        self,
        prompt_id: str,
        revision_id: str,
        variables: dict[str, Any] | None = None,
    ) -> str:
        return self.get(prompt_id, revision_id).render(variables)
