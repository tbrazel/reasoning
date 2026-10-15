"""Real SQLite workflow definitions, immutable history, tenant boundaries, and API.

Inline ASGI dispatch avoids sandbox-denied cross-thread wakeups; it does not
exercise TLS, rendered browser interaction, or workflow execution.
"""

import asyncio
import copy
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import fastapi.dependencies.utils
import fastapi.routing
import httpx
import pytest
import starlette.concurrency
from public_schema_fixture import remove_collaboration_schema

from reasoning_ledger.server.api import Config, create_app
from reasoning_ledger.server.backup import BackupError, backup, create_key, restore
from reasoning_ledger.server.bot_store import BotStore
from reasoning_ledger.server.compute_catalog import DEFAULT_RESOURCES, canonical
from reasoning_ledger.server.migrations import MIGRATIONS
from reasoning_ledger.server.store import Store, StoreError
from reasoning_ledger.server.workflow_store import WorkflowStore, definition, node_order, read_definition

PASSWORD = "long workflow fixture password"
ORIGIN = "https://chat.example.org"


def dag():
    return {
        "format": 1,
        "name": "Research DAG",
        "nodes": [
            {
                "id": "compute",
                "kind": "compute",
                "depends_on": [],
                "inputs": {},
                "parameters": {
                    "image_id": "approved-cas",
                    "language": "sage",
                    "program": "print(2 + 2)",
                    "resources": dict(DEFAULT_RESOURCES),
                },
            },
            {
                "id": "approve",
                "kind": "approval",
                "depends_on": ["compute"],
                "inputs": {"results": {"node": "compute", "output": "artifacts"}},
                "parameters": {"message": "Review the computation.", "required_role": "admin"},
            },
            {
                "id": "reason",
                "kind": "llm",
                "depends_on": ["compute", "approve"],
                "inputs": {"log": {"node": "compute", "output": "logs"}, "decision": {"node": "approve", "output": "decision"}},
                "parameters": {"model": "fixture/model", "prompt": "Explain the reviewed computation.", "max_completion_tokens": 1024},
            },
        ],
    }


@pytest.fixture
def installation(tmp_path):
    store = Store(tmp_path / "state")
    owner = store.bootstrap(store._setup_token, "owner@example.org", "Owner", PASSWORD, "Organization")
    user, org = owner["user_id"], owner["organization_id"]
    project = store.create_entity(user, org, "projects", "Research")
    channel = store.create_entity(user, org, "channels", "algebra", project["id"])
    topics = {kind: store.create_entity(user, org, "topics", kind, channel["id"], kind)["id"] for kind in ("alpha", "beta", "gamma")}
    return store, WorkflowStore(store), owner, topics


def create(installation, client="workflow-request-001", value=None):
    _, state, owner, topics = installation
    return state.create(owner["user_id"], owner["organization_id"], topics["gamma"], client, dag() if value is None else value)


def revise(installation, saved, client="workflow-request-002", value=None, expected=None):
    _, state, owner, _ = installation
    return state.revise(
        owner["user_id"],
        owner["organization_id"],
        saved["workflow_id"],
        client,
        saved["version"] if expected is None else expected,
        dag() if value is None else value,
    )


def test_complete_dag_is_normalized_without_mutating_source():
    source = dag()
    frozen = copy.deepcopy(source)
    valid = definition(source)
    assert source == frozen
    assert [node["id"] for node in valid["nodes"]] == ["approve", "compute", "reason"]
    assert node_order(valid) == ["compute", "approve", "reason"]
    assert valid["nodes"][-1]["depends_on"] == ["approve", "compute"]
    source["nodes"][0]["parameters"]["resources"]["cpu"] = 64
    assert valid["nodes"][1]["parameters"]["resources"]["cpu"] == 1


