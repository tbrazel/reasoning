"""Explicit operator configuration for an optional hosted Desktop provider.

No installation, account, or provider endpoint is bundled into the client.
Profiles contain public coordinates, not tokens, passwords, or signing keys.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .desktop_auth import DesktopAuthenticationError


@dataclass(frozen=True)
class DesktopLiveProfile:
    api_origin: str
    login_origin: str
    user_pool_id: str
    desktop_client_id: str
    redirect_uri: str
    account_id: str
    user_id: str
    tenant_id: str
    entitlement_version: str


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DesktopAuthenticationError("Desktop live profile has a duplicate field")
        result[key] = value
    return result


def load_desktop_live_profile(path: Path | None = None) -> DesktopLiveProfile:
    if path is None:
        configured = os.environ.get("REASONING_DESKTOP_PROFILE")
        if not configured:
            raise DesktopAuthenticationError("Desktop provider profile must be explicitly configured")
        path = Path(configured).expanduser()
    source = path
    if not source.is_file() or source.is_symlink() or source.stat().st_size > 4096:
        raise DesktopAuthenticationError("Desktop live profile is unavailable")
    try:
        document = json.loads(source.read_bytes(), object_pairs_hook=_unique)
    except (OSError, ValueError, UnicodeError):
        raise DesktopAuthenticationError("Desktop live profile is invalid") from None
    fields = set(DesktopLiveProfile.__dataclass_fields__)
    if (
        not isinstance(document, dict)
        or set(document) != fields | {"schema_version"}
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or any(not isinstance(document[name], str) or not document[name] for name in fields)
    ):
        raise DesktopAuthenticationError("Desktop live profile has an unsupported shape")
    return DesktopLiveProfile(**{name: document[name] for name in fields})


__all__ = ["DesktopLiveProfile", "load_desktop_live_profile"]
