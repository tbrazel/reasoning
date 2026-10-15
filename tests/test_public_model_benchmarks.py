"""Metadata snapshots use fake transports, never paid inference or live credentials."""

import asyncio
import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from test_public_server import installation as installation

from reasoning_ledger.server.api import create_app
from reasoning_ledger.server.model_benchmarks import MAX_STALE, RETRY, TTL, ModelBenchmarks, snapshot
from reasoning_ledger.server.openrouter import OpenRouter

KEY = "fixture-only-benchmark-key"
PAYLOAD = {
    "data": [{"source": "artificial-analysis", "model_permaslug": "provider/test", "intelligence_index": 71.2,
              "agentic_index": 58.3, "coding_index": 65.8}],
    "meta": {"source": "artificial-analysis", "as_of": "2026-10-09T12:00:00Z",
             "source_url": "https://artificialanalysis.ai", "citation": "Source: Artificial Analysis via OpenRouter."},
}


def test_snapshot_exact_mapping_nulls_and_untrusted_scores():
    value = copy.deepcopy(PAYLOAD)
    value["data"] += [
        {"source": "artificial-analysis", "model_permaslug": "provider/missing", "intelligence_index": None,
         "agentic_index": True, "coding_index": float("nan")},
        {"source": "artificial-analysis", "model_permaslug": "provider/invalid", "intelligence_index": 10**400,
         "agentic_index": "71.2", "coding_index": float("inf")},
        {"source": "other", "model_permaslug": "provider/other", "intelligence_index": 99},
        {"source": "artificial-analysis", "model_permaslug": "provider/zero", "intelligence_index": 0,
         "agentic_index": -1, "coding_index": 101},
    ]
    result = snapshot(value)
    assert list(result["models"]) == ["provider/test", "provider/zero"]
    assert result["models"]["provider/zero"] == {"intelligence_index": 0, "agentic_index": None, "coding_index": None}
    assert "provider/test:free" not in result["models"]
    assert "provider/test-20260101" not in result["models"]
    assert result["meta"] == PAYLOAD["meta"]


@pytest.mark.parametrize("field,value", [("source", "other"), ("as_of", "bad"), ("as_of", "2026-10-09"),
                                        ("source_url", "javascript:alert(1)"), ("source_url", "https://attacker.example"),
                                        ("citation", "")])
def test_snapshot_requires_safe_attribution(field, value):
    payload = copy.deepcopy(PAYLOAD)
    payload["meta"][field] = value
    with pytest.raises(ValueError):
        snapshot(payload)


def test_snapshot_bounded_input():
    with pytest.raises(ValueError):
        snapshot({**PAYLOAD, "data": PAYLOAD["data"] * 101})


def test_public_catalog_retains_explicit_canonical_identity_only():
    async def scenario():
        rows = [{"id": "provider/alias", "canonical_slug": canonical, "context_length": 1000,
                 "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}}
                for canonical in ["provider/test", None, "", 123, "x" * 201]]
        provider = OpenRouter(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": rows})))
        catalog = await provider.prices()
        assert [row["canonical_slug"] for row in catalog] == ["provider/test", None, None, None, None]

    asyncio.run(scenario())


def test_scope_rotation_concurrency_cache_and_stale_retry():
    async def scenario():
        requests, clock = [], [100.0]
        failing = False

        async def respond(request):
            requests.append(request)
            await asyncio.sleep(0)
            assert request.method == "GET" and request.url.path.endswith("/benchmarks")
            assert dict(request.url.params) == {"source": "artificial-analysis", "max_results": "100"}
            if failing:
                return httpx.Response(403, json={"error": "secret-do-not-show"})
            return httpx.Response(200, json=PAYLOAD)

        provider = OpenRouter(transport=httpx.MockTransport(respond))
        cache = ModelBenchmarks(provider, clock=lambda: clock[0])
        assert (await cache.fetch("org", None))["status"] == "unavailable"
        assert requests == []
        results = await asyncio.gather(*(cache.fetch("org", ("key1", KEY)) for _ in range(5)))
        assert len(requests) == 1 and results[0]["status"] == "live" and results[-1]["status"] == "cached"
        results[0]["models"].clear()
        assert (await cache.fetch("org", ("key1", KEY)))["models"]
        await cache.fetch("other", ("key1", KEY))
        await cache.fetch("org", ("key2", KEY))
        assert len(requests) == 3
        failing = True
        clock[0] += TTL + 1
        result = await cache.fetch("org", ("key1", KEY))
        assert result["status"] == "stale" and result["meta"] == PAYLOAD["meta"]
        count = len(requests)
        await cache.fetch("org", ("key1", KEY))
        assert len(requests) == count
        clock[0] += RETRY + 1
        await cache.fetch("org", ("key1", KEY))
        assert len(requests) == count + 1
        clock[0] += MAX_STALE
        result = await cache.fetch("org", ("key1", KEY))
        assert result == {"models": {}, "meta": None, "status": "unavailable"}
        assert KEY not in json.dumps(list(cache.cache.values())) and "secret-do-not-show" not in json.dumps(result)

    asyncio.run(scenario())


def test_failed_first_fetch_is_cached_and_never_exposes_provider_error():
    async def scenario():
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(429, text=KEY)

        cache = ModelBenchmarks(OpenRouter(transport=httpx.MockTransport(respond)))
        first = await cache.fetch("org", ("key", KEY))
        assert first == {"models": {}, "meta": None, "status": "unavailable"}
        assert await cache.fetch("org", ("key", KEY)) == first
        assert len(calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["network", "oversize", "malformed"])
def test_unavailable_metadata_does_not_break_catalog(failure):
    async def scenario():
        def respond(request):
            if failure == "network":
                raise httpx.ConnectError(KEY)
            return httpx.Response(200, content=b"x" * (1024 * 1024 + 1) if failure == "oversize" else b"not json")

        cache = ModelBenchmarks(OpenRouter(transport=httpx.MockTransport(respond)))
        result = await cache.fetch("org", ("key", KEY))
        assert result == {"models": {}, "meta": None, "status": "unavailable"}

    asyncio.run(scenario())


def test_organization_endpoint_authorizes_before_credential_and_network(installation):
    config, _, client, owner = installation
    requests = []

    def respond(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer " + KEY
        return httpx.Response(200, json=PAYLOAD)

    provider = OpenRouter(transport=httpx.MockTransport(respond))
    client = TestClient(create_app(config, provider=provider), base_url=config.public_url,
                        headers={"Authorization": "Bearer " + owner["access_token"]})
    root = f"/api/organizations/{owner['organization_id']}"
    assert client.get(root + "/model-benchmarks").json()["status"] == "unavailable"
    assert requests == []
    response = client.post(root + "/provider-keys", json={"key": KEY, "label": "Test metadata"})
    assert response.status_code == 201, response.text
    assert client.get("/api/organizations/" + "0" * 32 + "/model-benchmarks").status_code == 404
    assert requests == []
    result = client.get(root + "/model-benchmarks")
    assert result.status_code == 200 and result.json()["models"]["provider/test"]["agentic_index"] == 58.3
    assert client.get(root + "/model-benchmarks").json()["status"] == "cached"
    assert len(requests) == 1 and KEY not in result.text
    key_id = response.json()["id"]
    assert client.delete(root + "/provider-keys/" + key_id).status_code == 204
    assert client.get(root + "/model-benchmarks").json()["status"] == "unavailable"
    assert len(requests) == 1
