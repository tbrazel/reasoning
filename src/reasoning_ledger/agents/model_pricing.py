"""Live, bounded model-price catalogs for the workbench UI."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .openrouter_errors import openrouter_http_error_message, openrouter_ssl_context, openrouter_transport_error_message


class ModelPricingError(RuntimeError):
    """A provider's model-price catalog could not be loaded."""


JsonFetcher = Callable[[str, Mapping[str, str], float], Any]


def _request_json(url: str, headers: Mapping[str, str], timeout: float) -> Any:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=openrouter_ssl_context()) as response:
            body = response.read(8 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", errors="replace")
        raise ModelPricingError(openrouter_http_error_message(exc.code, detail)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ModelPricingError(openrouter_transport_error_message(exc, catalog=True)) from exc
    if len(body) > 8 * 1024 * 1024:
        raise ModelPricingError("OpenRouter price catalog exceeds 8 MiB")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelPricingError("OpenRouter returned an invalid price catalog") from exc


def _per_million(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return float(amount * 1_000_000)


def _unit_price(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return float(amount)


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 else None


def _iso_timestamp(value: object) -> str | None:
    seconds = _integer(value)
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds, UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _model_record(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    model_id = value.get("id")
    if not isinstance(model_id, str) or not model_id.strip():
        return None
    model_id = model_id.strip()
    pricing = value.get("pricing") if isinstance(value.get("pricing"), dict) else {}
    architecture = value.get("architecture") if isinstance(value.get("architecture"), dict) else {}
    top_provider = value.get("top_provider") if isinstance(value.get("top_provider"), dict) else {}
    parameters = value.get("supported_parameters")
    supported_parameters = (
        sorted(str(parameter) for parameter in parameters if isinstance(parameter, str)) if isinstance(parameters, list) else []
    )
    description = value.get("description")
    if not isinstance(description, str):
        description = ""
    description = description.strip()
    if len(description) > 1_200:
        description = description[:1_197].rstrip() + "…"
    input_modalities = architecture.get("input_modalities")
    output_modalities = architecture.get("output_modalities")
    modality = architecture.get("modality") if isinstance(architecture.get("modality"), str) else None
    hugging_face_id = value.get("hugging_face_id")
    hugging_face_id = (
        None
        if not isinstance(hugging_face_id, str) or not hugging_face_id.strip()
        else hugging_face_id.strip()[:300]
    )
    if modality and "->" in modality:
        advertised_input, advertised_output = modality.split("->", 1)
        if not isinstance(input_modalities, list):
            input_modalities = [item.strip() for item in advertised_input.replace(",", "+").split("+") if item.strip()]
        if not isinstance(output_modalities, list):
            output_modalities = [item.strip() for item in advertised_output.replace(",", "+").split("+") if item.strip()]
    return {
        "model_id": model_id,
        "canonical_slug": (value.get("canonical_slug") if isinstance(value.get("canonical_slug"), str) else model_id),
        "name": (value.get("name").strip() if isinstance(value.get("name"), str) and value.get("name").strip() else model_id),
        "provider": model_id.partition("/")[0],
        "hugging_face_id": hugging_face_id,
        "source_availability": "open_weight" if hugging_face_id else "unreported",
        "description": description,
        "context_length": _integer(value.get("context_length")),
        "max_output_tokens": _integer(top_provider.get("max_completion_tokens")),
        "created_at": _iso_timestamp(value.get("created")),
        "expiration_date": (value.get("expiration_date") if isinstance(value.get("expiration_date"), str) else None),
        "modality": modality,
        "input_modalities": [str(item) for item in input_modalities if isinstance(item, str)] if isinstance(input_modalities, list) else [],
        "output_modalities": [str(item) for item in output_modalities if isinstance(item, str)]
        if isinstance(output_modalities, list)
        else [],
        "supported_parameters": supported_parameters,
        "supports_tools": "tools" in supported_parameters,
        "supports_reasoning": bool({"reasoning", "include_reasoning"}.intersection(supported_parameters)),
        "input_mtok_usd": _per_million(pricing.get("prompt")),
        "output_mtok_usd": _per_million(pricing.get("completion")),
        "cache_read_mtok_usd": _per_million(pricing.get("input_cache_read")),
        "cache_write_mtok_usd": _per_million(pricing.get("input_cache_write")),
        "reasoning_mtok_usd": _per_million(pricing.get("internal_reasoning")),
        "request_usd": _unit_price(pricing.get("request")),
        "image_usd": _unit_price(pricing.get("image")),
        "web_search_usd": _unit_price(pricing.get("web_search")),
    }


class ModelPriceCatalog:
    """Fetch OpenRouter's live catalog with a short in-memory cache."""

    def __init__(
        self,
        api_key: Callable[[], str | None],
        *,
        base_url: str | None = None,
        fetch_json: JsonFetcher | None = None,
        ttl_seconds: float = 900,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._api_key = api_key
        self._base_url = (base_url or os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")
        self._fetch_json = fetch_json or _request_json
        self._ttl_seconds = ttl_seconds
        self._monotonic = monotonic
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def _cache_key(self, scope: str, api_key: str | None) -> str:
        if scope == "all":
            return "all"
        fingerprint = hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()
        return f"account:{fingerprint}"

    def fetch(self, *, scope: str = "all", refresh: bool = False) -> dict[str, Any]:
        if scope not in {"all", "account"}:
            raise ValueError("price scope must be 'all' or 'account'")
        api_key = self._api_key()
        if scope == "account" and not api_key:
            raise ModelPricingError("An OpenRouter API key is required for the account-filtered catalog")
        cache_key = self._cache_key(scope, api_key)
        now = self._monotonic()
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and not refresh and now - cached[0] < self._ttl_seconds:
                payload = copy.deepcopy(cached[1])
                payload["source"]["status"] = "cached"
                return payload

        endpoint = "models/user" if scope == "account" else "models?output_modalities=text"
        url = f"{self._base_url}/{endpoint}"
        headers = {
            "Accept": "application/json",
            "User-Agent": "Reasoning/0.1 model-price-browser",
        }
        if scope == "account" and api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            raw = self._fetch_json(url, headers, 12.0)
            values = raw.get("data") if isinstance(raw, dict) else None
            if not isinstance(values, list):
                raise ModelPricingError("OpenRouter returned an invalid model list")
            models = [record for value in values if (record := _model_record(value))]
            models.sort(key=lambda item: (item["provider"], item["name"].casefold()))
            fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
            payload = {
                "source": {
                    "provider": "openrouter",
                    "label": "OpenRouter",
                    "scope": scope,
                    "status": "live",
                    "fetched_at": fetched_at,
                    "model_count": len(models),
                    "account_filter_available": bool(api_key),
                    "catalog_url": "https://openrouter.ai/models",
                    "detail": (
                        "Filtered by this OpenRouter account's provider preferences" if scope == "account" else "Public text-model catalog"
                    ),
                },
                "models": models,
            }
        except ModelPricingError as exc:
            with self._lock:
                cached = self._cache.get(cache_key)
            if not cached:
                raise
            payload = copy.deepcopy(cached[1])
            payload["source"]["status"] = "stale"
            payload["source"]["error"] = str(exc)
            return payload

        with self._lock:
            self._cache[cache_key] = (now, copy.deepcopy(payload))
        return payload
