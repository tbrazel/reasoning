from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_only_desktop_backend_embeds_canonical_platform_assets() -> None:
    spec = (ROOT / "desktop" / "reasoning-backend.spec").read_text(encoding="utf-8")

    assert 'for version in ("v1alpha1", "v1alpha2")' in spec
    assert '.glob("*.json")' in spec
    assert 'collect_data_files("jsonschema_specifications")' in spec
    assert 'executable("backend_entry.py", "reasoning-ledger-web", datas=BACKEND_DATA)' in spec
    assert 'executable("ledger_entry.py", "reasoning-ledger")' in spec
    assert 'executable("ledger_entry.py", "reasoning-ledger", datas=BACKEND_DATA)' not in spec
    assert spec.count("datas=BACKEND_DATA") == 1


def test_only_explicit_local_workbench_launches_require_hybrid_runtime() -> None:
    main = (ROOT / "desktop" / "src" / "main.mjs").read_text(encoding="utf-8")
    local_main = (ROOT / "desktop" / "src" / "local-main.mjs").read_text(encoding="utf-8")
    public_host = (ROOT / "desktop" / "src" / "organization-host.mjs").read_text(encoding="utf-8")
    backend_entry = (ROOT / "desktop" / "backend_entry.py").read_text(encoding="utf-8")

    assert 'process.env.REASONING_DESKTOP_MODE === "local-workbench"' in main
    assert 'await import("./organization-host.mjs")' in main
    assert 'REASONING_DESKTOP_RUNTIME: "1"' in local_main
    assert "REASONING_DESKTOP_RUNTIME" not in public_host
    assert "backend_entry" not in public_host
    assert 'os.environ["REASONING_DESKTOP_RUNTIME"] = "1"' in backend_entry


def test_general_python_package_does_not_claim_platform_assets() -> None:
    packaging = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "contracts/platform" not in packaging