@pytest.mark.parametrize("maximum", [None, 131072])
def test_workflow_output_default_is_uncapped_and_explicit_large_limits_are_preserved(maximum):
    value = dag()
    value["nodes"][2]["parameters"]["max_completion_tokens"] = maximum
    assert definition(value)["nodes"][-1]["parameters"]["max_completion_tokens"] == maximum


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {},
        {**dag(), "format": True},
        {**dag(), "format": 2},
        {**dag(), "name": ""},
        {**dag(), "name": " "},
        {**dag(), "name": "x\0y"},
        {**dag(), "name": "x\ny"},
        {**dag(), "name": "\ud800"},
        {**dag(), "nodes": []},
        {**dag(), "nodes": {}},
        {**dag(), "extra": "field"},
    ],
)
def test_invalid_definition_envelopes(bad):
    with pytest.raises(StoreError) as error:
        definition(bad)
    assert error.value.status == 400


@pytest.mark.parametrize(
    "change",
    [
        {"id": "../escape"},
        {"id": "reason"},
        {"id": ""},
        {"id": []},
        {"kind": "shell"},
        {"kind": []},
        {"depends_on": ["compute"]},
        {"depends_on": ["missing"]},
        {"depends_on": ["reason", "reason"]},
        {"depends_on": [False]},
        {"depends_on": {}},
        {"inputs": []},
        {"inputs": {"bad alias": {"node": "reason", "output": "text"}}},
        {"inputs": {"x": {"node": "reason", "output": "text"}}},
        {"inputs": {"x": {"node": [], "output": "logs"}}},
        {"parameters": {}},
        {"parameters": {**dag()["nodes"][0]["parameters"], "network": True}},
        {"parameters": {**dag()["nodes"][0]["parameters"], "language": "bash"}},
        {"parameters": {**dag()["nodes"][0]["parameters"], "program": "\udfff"}},
        {"parameters": {**dag()["nodes"][0]["parameters"], "resources": {**DEFAULT_RESOURCES, "cpu": True}}},
    ],
)
def test_invalid_compute_node(change):
    source = dag()
    source["nodes"][0].update(change)
    with pytest.raises(StoreError) as error:
        definition(source)
    assert error.value.status == 400


@pytest.mark.parametrize(
    "kind,change",
    [
        ("llm", {"max_completion_tokens": True}),
        ("llm", {"max_completion_tokens": 15}),
        ("llm", {"max_completion_tokens": 2**31}),
        ("llm", {"prompt": "\0"}),
        ("llm", {"prompt": "\ud800"}),
        ("llm", {"model": "x\ny"}),
        ("llm", {"api_key": "secret"}),
        ("approval", {"required_role": "viewer"}),
        ("approval", {"required_role": []}),
        ("approval", {"message": " "}),
    ],
)
def test_invalid_llm_and_approval_node(kind, change):
    source = dag()
    next(node for node in source["nodes"] if node["kind"] == kind)["parameters"].update(change)
    with pytest.raises(StoreError) as error:
        definition(source)
    assert error.value.status == 400


def test_cycles_missing_outputs_and_limits():
    source = dag()
    source["nodes"][0]["depends_on"] = ["reason"]
    with pytest.raises(StoreError, match="cycle"):
        definition(source)
    source = dag()
    source["nodes"][2]["inputs"]["log"]["output"] = "text"
    with pytest.raises(StoreError, match="unsupported dependency output"):
        definition(source)
    source = dag()
    source["nodes"] = [{**source["nodes"][0], "id": f"node-{index}"} for index in range(33)]
    with pytest.raises(StoreError, match="32 nodes"):
        definition(source)
    source["nodes"].pop()
    for index, node in enumerate(source["nodes"]):
        node["depends_on"] = [f"node-{parent}" for parent in range(index)]
    with pytest.raises(StoreError, match="128 dependency edges"):
        definition(source)
    source = dag()
    source["nodes"][0]["parameters"]["program"] = "😀" * 16000
    with pytest.raises(StoreError, match="32 KiB"):
        definition(source)


