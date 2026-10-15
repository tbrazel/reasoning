"""Safe public diagnostics for OpenRouter and its upstream providers."""

from __future__ import annotations

import json
import re
import socket
import ssl
import urllib.error
from functools import lru_cache
from typing import Any

import certifi

_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(user(?:_id)?|request_id|trace_id|endpoint_id|region|api_key|authorization)"
    r"\b\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)"
)
_OPENROUTER_HTTP_PREFIX = re.compile(r"(?is)^OpenRouter returned HTTP\s+(\d+)\s*:\s*(.*)$")


@lru_cache(maxsize=1)
def openrouter_ssl_context() -> ssl.SSLContext:
    """Use the CA bundle shipped with Desktop instead of a host Python path."""

    return ssl.create_default_context(cafile=certifi.where())


def openrouter_transport_error_message(error: BaseException, *, catalog: bool = False) -> str:
    """Turn local/network failures into useful prose without leaking internals."""

    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    subject = "model catalog" if catalog else "request"
    if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(reason):
        connection = "the OpenRouter model catalog's secure connection" if catalog else "OpenRouter's secure connection"
        return (
            f"Reasoning could not verify {connection}. This is a local certificate "
            "or network-interception problem, not an expired API key. Check the system date and "
            "VPN or filtering proxy, then update Reasoning and try again."
        )
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return f"The OpenRouter {subject} timed out. Check the network connection and try again."
    return f"Reasoning could not reach the OpenRouter {subject}. Check the network connection and try again."


def _provider_error_messages(value: Any) -> list[str]:
    """Extract public provider messages without retaining routing metadata."""

    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return []
        try:
            nested = json.loads(candidate)
        except json.JSONDecodeError:
            return [candidate]
        return _provider_error_messages(nested)
    if not isinstance(value, dict):
        return []

    messages: list[str] = []
    for key in ("message", "detail", "error"):
        candidate = value.get(key)
        if isinstance(candidate, (str, dict)):
            messages.extend(_provider_error_messages(candidate))

    # OpenRouter sometimes wraps the upstream provider's public error body in
    # metadata.raw. Parse that body, but never persist the surrounding metadata
    # (which can include user, route, endpoint, or region identifiers).
    metadata = value.get("metadata")
    if isinstance(metadata, dict) and "raw" in metadata:
        messages.extend(_provider_error_messages(metadata["raw"]))
    return messages


def openrouter_http_error_message(status_code: int, detail: str) -> str:
    """Reduce one HTTP body to bounded, actionable, non-routing prose."""

    try:
        payload: Any = json.loads(detail)
    except json.JSONDecodeError:
        payload = detail
    messages = _provider_error_messages(payload)
    unique: list[str] = []
    for message in messages:
        compact = " ".join(message.split())
        if compact and compact not in unique:
            unique.append(compact)
    if not unique:
        return f"OpenRouter returned HTTP {status_code}."
    public_detail = sanitize_provider_error_message("; ".join(unique))[:1200].rstrip()
    return f"OpenRouter returned HTTP {status_code}: {public_detail}"


def sanitize_provider_error_message(message: str) -> str:
    """Bound and redact a provider error, collapsing embedded JSON when possible."""

    value = " ".join(str(message).split())
    matched = _OPENROUTER_HTTP_PREFIX.match(value)
    if matched:
        return openrouter_http_error_message(int(matched.group(1)), matched.group(2))

    decoder = json.JSONDecoder()
    for index, character in enumerate(value):
        if character != "{":
            continue
        try:
            payload, _end = decoder.raw_decode(value[index:])
        except json.JSONDecodeError:
            continue
        public = _provider_error_messages(payload)
        if public:
            prefix = _SENSITIVE_ASSIGNMENT.sub(r"\1=[redacted]", value[:index]).strip(" :-")
            detail = "; ".join(dict.fromkeys(" ".join(item.split()) for item in public))
            value = f"{prefix}: {detail}" if prefix else detail
            break
    return _SENSITIVE_ASSIGNMENT.sub(r"\1=[redacted]", value)[:1600].rstrip()
