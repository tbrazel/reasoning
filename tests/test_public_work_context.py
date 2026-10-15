"""Pinned role material, narrow version reads, and durable internal compaction."""

import json
from typing import get_args

import pytest
from postgres_fixture import postgres_configuration as postgres_configuration
from pydantic import ValidationError
from test_public_work_contracts import specification, task
from test_public_work_jobs import claim, environment, expire, lease, queued, worker_post
from test_public_work_jobs import installation as installation

from reasoning_ledger.server import work_prompts
from reasoning_ledger.server.research_artifacts import ResearchArtifacts
from reasoning_ledger.server.research_context import encoded
from reasoning_ledger.server.security import digest
from reasoning_ledger.server.work_contracts import Role, Strategy, WorkSpecification
from reasoning_ledger.server.work_jobs import WorkJobs


def test_strategies_have_no_implicit_modifier_and_reject_duplicates_or_unknowns():
    spec = WorkSpecification.model_validate(specification())
    assert spec.strategies == []
    expected = spec.model_dump()
    expected.pop("strategies")
    assert spec.canonical() == encoded(expected)
    assert WorkSpecification.model_validate_json(encoded(expected)).digest() == spec.digest()
    for strategies in (["breadth", "breadth"], ["domain_specific_hardcoded_strategy"]):
        with pytest.raises(ValidationError):
            WorkSpecification.model_validate(specification(strategies=strategies))


def test_catalog_has_seven_general_roles_and_content_verified_modifiers(installation):
    _, _, client, owner = installation
    reply = client.get(f"/api/organizations/{owner['organization_id']}/work-prompts")
    assert reply.status_code == 200
    catalog = reply.json()
    assert set(catalog["roles"]) == set(get_args(Role)) and len(catalog["roles"]) == 7
    assert set(catalog["strategies"]) == set(get_args(Strategy))
    for part in [catalog["common"], *catalog["roles"].values(), *catalog["strategies"].values()]:
        assert part["sha256"] == digest(part["text"]) and part["revision"] == 1
    assert "without configuring" in catalog["roles"]["specification_reviewer"]["text"]


def test_task_prompt_is_pinned_before_template_changes(installation, monkeypatch):
    env = environment(installation)
    created = task(
        env["client"],
        env["path"],
        env["root"],
        "prompt-task",
        specification=specification(role="worker", strategies=["retrieval_first", "adversarial"], resources=env["resources"]),
    ).json()
    prompt_path = env["target"] + "/tasks/" + created["id"] + "/prompt"
    original = env["client"].get(prompt_path).json()
    assert original["sha256"] == digest(encoded(original["prompt"]))
    assert [part["id"] for part in original["prompt"]["parts"]] == [
        "work-common",
        "work-role-worker",
        "work-strategy-retrieval_first",
        "work-strategy-adversarial",
    ]
    prior = work_prompts._part

    def changed(name):
        part = prior(name)
        part["text"] += "\nTemplate update for a future assignment.\n"
        part["sha256"] = digest(part["text"])
        return part

    monkeypatch.setattr(work_prompts, "_part", changed)
    env["client"].post(env["target"] + "/tasks/" + created["id"] + "/jobs", json={"expected_epoch": 0}).raise_for_status()
    row = claim(env)
    assert row["packet"]["prompt"] == original["prompt"]
    packet = row["packet"].copy()
    assert packet.pop("sha256") == digest(encoded(packet))
    assert env["client"].get(prompt_path).json() == original
    other, _ = queued(env, "future-task")
    newer = env["client"].get(env["target"] + "/tasks/" + other["id"] + "/prompt").json()
    assert newer["sha256"] != original["sha256"]
    with env["store"].transaction(write=True) as db, pytest.raises(Exception, match="task prompts are immutable"):
        db.execute("UPDATE work_task_prompts SET sha256=? WHERE task_id=?", ("a" * 64, created["id"]))


def assigned_input(env):
    artifacts = ResearchArtifacts(env["store"])
    first = artifacts.write(
        env["owner"]["user_id"], env["owner"]["organization_id"], env["topics"][1]["id"], "source.md", "Pinned source text"
    )
    artifacts.write(
        env["owner"]["user_id"], env["owner"]["organization_id"], env["topics"][1]["id"], "source.md", "Newer text", first["sha256"]
    )
    ref = {"kind": "research_artifact", "id": first["id"], "revision": 1, "sha256": first["sha256"]}
    record = task(
        env["client"],
        env["path"],
        env["root"],
        "scoped-input",
        specification=specification(role="worker", resources=env["resources"], inputs=[ref]),
    ).json()
    env["client"].post(env["target"] + "/tasks/" + record["id"] + "/jobs", json={"expected_epoch": 0}).raise_for_status()
    return ref, record, claim(env)


