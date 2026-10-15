"""General work versions, narrow provenance, replay safety and concurrent edits."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError
from test_public_server import installation as installation
from test_public_server import invited, structure

from reasoning_ledger.server.research_artifacts import ResearchArtifacts
from reasoning_ledger.server.research_context import encoded
from reasoning_ledger.server.store import StoreError
from reasoning_ledger.server.work_contracts import AuthorizedResources, WorkCreate, WorkRevision, WorkSpecification


def specification(**changes):
    return {
        "objective": "Map the evidence for competing approaches, recording coverage gaps.",
        "kind": "explore", "role": "coordinator", "controller": "parallel_integrate",
        "acceptance": [{
            "id": "coverage", "description": "Deliver a sourced coverage map, not proof of its claims.", "check": "source_check"
        }],
        **changes,
    }


def program(installation, **changes):
    _, _, client, owner = installation
    project, _, topics = structure(client, owner["organization_id"])
    path = f"/api/organizations/{owner['organization_id']}/projects/{project['id']}/work"
    response = client.post(path, json={"client_id": "initial-work", "specification": specification(**changes)})
    assert response.status_code == 201, response.text
    return path, response.json(), project, topics


def task(client, path, root, client_id, **changes):
    return client.post(path + "/" + root["id"] + "/tasks", json={
        "client_id": client_id, "expected_root_revision": root["head_revision"],
        "specification": specification(role="worker"), **changes,
    })


def test_general_contract_defaults_do_not_hide_tool_token_or_turn_ceilings():
    spec = WorkSpecification.model_validate(specification())
    assert spec.resources.max_tool_calls is None
    assert spec.resources.max_turns is None
    assert spec.resources.budget_microusd is None
    assert spec.resources.cpu_seconds is None
    assert spec.resources.allowed_models == []  # No implicit permission to spend.
    assert "tokens" not in spec.model_dump()["resources"]
    assert len(spec.digest()) == 64
    assert WorkSpecification.model_validate_json(spec.canonical()).digest() == spec.digest()


@pytest.mark.parametrize("change", [
    {"acceptance": []}, {"kind": "prove_all_math"}, {"role": "independent_reviewer"},
    {"controller": "make_stuff_up"}, {"objective": " "}, {"extra": True},
    {"resources": {"max_tool_calls": 0}}, {"resources": {"max_turns": True}},
    {"resources": {"budget_microusd": -1}}, {"resources": {"allowed_models": ["a/b", "a/b"]}},
    {"acceptance": [{"id": "same", "description": "x", "check": "tests_passed"}] * 2},
    {"inputs": [{"kind": "work_spec", "id": "any", "revision": "head", "sha256": "a" * 64}]},
    {"inputs": [{"kind": "mathematical", "id": "claims/x/head.json", "revision": 1, "sha256": "a" * 64}]},
])
def test_contract_rejects_ambiguous_or_invalid_authorization(change):
    with pytest.raises(ValidationError):
        WorkSpecification.model_validate(specification(**change))


def test_each_verification_kind_is_distinct():
    kinds = ("human_review", "source_check", "tests_passed", "llm_review", "proof_checked")
    spec = WorkSpecification.model_validate(specification(acceptance=[
        {"id": kind, "description": "Explicit evidence required", "check": kind} for kind in kinds
    ]))
    assert [item.check for item in spec.acceptance] == list(kinds)


def test_child_envelope_cannot_escalate_or_remove_parent_limits():
    parent = AuthorizedResources(budget_microusd=100, max_tool_calls=500, allowed_models=["a/b"], max_concurrency=8)
    assert parent.contains(parent)
    assert parent.contains(AuthorizedResources(budget_microusd=80, max_tool_calls=20, allowed_models=["a/b"]))
    assert not parent.contains(AuthorizedResources(budget_microusd=101, max_tool_calls=20))
    assert not parent.contains(AuthorizedResources(budget_microusd=10))
    assert not parent.contains(AuthorizedResources(budget_microusd=10, max_tool_calls=20, allowed_models=["a/c"]))
    assert not parent.contains(AuthorizedResources(budget_microusd=10, max_tool_calls=20, max_concurrency=9))


def test_create_replay_versions_and_stale_edits(installation):
    _, store, client, _ = installation
    path, root, _, _ = program(installation)
    resource = path + "/" + root["id"]
    response = client.patch(resource, json={
        "expected_revision": 1, "reason": "Broaden the source coverage", "specification": specification(objective="Read additional sources")
    })
    assert response.status_code == 200, response.text
    assert response.json()["head_revision"] == 2
    assert response.json()["version"]["sha256"] != root["version"]["sha256"]
    original = client.get(resource, params={"revision": 1}).json()
    assert original["version"] == root["version"]
    assert original["head_revision"] == 2
    assert client.get(resource, params={"revision": 3}).status_code == 404
    assert client.patch(resource, json={
        "expected_revision": 1, "reason": "Stale update", "specification": specification()
    }).status_code == 409
    assert client.post(path, json={"client_id": "initial-work", "specification": specification()}).json()["head_revision"] == 2
    assert client.post(path, json={"client_id": "initial-work", "specification": specification(objective="Different")}).status_code == 409
    with store.transaction(write=True) as db, pytest.raises(Exception, match="work specifications are immutable"):
        db.execute("UPDATE work_specifications SET reason='changed' WHERE program_id=?", (root["id"],))


def test_concurrent_create_and_revision_are_compare_and_set(installation):
    _, store, client, owner = installation
    project, _, _ = structure(client, owner["organization_id"])
    work = client.app.state.work
    body = WorkCreate(client_id="concurrent", specification=specification())
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda _: work.create(owner["user_id"], owner["organization_id"], project["id"], body), range(8)))
    assert len({row["id"] for row in rows}) == 1
    root = rows[0]

    def edit(index):
        try:
            return work.revise(owner["user_id"], owner["organization_id"], project["id"], root["id"], WorkRevision(
                expected_revision=1, reason=f"Concurrent edit {index}", specification=specification(objective=f"Objective {index}")
            ))
        except StoreError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(edit, range(8)))
    assert results.count(409) == 7
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_specifications WHERE program_id=?", (root["id"],)).fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM work_events WHERE program_id=?", (root["id"],)).fetchone()[0] == 2


def test_tasks_are_pinned_and_replay_checked(installation):
    _, store, client, _ = installation
    path, root, _, _ = program(installation)
    first = task(client, path, root, "source-collection", specification=specification(kind="collect", role="librarian")).json()
    second = task(client, path, root, "analysis", parent_id=first["id"], depends_on=[first["id"]])
    assert second.status_code == 201, second.text
    replay = task(client, path, root, "analysis", parent_id=first["id"], depends_on=[first["id"]])
    assert replay.json() == second.json()
    assert task(client, path, root, "analysis").status_code == 409
    assert task(client, path, root, "unavailable", depends_on=["missing"]).status_code == 404
    resource = path + "/" + root["id"]
    client.patch(resource, json={"expected_revision": 1, "reason": "Correction", "specification": specification(objective="New objective")})
    assert task(client, path, root, "stale-packet").status_code == 409
    read = client.get(resource + "/tasks/" + second.json()["id"]).json()
    assert read["root_revision"] == 1
    assert read["depends_on"] == [first["id"]]
    with store.transaction(write=True) as db, pytest.raises(Exception, match="task specifications are immutable"):
        db.execute("UPDATE work_tasks SET body_json='{}' WHERE id=?", (first["id"],))


def test_task_envelopes_share_root_authorization(installation):
    _, _, client, _ = installation
    path, root, _, _ = program(installation, resources={"budget_microusd": 100, "max_tool_calls": 800, "max_concurrency": 3})
    assert task(client, path, root, "remove-bounds").status_code == 403
    assert task(client, path, root, "excess", specification=specification(resources={
        "budget_microusd": 101, "max_tool_calls": 800, "max_concurrency": 3
    })).status_code == 403
    for index in range(6):
        response = task(client, path, root, f"bounded-{index}", specification=specification(resources={
            "budget_microusd": 100, "max_tool_calls": 800, "max_concurrency": 3
        }))
        assert response.status_code == 201, response.text  # Queue length is not concurrency.


def test_progressive_lists_and_role_frontiers(installation):
    _, _, client, _ = installation
    path, root, _, _ = program(installation)
    for index in range(7):
        assert task(client, path, root, f"task-{index}").status_code == 201
    resource = path + "/" + root["id"] + "/tasks"
    items, after = [], ""
    while True:
        page = client.get(resource, params={"limit": 2, "after": after, "status": "pending"}).json()
        items.extend(page["items"])
        if not page["next"]:
            break
        after = page["next"]
    assert len(items) == 7
    assert len({item["id"] for item in items}) == 7
    assert all("specification" not in item for item in items)
    assert client.get(resource, params={"limit": 101}).status_code == 422
    assert client.get(resource, params={"status": "nope"}).status_code == 422
    assert len(client.get(path).json()["items"]) == 1


def test_exact_artifact_and_work_spec_provenance(installation):
    _, store, client, owner = installation
    path, root, project, topics = program(installation)
    artifacts = ResearchArtifacts(store)
    first = artifacts.write(owner["user_id"], owner["organization_id"], topics[1]["id"], "sources.md", "First source")
    artifacts.write(owner["user_id"], owner["organization_id"], topics[1]["id"], "sources.md", "Updated source", first["sha256"])
    refs = [
        {"kind": "research_artifact", "id": first["id"], "revision": 1, "sha256": first["sha256"]},
        {"kind": "work_spec", "id": root["id"], "revision": 1, "sha256": root["version"]["sha256"]},
    ]
    response = task(client, path, root, "pinned", specification=specification(inputs=refs))
    assert response.status_code == 201, response.text
    with store.transaction() as db:
        rows = list(db.execute("SELECT * FROM work_provenance WHERE owner_id=?", (response.json()["id"],)))
    assert len(rows) == 2
    assert all(row["input_revision"] == "1" for row in rows)
    bad = [refs[0] | {"sha256": "0" * 64}]
    assert task(client, path, root, "wrong-hash", specification=specification(inputs=bad)).status_code == 409
    bad = [refs[0] | {"revision": 3}]
    assert task(client, path, root, "wrong-version", specification=specification(inputs=bad)).status_code == 404
    # A human who owns both projects still cannot widen project-scoped evidence.
    other = client.post(f"/api/organizations/{owner['organization_id']}/projects", json={"name": "Other"}).json()
    other_path = path.replace(project["id"], other["id"])
    assert client.post(other_path, json={"client_id": "leak", "specification": specification(inputs=refs)}).status_code == 404


def test_private_projects_and_viewers_do_not_gain_work_write_access(installation):
    _, _, client, owner = installation
    root_path = f"/api/organizations/{owner['organization_id']}"
    private = client.post(root_path + "/projects", json={
        "name": "Private", "visibility": "private", "owner_id": owner["user_id"]
    }).json()
    private_path = root_path + "/projects/" + private["id"] + "/work"
    root = client.post(private_path, json={"client_id": "private-work", "specification": specification()}).json()
    _, viewer, _ = invited(installation, role="viewer")
    assert viewer.get(private_path).status_code == 404
    assert viewer.get(private_path + "/" + root["id"]).status_code == 404
    path, _, _, _ = program(installation)
    assert viewer.get(path).status_code == 200
    assert viewer.post(path, json={"client_id": "forbidden", "specification": specification()}).status_code == 403


def test_catalogue_hints_do_not_restrict_program_models_or_allow_invalid_utf8(installation):
    _, store, client, _ = installation
    path, _, _, _ = program(installation)
    assert client.post(path, json={"client_id": "current-catalogue-model", "specification": specification(resources={
        "allowed_models": ["newly/available"]
    })}).status_code == 201
    body = json.dumps({"client_id": "bad-text", "specification": specification(objective="\ud800")})
    assert client.post(path, content=body, headers={"content-type": "application/json"}).status_code == 422
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_programs").fetchone()[0] == 2


def test_knowledge_neighborhoods_allow_cycles_without_proof_or_execution_edges(installation):
    _, store, client, owner = installation
    path, root, project, _ = program(installation)
    knowledge_path = f"/api/organizations/{owner['organization_id']}/knowledge"
    references = []
    for index in range(3):
        response = client.post(knowledge_path, json={
            "client_id": f"knowledge-record-{index:016d}", "kind": "ledger", "title": f"Approach {index}",
            "body": "A source-backed observation, not a checked proof.", "state": "note",
            "scope_kind": "project", "scope_id": project["id"],
        })
        assert response.status_code == 201, response.text
        record = response.json()
        checksum = hashlib.sha256(encoded({key: record[key] for key in ("title", "body", "state")}).encode()).hexdigest()
        references.append({"kind": "knowledge", "id": record["id"], "revision": 1, "sha256": checksum})
    relation_path = path + "/" + root["id"] + "/relations"
    for index, target in enumerate((1, 2, 0)):
        body = {"client_id": f"relation-{index}", "source": references[index], "target": references[target],
                "relation": "equivalent", "explanation": "Compare these approaches; equivalence remains an annotation."}
        response = client.post(relation_path, json=body)
        assert response.status_code == 201, response.text
        assert client.post(relation_path, json=body).json() == response.json()
        assert client.post(relation_path, json=body | {"relation": "contradicts"}).status_code == 409
    for direction in ("in", "out"):
        page = client.get(relation_path, params={"kind": "knowledge", "identity": references[0]["id"],
                                                "revision": "1", "direction": direction, "limit": 1}).json()
        assert len(page["items"]) == 1
        assert page["next"] is None
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_dependencies").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM mathematical_records").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM work_provenance WHERE owner_kind='relation'").fetchone()[0] == 6
    body = {"client_id": "inconsistent-digests", "source": references[0] | {"sha256": "0" * 64},
            "target": references[0], "relation": "equivalent", "explanation": "Invalid source digest must not disappear."}
    assert client.post(relation_path, json=body).status_code == 409


def test_mathematical_revision_inputs_never_resolve_mutable_heads(installation):
    from reasoning_ledger.server.mathematical_store import MathematicalStore

    _, store, client, owner = installation
    path, root, project, _ = program(installation)
    with store.transaction(write=True) as db:
        ledger = MathematicalStore(store, db, owner["user_id"], owner["organization_id"], project=project["id"])
        head, revision = ledger.create_claim("An example research statement, with no asserted proof.")
        row = db.execute("SELECT path,body_json FROM mathematical_records WHERE record_type='claim-revision'").fetchone()
        reference = {"kind": "mathematical", "id": row["path"], "revision": revision["revision_id"],
                     "sha256": hashlib.sha256(row["body_json"].encode()).hexdigest()}
    assert task(client, path, root, "exact-math", specification=specification(inputs=[reference])).status_code == 201
    reference["id"] = f"claims/{head['claim_id']}/head.json"
    assert task(client, path, root, "mutable-head", specification=specification(inputs=[reference])).status_code == 404


def test_topic_scoped_knowledge_is_not_widened_to_a_project(installation):
    _, _, client, owner = installation
    path, root, _, topics = program(installation)
    record = client.post(f"/api/organizations/{owner['organization_id']}/knowledge", json={
        "client_id": "narrow-topic-knowledge", "kind": "ledger", "title": "Narrow", "body": "Restricted discussion",
        "state": "note", "scope_kind": "topic", "scope_id": topics[1]["id"],
    }).json()
    ref = {"kind": "knowledge", "id": record["id"], "revision": 1,
           "sha256": hashlib.sha256(encoded({key: record[key] for key in ("title", "body", "state")}).encode()).hexdigest()}
    assert task(client, path, root, "narrow-input", specification=specification(inputs=[ref])).status_code == 404


def test_task_models_use_parent_envelope_not_organization_catalogue_hints(installation):
    _, _, client, owner = installation
    policy_path = f"/api/organizations/{owner['organization_id']}/policy"
    client.put(policy_path, json={"allowed_models": ["example/model"], "monthly_budget_microusd": 1000}).raise_for_status()
    path, root, _, _ = program(installation, resources={"allowed_models": ["example/model"]})
    client.put(policy_path, json={"allowed_models": [], "monthly_budget_microusd": 1000}).raise_for_status()
    response = task(client, path, root, "revoked-model", specification=specification(resources={"allowed_models": ["example/model"]}))
    assert response.status_code == 201
    outside = task(client, path, root, "outside-parent-model", specification=specification(resources={"allowed_models": ["other/model"]}))
    assert outside.status_code == 403
