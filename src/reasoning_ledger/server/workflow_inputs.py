"""Bounded inert JSON transport for workflow dependency results."""

from __future__ import annotations

import hashlib
import json
import math
import re

from .compute_catalog import canonical

MAX_INPUT_BYTES = 65536


def input_bytes(value: dict) -> bytes:
    if (
        not isinstance(value, dict)
        or len(value) > 32
        or any(not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key) for key in value)
    ):
        raise ValueError("invalid workflow input aliases")

    def check(item, depth=0):
        if depth > 8:
            raise ValueError("workflow input nesting exceeds eight levels")
        if isinstance(item, dict):
            if len(item) > 128 or any(not isinstance(key, str) or len(key) > 200 for key in item):
                raise ValueError("invalid workflow input object")
            for key, child in item.items():
                key.encode("utf-8")
                check(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > 128:
                raise ValueError("workflow input array exceeds 128 items")
            for child in item:
                check(child, depth + 1)
        elif isinstance(item, str):
            if len(item.encode("utf-8")) > MAX_INPUT_BYTES:
                raise ValueError("workflow input exceeds 64 KiB")
        elif item is not None and type(item) not in {int, float, bool}:
            raise ValueError("workflow inputs require JSON values")
        elif type(item) is float and not math.isfinite(item):
            raise ValueError("workflow inputs require finite numbers")

    check(value)
    encoded = canonical(value).encode("utf-8")
    if len(encoded) > MAX_INPUT_BYTES:
        raise ValueError("workflow inputs exceed 64 KiB")
    return encoded


def read_inputs(source: str, expected_digest: str) -> dict:
    """Validate a persisted capture before exposing or accepting its contents."""
    try:
        if not isinstance(source, str) or len(source.encode("utf-8")) > MAX_INPUT_BYTES:
            raise ValueError("invalid input capture")
        value = json.loads(source)
        encoded = input_bytes(value)
        if encoded.decode("utf-8") != source or expected_digest != "sha256:" + hashlib.sha256(encoded).hexdigest():
            raise ValueError("invalid input capture")
        return value
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("workflow input integrity check failed") from exc
