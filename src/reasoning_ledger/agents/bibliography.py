"""Workbench-wide bibliography with project and chat views."""

from __future__ import annotations

import fcntl
import hashlib
import json
import mimetypes
import os
import re
import shutil
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..canonical import canonical_json
from .tools import ToolRegistry

_CITATION_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9:_.+/-]{0,127}$")
_ITEM_TYPES = {
    "article",
    "book",
    "incollection",
    "inproceedings",
    "mastersthesis",
    "misc",
    "phdthesis",
    "preprint",
    "techreport",
}
_SUMMARY_KINDS = {"overview", "reading_note", "relevance", "critique"}
_SUMMARY_SCOPES = {"global", "project", "chat"}
_CONNECTION_KINDS = {"claim", "workflow", "run", "artifact", "ledger_reference"}
_FIELDS = (
    "item_type",
    "citation_key",
    "title",
    "authors",
    "year",
    "doi",
    "arxiv_id",
    "url",
    "container_title",
    "publisher",
    "volume",
    "issue",
    "pages",
    "isbn",
    "primary_category",
    "notes",
    "tags",
    "provenance",
)


class BibliographyError(ValueError):
    """A bibliography record or view operation is invalid."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return "".join(character for character in normalized if character.isalnum()).lower()


def _doi(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if result.lower().startswith(prefix):
            result = result[len(prefix) :]
            break
    return result.lower() or None


def _arxiv(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    for prefix in ("https://arxiv.org/abs/", "http://arxiv.org/abs/", "arxiv:"):
        if result.lower().startswith(prefix):
            result = result[len(prefix) :]
            break
    return result or None


def _normalized(value: dict[str, Any]) -> dict[str, Any]:
    title = str(value.get("title") or "").strip()
    authors = value.get("authors") or []
    if isinstance(authors, str):
        authors = [part.strip() for part in authors.split(" and ") if part.strip()]
    authors = [str(author).strip() for author in authors if str(author).strip()]
    if not title or not authors:
        raise BibliographyError("a bibliography item needs a title and at least one author")
    item_type = str(value.get("item_type") or "article").lower()
    if item_type not in _ITEM_TYPES:
        raise BibliographyError(f"unsupported bibliography item type: {item_type}")
    year = value.get("year")
    if year is not None and (type(year) is not int or not 0 <= year <= 9999):
        raise BibliographyError("bibliography year must be an integer from 0 through 9999")
    citation_key = str(value.get("citation_key") or "").strip() or None
    if citation_key and not _CITATION_KEY.fullmatch(citation_key):
        raise BibliographyError("citation key must start with a letter and contain only BibTeX-safe characters")
    tags = sorted({str(tag).strip() for tag in value.get("tags") or [] if str(tag).strip()})
    result: dict[str, Any] = {
        "item_type": item_type,
        "citation_key": citation_key,
        "title": title,
        "authors": authors,
        "year": year,
        "doi": _doi(value.get("doi")),
        "arxiv_id": _arxiv(value.get("arxiv_id")),
        "tags": tags,
        "provenance": value.get("provenance") if isinstance(value.get("provenance"), dict) else {},
    }
    for field in (
        "url",
        "container_title",
        "publisher",
        "volume",
        "issue",
        "pages",
        "isbn",
        "primary_category",
        "notes",
    ):
        raw = value.get(field)
        result[field] = str(raw).strip() if raw is not None and str(raw).strip() else None
    return result


def _identity(value: dict[str, Any]) -> str:
    if value.get("doi"):
        return f"doi:{value['doi']}"
    if value.get("arxiv_id"):
        return f"arxiv:{re.sub(r'v[0-9]+$', '', value['arxiv_id'], flags=re.IGNORECASE).lower()}"
    if value.get("isbn"):
        return f"isbn:{_slug(value['isbn'])}"
    return canonical_json(
        {
            "title": _slug(value["title"]),
            "authors": [_slug(author) for author in value["authors"]],
            "year": value.get("year"),
        }
    )


def _default_key(value: dict[str, Any]) -> str:
    family = value["authors"][0].split(",", 1)[0].split()[-1]
    title_word = next((_slug(word) for word in value["title"].split() if len(_slug(word)) > 2), "work")
    return f"{_slug(family) or 'work'}{value.get('year') or 'nd'}{title_word}"[:96]


def _bibtex_escape(value: Any) -> str:
    text = str(value)
    for source, target in (("%", r"\%"), ("&", r"\&"), ("#", r"\#")):
        text = text.replace(source, target)
    return text.replace("\n", " ").replace("\r", " ")


def _merge_provenance(
    previous: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge discovery history while deduplicating ledger source edges."""
    before = dict(previous or {})
    after = dict(incoming or {})
    merged = {**before, **after}
    sources = {
        str(value).strip()
        for value in (
            *(before.get("sources") or ()),
            *(after.get("sources") or ()),
            before.get("source"),
            after.get("source"),
        )
        if value and str(value).strip()
    }
    if sources:
        merged["sources"] = sorted(sources)
    legacy: dict[str, dict[str, Any]] = {}
    for value in (
        *(before.get("legacy_references") or ()),
        *(after.get("legacy_references") or ()),
    ):
        if isinstance(value, dict):
            legacy[canonical_json(value)] = dict(value)
    if legacy:
        merged["legacy_references"] = [legacy[key] for key in sorted(legacy)]
    return merged


class BibliographyStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.path = self.root / "bibliography.json"
        self.lock_path = self.root / ".bibliography.lock"
        self.resources_root = self.root / "resources"
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _blank() -> dict[str, Any]:
        return {
            "version": 4,
            "items": {},
            "links": {},
            "resources": {},
            "summaries": {},
            "connections": {},
        }

    @contextmanager
    def _locked(self, *, exclusive: bool):
        with self.lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return self._blank()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise BibliographyError("the global bibliography file is unreadable") from error
        if not isinstance(value.get("items"), dict) or not isinstance(value.get("links"), dict):
            raise BibliographyError("the global bibliography file has an invalid structure")
        value.setdefault("resources", {})
        value.setdefault("summaries", {})
        value.setdefault("connections", {})
        value["version"] = 4
        return value

    def _write(self, value: dict[str, Any]) -> None:
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    @staticmethod
    def _unique_key(items: dict[str, Any], requested: str, item_id: str | None = None) -> str:
        occupied = {
            item["citation_key"].lower()
            for identifier, item in items.items()
            if identifier != item_id and item.get("citation_key")
        }
        candidate = requested
        suffix = 2
        while candidate.lower() in occupied:
            candidate = f"{requested}{suffix}"
            suffix += 1
        return candidate

    def upsert(self, value: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        record = _normalized(value)
        identity = _identity(record)
        with self._locked(exclusive=True):
            state = self._read()
            existing_id = next(
                (identifier for identifier, item in state["items"].items() if item.get("identity") == identity),
                None,
            )
            created = existing_id is None
            item_id = existing_id or f"bib_{hashlib.sha256(identity.encode()).hexdigest()[:20]}"
            previous = state["items"].get(item_id, {})
            merged = {
                **previous,
                **{field: record[field] for field in _FIELDS if record.get(field) not in (None, "", [], {})},
                "bibliography_id": item_id,
                "identity": identity,
                "created_at": previous.get("created_at") or _now(),
                "updated_at": _now(),
            }
            requested_key = record.get("citation_key") or previous.get("citation_key") or _default_key(merged)
            merged["citation_key"] = self._unique_key(state["items"], requested_key, item_id)
            merged["tags"] = sorted(set(previous.get("tags") or ()) | set(record.get("tags") or ()))
            merged["provenance"] = _merge_provenance(
                previous.get("provenance"),
                record.get("provenance"),
            )
            state["items"][item_id] = merged
            state["links"].setdefault(item_id, {"projects": {}, "chats": {}})
            self._write(state)
            return dict(merged), created

    def update(self, item_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        with self._locked(exclusive=True):
            state = self._read()
            if item_id not in state["items"]:
                raise KeyError(item_id)
            candidate = {**state["items"][item_id], **changes}
            record = _normalized(candidate)
            identity = _identity(record)
            collision = next(
                (
                    identifier
                    for identifier, item in state["items"].items()
                    if identifier != item_id and item.get("identity") == identity
                ),
                None,
            )
            if collision:
                raise BibliographyError(f"metadata belongs to existing item {collision}")
            previous = state["items"][item_id]
            updated = {
                **previous,
                **record,
                "identity": identity,
                "updated_at": _now(),
            }
            updated["provenance"] = _merge_provenance(
                previous.get("provenance"),
                record.get("provenance"),
            )
            updated["citation_key"] = self._unique_key(
                state["items"], record.get("citation_key") or _default_key(updated), item_id
            )
            state["items"][item_id] = updated
            self._write(state)
            return dict(updated)

    def mirror_ledger_reference(
        self,
        reference: dict[str, Any],
        *,
        project_id: str,
        chat_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Upsert a ledger reference and attach it to its active scopes."""
        reference_id = str(reference.get("reference_id") or "").strip()
        revision_id = str(reference.get("revision_id") or "").strip()
        if not reference_id or not revision_id:
            raise BibliographyError("a mirrored ledger reference needs reference and revision ids")
        legacy_reference = {
            "project_id": project_id,
            "reference_id": reference_id,
            "revision_id": revision_id,
        }
        if chat_id:
            legacy_reference["chat_id"] = chat_id
        mapped = {
            "item_type": (
                "preprint"
                if reference.get("arxiv")
                else ("book" if reference.get("isbn") and not reference.get("doi") else "article")
            ),
            "title": reference.get("title"),
            "authors": reference.get("author"),
            "year": reference.get("year"),
            "arxiv_id": reference.get("arxiv"),
            "url": reference.get("url"),
            "doi": reference.get("doi"),
            "isbn": reference.get("isbn"),
            "notes": reference.get("notes"),
            "provenance": {
                "source": "ledger_reference",
                "legacy_references": [legacy_reference],
            },
        }
        with self._locked(exclusive=False):
            state = self._read()
        existing_id = next(
            (
                item_id
                for item_id, item in state["items"].items()
                if any(
                    value.get("project_id") == project_id
                    and value.get("reference_id") == reference_id
                    for value in (item.get("provenance") or {}).get("legacy_references") or ()
                    if isinstance(value, dict)
                )
            ),
            None,
        )
        if existing_id:
            try:
                item = self.update(existing_id, mapped)
                created = False
            except BibliographyError:
                item, created = self.upsert(mapped)
        else:
            item, created = self.upsert(mapped)
        self.attach(item["bibliography_id"], project_id=project_id)
        if chat_id:
            self.attach(
                item["bibliography_id"],
                project_id=project_id,
                chat_id=chat_id,
            )
        self.add_connection(
            item["bibliography_id"],
            kind="ledger_reference",
            project_id=project_id,
            chat_id=chat_id,
            reference_id=reference_id,
            label=str(reference.get("title") or item["title"]),
            provenance={"revision_id": revision_id, "source": "ledger"},
        )
        return self.get(
            item["bibliography_id"],
            project_id=project_id,
            chat_id=chat_id,
        ), created

    def delete(self, item_id: str) -> dict[str, Any]:
        with self._locked(exclusive=True):
            state = self._read()
            if item_id not in state["items"]:
                raise KeyError(item_id)
            del state["items"][item_id]
            state["links"].pop(item_id, None)
            state["resources"].pop(item_id, None)
            state["summaries"].pop(item_id, None)
            state["connections"].pop(item_id, None)
            self._write(state)
        shutil.rmtree(self.resources_root / item_id, ignore_errors=True)
        return {"bibliography_id": item_id, "deleted": True}

    def add_resource(
        self,
        item_id: str,
        *,
        name: str,
        content: bytes,
        media_type: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Attach one immutable, content-addressed document to a work."""
        clean_name = Path(name).name.strip()
        if not clean_name or clean_name in {".", ".."}:
            raise BibliographyError("a library document needs a filename")
        if not content:
            raise BibliographyError("a library document cannot be empty")
        if len(content) > 100 * 1024 * 1024:
            raise BibliographyError("a library document cannot exceed 100 MB")
        digest = hashlib.sha256(content).hexdigest()
        resource_id = f"res_{digest[:20]}"
        kind = (media_type or mimetypes.guess_type(clean_name)[0] or "application/octet-stream").split(";", 1)[0]
        relative = Path("resources") / item_id / f"{resource_id}-{clean_name}"
        target = self.root / relative
        with self._locked(exclusive=True):
            state = self._read()
            if item_id not in state["items"]:
                raise KeyError(item_id)
            resources = state["resources"].setdefault(item_id, {})
            existing = resources.get(resource_id)
            if existing:
                return dict(existing), False
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_bytes(content)
            os.replace(temporary, target)
            resource = {
                "resource_id": resource_id,
                "name": clean_name,
                "media_type": kind,
                "bytes": len(content),
                "sha256": digest,
                "path": relative.as_posix(),
                "created_at": _now(),
            }
            resources[resource_id] = resource
            self._write(state)
            return dict(resource), True

    def resources(self, item_id: str) -> list[dict[str, Any]]:
        with self._locked(exclusive=False):
            state = self._read()
        if item_id not in state["items"]:
            raise KeyError(item_id)
        values = (state["resources"].get(item_id) or {}).values()
        return sorted((dict(value) for value in values), key=lambda value: (value["created_at"], value["name"]))

    def resource_path(self, item_id: str, resource_id: str) -> tuple[Path, dict[str, Any]]:
        with self._locked(exclusive=False):
            state = self._read()
        if item_id not in state["items"]:
            raise KeyError(item_id)
        resource = (state["resources"].get(item_id) or {}).get(resource_id)
        if resource is None:
            raise KeyError(resource_id)
        target = (self.root / resource["path"]).resolve()
        if not target.is_relative_to(self.resources_root.resolve()) or not target.is_file():
            raise BibliographyError("the library document is missing")
        return target, dict(resource)

    def add_summary(
        self,
        item_id: str,
        *,
        text: str,
        title: str | None = None,
        kind: str = "reading_note",
        scope: str = "global",
        project_id: str | None = None,
        chat_id: str | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append a scoped interpretation without overwriting earlier research."""
        body = text.strip()
        heading = title.strip() if title and title.strip() else None
        if not body:
            raise BibliographyError("a library summary cannot be empty")
        if len(body) > 200_000:
            raise BibliographyError("a library summary cannot exceed 200,000 characters")
        if heading and len(heading) > 300:
            raise BibliographyError("a library summary title cannot exceed 300 characters")
        if kind not in _SUMMARY_KINDS:
            raise BibliographyError(f"unsupported library summary kind: {kind}")
        if scope not in _SUMMARY_SCOPES:
            raise BibliographyError(f"unsupported library summary scope: {scope}")
        if scope == "global" and (project_id or chat_id):
            raise BibliographyError("a global library summary cannot name a project or chat")
        if scope == "project" and (not project_id or chat_id):
            raise BibliographyError("a project library summary needs only a project id")
        if scope == "chat" and (not project_id or not chat_id):
            raise BibliographyError("a chat library summary needs a project and chat id")
        summary_id = f"sum_{uuid.uuid4().hex[:20]}"
        created_at = _now()
        summary = {
            "summary_id": summary_id,
            "kind": kind,
            "scope": scope,
            "title": heading,
            "text": body,
            "project_id": project_id,
            "chat_id": chat_id,
            "provenance": dict(provenance or {}),
            "created_at": created_at,
        }
        with self._locked(exclusive=True):
            state = self._read()
            if item_id not in state["items"]:
                raise KeyError(item_id)
            state["summaries"].setdefault(item_id, {})[summary_id] = summary
            self._write(state)
        return dict(summary)

    def add_connection(
        self,
        item_id: str,
        *,
        kind: str,
        project_id: str,
        chat_id: str | None = None,
        claim_id: str | None = None,
        reference_id: str | None = None,
        workflow_id: str | None = None,
        run_id: str | None = None,
        artifact_path: str | None = None,
        label: str | None = None,
        notes: str | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Index one durable edge from a source into the research workbench."""
        if kind not in _CONNECTION_KINDS:
            raise BibliographyError(f"unsupported library connection kind: {kind}")
        project = project_id.strip()
        if not project:
            raise BibliographyError("a library connection needs a project id")
        targets = {
            "claim": claim_id,
            "ledger_reference": reference_id,
            "workflow": workflow_id,
            "run": run_id,
            "artifact": artifact_path,
        }
        target = str(targets[kind] or "").strip()
        if not target:
            raise BibliographyError(f"a {kind} library connection needs its target id")
        if kind == "artifact":
            path = Path(target)
            if path.is_absolute() or ".." in path.parts:
                raise BibliographyError("a library artifact connection needs a safe relative path")
            artifact_path = path.as_posix()
        identity = canonical_json(
            {
                "kind": kind,
                "project_id": project,
                "chat_id": chat_id,
                "claim_id": claim_id,
                "reference_id": reference_id,
                "workflow_id": workflow_id,
                "run_id": run_id,
                "artifact_path": artifact_path,
            }
        )
        connection_id = f"con_{hashlib.sha256(identity.encode()).hexdigest()[:20]}"
        with self._locked(exclusive=True):
            state = self._read()
            if item_id not in state["items"]:
                raise KeyError(item_id)
            connections = state["connections"].setdefault(item_id, {})
            previous = connections.get(connection_id) or {}
            connection = {
                **previous,
                "connection_id": connection_id,
                "kind": kind,
                "project_id": project,
                "chat_id": chat_id.strip() if chat_id and chat_id.strip() else None,
                "claim_id": claim_id,
                "reference_id": reference_id,
                "workflow_id": workflow_id,
                "run_id": run_id,
                "artifact_path": artifact_path,
                "label": label.strip() if label and label.strip() else previous.get("label"),
                "notes": notes.strip() if notes and notes.strip() else previous.get("notes"),
                "provenance": {**(previous.get("provenance") or {}), **(provenance or {})},
                "created_at": previous.get("created_at") or _now(),
                "updated_at": _now(),
            }
            connections[connection_id] = connection
            self._write(state)
        return dict(connection), not bool(previous)

    def connections(self, item_id: str) -> list[dict[str, Any]]:
        """Return explicit edges plus the source's project/chat attachments."""
        with self._locked(exclusive=False):
            state = self._read()
        if item_id not in state["items"]:
            raise KeyError(item_id)
        result = [
            dict(value)
            for value in (state["connections"].get(item_id) or {}).values()
        ]
        links = state["links"].get(item_id) or {"projects": {}, "chats": {}}
        for project_id, link in (links.get("projects") or {}).items():
            result.append(
                {
                    "connection_id": f"project:{project_id}",
                    "kind": "project",
                    **link,
                }
            )
        for key, link in (links.get("chats") or {}).items():
            result.append(
                {
                    "connection_id": f"chat:{key}",
                    "kind": "chat",
                    **link,
                }
            )
        return sorted(
            result,
            key=lambda value: (
                value.get("project_id") or "",
                value.get("chat_id") or "",
                value.get("kind") or "",
                value.get("created_at") or value.get("added_at") or "",
            ),
        )

    @staticmethod
    def _visible_summaries(
        summaries: dict[str, Any],
        project_id: str | None,
        chat_id: str | None,
    ) -> list[dict[str, Any]]:
        visible = []
        for summary in summaries.values():
            scope = summary.get("scope")
            if (
                scope == "global"
                or (
                    scope == "project"
                    and project_id
                    and summary.get("project_id") == project_id
                )
                or (
                    scope == "chat"
                    and project_id
                    and chat_id
                    and summary.get("project_id") == project_id
                    and summary.get("chat_id") == chat_id
                )
            ):
                visible.append(dict(summary))
        scope_order = {"global": 0, "project": 1, "chat": 2}
        return sorted(
            visible,
            key=lambda value: (scope_order.get(value.get("scope"), 3), value["created_at"]),
        )

    def attach(
        self,
        item_id: str,
        *,
        project_id: str,
        chat_id: str | None = None,
        notes: str | None = None,
        tags: list[str] | tuple[str, ...] = (),
    ) -> dict[str, Any]:
        with self._locked(exclusive=True):
            state = self._read()
            if item_id not in state["items"]:
                raise KeyError(item_id)
            links = state["links"].setdefault(item_id, {"projects": {}, "chats": {}})
            group = links["chats"] if chat_id else links["projects"]
            key = f"{project_id}/{chat_id}" if chat_id else project_id
            previous = group.get(key) or {}
            group[key] = {
                "project_id": project_id,
                "chat_id": chat_id,
                "notes": notes.strip() if notes and notes.strip() else previous.get("notes"),
                "tags": sorted(set(previous.get("tags") or ()) | {tag.strip() for tag in tags if tag.strip()}),
                "added_at": previous.get("added_at") or _now(),
                "updated_at": _now(),
            }
            self._write(state)
            return dict(group[key])

    def detach(self, item_id: str, *, project_id: str, chat_id: str | None = None) -> dict[str, Any]:
        with self._locked(exclusive=True):
            state = self._read()
            links = state["links"].get(item_id)
            if not links:
                raise KeyError(item_id)
            group = links["chats"] if chat_id else links["projects"]
            key = f"{project_id}/{chat_id}" if chat_id else project_id
            if key not in group:
                raise KeyError(key)
            del group[key]
            self._write(state)
        return {"bibliography_id": item_id, "detached": True, "project_id": project_id, "chat_id": chat_id}

    @staticmethod
    def _view(
        item: dict[str, Any],
        links: dict[str, Any],
        resources: dict[str, Any],
        summaries: dict[str, Any],
        project_id: str | None,
        chat_id: str | None,
    ) -> dict[str, Any]:
        project_link = (links.get("projects") or {}).get(project_id) if project_id else None
        chat_link = (links.get("chats") or {}).get(f"{project_id}/{chat_id}") if project_id and chat_id else None
        project_chat_links = [
            link for key, link in (links.get("chats") or {}).items() if project_id and key.startswith(f"{project_id}/")
        ]
        return {
            **item,
            "resources": sorted(
                (dict(value) for value in resources.values()),
                key=lambda value: (value["created_at"], value["name"]),
            ),
            "summaries": BibliographyStore._visible_summaries(
                summaries,
                project_id,
                chat_id,
            ),
            "view": {
                "project_attached": project_link is not None,
                "chat_attached": chat_link is not None,
                "project_chat_count": len(project_chat_links),
                "project": project_link,
                "chat": chat_link,
            },
        }

    def list(
        self,
        *,
        query: str = "",
        project_id: str | None = None,
        chat_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._locked(exclusive=False):
            state = self._read()
        needle = query.strip().casefold()
        result = []
        for item_id, item in state["items"].items():
            links = state["links"].get(item_id) or {"projects": {}, "chats": {}}
            project_linked = project_id in links["projects"] if project_id else True
            project_chat_linked = any(key.startswith(f"{project_id}/") for key in links["chats"]) if project_id else True
            chat_linked = f"{project_id}/{chat_id}" in links["chats"] if project_id and chat_id else False
            if project_id and chat_id and not (project_linked or chat_linked):
                continue
            if project_id and not chat_id and not (project_linked or project_chat_linked):
                continue
            searchable = " ".join(
                str(value)
                for value in (
                    item.get("citation_key"),
                    item.get("title"),
                    " ".join(item.get("authors") or ()),
                    item.get("year"),
                    item.get("doi"),
                    item.get("arxiv_id"),
                    " ".join(item.get("tags") or ()),
                )
                if value is not None
            ).casefold()
            if needle and needle not in searchable:
                continue
            result.append(
                self._view(
                    item,
                    links,
                    state["resources"].get(item_id) or {},
                    state["summaries"].get(item_id) or {},
                    project_id,
                    chat_id,
                )
            )
        return sorted(result, key=lambda item: (-(item.get("year") or 0), item["title"].casefold()))

    def get(self, item_id: str, *, project_id: str | None = None, chat_id: str | None = None) -> dict[str, Any]:
        with self._locked(exclusive=False):
            state = self._read()
        if item_id not in state["items"]:
            raise KeyError(item_id)
        return self._view(
            state["items"][item_id],
            state["links"].get(item_id) or {},
            state["resources"].get(item_id) or {},
            state["summaries"].get(item_id) or {},
            project_id,
            chat_id,
        )

    def bibtex(self, *, project_id: str | None = None, chat_id: str | None = None, query: str = "") -> str:
        blocks = []
        for item in self.list(query=query, project_id=project_id, chat_id=chat_id):
            kind = "misc" if item["item_type"] == "preprint" else item["item_type"]
            fields = [
                ("author", " and ".join(item["authors"])),
                ("title", item["title"]),
                ("year", item.get("year")),
                ("journal", item.get("container_title") if kind == "article" else None),
                ("booktitle", item.get("container_title") if kind in {"inproceedings", "incollection"} else None),
                ("publisher", item.get("publisher")),
                ("volume", item.get("volume")),
                ("number", item.get("issue")),
                ("pages", item.get("pages")),
                ("doi", item.get("doi")),
                ("eprint", item.get("arxiv_id")),
                ("archivePrefix", "arXiv" if item.get("arxiv_id") else None),
                ("primaryClass", item.get("primary_category")),
                ("isbn", item.get("isbn")),
                ("url", item.get("url") or (f"https://arxiv.org/abs/{item['arxiv_id']}" if item.get("arxiv_id") else None)),
                ("note", item.get("notes")),
                ("keywords", ", ".join(item.get("tags") or ())),
            ]
            body = ",\n".join(f"  {name} = {{{_bibtex_escape(value)}}}" for name, value in fields if value not in (None, ""))
            blocks.append(f"@{kind}{{{item['citation_key']},\n{body}\n}}")
        return "\n\n".join(blocks) + ("\n" if blocks else "")


def register_bibliography_tools(
    registry: ToolRegistry,
    store: BibliographyStore,
    *,
    project_id: str,
    chat_id: str | None = None,
) -> None:
    @registry.tool(
        name="bibliography_search",
        description="Search the reusable global bibliography or this project's attached bibliography view.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "maxLength": 1000},
                "scope": {"enum": ["global", "project"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("bibliography:read",),
    )
    def bibliography_search(query: str = "", scope: str = "project", limit: int = 20) -> dict[str, Any]:
        items = store.list(query=query, project_id=project_id if scope == "project" else None)
        return {"scope": scope, "returned": min(len(items), limit), "total": len(items), "items": items[:limit]}

    @registry.tool(
        name="bibliography_get",
        description=(
            "Read one Library source with its metadata, layered summaries, "
            "document inventory, and durable research connections."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "bibliography_id": {"type": "string", "minLength": 1},
                "scope": {"enum": ["global", "project", "chat"]},
            },
            "required": ["bibliography_id"],
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("bibliography:read",),
    )
    def bibliography_get(
        bibliography_id: str,
        scope: str = "project",
    ) -> dict[str, Any]:
        if scope == "chat" and not chat_id:
            raise BibliographyError("this agent does not have a chat Library scope")
        item = store.get(
            bibliography_id,
            project_id=project_id if scope in {"project", "chat"} else None,
            chat_id=chat_id if scope == "chat" else None,
        )
        return {"item": item, "connections": store.connections(bibliography_id)}

    @registry.tool(
        name="bibliography_resource_read",
        description=(
            "Read one attached Library document. UTF-8 text is returned inline; "
            "other formats return their sandbox-visible path and media metadata."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "bibliography_id": {"type": "string", "minLength": 1},
                "resource_id": {"type": "string", "minLength": 1},
                "max_characters": {
                    "type": "integer",
                    "minimum": 1_000,
                    "maximum": 500_000,
                },
            },
            "required": ["bibliography_id", "resource_id"],
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("bibliography:read",),
    )
    def bibliography_resource_read(
        bibliography_id: str,
        resource_id: str,
        max_characters: int = 200_000,
    ) -> dict[str, Any]:
        path, resource = store.resource_path(bibliography_id, resource_id)
        text_suffixes = {
            ".bib",
            ".csv",
            ".json",
            ".jsonl",
            ".lean",
            ".log",
            ".md",
            ".markdown",
            ".py",
            ".tex",
            ".toml",
            ".txt",
            ".yaml",
            ".yml",
        }
        readable = resource["media_type"].startswith("text/") or path.suffix.lower() in text_suffixes
        result = {**resource, "path": str(path), "text_readable": readable}
        if readable:
            text = path.read_text(encoding="utf-8", errors="replace")
            result.update(
                {
                    "text": text[:max_characters],
                    "truncated": len(text) > max_characters,
                }
            )
        return result

    @registry.tool(
        name="bibliography_summary_add",
        description=(
            "Append a provenance-bearing Library overview, reading note, "
            "relevance note, or critique without replacing earlier summaries."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "bibliography_id": {"type": "string", "minLength": 1},
                "kind": {"enum": sorted(_SUMMARY_KINDS)},
                "scope": {"enum": sorted(_SUMMARY_SCOPES)},
                "title": {"type": ["string", "null"], "maxLength": 300},
                "text": {"type": "string", "minLength": 1, "maxLength": 200_000},
                "provenance": {"type": "object"},
            },
            "required": ["bibliography_id", "text"],
            "additionalProperties": False,
        },
        risk="write",
        capabilities=("bibliography:write",),
    )
    def bibliography_summary_add(
        bibliography_id: str,
        text: str,
        kind: str = "overview",
        scope: str = "project",
        title: str | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if scope == "chat" and not chat_id:
            raise BibliographyError("this agent does not have a chat Library scope")
        summary = store.add_summary(
            bibliography_id,
            text=text,
            title=title,
            kind=kind,
            scope=scope,
            project_id=project_id if scope in {"project", "chat"} else None,
            chat_id=chat_id if scope == "chat" else None,
            provenance=provenance,
        )
        return {
            "summary": summary,
            "item": store.get(
                bibliography_id,
                project_id=project_id if scope in {"project", "chat"} else None,
                chat_id=chat_id if scope == "chat" else None,
            ),
        }

    @registry.tool(
        name="bibliography_add",
        description="Add or deduplicate one reusable global bibliography item and attach it to this project.",
        input_schema={
            "type": "object",
            "properties": {
                "item_type": {"enum": sorted(_ITEM_TYPES)},
                "citation_key": {"type": ["string", "null"]},
                "title": {"type": "string", "minLength": 1, "maxLength": 2000},
                "authors": {"type": "array", "minItems": 1, "maxItems": 200, "items": {"type": "string", "minLength": 1}},
                "year": {"type": ["integer", "null"], "minimum": 0, "maximum": 9999},
                "doi": {"type": ["string", "null"]},
                "arxiv_id": {"type": ["string", "null"]},
                "url": {"type": ["string", "null"]},
                "container_title": {"type": ["string", "null"]},
                "publisher": {"type": ["string", "null"]},
                "notes": {"type": ["string", "null"], "maxLength": 10000},
                "tags": {"type": "array", "maxItems": 100, "items": {"type": "string"}},
            },
            "required": ["title", "authors"],
            "additionalProperties": False,
        },
        risk="write",
        capabilities=("bibliography:write",),
    )
    def bibliography_add(**value: Any) -> dict[str, Any]:
        item, created = store.upsert(value)
        store.attach(item["bibliography_id"], project_id=project_id)
        return {"created": created, "item": store.get(item["bibliography_id"], project_id=project_id)}

    @registry.tool(
        name="bibliography_export",
        description="Export the global or project bibliography view as UTF-8 BibTeX.",
        input_schema={
            "type": "object",
            "properties": {"scope": {"enum": ["global", "project"]}, "query": {"type": "string", "maxLength": 1000}},
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("bibliography:read",),
    )
    def bibliography_export(scope: str = "project", query: str = "") -> dict[str, Any]:
        text = store.bibtex(project_id=project_id if scope == "project" else None, query=query)
        return {"format": "bibtex", "scope": scope, "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}