def test_version_persistence_replay_conflicts_immutable_rows_and_restart(installation):
    store, state, owner, topics = installation
    original = create(installation)
    assert original["definition"] == definition(dag())
    assert original["definition_digest"] == "sha256:" + hashlib.sha256(canonical(original["definition"]).encode()).hexdigest()
    assert original["node_order"] == ["compute", "approve", "reason"]
    changed = dag()
    changed["name"] = "Revised DAG"
    second = revise(installation, original, value=changed)
    assert second["version"] == 2
    assert create(installation) == original
    assert revise(installation, original, value=changed) == second
    with pytest.raises(StoreError) as error:
        revise(installation, original, "another-request-003")
    assert error.value.status == 409
    with pytest.raises(StoreError) as error:
        create(installation, value=changed)
    assert error.value.status == 409
    restarted = WorkflowStore(Store(store.data_dir))
    assert restarted.get(owner["user_id"], owner["organization_id"], original["workflow_id"]) == second
    assert restarted.get(owner["user_id"], owner["organization_id"], original["workflow_id"], 1) == original
    assert restarted.versions(owner["user_id"], owner["organization_id"], original["workflow_id"], before=2)[0]["version"] == 1
    summary = restarted.list(owner["user_id"], owner["organization_id"], topics["gamma"])[0]
    assert summary["name"] == changed["name"] and summary["version"] == 2
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM compute_jobs").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM llm_requests").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE action='workflow.version.create'").fetchone()[0] == 2
        for command in ("UPDATE workflow_versions SET definition_json='{}'", "DELETE FROM workflow_versions"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                db.execute(command)


def test_atomic_failure_has_no_head_or_idempotency_or_audit_side_effect(installation):
    store, _, _, _ = installation
    with store.transaction(write=True) as db:
        db.execute("CREATE TRIGGER deny_workflow_write BEFORE INSERT ON workflow_writes BEGIN SELECT RAISE(ABORT,'fixture denial'); END")
    with pytest.raises(sqlite3.IntegrityError, match="fixture denial"):
        create(installation)
    with store.transaction() as db:
        for table in ("workflows", "workflow_versions", "workflow_writes"):
            assert db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE action='workflow.version.create'").fetchone()[0] == 0


def test_parallel_duplicate_and_competing_revisions(installation):
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: create(installation), range(8)))
    assert all(receipt == receipts[0] for receipt in receipts)

    def edit(index):
        try:
            return revise(installation, receipts[0], f"parallel-request-{index:03}")
        except StoreError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(edit, range(4)))
    assert sum(isinstance(result, dict) for result in results) == 1 and results.count(409) == 3


def test_tenant_human_roles_membership_revocation_and_gamma_only(installation):
    store, state, owner, topics = installation
    user, org = owner["user_id"], owner["organization_id"]
    original = create(installation)
    other = store.create_organization(user, "Other")["id"]
    for operation in (
        lambda: state.get(user, other, original["workflow_id"]),
        lambda: state.versions(user, other, original["workflow_id"]),
        lambda: state.list(user, other, topics["gamma"]),
        lambda: state.create(user, other, topics["gamma"], "foreign-request-001", dag()),
        lambda: state.revise(user, other, original["workflow_id"], "foreign-request-002", 1, dag()),
    ):
        with pytest.raises(StoreError) as error:
            operation()
        assert error.value.status == 404
    for kind in ("alpha", "beta"):
        with pytest.raises(StoreError, match="gamma"):
            state.create(user, org, topics[kind], "invalid-topic-001", dag())
    invite = store.invite(user, org, "member@example.org", "member", 3600)
    member = store.accept_invitation(invite["token"], user_id=None, name="Member", password=PASSWORD)["user_id"]
    revision = state.revise(member, org, original["workflow_id"], "member-request-001", 1, dag())
    assert revision["created_by"] == member
    store.change_member(user, org, member, "viewer")
    assert state.get(member, org, original["workflow_id"]) == revision
    for operation in (
        lambda: state.create(member, org, topics["gamma"], "viewer-request-001", dag()),
        lambda: state.revise(member, org, original["workflow_id"], "member-request-001", 1, dag()),
    ):
        with pytest.raises(StoreError) as error:
            operation()
        assert error.value.status == 403
    store.change_member(user, org, member, None)
    with pytest.raises(StoreError) as error:
        state.get(member, org, original["workflow_id"])
    assert error.value.status == 404
    bot = BotStore(store).create(user, org, "Scoped bot", [topics["gamma"]], ["messages:read"])
    # Even an erroneously granted human membership cannot authorize a bot account.
    with store.transaction(write=True) as db:
        db.execute("INSERT INTO memberships VALUES(?,?,?)", (org, bot["id"], "admin"))
    for operation in (
        lambda: state.get(bot["id"], org, original["workflow_id"]),
        lambda: state.create(bot["id"], org, topics["gamma"], "bot-request-001", dag()),
    ):
        with pytest.raises(StoreError) as error:
            operation()
        assert error.value.status == 404


