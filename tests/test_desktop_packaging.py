import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_shared_executables_bundle_jsonschema_specifications() -> None:
    spec = (ROOT / "desktop" / "reasoning-backend.spec").read_text()

    package_data_assignment = next(line for line in spec.splitlines() if line.startswith("PACKAGE_DATA = "))
    assert 'collect_data_files("certifi")' in package_data_assignment
    assert 'collect_data_files("jsonschema_specifications")' in package_data_assignment


def test_desktop_startup_treats_codex_credentials_as_optional() -> None:
    source = (ROOT / "desktop" / "src" / "local-main.mjs").read_text(encoding="utf-8")
    credential_import = source.split("function importCodexCredentials", 1)[1].split("async function startBackend", 1)[0]

    assert "throw new Error" not in credential_import
    assert "continuing without Codex" in credential_import
    assert "API-based agents available" in credential_import


def test_desktop_cryptography_is_pinned_for_reproducible_local_builds() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "cryptography==50.0.1" in project["project"]["dependencies"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    package = next(item for item in lock["package"] if item["name"] == "cryptography")
    assert package["version"] == "50.0.1"
