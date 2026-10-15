from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from reasoning_ledger.cloud_product_policy import CloudProductPolicyContractError, CloudProductPolicyValidator

ROOT = Path(__file__).resolve().parents[1]


def _manifest_paths(path: Path) -> set[str]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return {
        path.relative_to(ROOT).as_posix(),
        *(item["path"] for key in ("members", "dependencies") for item in manifest[key]),
    }


def _copy_frozen_policy_assets(destination: Path) -> Path:
    product_manifest = ROOT / "contracts" / "cloud-product-policy" / "v1alpha1" / "bundle-manifest.json"
    cloud_manifest = ROOT / "contracts" / "cloud-client" / "v1alpha1" / "bundle-manifest.json"
    paths = _manifest_paths(product_manifest) | _manifest_paths(cloud_manifest)
    for version in ("v1alpha1", "v1alpha2"):
        paths.update(path.relative_to(ROOT).as_posix() for path in (ROOT / "contracts" / "platform" / version).glob("*.json"))
    for relative in paths:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return destination.resolve()


def test_only_web_backend_packages_exact_policy_and_cloud_dependency() -> None:
    spec = (ROOT / "desktop" / "reasoning-backend.spec").read_text(encoding="utf-8")
    assert "CLOUD_PRODUCT_POLICY_MANIFEST" in spec
    assert "CLOUD_PRODUCT_POLICY_DATA" in spec
    assert "sha256:c78d03c0204a2524af0e78209a4cc51c1aaf40cac092b3fb4d0063cc81540b26" in spec
    assert 'executable("backend_entry.py", "reasoning-ledger-web", datas=BACKEND_DATA)' in spec
    assert spec.count("datas=BACKEND_DATA") == 1
    assert 'executable("ledger_entry.py", "reasoning-ledger", datas=BACKEND_DATA)' not in spec


def test_exact_policy_and_cloud_dependency_verify_from_frozen_layout(tmp_path: Path) -> None:
    frozen = _copy_frozen_policy_assets(tmp_path / "frozen")
    validator = CloudProductPolicyValidator(frozen, frozen_layout=True)
    assert validator.manifest["bundle_digest"] == "sha256:c78d03c0204a2524af0e78209a4cc51c1aaf40cac092b3fb4d0063cc81540b26"


@pytest.mark.parametrize("mutation", ["omitted", "altered", "duplicate", "mixed", "extra"])
def test_frozen_policy_projection_rejects_incomplete_or_mixed_inputs(mutation: str, tmp_path: Path) -> None:
    frozen = _copy_frozen_policy_assets(tmp_path / mutation / "frozen")
    contract = frozen / "contracts" / "cloud-product-policy" / "v1alpha1"
    if mutation == "omitted":
        (contract / "account-policy.schema.json").unlink()
    elif mutation == "altered":
        (contract / "account-policy.schema.json").write_bytes((contract / "account-policy.schema.json").read_bytes() + b"\n")
    elif mutation == "duplicate":
        manifest = contract / "bundle-manifest.json"
        payload = manifest.read_text(encoding="utf-8")
        manifest.write_text(payload.replace('"schema_id":', '"schema_id":"duplicate", "schema_id":', 1), encoding="utf-8")
    elif mutation == "mixed":
        source = frozen / "contracts" / "cloud-client" / "v1alpha1" / "bundle-manifest.json"
        source.write_bytes(source.read_bytes() + b"\n")
    else:
        (contract / "extra.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(CloudProductPolicyContractError):
        CloudProductPolicyValidator(frozen, frozen_layout=True)
