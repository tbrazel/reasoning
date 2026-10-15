"""Canonical JSON and content-addressed record ids.

UTF-8, lexicographically sorted keys, compact separators, exactly one
trailing newline.
"""

from __future__ import annotations

import hashlib
import json

_ID_HEX_LENGTH = 24


def canonical_json(payload: dict) -> str:
    return (
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        + "\n"
    )


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_id(prefix: str, payload: dict, *, blank_fields: tuple[str, ...]) -> str:
    """Derive an id from the payload with its identity fields blanked.

    The blanked fields are set to empty strings so the hash covers the full
    key set: adding a field later can never collide with an older record
    that merely lacked it.
    """
    basis = dict(payload)
    for field in blank_fields:
        basis[field] = ""
    digest = sha256_hex(canonical_json(basis))
    return f"{prefix}_{digest[:_ID_HEX_LENGTH]}"
