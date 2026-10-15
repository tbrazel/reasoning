"""Guard the supported application surface and its dependency boundary."""

import importlib.util
import tomllib
from pathlib import Path

from fastapi.testclient import TestClient

from reasoning_ledger import LedgerStore
from reasoning_ledger.agents import build_project_tool_registry
from reasoning_ledger.webapp import build_app

ROOT = Path(__file__).resolve().parents[1]
REMOVED_MODULES = (
    "reasoning_ledger.notebooks",
    "reasoning_ledger.jupyter",
    "reasoning_ledger.agents.swarms",
    "reasoning_ledger.agents.validation",
)


def test_application_registers_only_supported_surfaces(tmp_path, monkeypatch):
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(tmp_path / "workbench"))
    app = build_app()
    routes = [route.path for route in app.routes]
    for obsolete in ("/notebooks", "/jupyter", "/swarms", "/validation"):
        assert not any(obsolete in path for path in routes)
    with TestClient(app) as client:
        project = client.post("/api/projects", json={"name": "Research"}).json()
        assert "notebooks" not in project
        for suffix in ("notebooks", "swarms/status", "validation/experiments"):
            assert client.get(f"/api/projects/{project['id']}/{suffix}").status_code == 404
        assert client.get(f"/api/projects/{project['id']}/workflows").status_code == 200
        assert client.get(f"/api/projects/{project['id']}/answer-keys").status_code == 200


def test_tools_and_packages_do_not_expose_removed_features(tmp_path):
    registry = build_project_tool_registry(LedgerStore(tmp_path / "ledger"), tmp_path / "artifacts")
    names = {tool.name for tool in registry.list()}
    assert "scientific_calculate" in names
    assert "artifact_write" in names
    assert not any(name.startswith("notebook_") for name in names)
    for module in REMOVED_MODULES:
        assert importlib.util.find_spec(module) is None
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    dependencies = {package["name"] for package in lock["package"]}
    assert not dependencies.intersection({"notebook", "nbformat", "nbclient", "ipykernel"})
    assert not any(name.startswith("jupyter") for name in dependencies)
    for name in ("NotebookPane", "SwarmLab", "ValidationLab"):
        assert not (ROOT / "web" / "src" / f"{name}.tsx").exists()
    spec = (ROOT / "desktop" / "reasoning-backend.spec").read_text(encoding="utf-8")
    assert 'executable("backend_entry.py", "reasoning-ledger-web", datas=BACKEND_DATA)' in spec
    assert "notebook" not in spec.casefold()
