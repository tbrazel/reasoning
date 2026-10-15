"""Agent Skills-compatible packages with immutable Reasoning revisions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ..canonical import canonical_json
from .contracts import PreparedSkill, SkillBinding

_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_IGNORED_PARTS = {".git", "__pycache__", ".DS_Store"}


class SkillRegistryError(ValueError):
    """A skill package or immutable revision is invalid."""


def _now() -> str:
    # Revisions may be authored back-to-back in one request sequence; retain
    # enough precision for their public chronological listing to stay stable.
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        if value.startswith("[") and value.endswith("]"):
            return [part.strip().strip("'\"") for part in value[1:-1].split(",") if part.strip()]
        return value.strip("'\"")


def _yaml_mapping(lines: list[str], start: int = 0, indent: int = 0) -> tuple[dict[str, Any], int]:
    """Parse the mapping subset used by Agent Skills frontmatter.

    The open format's standard fields are scalars plus a nested metadata map.
    JSON flow values and literal/folded text blocks are accepted as well.
    """

    result: dict[str, Any] = {}
    index = start
    while index < len(lines):
        raw = lines[index]
        if not raw.strip() or raw.lstrip().startswith("#"):
            index += 1
            continue
        current_indent = len(raw) - len(raw.lstrip(" "))
        if current_indent < indent:
            break
        if current_indent > indent or ":" not in raw:
            raise SkillRegistryError(f"invalid SKILL.md frontmatter line: {raw!r}")
        key, value = raw.strip().split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise SkillRegistryError("empty frontmatter key")
        if value in {"|", ">"}:
            block: list[str] = []
            index += 1
            while index < len(lines):
                child = lines[index]
                child_indent = len(child) - len(child.lstrip(" "))
                if child.strip() and child_indent <= current_indent:
                    break
                block.append(child[current_indent + 2 :] if child.strip() else "")
                index += 1
            result[key] = "\n".join(block) if value == "|" else " ".join(part.strip() for part in block)
            continue
        if not value:
            index += 1
            nested, index = _yaml_mapping(lines, index, current_indent + 2)
            result[key] = nested
            continue
        result[key] = _scalar(value)
        index += 1
    return result, index


def _read_skill_markdown(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillRegistryError("SKILL.md must start with YAML frontmatter")
    try:
        closing = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as exc:
        raise SkillRegistryError("SKILL.md frontmatter is not closed") from exc
    frontmatter, consumed = _yaml_mapping(lines[1:closing])
    if consumed < len(lines[1:closing]):
        raise SkillRegistryError("unsupported SKILL.md frontmatter structure")
    instructions = "\n".join(lines[closing + 1 :]).strip()
    if not instructions:
        raise SkillRegistryError("SKILL.md needs instruction content after frontmatter")
    return frontmatter, instructions


def _source_files(source: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in _IGNORED_PARTS for part in relative.parts):
            continue
        if path.is_symlink():
            raise SkillRegistryError(f"skill packages cannot contain symlinks: {relative}")
        if path.is_file():
            files[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


@dataclass(frozen=True)
class SkillRevision:
    skill_id: str
    revision_id: str
    parent_revision_id: str | None
    name: str
    description: str
    instructions: str
    source_files: dict[str, str]
    metadata: dict[str, Any] = field(default_factory=dict)
    license: str | None = None
    compatibility: str | None = None
    allowed_tools: str | None = None
    created_at: str = ""
    package_path: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SkillRevision:
        return cls(**value)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def reasoning(self) -> dict[str, Any]:
        value = self.metadata.get("reasoning") or {}
        return value if isinstance(value, dict) else {}

    @property
    def tool_dependencies(self) -> tuple[str, ...]:
        tools = self.reasoning.get("tools") or []
        return tuple(tools) if isinstance(tools, list) else ()

    @property
    def required_capabilities(self) -> tuple[str, ...]:
        capabilities = self.reasoning.get("capabilities") or []
        return tuple(capabilities) if isinstance(capabilities, list) else ()


class SkillRegistry:
    """Import, author, pin, and adapt open Agent Skills packages."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.adapters_root = self.root / ".adapters"

    def _directory(self, skill_id: str, *, active: bool = True) -> Path:
        directory = self.root / skill_id
        if not (directory / "revisions").is_dir() or (
            active and not (directory / "head.json").is_file()
        ):
            raise KeyError(f"unknown skill: {skill_id}")
        return directory

    @staticmethod
    def inspect(source: Path | str) -> tuple[dict[str, Any], str, dict[str, str]]:
        source = Path(source)
        if not source.is_dir() or not (source / "SKILL.md").is_file():
            raise SkillRegistryError("skill package must be a directory containing SKILL.md")
        frontmatter, instructions = _read_skill_markdown(source / "SKILL.md")
        name = frontmatter.get("name")
        description = frontmatter.get("description")
        if not isinstance(name, str) or not _SKILL_NAME.fullmatch(name) or len(name) > 64:
            raise SkillRegistryError("skill name must be 1-64 lowercase letters, numbers, and single hyphens")
        if source.name != name:
            raise SkillRegistryError(f"skill directory must match its name: expected {name!r}")
        if not isinstance(description, str) or not description.strip() or len(description) > 1024:
            raise SkillRegistryError("skill description must be 1-1024 characters")
        metadata = frontmatter.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise SkillRegistryError("skill metadata must be a mapping")
        reasoning = metadata.get("reasoning") or {}
        if reasoning and not isinstance(reasoning, dict):
            raise SkillRegistryError("metadata.reasoning must be a mapping")
        for schema_key in ("input_schema", "output_schema"):
            schema = reasoning.get(schema_key) if isinstance(reasoning, dict) else None
            if schema:
                Draft202012Validator.check_schema(schema)
        return frontmatter, instructions, _source_files(source)

    def create(
        self,
        *,
        name: str,
        description: str,
        instructions: str,
        metadata: dict[str, Any] | None = None,
        license: str | None = None,
        compatibility: str | None = None,
        allowed_tools: str | None = None,
    ) -> SkillRevision:
        if (self.root / name).exists():
            raise SkillRegistryError(f"skill already exists: {name}")
        with tempfile.TemporaryDirectory(prefix="reasoning-skill-") as temporary:
            source = Path(temporary) / name
            source.mkdir()
            (source / "SKILL.md").write_text(
                self._markdown(
                    name=name,
                    description=description,
                    instructions=instructions,
                    metadata=metadata or {},
                    license=license,
                    compatibility=compatibility,
                    allowed_tools=allowed_tools,
                ),
                encoding="utf-8",
            )
            return self.import_package(source)

    def revise(
        self,
        skill_id: str,
        *,
        description: str | None = None,
        instructions: str | None = None,
        metadata: dict[str, Any] | None = None,
        license: str | None = None,
        compatibility: str | None = None,
        allowed_tools: str | None = None,
        parent_revision_id: str | None = None,
    ) -> SkillRevision:
        """Author a new immutable package revision from an existing skill."""
        current = self.get(skill_id, parent_revision_id)
        with tempfile.TemporaryDirectory(prefix="reasoning-skill-") as temporary:
            source = Path(temporary) / skill_id
            source.mkdir()
            (source / "SKILL.md").write_text(
                self._markdown(
                    name=skill_id,
                    description=current.description if description is None else description,
                    instructions=current.instructions if instructions is None else instructions,
                    metadata=current.metadata if metadata is None else metadata,
                    license=current.license if license is None else license,
                    compatibility=(
                        current.compatibility if compatibility is None else compatibility
                    ),
                    allowed_tools=(
                        current.allowed_tools if allowed_tools is None else allowed_tools
                    ),
                ),
                encoding="utf-8",
            )
            return self.import_package(
                source, parent_revision_id=current.revision_id
            )

    @staticmethod
    def _markdown(
        *,
        name: str,
        description: str,
        instructions: str,
        metadata: dict[str, Any],
        license: str | None,
        compatibility: str | None,
        allowed_tools: str | None,
    ) -> str:
        fields: list[tuple[str, Any]] = [("name", name), ("description", description)]
        fields.extend(
            (key, value)
            for key, value in (
                ("license", license),
                ("compatibility", compatibility),
                ("allowed-tools", allowed_tools),
            )
            if value is not None
        )
        if metadata:
            fields.append(("metadata", metadata))
        header = "\n".join(
            f"{key}: {json.dumps(value, ensure_ascii=False, separators=(',', ':'))}"
            for key, value in fields
        )
        return f"---\n{header}\n---\n\n{instructions.strip()}\n"

    def import_package(
        self,
        source: Path | str,
        *,
        parent_revision_id: str | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> SkillRevision:
        source = Path(source).resolve()
        frontmatter, instructions, files = self.inspect(source)
        skill_id = str(frontmatter["name"])
        directory = self.root / skill_id
        if directory.exists() and not (directory / "head.json").is_file():
            raise SkillRegistryError(f"skill storage path is not a registry entry: {skill_id}")
        if (directory / "head.json").is_file() and parent_revision_id is None:
            current_id = json.loads(
                (directory / "head.json").read_text(encoding="utf-8")
            )["revision_id"]
            current = self.get(skill_id, current_id)
            same_source = not provenance or (
                current.provenance.get("source_id") == provenance.get("source_id")
                and current.provenance.get("content_sha256")
                == provenance.get("content_sha256")
            )
            if current.source_files == files and same_source:
                return current
            parent_revision_id = current_id
        elif parent_revision_id is not None:
            self.get(skill_id, parent_revision_id)
        identity = {
            "skill_id": skill_id,
            "parent_revision_id": parent_revision_id,
            "source_files": files,
        }
        revision_id = "sklrev_" + hashlib.sha256(
            canonical_json(identity).encode("utf-8")
        ).hexdigest()[:24]
        revision_dir = directory / "revisions" / revision_id
        package_path = revision_dir / "package"
        if not package_path.exists():
            revision_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(
                source,
                package_path,
                ignore=shutil.ignore_patterns(*_IGNORED_PARTS),
            )
        revision = SkillRevision(
            skill_id=skill_id,
            revision_id=revision_id,
            parent_revision_id=parent_revision_id,
            name=skill_id,
            description=str(frontmatter["description"]).strip(),
            instructions=instructions,
            source_files=files,
            metadata=frontmatter.get("metadata") or {},
            license=frontmatter.get("license"),
            compatibility=frontmatter.get("compatibility"),
            allowed_tools=frontmatter.get("allowed-tools"),
            created_at=_now(),
            package_path=str(package_path),
            provenance=provenance or {},
        )
        _atomic_json(revision_dir / "revision.json", revision.to_dict())
        _atomic_json(
            directory / "head.json",
            {
                "skill_id": skill_id,
                "revision_id": revision_id,
                "name": revision.name,
                "description": revision.description,
                "updated_at": _now(),
            },
        )
        return revision

    def get(self, skill_id: str, revision_id: str | None = None) -> SkillRevision:
        # Removed library heads remain addressable by pinned revision so saved
        # workflow revisions and run manifests stay reproducible.
        directory = self._directory(skill_id, active=revision_id is None)
        if revision_id is None:
            revision_id = json.loads(
                (directory / "head.json").read_text(encoding="utf-8")
            )["revision_id"]
        path = directory / "revisions" / revision_id / "revision.json"
        if not path.is_file():
            raise KeyError(f"unknown skill revision: {skill_id}/{revision_id}")
        return SkillRevision.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list(self) -> list[dict[str, Any]]:
        return sorted(
            (
                json.loads(path.read_text(encoding="utf-8"))
                for path in self.root.glob("*/head.json")
            ),
            key=lambda item: item["name"],
        )

    def revisions(self, skill_id: str) -> list[SkillRevision]:
        directory = self._directory(skill_id)
        values = [
            SkillRevision.from_dict(json.loads(path.read_text(encoding="utf-8")))
            for path in (directory / "revisions").glob("*/revision.json")
        ]
        return sorted(values, key=lambda revision: revision.created_at)

    def delete(self, skill_id: str) -> dict[str, Any]:
        """Remove a skill head while preserving every immutable package revision."""
        directory = self._directory(skill_id)
        head_path = directory / "head.json"
        head = json.loads(head_path.read_text(encoding="utf-8"))
        tombstone = {**head, "deleted_at": _now()}
        _atomic_json(directory / "deleted.json", tombstone)
        head_path.unlink()
        return tombstone

    def verify(self, skill_id: str, revision_id: str) -> bool:
        revision = self.get(skill_id, revision_id)
        return _source_files(Path(revision.package_path)) == revision.source_files

    def read_package_file(
        self,
        skill_id: str,
        revision_id: str,
        path: str = "SKILL.md",
    ) -> dict[str, Any]:
        """Read one UTF-8 file from an exact immutable package revision."""
        revision = self.get(skill_id, revision_id)
        package = Path(revision.package_path).resolve()
        target = (package / path).resolve()
        if target == package or not target.is_relative_to(package):
            raise SkillRegistryError("skill file path escapes the package")
        if not target.is_file():
            raise SkillRegistryError(f"unknown skill package file: {path}")
        if target.stat().st_size > 512 * 1024:
            raise SkillRegistryError("skill package file exceeds the 512 KiB read limit")
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeError as error:
            raise SkillRegistryError(f"skill package file is not UTF-8 text: {path}") from error
        return {
            "skill_id": skill_id,
            "revision_id": revision_id,
            "path": str(target.relative_to(package)),
            "content": content,
            "sha256": revision.source_files.get(str(target.relative_to(package))),
        }

    def _claude_plugin(self, revision: SkillRevision) -> Path:
        target = self.adapters_root / revision.revision_id / "claude-plugin"
        skill_target = target / "skills" / revision.name
        if not skill_target.exists():
            skill_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(revision.package_path, skill_target)
            manifest = target / ".claude-plugin" / "plugin.json"
            _atomic_json(
                manifest,
                {
                    "name": f"reasoning-{revision.name}",
                    "version": "0.0.0",
                    "description": revision.description,
                },
            )
        return target

    def prepare(
        self,
        bindings: tuple[SkillBinding, ...],
        *,
        executor_id: str,
    ) -> tuple[PreparedSkill, ...]:
        prepared = []
        for binding in bindings:
            revision = self.get(binding.skill_id, binding.revision_id)
            directory = (
                self._claude_plugin(revision)
                if executor_id == "claude-code"
                else Path(revision.package_path)
            )
            prepared.append(
                PreparedSkill(
                    skill_id=revision.skill_id,
                    revision_id=revision.revision_id,
                    name=revision.name,
                    instructions=revision.instructions,
                    directory=str(directory),
                    description=revision.description,
                    package_directory=revision.package_path,
                )
            )
        return tuple(prepared)
