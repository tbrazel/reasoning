"""Scoped, bounded Artificial Analysis snapshots from the OpenRouter metadata API."""

from __future__ import annotations

import asyncio
import copy
import json
import math
import time
from collections import OrderedDict
from datetime import datetime
from urllib.parse import urlsplit

import httpx

from .openrouter import OpenRouter, ProviderError

SOURCE = "artificial-analysis"
FIELDS = ("intelligence_index", "agentic_index", "coding_index")
EMPTY = {"models": {}, "meta": None, "status": "unavailable"}
TTL = 24 * 3600
RETRY = 300
MAX_STALE = 7 * 24 * 3600


def metadata_key(store, user: str, organization: str) -> tuple[str, str] | None:
    """Authorize before opening a credential; no budget reservation or inference."""
    with store.transaction() as db:
        store._require(db, user, organization)
        row = db.execute(
            "SELECT p.* FROM provider_keys p LEFT JOIN audit_events a ON a.organization_id=p.organization_id "
            "AND a.target_id=p.id AND a.action='provider-key.create' "
            "WHERE p.organization_id=? AND p.provider='openrouter' AND p.disabled_at IS NULL "
            "ORDER BY a.sequence DESC,p.created_at DESC,p.id DESC LIMIT 1", (organization,),
        ).fetchone()
        if row is None:
            return None
        return row["id"], store._box.open(
            row["ciphertext"], organization_id=organization, provider="openrouter", key_id=row["id"]
        )


def snapshot(payload: object) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or len(payload["data"]) > 100:
        raise ValueError("invalid benchmark snapshot")
    meta = payload.get("meta")
    if not isinstance(meta, dict) or meta.get("source") != SOURCE:
        raise ValueError("invalid benchmark source")
    as_of, url, citation = (meta.get(field) for field in ("as_of", "source_url", "citation"))
    if not isinstance(as_of, str) or len(as_of) > 64:
        raise ValueError("invalid benchmark date")
    date = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    if date.tzinfo is None:
        raise ValueError("benchmark date must specify timezone")
    if not isinstance(url, str) or len(url) > 500:
        raise ValueError("invalid benchmark URL")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in {"artificialanalysis.ai", "www.artificialanalysis.ai"}
            or parsed.username or parsed.password):
        raise ValueError("invalid benchmark URL")
    if not isinstance(citation, str) or not citation.strip() or len(citation) > 1000:
        raise ValueError("invalid benchmark citation")
    models = {}
    for row in payload["data"]:
        if not isinstance(row, dict) or row.get("source") != SOURCE:
            continue
        identifier = row.get("model_permaslug")
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 200 or identifier in models:
            continue
        # Exact canonical identifiers only; never borrow scores for free/dated/model variants.
        values = {}
        for field in FIELDS:
            number = row.get(field)
            values[field] = number if type(number) in {int, float} and 0 <= number <= 100 and math.isfinite(number) else None
        if any(number is not None for number in values.values()):
            models[identifier] = values
    return {"models": models, "meta": {"source": SOURCE, "as_of": as_of, "source_url": url, "citation": citation}, "status": "live"}


class ModelBenchmarks:
    def __init__(self, provider: OpenRouter, *, clock=time.monotonic):
        self.provider, self.clock = provider, clock
        self.cache: OrderedDict[tuple[str, str], dict] = OrderedDict()
        self.lock = asyncio.Lock()

    async def fetch(self, organization: str, credential: tuple[str, str] | None) -> dict:
        if credential is None:
            return copy.deepcopy(EMPTY)
        identity, key = credential
        scope = (organization, identity)
        # Serializing refreshes prevents concurrent tabs consuming the upstream rate limit.
        async with self.lock:
            now = self.clock()
            prior = self.cache.get(scope)
            if prior is not None and prior["retry_at"] > now:
                self.cache.move_to_end(scope)
                result = copy.deepcopy(prior["value"])
                if result["status"] == "live":
                    result["status"] = "cached"
                return result
            try:
                async with self.provider.client() as client, client.stream(
                    "GET", self.provider.base_url + "/benchmarks",
                    params={"source": SOURCE, "max_results": 100}, headers={"Authorization": "Bearer " + key},
                    timeout=httpx.Timeout(10, connect=5),
                ) as response:
                    response.raise_for_status()
                    value = snapshot(json.loads(await self.provider._bounded(response, 1024 * 1024)))
                entry = {"value": value, "fetched_at": now, "retry_at": now + TTL}
            except (httpx.HTTPError, ProviderError, ValueError, TypeError, RecursionError, AttributeError):
                # Provider error bodies/credentials never enter logs, client payloads, or caches.
                value = copy.deepcopy(prior["value"]) if prior and now - prior["fetched_at"] < MAX_STALE else copy.deepcopy(EMPTY)
                if value["meta"] is not None:
                    value["status"] = "stale"
                entry = {"value": value, "fetched_at": prior["fetched_at"] if prior else now, "retry_at": now + RETRY}
            self.cache[scope] = entry
            self.cache.move_to_end(scope)
            while len(self.cache) > 128:
                self.cache.popitem(last=False)
            return copy.deepcopy(value)