def test_schema_ten_migration_and_backup_restore_preserve_versions_and_replay(installation, tmp_path):
    store, state, owner, topics = installation
    with store.transaction(write=True) as db:
        remove_collaboration_schema(db)
        for table in (
            "knowledge_writes",
            "knowledge_versions",
            "knowledge_entries",
            "project_memberships",
            "user_presence",
            "bot_webhook_requests",
            "bot_webhook_deliveries",
            "bot_webhooks",
            "workflow_run_actions",
            "workflow_run_events",
            "workflow_node_attempts",
            "workflow_runs",
            "workflow_writes",
            "workflow_versions",
            "workflows",
        ):
            db.execute("DROP TABLE " + table)
        db.execute("PRAGMA user_version=10")
        db.execute("DROP TABLE model_impact_profiles")
        db.execute("ALTER TABLE llm_runs DROP COLUMN impact_profile_json")
        db.execute("ALTER TABLE topics DROP COLUMN default_model")
        db.execute("ALTER TABLE topics DROP COLUMN knowledge_policy_json")
        db.execute("ALTER TABLE projects DROP COLUMN visibility")
        db.execute("DROP INDEX message_org_identity")
    upgraded = Store(store.data_dir)
    with upgraded.transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    assert upgraded.session(owner["access_token"])["user_id"] == owner["user_id"]
    saved = create(installation)
    key, archive, target = tmp_path / "key", tmp_path / "backup.rnbk", tmp_path / "restored"
    create_key(key)
    result = backup(store.data_dir, archive, key)
    restore(archive, key, result["sha256"], target, source_stopped=True)
    restored = WorkflowStore(Store(target))
    assert restored.get(owner["user_id"], owner["organization_id"], saved["workflow_id"]) == saved
    assert restored.create(owner["user_id"], owner["organization_id"], topics["gamma"], "workflow-request-001", dag()) == saved
    assert state.get(owner["user_id"], owner["organization_id"], saved["workflow_id"]) == saved


def test_api_authentication_csrf_versions_schema_and_validation(installation, monkeypatch):
    store, _, owner, topics = installation

    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    for module in (fastapi.dependencies.utils, fastapi.routing, starlette.concurrency):
        monkeypatch.setattr(module, "run_in_threadpool", inline)
    app = create_app(Config(store.data_dir, ORIGIN, inference_enabled=False))
    root = "/api/organizations/" + owner["organization_id"]
    path = root + "/topics/" + topics["gamma"] + "/workflows"
    body = {"client_id": "workflow-request-001", "definition": dag()}
    bot = BotStore(store).create(owner["user_id"], owner["organization_id"], "Scoped bot", [topics["gamma"]], ["messages:read"])

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
            assert (await client.get(path)).status_code == 401
            client.headers["Authorization"] = "Bearer " + bot["token"]
            assert (await client.get(path)).status_code == 401
            assert (await client.post(path, json=body)).status_code == 401
            client.headers["Authorization"] = "Bearer " + owner["access_token"]
            response = await client.post(path, json=body)
            assert response.status_code == 201
            saved = response.json()
            resource = root + "/workflows/" + saved["workflow_id"]
            assert (await client.post(path, json=body)).json() == saved
            assert (await client.get(resource)).json() == saved
            assert (await client.get(resource + "/versions/1")).json() == saved
            assert (await client.get(resource + "/versions/0")).status_code == 422
            assert (await client.get(resource + "/versions/2")).status_code == 404
            assert (await client.get(path + "?limit=0")).status_code == 422
            assert (await client.get(path + "?after=invalid")).status_code == 400
            assert (await client.get(resource + "/versions")).json()[0]["version"] == 1
            assert (
                await client.post(resource + "/versions", json={**body, "client_id": "workflow-request-002", "expected_version": True})
            ).status_code == 422
            invalid = {**body, "definition": {**dag(), "secret-field": "DO-NOT-REFLECT"}}
            refused = await client.post(path, json=invalid)
            assert refused.status_code == 400 and "DO-NOT-REFLECT" not in refused.text
            assert (await client.post(path, content=b"{}", headers={"Content-Type": "text/plain"})).status_code == 415
            assert (await client.post(path, content=b" " * 65537, headers={"Content-Type": "application/json"})).status_code == 413
            client.headers.pop("Authorization")
            client.cookies.set("__Host-reasoning_session", owner["access_token"])
            assert (await client.post(path, json=body)).status_code == 403
            assert (await client.post(path, json=body, headers={"Origin": ORIGIN, "X-CSRF-Token": owner["csrf_token"]})).json() == saved

    asyncio.run(check())


