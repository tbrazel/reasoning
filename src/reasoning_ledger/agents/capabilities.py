"""Safe discovery and preview of portable Agent Skill packages.

Only directories rooted in explicitly supported skill locations are scanned.
The catalog never walks Codex/Hermes session, authentication, or history
stores: a candidate is a directory whose own root contains ``SKILL.md``.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..canonical import canonical_json
from .skills import SkillRegistry, SkillRegistryError

_MAX_FILES = 512
_MAX_TOTAL_BYTES = 16 * 1024 * 1024
_MAX_FILE_BYTES = 8 * 1024 * 1024
_SECRET_NAMES = {
    ".env",
    "auth.json",
    "credentials.json",
    "credentials.yaml",
    "credentials.yml",
    "id_rsa",
    "id_ed25519",
    "session.json",
    "token.json",
}
_SECRET_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}
_SCRIPT_SUFFIXES = {".bash", ".js", ".mjs", ".py", ".rb", ".sh", ".ts"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class CapabilityRoot:
    kind: str
    label: str
    path: Path


@dataclass(frozen=True)
class CapabilityCandidate:
    source_id: str
    source_kind: str
    source_label: str
    source_path: str


def default_capability_roots() -> tuple[CapabilityRoot, ...]:
    """Known package roots, deliberately excluding state/cache parents."""

    home = Path.home()
    cwd = Path.cwd()
    configured = list(filter(None, os.environ.get("REASONING_CAPABILITY_ROOTS", "").split(os.pathsep)))
    values = (
        []
        if configured
        else [
            CapabilityRoot("codex", "Codex skills", home / ".codex" / "skills"),
            CapabilityRoot("codex-plugin", "Codex plugin skills", home / ".codex" / "plugins" / "cache"),
            CapabilityRoot("hermes", "Hermes skills", home / ".hermes" / "skills"),
            CapabilityRoot("claude", "Claude skills", home / ".claude" / "skills"),
            CapabilityRoot("opencode", "OpenCode skills", home / ".config" / "opencode" / "skills"),
            CapabilityRoot("agent-skills", "Shared agent skills", home / ".agents" / "skills"),
            CapabilityRoot("workspace", "Workspace agent skills", cwd / ".agents" / "skills"),
            CapabilityRoot("workspace", "Workspace OpenCode skills", cwd / ".opencode" / "skills"),
        ]
    )
    for index, raw in enumerate(configured):
        values.append(CapabilityRoot("configured", f"Configured skill root {index + 1}", Path(raw).expanduser()))
    unique: list[CapabilityRoot] = []
    seen: set[Path] = set()
    for value in values:
        resolved = value.path.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(CapabilityRoot(value.kind, value.label, resolved))
    return tuple(unique)


class CapabilityCatalog:
    """Discover, inspect, and resolve packages from an allowlisted root set."""

    def __init__(self, roots: Iterable[CapabilityRoot] | None = None):
        self.roots = tuple(roots) if roots is not None else default_capability_roots()

    @staticmethod
    def _source_id(root: CapabilityRoot, source: Path) -> str:
        identity = f"{root.kind}\0{source.resolve()}"
        return "capsrc_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]

    def _candidates(self) -> list[CapabilityCandidate]:
        candidates: list[CapabilityCandidate] = []
        seen: set[Path] = set()
        for root in self.roots:
            if not root.path.is_dir():
                continue
            try:
                manifests = root.path.rglob("SKILL.md")
                for manifest in manifests:
                    source = manifest.parent
                    try:
                        resolved = source.resolve(strict=True)
                    except OSError:
                        continue
                    if resolved in seen or not resolved.is_relative_to(root.path):
                        continue
                    seen.add(resolved)
                    candidates.append(
                        CapabilityCandidate(
                            source_id=self._source_id(root, resolved),
                            source_kind=root.kind,
                            source_label=root.label,
                            source_path=str(resolved),
                        )
                    )
                    if len(candidates) >= 500:
                        return sorted(candidates, key=lambda item: (item.source_kind, item.source_path))
            except OSError:
                continue
        return sorted(candidates, key=lambda item: (item.source_kind, item.source_path))

    def resolve(self, source_id: str) -> CapabilityCandidate:
        for candidate in self._candidates():
            if candidate.source_id == source_id:
                return candidate
        raise KeyError(f"unknown capability source: {source_id}")

    @staticmethod
    def _manifest(source: Path) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        files: list[dict[str, Any]] = []
        warnings: list[str] = []
        errors: list[str] = []
        total_bytes = 0
        if source.is_symlink():
            errors.append("Package root cannot be a symbolic link.")
            return files, warnings, errors
        try:
            paths = sorted(source.rglob("*"))
        except OSError as error:
            return files, warnings, [f"Cannot inspect package files: {error}"]
        for path in paths:
            relative = path.relative_to(source)
            if any(part in {".git", "__pycache__", ".DS_Store"} for part in relative.parts):
                continue
            if path.is_symlink():
                errors.append(f"Symbolic links are not importable: {relative}")
                continue
            if not path.is_file():
                continue
            try:
                info = path.stat()
            except OSError as error:
                errors.append(f"Cannot inspect {relative}: {error}")
                continue
            total_bytes += info.st_size
            lowered = path.name.lower()
            executable = bool(info.st_mode & stat.S_IXUSR) or path.suffix.lower() in _SCRIPT_SUFFIXES
            secret_like = lowered in _SECRET_NAMES or path.suffix.lower() in _SECRET_SUFFIXES
            files.append(
                {
                    "path": str(relative),
                    "bytes": info.st_size,
                    "executable": executable,
                }
            )
            if secret_like:
                errors.append(f"Secret-like file is not importable: {relative}")
            if info.st_size > _MAX_FILE_BYTES:
                errors.append(f"File exceeds the 8 MiB import limit: {relative}")
        if len(files) > _MAX_FILES:
            errors.append(f"Package has {len(files)} files; the import limit is {_MAX_FILES}.")
        if total_bytes > _MAX_TOTAL_BYTES:
            errors.append("Package exceeds the 16 MiB import limit.")
        script_count = sum(1 for item in files if item["executable"])
        if script_count:
            warnings.append(f"Package contains {script_count} executable or script file(s); review them before granting tools.")
        return files, warnings, errors

    def preview_candidate(self, candidate: CapabilityCandidate) -> dict[str, Any]:
        source = Path(candidate.source_path)
        files, warnings, errors = self._manifest(source)
        frontmatter: dict[str, Any] = {}
        hashes: dict[str, str] = {}
        instructions = ""
        if not errors:
            try:
                frontmatter, instructions, hashes = SkillRegistry.inspect(source)
            except (OSError, UnicodeError, SkillRegistryError) as error:
                errors.append(str(error))
        if frontmatter and not frontmatter.get("license"):
            warnings.append("No license is declared in SKILL.md.")
        content_sha256 = hashlib.sha256(canonical_json(hashes).encode("utf-8")).hexdigest() if hashes else None
        for item in files:
            item["sha256"] = hashes.get(item["path"])
        reasoning = (frontmatter.get("metadata") or {}).get("reasoning") or {}
        requested_tools = reasoning.get("tools") if isinstance(reasoning, dict) else []
        return {
            **asdict(candidate),
            "name": frontmatter.get("name") or source.name,
            "description": frontmatter.get("description") or "",
            "license": frontmatter.get("license"),
            "compatibility": frontmatter.get("compatibility"),
            "allowed_tools": frontmatter.get("allowed-tools"),
            "requested_tools": requested_tools if isinstance(requested_tools, list) else [],
            "instruction_bytes": len(instructions.encode("utf-8")),
            "content_sha256": content_sha256,
            "files": files,
            "total_bytes": sum(int(item["bytes"]) for item in files),
            "warnings": warnings,
            "errors": errors,
            "importable": not errors,
            "compatible_executors": [
                "reasoning-native",
                "hermes-agent",
                "claude-code",
                "opencode",
            ],
        }

    def preview(self, source_id: str) -> dict[str, Any]:
        return self.preview_candidate(self.resolve(source_id))

    def discover(self) -> list[dict[str, Any]]:
        values = []
        for candidate in self._candidates():
            preview = self.preview_candidate(candidate)
            values.append(
                {
                    key: preview[key]
                    for key in (
                        "source_id",
                        "source_kind",
                        "source_label",
                        "source_path",
                        "name",
                        "description",
                        "license",
                        "compatibility",
                        "content_sha256",
                        "total_bytes",
                        "warnings",
                        "errors",
                        "importable",
                    )
                }
            )
        return sorted(values, key=lambda item: (str(item["name"]), str(item["source_path"])))

    @staticmethod
    def provenance(preview: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_id": preview["source_id"],
            "source_kind": preview["source_kind"],
            "source_label": preview["source_label"],
            "source_path": preview["source_path"],
            "content_sha256": preview["content_sha256"],
            "imported_at": _now(),
        }
