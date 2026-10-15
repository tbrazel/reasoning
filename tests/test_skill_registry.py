import json
import os
from pathlib import Path

import pytest

from reasoning_ledger.agents import (
    SkillBinding,
    SkillRegistry,
    SkillRegistryError,
    ensure_builtin_skill,
)


def _package(tmp_path, instructions="Use the ledger carefully."):
    source = tmp_path / "ledger-research"
    source.mkdir(exist_ok=True)
    (source / "references").mkdir(exist_ok=True)
    (source / "references" / "method.md").write_text("Check dependencies.")
    (source / "SKILL.md").write_text(
        """---
name: ledger-research
description: >
  Conduct careful research against
  the Reasoning ledger.
license: MIT
compatibility: Requires Reasoning project tools
allowed-tools: ledger_status ledger_search
metadata:
  reasoning:
    tools: [ledger_status, ledger_search]
    capabilities: [ledger:read]
    input_schema: {"type":"object","properties":{"question":{"type":"string"}}}
---

"""
        + instructions
        + "\n",
        encoding="utf-8",
    )
    return source


def test_imports_agent_skill_and_preserves_immutable_revisions(tmp_path):
    registry = SkillRegistry(tmp_path / "registry")
    source = _package(tmp_path)
    first = registry.import_package(source)
    assert first.name == "ledger-research"
    assert first.description == "Conduct careful research against the Reasoning ledger."
    assert first.tool_dependencies == ("ledger_status", "ledger_search")
    assert first.required_capabilities == ("ledger:read",)
    assert registry.verify(first.skill_id, first.revision_id)

    (source / "SKILL.md").write_text(
        (source / "SKILL.md").read_text().replace(
            "Use the ledger carefully.", "Search first, then inspect dependencies."
        )
    )
    second = registry.import_package(source)
    assert second.revision_id != first.revision_id
    assert second.parent_revision_id == first.revision_id
    assert registry.get(first.skill_id, first.revision_id).instructions == "Use the ledger carefully."
    assert registry.get(first.skill_id).revision_id == second.revision_id


def test_authors_skill_and_materializes_claude_plugin(tmp_path):
    registry = SkillRegistry(tmp_path / "registry")
    revision = registry.create(
        name="weekly-advisor",
        description="Review weekly research progress.",
        instructions="Challenge the student's strongest claim.",
        metadata={"reasoning": {"tools": ["ledger_status"]}},
    )
    prepared = registry.prepare(
        (SkillBinding(revision.skill_id, revision.revision_id),),
        executor_id="claude-code",
    )[0]
    plugin = os.path.commonpath(
        [prepared.directory, str(tmp_path / "registry" / ".adapters")]
    )
    assert plugin == str(tmp_path / "registry" / ".adapters")
    plugin_path = Path(prepared.directory)
    manifest = json.loads((plugin_path / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "reasoning-weekly-advisor"
    assert (
        plugin_path / "skills" / "weekly-advisor" / "SKILL.md"
    ).is_file()


def test_reads_only_text_files_from_an_exact_immutable_skill_revision(tmp_path):
    registry = SkillRegistry(tmp_path / "registry")
    revision = registry.import_package(_package(tmp_path))

    method = registry.read_package_file(
        revision.skill_id, revision.revision_id, "references/method.md"
    )
    assert method["content"] == "Check dependencies."
    assert method["sha256"] == revision.source_files["references/method.md"]
    with pytest.raises(SkillRegistryError, match="escapes"):
        registry.read_package_file(revision.skill_id, revision.revision_id, "../outside")


def test_authored_skills_can_be_revised_and_removed_without_breaking_pins(tmp_path):
    registry = SkillRegistry(tmp_path / "registry")
    first = registry.create(
        name="weekly-advisor",
        description="Review research progress.",
        instructions="Read the student's report.",
        metadata={"reasoning": {"tools": ["ledger_status"]}},
    )
    second = registry.revise(
        first.skill_id,
        instructions="Read the report, then challenge its strongest claim.",
        parent_revision_id=first.revision_id,
    )

    assert second.parent_revision_id == first.revision_id
    assert second.tool_dependencies == ("ledger_status",)
    assert len(registry.revisions(first.skill_id)) == 2
    assert registry.get(first.skill_id, first.revision_id).instructions == first.instructions

    tombstone = registry.delete(first.skill_id)
    assert tombstone["revision_id"] == second.revision_id
    assert registry.list() == []
    assert registry.get(first.skill_id, second.revision_id).instructions == second.instructions
    with pytest.raises(KeyError, match="unknown skill"):
        registry.get(first.skill_id)


def test_rejects_mismatched_directories_and_symlinks(tmp_path):
    source = _package(tmp_path)
    wrong = tmp_path / "wrong-name"
    source.rename(wrong)
    with pytest.raises(SkillRegistryError, match="directory must match"):
        SkillRegistry(tmp_path / "registry").import_package(wrong)

    wrong.rename(source)
    target = tmp_path / "outside.txt"
    target.write_text("secret")
    (source / "unsafe").symlink_to(target)
    with pytest.raises(SkillRegistryError, match="symlinks"):
        SkillRegistry(tmp_path / "registry-2").import_package(source)


def test_builtin_skill_is_seeded_once_and_user_deletion_is_respected(tmp_path):
    registry = SkillRegistry(tmp_path / "agentic" / "skills")

    ensure_builtin_skill(registry, "sequence-research")

    listed = registry.list()
    assert [item["skill_id"] for item in listed] == ["sequence-research"]
    revision = registry.get("sequence-research")
    assert revision.provenance["source_kind"] == "reasoning-builtin"
    assert registry.verify(revision.skill_id, revision.revision_id)
    assert "finite prefixes" in registry.read_package_file(
        revision.skill_id, revision.revision_id
    )["content"]

    registry.delete("sequence-research")
    ensure_builtin_skill(registry, "sequence-research")
    ensure_builtin_skill(registry, "sequence-research", version=2)

    assert registry.list() == []
    assert registry.get("sequence-research", revision.revision_id) == revision


def test_builtin_upgrade_revises_only_an_unchanged_builtin_head(tmp_path):
    registry = SkillRegistry(tmp_path / "agentic" / "skills")
    old_source = tmp_path / "conjecture-audit"
    old_source.mkdir()
    (old_source / "SKILL.md").write_text(
        "---\n"
        "name: conjecture-audit\n"
        "description: Audit a conjecture.\n"
        "---\n\n"
        "Old packaged instructions.\n",
        encoding="utf-8",
    )
    old = registry.import_package(
        old_source,
        provenance={
            "source_kind": "reasoning-builtin",
            "source_label": "Reasoning",
            "source_path": "old-package",
        },
    )

    ensure_builtin_skill(registry, "conjecture-audit", version=99)

    upgraded = registry.get("conjecture-audit")
    assert upgraded.revision_id != old.revision_id
    assert upgraded.parent_revision_id == old.revision_id
    assert "substitution table" in upgraded.instructions
    assert "load-bearing side condition" in upgraded.instructions

    user_revision = registry.revise(
        "conjecture-audit",
        instructions="User-owned conjecture audit instructions.",
    )
    ensure_builtin_skill(registry, "conjecture-audit", version=100)

    assert registry.get("conjecture-audit") == user_revision
