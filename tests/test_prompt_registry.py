import json

import pytest
from jsonschema import ValidationError

from reasoning_ledger.agents import PromptRegistry


def test_prompt_revisions_are_immutable_and_exact(tmp_path):
    registry = PromptRegistry(tmp_path / "prompts")
    first = registry.create(
        name="Weekly student",
        kind="user",
        template="Week {{ week }}\nQuestion: {{question}}\n",
        variable_schema={
            "type": "object",
            "properties": {
                "week": {"type": "integer"},
                "question": {"type": "string"},
            },
            "required": ["week", "question"],
            "additionalProperties": False,
        },
    )
    assert first.prompt_id == "weekly-student"
    assert first.render({"week": 3, "question": "Prove X."}) == "Week 3\nQuestion: Prove X.\n"

    second = registry.revise(
        first.prompt_id,
        template="Research week {{week}}: {{question}}",
    )
    assert second.revision_id != first.revision_id
    assert second.parent_revision_id == first.revision_id
    assert registry.get(first.prompt_id, first.revision_id).template == first.template
    assert registry.get(first.prompt_id).revision_id == second.revision_id
    assert len(registry.revisions(first.prompt_id)) == 2


def test_prompt_variables_are_validated_and_json_values_are_canonical(tmp_path):
    registry = PromptRegistry(tmp_path / "prompts")
    revision = registry.create(
        name="Context packet",
        kind="context",
        template="{{packet}}",
        variable_schema={"type": "object", "required": ["packet"]},
    )
    assert revision.render({"packet": {"b": 2, "a": 1}}) == '{"a":1,"b":2}'
    with pytest.raises(ValueError, match="missing prompt variables"):
        revision.render({})

    strict = registry.create(
        name="Strict",
        kind="user",
        template="{{week}}",
        variable_schema={
            "type": "object",
            "properties": {"week": {"type": "integer"}},
            "required": ["week"],
        },
    )
    with pytest.raises(ValidationError):
        strict.render({"week": "one"})


def test_duplicate_ids_and_unknown_revisions_are_rejected(tmp_path):
    registry = PromptRegistry(tmp_path / "prompts")
    revision = registry.create(name="Advisor", kind="system", template="Advise.")
    with pytest.raises(ValueError, match="already exists"):
        registry.create(
            prompt_id=revision.prompt_id,
            name="Other",
            kind="system",
            template="Other",
        )
    with pytest.raises(KeyError, match="unknown prompt revision"):
        registry.get(revision.prompt_id, "prmrev_missing")

    stored = json.loads(
        (
            tmp_path
            / "prompts"
            / revision.prompt_id
            / "revisions"
            / f"{revision.revision_id}.json"
        ).read_text()
    )
    assert stored["revision_id"] == revision.revision_id


def test_deleted_prompt_leaves_pinned_revisions_reproducible(tmp_path):
    registry = PromptRegistry(tmp_path / "prompts")
    revision = registry.create(
        name="Temporary advisor",
        kind="system",
        template="Advise on {{question}}.",
    )

    tombstone = registry.delete(revision.prompt_id)

    assert tombstone["revision_id"] == revision.revision_id
    assert registry.list() == []
    assert registry.get(revision.prompt_id, revision.revision_id).render(
        {"question": "X"}
    ) == "Advise on X."
    with pytest.raises(KeyError, match="unknown prompt"):
        registry.get(revision.prompt_id)
