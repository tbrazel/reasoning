"""Owner-private user MCP connections for workflow agent executors."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class McpConnectionError(ValueError):
    """A user MCP connection is invalid or unavailable."""


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TRANSPORTS = {"stdio", "http", "sse"}
_RESERVED_IDS = {"ledger", "project", "reasoning"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class McpConnectionStore:
    """Persist MCP definitions without returning their secrets to the UI.

    Connections are an explicit per-node grant. Merely saving or enabling a
    connection does not attach it to an agent run.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            str(key): value
            for key, value in payload.items()
            if isinstance(key, str) and isinstance(value, dict)
        }

    def _write(self, values: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(values, output, ensure_ascii=False, indent=2, sort_keys=True)
                output.write("\n")
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _identifier(value: Any) -> str:
        connection_id = str(value or "").strip()
        if not _IDENTIFIER.fullmatch(connection_id):
            raise McpConnectionError(
                "connection id must be 1–64 letters, numbers, hyphens, or underscores"
            )
        if connection_id in _RESERVED_IDS:
            raise McpConnectionError(f"connection id is reserved by Reasoning: {connection_id}")
        return connection_id

    @staticmethod
    def _string_map(value: Any, label: str) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise McpConnectionError(f"{label} must be an object")
        result: dict[str, str] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key).strip()
            if not key or any(character in key for character in "\r\n"):
                raise McpConnectionError(f"{label} contains an invalid name")
            if label == "environment" and not _ENVIRONMENT_NAME.fullmatch(key):
                raise McpConnectionError(f"invalid environment variable name: {key}")
            if not isinstance(raw_value, str) or not raw_value:
                raise McpConnectionError(f"{label} values must be non-empty strings")
            if any(character in raw_value for character in "\x00"):
                raise McpConnectionError(f"{label} contains a null byte")
            result[key] = raw_value
        return result

    @classmethod
    def _normalize(
        cls,
        connection_id: str,
        value: dict[str, Any],
        previous: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        transport = str(value.get("transport") or "stdio").strip().lower()
        if transport not in _TRANSPORTS:
            raise McpConnectionError("transport must be stdio, http, or sse")
        name = str(value.get("name") or "").strip()
        if not name:
            raise McpConnectionError("connection name is required")
        if len(name) > 120:
            raise McpConnectionError("connection name cannot exceed 120 characters")

        args = value.get("args") or []
        if not isinstance(args, list) or any(not isinstance(item, str) for item in args):
            raise McpConnectionError("args must be a list of strings")
        command = str(value.get("command") or "").strip()
        url = str(value.get("url") or "").strip()
        if transport == "stdio":
            if not command or any(character in command for character in "\r\n\x00"):
                raise McpConnectionError("stdio connections need one command executable")
            url = ""
        else:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise McpConnectionError("remote connections need an http or https URL")
            command = ""
            args = []

        previous = previous or {}
        environment = (
            cls._string_map(value["environment"], "environment")
            if "environment" in value
            else dict(previous.get("environment") or {})
        )
        headers = (
            cls._string_map(value["headers"], "headers")
            if "headers" in value
            else dict(previous.get("headers") or {})
        )
        if transport == "stdio":
            headers = {}
        else:
            environment = {}
        created_at = str(previous.get("created_at") or _now())
        return {
            "connection_id": connection_id,
            "name": name,
            "transport": transport,
            "enabled": bool(value.get("enabled", True)),
            "command": command,
            "args": args,
            "url": url,
            "environment": environment,
            "headers": headers,
            "created_at": created_at,
            "updated_at": _now(),
        }

    @staticmethod
    def _public(value: dict[str, Any]) -> dict[str, Any]:
        return {
            "connection_id": value["connection_id"],
            "name": value["name"],
            "transport": value["transport"],
            "enabled": bool(value.get("enabled")),
            "command": value.get("command") or None,
            "args": list(value.get("args") or []),
            "url": value.get("url") or None,
            "environment_names": sorted((value.get("environment") or {}).keys()),
            "header_names": sorted((value.get("headers") or {}).keys()),
            "created_at": value.get("created_at"),
            "updated_at": value.get("updated_at"),
        }

    def list(self) -> list[dict[str, Any]]:
        return sorted(
            (self._public(value) for value in self._load().values()),
            key=lambda value: (str(value["name"]).lower(), value["connection_id"]),
        )

    def get(self, connection_id: str) -> dict[str, Any]:
        connection_id = self._identifier(connection_id)
        try:
            return self._public(self._load()[connection_id])
        except KeyError as exc:
            raise KeyError(f"unknown MCP connection: {connection_id}") from exc

    def set(self, connection_id: str, value: dict[str, Any]) -> dict[str, Any]:
        connection_id = self._identifier(connection_id)
        with self._lock:
            connections = self._load()
            previous = connections.get(connection_id)
            connections[connection_id] = self._normalize(connection_id, value, previous)
            self._write(connections)
            return self._public(connections[connection_id])

    def delete(self, connection_id: str) -> dict[str, Any]:
        connection_id = self._identifier(connection_id)
        with self._lock:
            connections = self._load()
            try:
                removed = connections.pop(connection_id)
            except KeyError as exc:
                raise KeyError(f"unknown MCP connection: {connection_id}") from exc
            self._write(connections)
            return self._public(removed)

    def runtime_config(self, connection_ids: tuple[str, ...]) -> dict[str, dict[str, Any]]:
        """Resolve exact, enabled definitions for one immutable agent run."""
        connections = self._load()
        result: dict[str, dict[str, Any]] = {}
        for raw_id in connection_ids:
            connection_id = self._identifier(raw_id)
            try:
                value = connections[connection_id]
            except KeyError as exc:
                raise McpConnectionError(
                    f"unknown MCP connection selected by this agent: {connection_id}"
                ) from exc
            if not value.get("enabled"):
                raise McpConnectionError(f"MCP connection is disabled: {connection_id}")
            if value["transport"] == "stdio":
                config: dict[str, Any] = {
                    "command": value["command"],
                    "args": list(value.get("args") or []),
                }
                if value.get("environment"):
                    config["env"] = dict(value["environment"])
            else:
                config = {
                    "type": value["transport"],
                    "url": value["url"],
                }
                if value.get("headers"):
                    config["headers"] = dict(value["headers"])
            result[connection_id] = config
        return result

    def requires_network(self, connection_ids: tuple[str, ...]) -> bool:
        connections = self._load()
        for raw_id in connection_ids:
            connection_id = self._identifier(raw_id)
            value = connections.get(connection_id)
            if value and value.get("transport") != "stdio":
                return True
        return False
