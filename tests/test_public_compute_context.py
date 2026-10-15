"""Pinned source staging, never a mathematical or public distribution audit.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from reasoning_ledger.image_candidate import validate_run
from reasoning_ledger.image_context import materialize, stage_noether, validate_noether

ROOT = Path(__file__).resolve().parents[1]


def fixture_archive(tmp_path, extra=None):
    path = tmp_path / "source.tar.gz"
    entries = {name: b"fixture" for name in ("LICENSE", "Cargo.toml", "Cargo.lock", "pyproject.toml", "rust-toolchain.toml", "Makefile")}
    with tarfile.open(path, "w:gz") as archive:
        for name, body in entries.items():
            item = tarfile.TarInfo(name)
            item.size = len(body)
            archive.addfile(item, io.BytesIO(body))
        if extra:
            archive.addfile(extra, io.BytesIO(b"x" * extra.size) if extra.isreg() else None)
    lock = {"commit": "a" * 40, "version": "0.3.1", "build_context": "noether-source", "source_archive": {
        "url": "https://api.github.com/repos/research/bench/releases/assets/123", "transport": "github-api",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}}
    return path, lock


def test_supplied_archive_is_private_hash_locked_snapshot_without_credentials(tmp_path):
    source, lock = fixture_archive(tmp_path)
    staged = stage_noether(lock, tmp_path / "context", source_archive=source)
    assert json.loads((staged / "reasoning-source-pin.json").read_bytes()) == {"commit": lock["commit"], "version": lock["version"]}
    assert not (staged / ".git").exists()
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="exact-size"):
        stage_noether(lock, tmp_path / "other", source_archive=source)


@pytest.mark.parametrize("name", ["../escape", "/tmp/escape", ".git/config", ".env", "reasoning-source-pin.json", "LICENSE"])
def test_archive_escapes_duplicates_and_forged_pin_are_refused(tmp_path, name):
    item = tarfile.TarInfo(name)
    item.size = 1
    source, lock = fixture_archive(tmp_path, item)
    with pytest.raises(ValueError, match="unsafe or duplicate"):
        materialize(source, tmp_path / "context", lock)


def test_archive_links_and_mutable_archive_symlink_are_refused(tmp_path):
    item = tarfile.TarInfo("link")
    item.type, item.linkname = tarfile.SYMTYPE, "/etc/passwd"
    source, lock = fixture_archive(tmp_path, item)
    with pytest.raises(ValueError, match="unsafe"):
        stage_noether(lock, tmp_path / "context", source_archive=source)
    link = tmp_path / "alias"
    link.symlink_to(source)
    with pytest.raises(OSError):
        stage_noether(lock, tmp_path / "other", source_archive=link)


def test_real_recipe_has_bounded_source_pin_without_embedded_credentials():
    lock = json.loads((ROOT / "images/research-cpu/inputs.json").read_bytes())
    assert validate_noether(lock)["commit"] == "ca8fc50f3d9a902a827fbd558dba138ad7bf0f8f"
    lock["noether"]["source_archive"]["url"] += "?token=private"
    with pytest.raises(ValueError, match="bounded hash-locked"):
        validate_noether(lock)


@pytest.mark.parametrize("field,value", [("head_sha", "b" * 40), ("status", "in_progress"),
    ("conclusion", "failure"), ("event", "pull_request"), ("path", ".github/workflows/other.yml"),
    ("id", True), ("head_repository", {"id": 12})])
def test_publication_refuses_wrong_or_unfinished_candidate_run(field, value):
    run = {"head_sha": "a" * 40, "status": "completed", "conclusion": "success", "event": "workflow_dispatch",
           "path": ".github/workflows/compute-images.yml", "id": 11, "repository": {"id": 10}, "head_repository": {"id": 10}}
    validate_run(run, "a" * 40)
    run[field] = value
    with pytest.raises(ValueError, match="exact successful"):
        validate_run(run, "a" * 40)