def test_worker_reads_only_assigned_exact_inputs_in_bounded_slices(installation):
    env = environment(installation)
    ref, _, row = assigned_input(env)
    path = f"jobs/{row['id']}/inputs/read"
    base = lease(row) | {"reference": ref, "length": 6}
    first = worker_post(env, path, base)
    assert first.status_code == 200, first.text
    assert first.json()["text"] == "Pinned" and first.json()["truncated"]
    assert worker_post(env, path, base | {"offset": 7, "length": 16000}).json()["text"] == "source text"
    assert worker_post(env, path, base | {"reference": ref | {"revision": 2}}).status_code == 404
    assert worker_post(env, path, base | {"reference": ref | {"sha256": "0" * 64}}).status_code == 404
    assert worker_post(env, path, base | {"length": 16001}).status_code == 422
    unassigned = {"kind": "work_spec", "id": env["root"]["id"], "revision": 1, "sha256": env["root"]["version"]["sha256"]}
    assert worker_post(env, path, base | {"reference": unassigned}).status_code == 404


def test_compaction_retains_provenance_and_survives_fenced_reassignment(installation):
    env = environment(installation)
    ref, record, old = assigned_input(env)
    body = lease(old) | {
        "client_id": "compact-1",
        "summary": "Keep the exact source and unresolved coverage gap.",
        "retained_inputs": [ref],
        "unresolved": ["Coverage is not exhaustive"],
        "next_actions": ["Ask a librarian for the missing source"],
        "obstructions": ["No primary source for the proposed implication"],
    }
    response = worker_post(env, f"jobs/{old['id']}/compact", body)
    assert response.status_code == 200, response.text
    assert worker_post(env, f"jobs/{old['id']}/compact", body).json() == response.json()
    assert worker_post(env, f"jobs/{old['id']}/compact", body | {"summary": "Altered replay"}).status_code == 409
    with env["store"].transaction() as db:
        saved = db.execute("SELECT body_json FROM work_compactions WHERE sequence=?", (response.json()["sequence"],)).fetchone()[0]
        assert digest(saved) == response.json()["sha256"]
        assert "lease" not in saved and "attempt_id" not in saved
        refs = db.execute(
            "SELECT input_sha256 FROM work_provenance WHERE owner_kind='compaction' AND owner_id=?", (str(response.json()["sequence"]),)
        ).fetchall()
        assert [item[0] for item in refs] == [ref["sha256"]]
    current = worker_post(env, f"jobs/{old['id']}/context", lease(old)).json()
    assert current["compaction"]["sha256"] == response.json()["sha256"]
    assert current["specification"]["acceptance"] == record["specification"]["acceptance"]
    expire(env, old["id"])
    WorkJobs(env["store"]).expire_due()
    fresh = claim(env, "fresh-context")
    assert fresh["fence"] == old["fence"] + 1
    assert fresh["packet"]["compaction"] == current["compaction"]
    assert worker_post(env, f"jobs/{old['id']}/compact", body).status_code == 409


def test_compaction_cannot_launder_an_unassigned_input(installation):
    env = environment(installation)
    _, _, row = assigned_input(env)
    unassigned = {"kind": "work_spec", "id": env["root"]["id"], "revision": 1, "sha256": env["root"]["version"]["sha256"]}
    body = lease(row) | {"client_id": "launder", "summary": "An unassigned reference", "retained_inputs": [unassigned]}
    assert worker_post(env, f"jobs/{row['id']}/compact", body).status_code == 404
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_compactions").fetchone()[0] == 0


def test_revoked_project_grant_hides_context_and_replayed_packets(installation):
    env = environment(installation)
    ref, _, row = assigned_input(env)
    with env["store"].transaction(write=True) as db:
        db.execute("DELETE FROM work_worker_projects WHERE worker_id=?", (env["worker"]["id"],))
    assert worker_post(env, f"jobs/{row['id']}/context", lease(row)).status_code == 403
    assert worker_post(env, f"jobs/{row['id']}/inputs/read", lease(row) | {"reference": ref}).status_code == 403
    replay = claim(env)
    assert replay["dispatch"] is False and replay["packet"] is None
    assert "Pinned source" not in json.dumps(replay)


def test_compaction_and_pinned_prompt_survive_portable_recovery(installation, tmp_path):
    from reasoning_ledger.server.backup import backup, create_key, restore
    from reasoning_ledger.server.store import Store

    env = environment(installation)
    reference, record, row = assigned_input(env)
    response = worker_post(
        env,
        f"jobs/{row['id']}/compact",
        lease(row) | {"client_id": "recovery-context", "summary": "Retain exact evidence for human review", "retained_inputs": [reference]},
    )
    assert response.status_code == 200, response.text
    prompt = env["client"].get(env["target"] + "/tasks/" + record["id"] + "/prompt").json()
    key, archive, destination = tmp_path / "key", tmp_path / "archive.rnbk", tmp_path / "recovered"
    create_key(key)
    saved = backup(env["store"].data_dir, archive, key)
    restore(archive, key, saved["sha256"], destination, source_stopped=True)
    recovered = Store(destination)
    with recovered.transaction() as db:
        assert (
            db.execute("SELECT sha256 FROM work_compactions WHERE sequence=?", (response.json()["sequence"],)).fetchone()[0]
            == response.json()["sha256"]
        )
        retained = db.execute("SELECT body_json,sha256 FROM work_task_prompts WHERE task_id=?", (record["id"],)).fetchone()
        assert retained["sha256"] == prompt["sha256"] and json.loads(retained["body_json"]) == prompt["prompt"]
