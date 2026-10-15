"""Workbench-local storage for recently selected and pinned provider models."""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

MAX_RECENT_MODELS = 12
MAX_PINNED_MODELS = 48
MODEL_PROVIDERS = ("openrouter",)


class RecentModelStore:
    """Keep a bounded, most-recent-first model list for each provider."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()

    @staticmethod
    def _provider(provider: str) -> str:
        if provider not in MODEL_PROVIDERS:
            raise KeyError(f"unknown model provider: {provider}")
        return provider

    @staticmethod
    def _normalize(values: object) -> list[str]:
        if not isinstance(values, list):
            return []
        normalized: list[str] = []
        for value in values:
            if not isinstance(value, str):
                continue
            model_id = value.strip()
            if model_id and model_id not in normalized:
                normalized.append(model_id)
            if len(normalized) >= MAX_RECENT_MODELS:
                break
        return normalized

    def _load(self) -> dict[str, list[str]]:
        if not self.path.is_file():
            return {provider: [] for provider in MODEL_PROVIDERS}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return {
            provider: self._normalize(payload.get(provider, []))
            for provider in MODEL_PROVIDERS
        }

    def _write(self, values: dict[str, list[str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as output:
                json.dump(values, output, ensure_ascii=False, indent=2, sort_keys=True)
                output.write("\n")
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def list(self) -> dict[str, list[str]]:
        return self._load()

    def remember(self, provider: str, model_id: str) -> dict[str, list[str]]:
        provider = self._provider(provider)
        selected = model_id.strip()
        if not selected:
            raise ValueError("model ID cannot be empty")
        with self._lock:
            values = self._load()
            values[provider] = [
                selected,
                *(model for model in values[provider] if model != selected),
            ][:MAX_RECENT_MODELS]
            self._write(values)
            return values


class PinnedModelStore(RecentModelStore):
    """Keep a user-ordered collection of durable model shortcuts."""

    @staticmethod
    def _normalize(values: object) -> list[str]:
        if not isinstance(values, list):
            return []
        normalized: list[str] = []
        for value in values:
            if not isinstance(value, str):
                continue
            model_id = value.strip()
            if model_id and model_id not in normalized:
                normalized.append(model_id)
            if len(normalized) >= MAX_PINNED_MODELS:
                break
        return normalized

    def replace(self, provider: str, model_ids: list[str]) -> dict[str, list[str]]:
        provider = self._provider(provider)
        selected = self._normalize(model_ids)
        with self._lock:
            values = self._load()
            values[provider] = selected
            self._write(values)
            return values
