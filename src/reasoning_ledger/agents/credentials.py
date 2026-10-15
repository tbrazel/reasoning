"""Workbench-local API key storage that never returns secrets to the UI."""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

API_KEY_PROVIDERS = {
    "openrouter": {
        "label": "OpenRouter",
        "environment_variable": "OPENROUTER_API_KEY",
    },
    "anthropic": {
        "label": "Anthropic",
        "environment_variable": "ANTHROPIC_API_KEY",
    },
    "openai": {
        "label": "OpenAI",
        "environment_variable": "OPENAI_API_KEY",
    },
}


class ApiKeyStore:
    """Store user-supplied keys in one owner-readable workbench file."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()

    @staticmethod
    def _provider(provider: str) -> dict[str, str]:
        try:
            return API_KEY_PROVIDERS[provider]
        except KeyError as exc:
            raise KeyError(f"unknown API key provider: {provider}") from exc

    def _load(self) -> dict[str, str]:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            str(provider): str(value)
            for provider, value in payload.items()
            if provider in API_KEY_PROVIDERS and isinstance(value, str) and value
        }

    def _write(self, values: dict[str, str]) -> None:
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

    def get(self, provider: str) -> str | None:
        definition = self._provider(provider)
        stored = self._load().get(provider)
        return stored or os.environ.get(definition["environment_variable"])

    def environment(self) -> dict[str, str]:
        environment: dict[str, str] = {}
        for provider, definition in API_KEY_PROVIDERS.items():
            value = self.get(provider)
            if value:
                environment[definition["environment_variable"]] = value
        return environment

    def statuses(self) -> list[dict[str, str | bool | None]]:
        stored = self._load()
        statuses = []
        for provider, definition in API_KEY_PROVIDERS.items():
            value = stored.get(provider)
            source = "user" if value else None
            if not value:
                value = os.environ.get(definition["environment_variable"])
                source = "environment" if value else None
            statuses.append(
                {
                    "provider": provider,
                    "label": definition["label"],
                    "configured": bool(value),
                    "source": source,
                    "hint": f"••••{value[-4:]}" if value else None,
                }
            )
        return statuses

    def set(self, provider: str, api_key: str) -> None:
        self._provider(provider)
        value = api_key.strip()
        if not value:
            raise ValueError("API key cannot be empty")
        with self._lock:
            values = self._load()
            values[provider] = value
            self._write(values)

    def delete(self, provider: str) -> None:
        self._provider(provider)
        with self._lock:
            values = self._load()
            values.pop(provider, None)
            self._write(values)
