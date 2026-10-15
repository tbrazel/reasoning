import json
from pathlib import Path

from fastapi.testclient import TestClient

from reasoning_ledger.agents import CapabilityCatalog, CapabilityRoot, SkillRegistry
from reasoning_ledger.webapp import build_app


def _skill(root: Path, name: str = "sequence-scout") -> Path:
    package = root / name
    (package / "references").mkdir(parents=True)
    (package / "scripts").mkdir()
    (package / "SKILL.md").write_text(
        f"""---
name: {name}
description: Identify and test integer sequences with cited evidence.
license: MIT
compatibility: Python 3.11 or newer
metadata:
  reasoning:
    tools: [oeis_search]
---

Search candidate sequences, test offsets, and cite the exact record used.
""",
        encoding="utf-8",
    )
    (package / "references" / "method.md").write_text("Compare several terms before identifying a sequence.\n", encoding="utf-8")
    (package / "scripts" / "normalize.py").write_text("print('normalize')\n", encoding="utf-8")
    return package


def test_discovers_only_skill_packages_and_builds_reviewable_previews(tmp_path):
    source_root = tmp_path / ".codex" / "skills"
    package = _skill(source_root)
    # This resembles adjacent application state but is outside the package and
    # must never enter either its preview or imported immutable revision.
    (source_root.parent / "session.json").write_text('{"token":"no"}\n')
    catalog = CapabilityCatalog((CapabilityRoot("codex", "Codex skills", source_root.resolve()),))

    sources = catalog.discover()
    assert len(sources) == 1
    assert sources[0]["name"] == "sequence-scout"
    preview = catalog.preview(sources[0]["source_id"])
    assert preview["importable"] is True
    assert preview["license"] == "MIT"
    assert preview["requested_tools"] == ["oeis_search"]
    assert preview["content_sha256"]
    assert {item["path"] for item in preview["files"]} == {
        "SKILL.md",
        "references/method.md",
        "scripts/normalize.py",
    }
    assert any("executable or script" in warning for warning in preview["warnings"])

    registry = SkillRegistry(tmp_path / "registry")
    revision = registry.import_package(
        package,
        provenance=catalog.provenance(preview),
    )
    assert revision.provenance["source_kind"] == "codex"
    assert revision.provenance["content_sha256"] == preview["content_sha256"]
    assert "session.json" not in revision.source_files


def test_preview_blocks_secret_like_files_and_symlinks(tmp_path):
    source_root = tmp_path / "skills"
    package = _skill(source_root, "unsafe-skill")
    (package / ".env").write_text("API_KEY=secret\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    (package / "linked.txt").symlink_to(outside)
    catalog = CapabilityCatalog((CapabilityRoot("configured", "Test skills", source_root.resolve()),))

    preview = catalog.discover()[0]
    assert preview["importable"] is False
    assert any("Secret-like" in error for error in preview["errors"])
    assert any("Symbolic links" in error for error in preview["errors"])


def test_capability_source_api_previews_and_imports_with_provenance(tmp_path, monkeypatch):
    source_root = tmp_path / "external-skills"
    _skill(source_root)
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(tmp_path / "workbench"))
    monkeypatch.setenv("REASONING_CAPABILITY_ROOTS", str(source_root))
    client = TestClient(build_app())

    listing = client.get("/api/agent/capability-sources")
    assert listing.status_code == 200
    source = listing.json()["sources"][0]
    detail = client.get(f"/api/agent/capability-sources/{source['source_id']}")
    assert detail.status_code == 200
    assert detail.json()["files"][0]["path"] == "SKILL.md"

    imported = client.post(f"/api/agent/capability-sources/{source['source_id']}/import")
    assert imported.status_code == 200
    revision = imported.json()
    assert revision["skill_id"] == "sequence-scout"
    assert revision["provenance"]["source_id"] == source["source_id"]
    stored = json.loads(
        (
            tmp_path / "workbench" / "agentic" / "skills" / "sequence-scout" / "revisions" / revision["revision_id"] / "revision.json"
        ).read_text(encoding="utf-8")
    )
    assert stored["provenance"]["source_path"].endswith("sequence-scout")

    imported_again = client.post(
        f"/api/agent/capability-sources/{source['source_id']}/import"
    ).json()
    assert imported_again["revision_id"] == revision["revision_id"]

    assert client.get("/api/agent/capability-sources/capsrc_missing").status_code == 404
