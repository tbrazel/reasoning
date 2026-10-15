from __future__ import annotations

from fastapi.testclient import TestClient

from reasoning_ledger.agents.model_pricing import (
    ModelPriceCatalog,
    ModelPricingError,
)
from reasoning_ledger.webapp import build_app


def _openrouter_payload():
    return {
        "data": [
            {
                "id": "example/precise-model",
                "canonical_slug": "example/precise-model-20260801",
                "name": "Precise Model",
                "hugging_face_id": "example/precise-model",
                "description": "A model with every price field.",
                "created": 1_700_000_000,
                "context_length": 200_000,
                "architecture": {
                    "modality": "text+image->text",
                    "input_modalities": ["text", "image"],
                    "output_modalities": ["text"],
                },
                "top_provider": {"max_completion_tokens": 32_000},
                "supported_parameters": ["temperature", "tools", "reasoning"],
                "pricing": {
                    "prompt": "0.000002",
                    "completion": "0.000006",
                    "input_cache_read": "0.0000002",
                    "input_cache_write": "0.0000025",
                    "internal_reasoning": "0.000001",
                    "request": "0.0004",
                    "image": "0.001",
                    "web_search": "0.01",
                },
            },
            {
                "id": "example/free-model",
                "name": "Free Model",
                "pricing": {"prompt": "0", "completion": "0"},
            },
            {"name": "missing id"},
        ]
    }


def test_openrouter_catalog_normalizes_rates_and_caches():
    calls = []
    clock = [10.0]

    def fetch_json(url, headers, timeout):
        calls.append((url, dict(headers), timeout))
        return _openrouter_payload()

    catalog = ModelPriceCatalog(
        lambda: "sk-secret",
        base_url="https://prices.test/v1",
        fetch_json=fetch_json,
        ttl_seconds=60,
        monotonic=lambda: clock[0],
    )
    first = catalog.fetch()
    assert first["source"]["status"] == "live"
    assert first["source"]["account_filter_available"] is True
    assert first["source"]["model_count"] == 2
    model = next(item for item in first["models"] if item["model_id"] == "example/precise-model")
    assert model["input_mtok_usd"] == 2.0
    assert model["output_mtok_usd"] == 6.0
    assert model["cache_read_mtok_usd"] == 0.2
    assert model["cache_write_mtok_usd"] == 2.5
    assert model["reasoning_mtok_usd"] == 1.0
    assert model["request_usd"] == 0.0004
    assert model["image_usd"] == 0.001
    assert model["web_search_usd"] == 0.01
    assert model["supports_tools"] is True
    assert model["supports_reasoning"] is True
    assert model["hugging_face_id"] == "example/precise-model"
    assert model["source_availability"] == "open_weight"
    assert model["context_length"] == 200_000
    assert model["max_output_tokens"] == 32_000
    free = next(item for item in first["models"] if item["model_id"] == "example/free-model")
    assert free["source_availability"] == "unreported"
    assert "Authorization" not in calls[0][1]

    second = catalog.fetch()
    assert second["source"]["status"] == "cached"
    assert len(calls) == 1

    account = catalog.fetch(scope="account")
    assert account["source"]["scope"] == "account"
    assert calls[-1][0] == "https://prices.test/v1/models/user"
    assert calls[-1][1]["Authorization"] == "Bearer sk-secret"
    assert "sk-secret" not in str(account)


def test_openrouter_catalog_returns_stale_data_after_refresh_failure():
    clock = [0.0]
    failing = [False]

    def fetch_json(_url, _headers, _timeout):
        if failing[0]:
            raise ModelPricingError("provider unavailable")
        return _openrouter_payload()

    catalog = ModelPriceCatalog(
        lambda: None,
        fetch_json=fetch_json,
        ttl_seconds=1,
        monotonic=lambda: clock[0],
    )
    assert catalog.fetch()["source"]["status"] == "live"
    clock[0] = 2
    failing[0] = True
    stale = catalog.fetch()
    assert stale["source"]["status"] == "stale"
    assert stale["source"]["error"] == "provider unavailable"


def test_account_catalog_requires_openrouter_key():
    catalog = ModelPriceCatalog(lambda: None, fetch_json=lambda *_args: {})
    try:
        catalog.fetch(scope="account")
    except ModelPricingError as error:
        assert "API key" in str(error)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("account scope unexpectedly worked without a key")


def test_model_price_endpoint_adds_executor_coverage(tmp_path, monkeypatch):
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(tmp_path / "workbench"))
    monkeypatch.setenv("REASONING_CODEX_COMMAND", str(tmp_path / "missing-codex"))
    monkeypatch.setenv("REASONING_CLAUDE_COMMAND", str(tmp_path / "missing-claude"))
    monkeypatch.setenv("REASONING_HERMES_COMMAND", str(tmp_path / "missing-hermes"))
    monkeypatch.setenv("REASONING_OPENCODE_COMMAND", str(tmp_path / "missing-opencode"))

    observed = {}

    def fake_fetch(self, *, scope="all", refresh=False):
        observed.update(scope=scope, refresh=refresh)
        return {
            "source": {
                "provider": "openrouter",
                "label": "OpenRouter",
                "scope": scope,
                "status": "live",
                "fetched_at": "2026-08-11T12:00:00+00:00",
                "model_count": 1,
                "account_filter_available": False,
                "catalog_url": "https://openrouter.ai/models",
                "detail": "Public text-model catalog",
            },
            "models": [{"model_id": "example/model"}],
        }

    monkeypatch.setattr(ModelPriceCatalog, "fetch", fake_fetch)
    client = TestClient(build_app())
    response = client.get("/api/model-prices?scope=all&refresh=true")
    assert response.status_code == 200
    payload = response.json()
    assert observed == {"scope": "all", "refresh": True}
    assert payload["models"] == [{"model_id": "example/model"}]
    coverage = {item["executor_id"]: item for item in payload["coverage"]}
    assert set(coverage) == {
        "reasoning-native",
        "codex",
        "claude-code",
        "hermes",
        "opencode",
    }
    assert coverage["reasoning-native"]["pricing_kind"] == "live_catalog"
    assert coverage["codex"]["pricing_kind"] == "token_estimate"
    assert "provider-reported charge" in coverage["codex"]["detail"]
    assert coverage["claude-code"]["pricing_kind"] == "reported_after_run"
    assert coverage["codex"]["model_options"]

    invalid = client.get("/api/model-prices?scope=private")
    assert invalid.status_code == 400
