"""OEIS research tools built on the shared scientific gateway."""

from __future__ import annotations

import re
from contextlib import suppress
from typing import Any

from .scientific import ScientificGateway, ScientificToolError
from .tools import ToolRegistry

_A_NUMBER = re.compile(r"^A?([0-9]{1,6})$", re.IGNORECASE)
_OEIS_LICENSE = "CC BY-SA 4.0; subject to the OEIS End-User License Agreement"


def _a_number(value: str) -> str:
    match = _A_NUMBER.fullmatch(value.strip())
    if not match:
        raise ValueError("OEIS identifier must be A followed by up to six digits")
    return f"A{int(match.group(1)):06d}"


def _integers(value: Any) -> list[int]:
    if not isinstance(value, str):
        return []
    values = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            values.append(int(raw))
        except ValueError:
            return []
    return values


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item]
    return []


def _entry(value: dict[str, Any]) -> dict[str, Any]:
    number = value.get("number")
    try:
        identifier = f"A{int(number):06d}"
    except (TypeError, ValueError):
        identifier = str(value.get("id") or "")
    offset = str(value.get("offset") or "")
    offsets = []
    for raw in offset.split(","):
        with suppress(ValueError):
            offsets.append(int(raw.strip()))
    keywords = value.get("keyword") or ""
    if isinstance(keywords, str):
        keywords = [item for item in keywords.split(",") if item]
    return {
        key: item
        for key, item in {
            "a_number": identifier,
            "url": f"https://oeis.org/{identifier}" if identifier else None,
            "name": value.get("name"),
            "terms": _integers(value.get("data")),
            "offset": offsets,
            "keywords": keywords if isinstance(keywords, list) else [],
            "comments": _strings(value.get("comment")),
            "formulas": _strings(value.get("formula")),
            "references": _strings(value.get("reference")),
            "links": _strings(value.get("link")),
            "cross_references": _strings(value.get("xref")),
            "programs": _strings(value.get("program")),
            "author": value.get("author"),
        }.items()
        if item not in (None, "", [])
    }


def _normalize_search(envelope: dict[str, Any], maximum: int) -> dict[str, Any]:
    payload = envelope.get("data")
    if not isinstance(payload, dict):
        raise ScientificToolError("OEIS JSON response is not an object")
    results = payload.get("results") or []
    if not isinstance(results, list):
        raise ScientificToolError("OEIS JSON response has no result list")
    normalized = [_entry(value) for value in results[:maximum] if isinstance(value, dict)]
    envelope["data"] = {
        "count": payload.get("count", len(results)),
        "start": payload.get("start", 0),
        "returned": len(normalized),
        "results": normalized,
    }
    envelope["source"]["attribution"] = "The On-Line Encyclopedia of Integer Sequences (OEIS)"
    envelope["source"]["transformation"] = "Normalized and bounded by Reasoning"
    return envelope


def register_oeis_tools(registry: ToolRegistry, gateway: ScientificGateway) -> None:
    @registry.tool(
        name="oeis_search",
        description=(
            "Search the OEIS by initial integer terms or a text query. Returns cited "
            "candidate entries; a match is evidence for investigation, not a proof."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "terms": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 2,
                    "maxItems": 100,
                },
                "start": {"type": "integer", "minimum": 0, "maximum": 10000},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "anyOf": [{"required": ["query"]}, {"required": ["terms"]}],
            "additionalProperties": False,
        },
        risk="external",
        capabilities=("science:query",),
    )
    def oeis_search(
        query: str | None = None,
        terms: list[int] | None = None,
        start: int = 0,
        max_results: int = 10,
    ) -> dict[str, Any]:
        search = ",".join(str(term) for term in terms) if terms else (query or "").strip()
        if not search:
            raise ValueError("query or terms is required")
        envelope = gateway.get_json(
            provider="OEIS",
            url="https://oeis.org/search",
            query={"q": search, "fmt": "json", "start": start},
            license=_OEIS_LICENSE,
            ttl_seconds=7 * 24 * 60 * 60,
            minimum_interval=1.0,
        )
        return _normalize_search(envelope, max_results)

    @registry.tool(
        name="oeis_lookup",
        description=(
            "Read one exact OEIS entry by A-number with terms, offsets, formulas, "
            "references, cross-references, programs, and source provenance."
        ),
        input_schema={
            "type": "object",
            "properties": {"a_number": {"type": "string", "minLength": 1}},
            "required": ["a_number"],
            "additionalProperties": False,
        },
        risk="external",
        capabilities=("science:query",),
    )
    def oeis_lookup(a_number: str) -> dict[str, Any]:
        identifier = _a_number(a_number)
        envelope = gateway.get_json(
            provider="OEIS",
            url="https://oeis.org/search",
            query={"q": f"id:{identifier}", "fmt": "json"},
            license=_OEIS_LICENSE,
            ttl_seconds=7 * 24 * 60 * 60,
            minimum_interval=1.0,
        )
        normalized = _normalize_search(envelope, 1)
        results = normalized["data"]["results"]
        if not results or results[0].get("a_number") != identifier:
            raise ScientificToolError(f"OEIS entry not found: {identifier}")
        normalized["data"] = results[0]
        return normalized

    @registry.tool(
        name="oeis_bfile",
        description=(
            "Read a bounded prefix of an OEIS b-file for more indexed terms than "
            "the main entry. Preserves the exact source URL and response hash."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "a_number": {"type": "string", "minLength": 1},
                "max_terms": {"type": "integer", "minimum": 1, "maximum": 10000},
            },
            "required": ["a_number"],
            "additionalProperties": False,
        },
        risk="external",
        capabilities=("science:query",),
    )
    def oeis_bfile(a_number: str, max_terms: int = 1000) -> dict[str, Any]:
        identifier = _a_number(a_number)
        envelope = gateway.get_text(
            provider="OEIS",
            url=f"https://oeis.org/{identifier}/b{identifier[1:].lower()}.txt",
            license=_OEIS_LICENSE,
            ttl_seconds=30 * 24 * 60 * 60,
            minimum_interval=1.0,
        )
        pairs = []
        for line in str(envelope["data"]).splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            pieces = stripped.split()
            if len(pieces) != 2:
                continue
            try:
                pairs.append({"index": int(pieces[0]), "value": int(pieces[1])})
            except ValueError:
                continue
            if len(pairs) >= max_terms:
                break
        envelope["data"] = {
            "a_number": identifier,
            "returned": len(pairs),
            "terms": pairs,
        }
        envelope["source"]["attribution"] = "The On-Line Encyclopedia of Integer Sequences (OEIS)"
        envelope["source"]["transformation"] = "Parsed and bounded by Reasoning"
        return envelope
