from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from reasoning_ledger.collaboration import (
    CollaborationContractError,
    canonical_collaboration_validator,
)

ROOT = Path(__file__).resolve().parents[1]


def _copy_frozen_collaboration_assets(destination: Path) -> Path:
    source = ROOT / "contracts" / "collaboration" / "v1alpha1"
    target = destination / "contracts" / "collaboration" / "v1alpha1"
    target.mkdir(parents=True)
    for path in source.glob("*.json"):
        shutil.copyfile(path, target / path.name)
    return destination.resolve()


def test_only_web_backend_packages_collaboration_contracts() -> None:
    spec = (ROOT / "desktop" / "reasoning-backend.spec").read_text(encoding="utf-8")
    assert "COLLABORATION_DATA" in spec
    assert 'executable("backend_entry.py", "reasoning-ledger-web", datas=BACKEND_DATA)' in spec
    assert spec.count("datas=BACKEND_DATA") == 1


def test_canonical_collaboration_contracts_verify_from_frozen_layout(tmp_path: Path, monkeypatch) -> None:
    frozen = _copy_frozen_collaboration_assets(tmp_path / "frozen")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(frozen), raising=False)
    validator = canonical_collaboration_validator()
    assert validator.contract_root == frozen / "contracts" / "collaboration" / "v1alpha1"


def test_frozen_collaboration_contracts_fail_closed_when_incomplete(tmp_path: Path, monkeypatch) -> None:
    frozen = _copy_frozen_collaboration_assets(tmp_path / "frozen")
    (frozen / "contracts" / "collaboration" / "v1alpha1" / "topic.schema.json").unlink()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(frozen), raising=False)
    with pytest.raises(CollaborationContractError, match="incomplete"):
        canonical_collaboration_validator()
