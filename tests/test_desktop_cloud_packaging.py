from __future__ import annotations

# Ruff 0.3.4 and 0.15.x classify the src-layout package differently.
# ruff: noqa: I001

import json
import shutil
import sys
from pathlib import Path

import pytest
from reasoning_ledger.cloud_client import CloudClientContractError
from reasoning_ledger.desktop_cloud import DesktopCloudController
from reasoning_ledger.harnesses.desktop_hybrid import HybridPlanningError

ROOT = Path(__file__).resolve().parents[1]


def _copy_frozen_cloud_assets(destination: Path) -> Path:
    manifest_path = ROOT / "contracts" / "cloud-client" / "v1alpha1" / "bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    paths = {
        "contracts/cloud-client/v1alpha1/bundle-manifest.json",
        *(item["path"] for key in ("members", "dependencies") for item in manifest[key]),
    }
    for version in ("v1alpha1", "v1alpha2"):
        paths.update(path.relative_to(ROOT).as_posix() for path in (ROOT / "contracts" / "platform" / version).glob("*.json"))
    for relative in paths:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return destination.resolve()


def test_only_desktop_backend_packages_the_exact_cloud_client_projection() -> None:
    spec = (ROOT / "desktop" / "reasoning-backend.spec").read_text(encoding="utf-8")
    assert "CLOUD_CLIENT_MANIFEST" in spec
    assert "CLOUD_CLIENT_DATA" in spec
    assert "sha256:58f45ca5f9e5a53382b40444399e4cc792d297ead59b6387fd482ab72e0ad6b7" in spec
    assert 'executable("backend_entry.py", "reasoning-ledger-web", datas=BACKEND_DATA)' in spec
    assert spec.count("datas=BACKEND_DATA") == 1
    assert 'executable("ledger_entry.py", "reasoning-ledger", datas=BACKEND_DATA)' not in spec


def test_exact_cloud_client_assets_verify_from_a_simulated_frozen_layout(tmp_path: Path, monkeypatch) -> None:
    frozen = _copy_frozen_cloud_assets(tmp_path / "frozen")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(frozen), raising=False)
    controller = DesktopCloudController((tmp_path / "state").resolve())
    try:
        assert controller.readiness()["bundle_digest"] == ("sha256:58f45ca5f9e5a53382b40444399e4cc792d297ead59b6387fd482ab72e0ad6b7")
    finally:
        controller.close()


@pytest.mark.parametrize("mutation", ["omitted", "altered", "duplicate", "mixed", "extra"])
def test_frozen_cloud_client_projection_fails_closed_before_readiness(
    mutation: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    frozen = _copy_frozen_cloud_assets(tmp_path / mutation / "frozen")
    contract = frozen / "contracts" / "cloud-client" / "v1alpha1"
    if mutation == "omitted":
        (contract / "session.schema.json").unlink()
    elif mutation == "altered":
        (contract / "session.schema.json").write_bytes((contract / "session.schema.json").read_bytes() + b"\n")
    elif mutation == "duplicate":
        manifest = contract / "bundle-manifest.json"
        payload = manifest.read_text(encoding="utf-8")
        manifest.write_text(payload.replace('"schema_id":', '"schema_id":"duplicate", "schema_id":', 1), encoding="utf-8")
    elif mutation == "mixed":
        shutil.copyfile(
            frozen / "contracts" / "platform" / "v1alpha1" / "route-plan.schema.json",
            frozen / "contracts" / "platform" / "v1alpha2" / "route-plan.schema.json",
        )
    else:
        (contract / "extra.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(frozen), raising=False)
    with pytest.raises((CloudClientContractError, HybridPlanningError)):
        DesktopCloudController((tmp_path / mutation / "state").resolve())
