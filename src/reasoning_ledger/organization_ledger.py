"""Canonical organization ledger access and explicit workbench imports."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode

from .canonical import canonical_json
from .deployment.host import InstallError
from .deployment.private import locked_directory, protected_parent, read_document, write_document
from .records import validate_record
from .server.bot_client import BotClient, BotClientError, load_configuration

MARKER = "<!-- reasoning-ledger-import:v1 -->"
LINK_FIELDS = {"format", "config_file", "organization_id", "scope_kind", "scope_id"}


def link_path(root: Path | str) -> Path:
    return Path(root).absolute() / ".organization-ledger.json"


def linked_configuration(root: Path | str) -> dict | None:
    path = link_path(root)
    if not path.exists() and not path.is_symlink():
        return None
    value = read_document(path)
    if (
        set(value) != LINK_FIELDS
        or type(value["format"]) is not int
        or value["format"] != 1
        or not isinstance(value["config_file"], str)
        or not Path(value["config_file"]).is_absolute()
        or not isinstance(value["organization_id"], str)
        or not re.fullmatch(r"[a-f0-9]{32}", value["organization_id"])
        or value["scope_kind"] not in {"organization", "project", "topic"}
        or (value["scope_kind"] == "organization") != (value["scope_id"] is None)
        or value["scope_id"] is not None
        and not re.fullmatch(r"[a-f0-9]{32}", value["scope_id"])
    ):
        raise InstallError("invalid organization ledger link; local writes remain disabled")
    return value


class OrganizationLedger:
    def __init__(
        self,
        config_file: Path | str,
        *,
        organization_id: str | None = None,
        scope_kind: str = "organization",
        scope_id: str | None = None,
        client_factory=None,
    ):
        self.config_file = Path(config_file).absolute()
        self.organization_id = organization_id
        self.scope_kind, self.scope_id = scope_kind, scope_id
        self.client_factory = client_factory or BotClient
        if scope_kind not in {"organization", "project", "topic"} or (scope_kind == "organization") != (scope_id is None):
            raise BotClientError("organization ledger requires a valid scope")
        if scope_id is not None and not re.fullmatch(r"[a-f0-9]{32}", scope_id):
            raise BotClientError("invalid organization ledger scope identifier")

    @contextmanager
    def client(self):
        client = self.client_factory(load_configuration(self.config_file))
        try:
            identity = client.identity()
            if self.organization_id is not None and identity["organization_id"] != self.organization_id:
                raise BotClientError("bot configuration no longer belongs to the linked organization")
            if identity["enabled"] is not True or "knowledge:read" not in identity["scopes"]:
                raise BotClientError("organization ledger requires enabled bot access with knowledge:read")
            yield client, identity
        finally:
            client.close()

    def public_binding(self) -> dict:
        config = load_configuration(self.config_file)
        return {
            "linked": True,
            "server": config["server"],
            "organization_id": self.organization_id,
            "scope_kind": self.scope_kind,
            "scope_id": self.scope_id,
        }

    def listing(self, *, kind: str = "ledger", query: str = "", limit: int = 100, before: str | None = None) -> dict:
        if kind not in {"ledger", "bibliography", "journal"} or type(limit) is not int or not 1 <= limit <= 500:
            raise BotClientError("invalid organization ledger page")
        if not isinstance(query, str) or len(query) > 200:
            raise BotClientError("organization ledger queries must contain at most 200 characters")
        params = {"kind": kind, "q": query, "limit": limit}
        if before is not None:
            params["before"] = before
        with self.client() as (client, _identity):
            entries = client._request("GET", "/api/bot/knowledge?" + urlencode(params))
        if not isinstance(entries, list) or len(entries) > limit:
            raise BotClientError("invalid organization ledger page")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not re.fullmatch(r"[a-f0-9]{32}", entry["id"]):
                raise BotClientError("invalid organization ledger entry")
        cursor = f"{entries[-1]['revised_at']}:{entries[-1]['id']}" if len(entries) == limit else None
        return {"authority": "organization", "entries": entries, "next_cursor": cursor}

    def read(self, entry_id: str, revision: int | None = None) -> dict:
        if not re.fullmatch(r"[a-f0-9]{32}", entry_id):
            raise BotClientError("invalid organization ledger entry identifier")
        path = "/api/bot/knowledge/" + entry_id
        if revision is not None:
            path += "?" + urlencode({"revision": revision})
        with self.client() as (client, _identity):
            entry = client._request("GET", path)
        if not isinstance(entry, dict) or entry.get("id") != entry_id:
            raise BotClientError("invalid organization ledger entry")
        return entry

    def write(
        self,
        *,
        title: str,
        body: str,
        client_id: str,
        state: str = "note",
        entry_id: str | None = None,
        expected_revision: int | None = None,
        kind: str = "ledger",
    ) -> dict:
        if not isinstance(title, str) or not isinstance(body, str):
            raise BotClientError("ledger title and body must be text")
        title, body = title.strip(), body.strip()
        if not title or len(title) > 300 or not body or len(body) > 64_000:
            raise BotClientError("ledger title or body is empty or too large")
        if kind == "ledger" and state not in {"note", "conjecture"}:
            raise BotClientError("agents may propose notes or conjectures; establishment/refutation requires human review")
        request = {"kind": kind, "title": title, "body": body, "state": state, "client_id": client_id}
        if entry_id is None:
            request.update(scope_kind=self.scope_kind, scope_id=self.scope_id)
            path, method = "/api/bot/knowledge", "POST"
        else:
            if not re.fullmatch(r"[a-f0-9]{32}", entry_id) or type(expected_revision) is not int:
                raise BotClientError("revisions require an entry identifier and the last-read revision")
            request["expected_revision"] = expected_revision
            path, method = "/api/bot/knowledge/" + entry_id, "PUT"
        if len(json.dumps(request, ensure_ascii=False).encode()) > 64_000:
            raise BotClientError("ledger write exceeds the server's bounded request size")
        with self.client() as (client, identity):
            if "knowledge:write" not in identity["scopes"]:
                raise BotClientError("organization ledger requires knowledge:write for publishing")
            result = client._request(method, path, request)
        if not isinstance(result, dict) or result.get("kind") != kind or result.get("title") != title or result.get("body") != body:
            raise BotClientError("invalid ledger write acknowledgment; retry with the same client_id")
        return result


def linked_ledger(root: Path | str) -> OrganizationLedger | None:
    value = linked_configuration(root)
    if value is None:
        return None
    return OrganizationLedger(
        value["config_file"], organization_id=value["organization_id"], scope_kind=value["scope_kind"], scope_id=value["scope_id"]
    )


def link_ledger(
    root: Path | str, config_file: Path | str, *, scope_kind: str = "organization", scope_id: str | None = None, client_factory=None
) -> dict:
    ledger = OrganizationLedger(config_file, scope_kind=scope_kind, scope_id=scope_id, client_factory=client_factory)
    with ledger.client() as (client, identity):
        client._request("GET", "/api/bot/knowledge?kind=ledger&limit=1")
        if scope_kind == "topic" and scope_id not in {topic["id"] for topic in client.topics()}:
            raise BotClientError("organization ledger topic scope requires an exact bot topic grant")
        organization_id = identity["organization_id"]
    path = link_path(root)
    value = {
        "format": 1,
        "config_file": str(Path(config_file).absolute()),
        "organization_id": organization_id,
        "scope_kind": scope_kind,
        "scope_id": scope_id,
    }
    from .store import LedgerStore

    store = LedgerStore(root)
    with store._lock(read_only=True):
        if path.exists() or path.is_symlink():
            if linked_configuration(root) != value:
                raise BotClientError("this ledger already has a different organization link")
        else:
            write_document(path, value, new=True)
        if (store.root / ".git").exists():
            exclude = Path(store._git("rev-parse", "--git-path", "info/exclude"))
            if not exclude.is_absolute():
                exclude = store.root / exclude
            protected_parent(exclude.absolute())
            fd = os.open(exclude, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "a+b") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022 or info.st_nlink != 1:
                    raise InstallError("ledger Git exclusions must be an owner-owned protected regular file")
                stream.seek(0)
                if b".organization-ledger.json" not in stream.read(65536).splitlines():
                    stream.write(b"\n.organization-ledger.json\n")
                    stream.flush()
                    os.fsync(stream.fileno())
    return {"linked": True, "organization_id": organization_id, "link_file": str(path), "local_records_retained": True}


def import_provenance(entry: dict) -> dict | None:
    body = entry.get("body", "")
    if MARKER not in body:
        return None
    try:
        encoded = body.split(MARKER + "\n```json\n", 1)[1].rsplit("\n```", 1)[0]
        value = json.loads(encoded)
        return value if isinstance(value, dict) and value.get("format") == 1 else None
    except (IndexError, ValueError):
        return None


def import_local_ledger(store, ledger: OrganizationLedger, source_label: str, journal_dir: Path) -> dict:
    """Publish every immutable local record; exact request IDs recover lost receipts."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", source_label):
        raise BotClientError("source label must contain 1–100 letters, digits, underscores, or hyphens")
    with store._lock(read_only=True):
        commit = store.head_commit()
        if commit is None or store._git("status", "--porcelain"):
            raise BotClientError("import requires a clean committed source ledger")
        source = []
        for parent in (store.claims_dir, store.references_dir):
            for path in sorted(parent.rglob("*.json")):
                if path.is_symlink() or not path.is_file():
                    raise BotClientError("import source records must be regular files")
                record = json.loads(path.read_bytes())
                relative = str(path.relative_to(store.root))
                if "verifications/" in relative:
                    schema = "verification"
                elif relative.startswith("references/"):
                    schema = "reference-head" if path.name == "head.json" else "reference-revision"
                elif "/proofs/" in relative:
                    schema = "proof-head" if path.name == "head.json" else "proof-revision"
                else:
                    schema = "claim-head" if path.name == "head.json" else "claim-revision"
                validate_record(record, schema)
                source.append((relative, schema, record))
    requests = []
    for relative, schema, record in source:
        original_id = (
            record.get("revision_id")
            or record.get("verification_id")
            or record.get("proof_id")
            or record.get("claim_id")
            or record.get("reference_id")
        )
        identity = hashlib.sha256((source_label + ":" + relative + ":" + commit).encode()).hexdigest()
        provenance = {
            "format": 1,
            "source_label": source_label,
            "source_commit": commit,
            "source_path": relative,
            "schema": schema,
            "original_id": original_id,
            "record": record,
            "record_sha256": hashlib.sha256(canonical_json(record).encode()).hexdigest(),
        }
        text = record.get("statement_text") or record.get("title") or (record.get("body") or {}).get("text") or schema
        body = str(text) + "\n\n" + MARKER + "\n```json\n" + json.dumps(provenance, ensure_ascii=True, sort_keys=True) + "\n```"
        request = {
            "title": (schema + " · " + str(original_id))[:300],
            "body": body,
            "client_id": "import_" + identity,
            "kind": "bibliography" if schema.startswith("reference") else "ledger",
            "state": "active" if schema.startswith("reference") else "note",
        }
        wire = {**request, "scope_kind": ledger.scope_kind, "scope_id": ledger.scope_id}
        if len(json.dumps(wire, ensure_ascii=False).encode()) > 64_000 or len(body) > 64_000:
            raise BotClientError("source record exceeds the server import size limit: " + relative)
        requests.append((relative, request))
    with ledger.client() as (_client, identity):
        binding = {
            "server": load_configuration(ledger.config_file)["server"],
            "organization_id": identity["organization_id"],
            "bot_id": identity["id"],
            "source_label": source_label,
            "source_commit": commit,
            "scope_kind": ledger.scope_kind,
            "scope_id": ledger.scope_id,
        }
    with locked_directory(journal_dir) as directory:
        path = directory / "import.json"
        if path.exists():
            state = read_document(path)
            if state.get("binding") != binding:
                raise BotClientError("import journal belongs to different source, organization, bot, or scope")
        else:
            state = {"format": 1, "binding": binding, "receipts": {}}
            write_document(path, state, new=True)
        for relative, request in requests:
            if relative in state["receipts"]:
                continue
            result = ledger.write(**request)
            state["receipts"][relative] = {"entry_id": result["id"], "revision": result["revision"], "client_id": request["client_id"]}
            write_document(path, state)
    return {
        "imported_records": len(requests),
        "source_commit": commit,
        "source_label": source_label,
        "organization_id": identity["organization_id"],
        "journal_dir": str(journal_dir.absolute()),
        "local_records_retained": True,
    }
