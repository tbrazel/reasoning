"""Workbench research tools using the organization's canonical knowledge ledger."""

from __future__ import annotations

from ..organization_ledger import OrganizationLedger, import_provenance
from .tools import ToolRegistry


def register_organization_ledger_tools(registry: ToolRegistry, ledger: OrganizationLedger) -> None:
    paging = {"limit": {"type": "integer", "minimum": 1, "maximum": 100}, "before": {"type": ["string", "null"]}}

    def inventory(page):
        return {
            **page,
            "entries": [
                {**entry, "body": entry["body"][:800], "body_characters": len(entry["body"]), "body_complete": len(entry["body"]) <= 800}
                for entry in page["entries"]
            ],
        }

    def receipt(entry):
        return {
            key: entry[key] for key in ("id", "organization_id", "kind", "title", "state", "revision", "revision_by", "revision_by_kind")
        }

    @registry.tool(
        name="ledger_status",
        description="Read a page from the organization's canonical research ledger; follow next_cursor for more.",
        input_schema={"type": "object", "properties": {"claim_id": {"type": ["string", "null"]}, **paging}, "additionalProperties": False},
        risk="read",
        capabilities=("ledger:read",),
    )
    def ledger_status(claim_id: str | None = None, limit: int = 100, before: str | None = None):
        if claim_id is not None and len(claim_id) == 32:
            return {"authority": "organization", "entry": receipt(ledger.read(claim_id))}
        return inventory(ledger.listing(query=claim_id or "", limit=limit, before=before))

    @registry.tool(
        name="ledger_search",
        description="Search the organization's canonical ledger. Original workbench record IDs remain searchable.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 200}, **paging},
            "required": ["text"],
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("ledger:read",),
    )
    def ledger_search(text: str, limit: int = 100, before: str | None = None):
        return inventory(ledger.listing(query=text, limit=limit, before=before))

    @registry.tool(
        name="ledger_read",
        description=(
            "Read a bounded chunk of an exact ledger revision. Follow next_offset with the returned revision to read the complete body."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "entry_id": {"type": "string", "pattern": "^[a-f0-9]{32}$"},
                "revision": {"type": ["integer", "null"], "minimum": 1},
                "offset": {"type": "integer", "minimum": 0},
                "max_characters": {"type": "integer", "minimum": 1, "maximum": 12000},
            },
            "required": ["entry_id"],
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("ledger:read",),
    )
    def ledger_read(entry_id: str, revision: int | None = None, offset: int = 0, max_characters: int = 12000):
        entry = ledger.read(entry_id, revision)
        body = entry["body"]
        end = offset + max_characters
        return {
            "authority": "organization",
            "entry": {**entry, "body": body[offset:end]},
            "offset": offset,
            "body_characters": len(body),
            "body_complete": end >= len(body),
            "next_offset": end if end < len(body) else None,
        }

    @registry.tool(
        name="ledger_graph",
        description="Read a page of canonical ledger nodes and preserved source-record links; follow next_cursor for more.",
        input_schema={"type": "object", "properties": paging, "additionalProperties": False},
        risk="read",
        capabilities=("ledger:read",),
    )
    def ledger_graph(limit: int = 100, before: str | None = None):
        page = ledger.listing(limit=limit, before=before)
        nodes, edges = [], []
        for entry in page["entries"]:
            provenance = import_provenance(entry)
            node = {"id": entry["id"], "title": entry["title"], "state": entry["state"], "revision": entry["revision"]}
            if provenance is not None:
                node["original_id"] = provenance["original_id"]
                record = provenance["record"]
                for field in ("claim_id", "reference_id", "current_revision_id", "parent_revision_id", "target_revision_id"):
                    if record.get(field):
                        edges.append({"source": entry["id"], "target_original_id": record[field], "relation": field})
                for premise in record.get("premises", []):
                    if premise.get("revision_id"):
                        edges.append({"source": entry["id"], "target_original_id": premise["revision_id"], "relation": "premise"})
            nodes.append(node)
        return {"authority": "organization", "nodes": nodes, "edges": edges, "next_cursor": page["next_cursor"]}

    fields = {
        "title": {"type": "string", "minLength": 1, "maxLength": 300},
        "body": {"type": "string", "minLength": 1, "maxLength": 64000},
        "state": {"type": "string", "enum": ["note", "conjecture"]},
        "client_id": {"type": "string", "minLength": 16, "maxLength": 128, "pattern": "^[A-Za-z0-9_-]+$"},
    }

    @registry.tool(
        name="ledger_note",
        description="Publish an attributed note or conjecture to the canonical shared ledger. Reuse client_id after uncertainty.",
        input_schema={"type": "object", "properties": fields, "required": ["title", "body", "client_id"], "additionalProperties": False},
        risk="write",
        capabilities=("ledger:write",),
    )
    def ledger_note(title: str, body: str, client_id: str, state: str = "note"):
        return {"authority": "organization", "entry": receipt(ledger.write(title=title, body=body, client_id=client_id, state=state))}

    @registry.tool(
        name="ledger_revise",
        description="Append a proposed revision to shared knowledge using the last-read revision as a concurrency check.",
        input_schema={
            "type": "object",
            "properties": {
                **fields,
                "entry_id": {"type": "string", "pattern": "^[a-f0-9]{32}$"},
                "expected_revision": {"type": "integer", "minimum": 1},
            },
            "required": ["title", "body", "client_id", "entry_id", "expected_revision"],
            "additionalProperties": False,
        },
        risk="write",
        capabilities=("ledger:write",),
    )
    def ledger_revise(title: str, body: str, client_id: str, entry_id: str, expected_revision: int, state: str = "note"):
        return {
            "authority": "organization",
            "entry": receipt(
                ledger.write(
                    title=title, body=body, client_id=client_id, state=state, entry_id=entry_id, expected_revision=expected_revision
                )
            ),
        }