def test_definition_write_quota_is_durable_but_exact_replay_is_free(installation):
    store, _, _, _ = installation
    saved = create(installation)
    for index in range(59):
        create(installation, f"quota-request-{index:03}")
    with pytest.raises(StoreError) as error:
        create(installation, "quota-request-last")
    assert error.value.status == 429
    assert create(installation) == saved
    restarted = WorkflowStore(Store(store.data_dir))
    _, _, owner, topics = installation
    with pytest.raises(StoreError) as error:
        restarted.create(owner["user_id"], owner["organization_id"], topics["gamma"], "quota-request-last", dag())
    assert error.value.status == 429


def test_topic_and_version_caps_and_keyset_pages(installation, monkeypatch):
    from reasoning_ledger.server import workflow_store

    clock = [workflow_store.time.time()]
    monkeypatch.setattr(workflow_store.time, "time", lambda: clock[0])
    store, state, owner, topics = installation
    first = create(installation)
    for index in range(99):
        clock[0] += 3601
        create(installation, f"page-request-{index:03}")
    user, org = owner["user_id"], owner["organization_id"]
    page = state.list(user, org, topics["gamma"], limit=50)
    second = state.list(user, org, topics["gamma"], after=page[-1]["id"])
    assert len(page) == len(second) == 50 and not {item["id"] for item in page} & {item["id"] for item in second}
    with pytest.raises(StoreError, match="100 workflow limit"):
        create(installation, "page-request-last")
    current = first
    for index in range(127):
        clock[0] += 3601
        current = revise(installation, current, f"version-request-{index:03}")
    assert current["version"] == 128
    with pytest.raises(StoreError, match="128 version limit"):
        revise(installation, current, "version-request-last")
    assert state.get(user, org, first["workflow_id"])["version"] == 128
    all_versions, before = [], 129
    while page := state.versions(user, org, first["workflow_id"], before=before):
        all_versions.extend(item["version"] for item in page)
        before = page[-1]["version"]
    assert all_versions == list(range(128, 0, -1))
    with store.transaction() as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("source,digest", [("{}", "sha256:" + "0" * 64), ("{", "missing"), (canonical(definition(dag())), "missing")])
def test_read_rejects_invalid_or_inconsistent_stored_definitions(source, digest):
    with pytest.raises(ValueError, match="integrity check"):
        read_definition(source, digest)


def test_inspection_and_backup_refuse_corrupt_inserted_versions(installation, tmp_path):
    store, state, owner, _ = installation
    saved = create(installation)
    org, workflow = owner["organization_id"], saved["workflow_id"]
    with store.transaction(write=True) as db:
        db.execute(
            "INSERT INTO workflow_versions VALUES(?,?,?,?,?,?,?)", (org, workflow, 2, "{}", "sha256:" + "0" * 64, owner["user_id"], 1)
        )
        db.execute(
            "INSERT INTO workflow_writes VALUES(?,?,?,?,?,?,?)", (org, owner["user_id"], "corrupt-request-002", "{}", workflow, 2, 1)
        )
        db.execute("UPDATE workflows SET head_version=2 WHERE id=?", (workflow,))
    with pytest.raises(StoreError, match="integrity check"):
        state.get(owner["user_id"], org, workflow)
    key = tmp_path / "key"
    create_key(key)
    with pytest.raises(BackupError, match="workflow definition integrity check"):
        backup(store.data_dir, tmp_path / "invalid.rnbk", key)
    with pytest.raises(sqlite3.IntegrityError), store.transaction(write=True) as db:
        db.execute("UPDATE workflows SET head_version=3 WHERE id=?", (workflow,))
