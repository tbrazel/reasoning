"""Bounded private inputs for recoverable host-local initial owner creation."""

from __future__ import annotations

import hashlib
import json
import re

from .store import StoreError

MAX_OWNER_INPUT = 32768
OWNER_FIELDS = {"format", "setup_id", "request_token", "email", "name", "password", "organization_name"}


def owner_input(value):
    # Reuse the public account validation; the host-only random replay
    # credential is distinct from the installation's setup-token file.
    from .api import Bootstrap

    try:
        if (
            not isinstance(value, dict)
            or set(value) != OWNER_FIELDS
            or type(value["format"]) is not int
            or value["format"] != 1
            or not re.fullmatch(r"[a-f0-9]{32}", value["setup_id"])
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value["request_token"])
        ):
            raise ValueError
        body = Bootstrap(**{key: value[key] for key in ("email", "name", "password", "organization_name")}, setup_token="host-local-input")
        result = {
            "format": 1,
            "setup_id": value["setup_id"],
            "request_token": value["request_token"],
            "email": body.email,
            "name": body.name,
            "password": body.password.get_secret_value(),
            "organization_name": body.organization_name,
        }
        encoded = canonical_owner(result)
        if len(encoded) > MAX_OWNER_INPUT:
            raise ValueError
        # Reject normalization drift: the original operator input is immutable.
        if canonical_owner(value) != encoded:
            raise ValueError
        return result
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise StoreError(400, "invalid private initial-owner input") from exc


def canonical_owner(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def owner_binding(value):
    value = owner_input(value)
    return {"setup_id": value["setup_id"], "input_sha256": hashlib.sha256(canonical_owner(value)).hexdigest()}


def owner_result(value, binding):
    try:
        if (
            not isinstance(value, dict)
            or set(value) != {"format", "setup_id", "input_sha256", "organization_id", "user_id", "initialized"}
            or type(value["format"]) is not int
            or value["format"] != 1
            or value["initialized"] is not True
            or any(value[key] != item for key, item in binding.items())
            or not re.fullmatch(r"[a-f0-9]{32}", value["organization_id"])
            or not re.fullmatch(r"[a-f0-9]{32}", value["user_id"])
        ):
            raise ValueError
        return dict(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise StoreError(400, "initial-owner setup did not return an exact secret-free receipt") from exc
