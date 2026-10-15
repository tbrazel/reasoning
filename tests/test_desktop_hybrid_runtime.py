# Ruff 0.3.4 and 0.15.x classify the src-layout package differently.
# ruff: noqa: I001
from fastapi.testclient import TestClient

from reasoning_ledger.webapp import build_app


EXPECTED_RUNTIME_READINESS = {
    "status": "ready",
    "route_plan_versions": ["1.0.0-alpha.1", "1.0.0-alpha.2"],
    "pricing_versions": ["1.0.0-alpha.1", "1.0.0-alpha.2"],
    "bundles": [
        {
            "schema_version": "1.0.0-alpha.1",
            "bundle_id": "reasoning-platform-v1alpha1",
            "bundle_digest": "sha256:1c5e3221b21ffb6ecce27407c561b5d7ff2d3647bc435ce986c713c903252eb8",
        },
        {
            "schema_version": "1.0.0-alpha.2",
            "bundle_id": "reasoning-platform-v1alpha2",
            "bundle_digest": "sha256:f0988632e10c92095b4de66bc89d193b8931b62a67bedbf562e32794a2175daf",
        },
    ],
}


def test_browser_health_remains_exactly_compatible(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(tmp_path / "browser-workbench"))
    monkeypatch.delenv("REASONING_DESKTOP_RUNTIME", raising=False)

    app = build_app()
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
    assert app.state.desktop_hybrid_planner_factory is None


def test_desktop_health_requires_verified_hybrid_contracts(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(tmp_path / "desktop-workbench"))
    monkeypatch.setenv("REASONING_DESKTOP_RUNTIME", "1")

    app = build_app()
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {
            "status": "ok",
            "desktop_hybrid": EXPECTED_RUNTIME_READINESS,
        }
    assert app.state.desktop_hybrid_planner_factory is not None
