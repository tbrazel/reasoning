# Ruff 0.3.4 and 0.15.x classify the src-layout package differently.
# ruff: noqa: I001
from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

from reasoning_ledger.cloud_client import (
    CLOUD_CLIENT_VERSION,
    AcceptedCloudClientRecord,
    CloudClient,
    CloudClientContractError,
    CloudClientContractValidator,
    CloudClientCredentialError,
    CloudClientIdempotencyConflict,
    CloudClientStateError,
    CloudSessionProvider,
    ContractRecordReference,
    EventReplayState,
    InMemoryCloudClient,
    accept_event_page,
    byte_digest,
    canonical_run_request_digest,
    idempotency_key_digest,
    jcs_digest,
)
from reasoning_ledger.harnesses.desktop_hybrid import AcceptedContractRecord

ROOT = Path(__file__).resolve().parents[1]
POSITIVE = ROOT / "conformance" / "cloud-client" / "v1alpha1" / "fixtures" / "positive"
PLATFORM_POSITIVE = ROOT / "conformance" / "platform" / "v1alpha1" / "fixtures" / "positive"


def _load(path: Path):
    import json

    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def validator() -> CloudClientContractValidator:
    return CloudClientContractValidator(ROOT)


@pytest.fixture
def records(validator: CloudClientContractValidator):
    return {path.stem: AcceptedCloudClientRecord.from_bytes(path.read_bytes(), validator=validator) for path in POSITIVE.glob("*.json")}


@pytest.fixture
def contract_records():
    paths = (
        PLATFORM_POSITIVE / "harness-provider.local.json",
        PLATFORM_POSITIVE / "inference-route.hosted.json",
        PLATFORM_POSITIVE / "compute-target.hosted.json",
    )
    return {byte_digest(path.read_bytes()): path.read_bytes() for path in paths}


@pytest.fixture
def project_and_contents():
    value = _load(PLATFORM_POSITIVE / "project-bundle.local-unlinked.json")
    contents = {"README.md": b"problem\n", "inputs/cyclic5.json": b'{"x":1}'}
    for descriptor in value["contents"]:
        payload = contents[descriptor["path"]]
        descriptor["size_bytes"] = len(payload)
        descriptor["digest"] = byte_digest(payload)
    return AcceptedContractRecord.from_document(value), contents


@pytest.fixture
def client(
    validator: CloudClientContractValidator,
    records: dict[str, AcceptedCloudClientRecord],
    contract_records: dict[str, bytes],
):
    return InMemoryCloudClient(
        validator=validator,
        disconnected_session=records["session.disconnected"],
        connected_session=records["session.connected"],
        capabilities=records["capabilities"],
        contract_records=contract_records,
    )


def _run_for_project(
    validator: CloudClientContractValidator,
    records: dict[str, AcceptedCloudClientRecord],
    project: AcceptedContractRecord,
) -> AcceptedCloudClientRecord:
    value = records["run.succeeded"].document()
    value["project_binding"]["bundle_digest"] = jcs_digest(project.document())
    _bind_run_digests(value)
    return AcceptedCloudClientRecord.from_document(value, validator=validator)


def _bind_run_digests(value: dict) -> None:
    value["route_plan_digest"] = jcs_digest(value["route_plan"])
    value["request_digest"] = canonical_run_request_digest(value)
    if value["pricing"] is None:
        value["pricing_digest"] = None
    else:
        value["pricing"]["operation"]["request_digest"] = value["request_digest"]
        value["pricing_digest"] = jcs_digest(value["pricing"])


def _event_page(
    validator: CloudClientContractValidator,
    records: dict[str, AcceptedCloudClientRecord],
    *,
    after: str | None,
    events: list[dict],
    complete: bool,
) -> AcceptedCloudClientRecord:
    value = records["run-event-page.complete"].document()
    value["after_cursor"] = after
    value["events"] = copy.deepcopy(events)
    value["next_cursor"] = events[-1]["cursor"] if events else after
    value["complete"] = complete
    return AcceptedCloudClientRecord.from_document(value, validator=validator)


def test_reference_implements_runtime_checkable_provider_neutral_protocols(client: InMemoryCloudClient):
    assert isinstance(client, CloudSessionProvider)
    assert isinstance(client, CloudClient)
    assert client.current_session().document() == {
        "schema_id": "reasoning.cloud-client.session",
        "schema_version": CLOUD_CLIENT_VERSION,
        "state": "disconnected",
    }
    with pytest.raises(CloudClientStateError, match="disconnected"):
        client.capabilities()


def test_connect_is_idempotent_and_does_not_mutate_or_transfer_local_project(
    client: InMemoryCloudClient,
    project_and_contents,
):
    project, _contents = project_and_contents
    original_bytes = project.to_bytes() if hasattr(project, "to_bytes") else None
    original_document = project.document()
    original_transfers = copy.deepcopy(original_document["transfer_manifests"])
    store_snapshot = copy.deepcopy(client.store)
    first = client.connect()
    second = client.connect()
    assert first.to_bytes() == second.to_bytes()
    assert first.document()["state"] == "connected"
    assert client.store == store_snapshot
    assert project.document() == original_document
    assert project.document()["transfer_manifests"] == original_transfers
    if original_bytes is not None:
        assert project.to_bytes() == original_bytes


def test_capability_descriptors_resolve_to_exact_accepted_platform_bytes(
    client: InMemoryCloudClient,
):
    capabilities = client.connect().document()
    assert capabilities["state"] == "connected"
    capability_record = client.capabilities().document()
    references = [
        *capability_record["harnesses"],
        *capability_record["inference_routes"],
        *capability_record["compute_targets"],
    ]
    accepted = []
    for value in references:
        record = client.resolve_contract(ContractRecordReference.from_document(value))
        accepted.append((record.schema_id, record.digest))
    assert accepted == [(value["schema_id"], value["digest"]) for value in references]


def test_project_and_run_retries_are_idempotent_after_a_lost_response(
    client: InMemoryCloudClient,
    validator: CloudClientContractValidator,
    records: dict[str, AcceptedCloudClientRecord],
    project_and_contents,
):
    project, contents = project_and_contents
    client.connect()
    first_project = client.put_project(
        "project_cloud_algebra",
        project,
        contents,
        idempotency_key="project-put-idempotency-001",
    )
    after_project_response_loss = client.restarted()
    after_project_response_loss.connect()
    second_project = after_project_response_loss.put_project(
        "project_cloud_algebra",
        project,
        contents,
        idempotency_key="project-put-idempotency-001",
    )
    assert first_project.document() == second_project.document()
    run = _run_for_project(validator, records, project)
    first_run = after_project_response_loss.submit_run(run, project, idempotency_key="hybrid-fixture-001")
    after_run_response_loss = after_project_response_loss.restarted()
    after_run_response_loss.connect()
    second_run = after_run_response_loss.submit_run(run, project, idempotency_key="hybrid-fixture-001")
    assert first_run.to_bytes() == second_run.to_bytes()
    assert len(client.store.runs) == 1


@pytest.mark.parametrize("changed_part", ["route", "pricing", "request"])
def test_run_idempotency_binds_exact_canonical_request_content(
    changed_part: str,
    client: InMemoryCloudClient,
    validator: CloudClientContractValidator,
    records: dict[str, AcceptedCloudClientRecord],
    project_and_contents,
):
    project, contents = project_and_contents
    client.connect()
    client.put_project("project_cloud_algebra", project, contents, idempotency_key="project-put-idempotency-001")
    initial = _run_for_project(validator, records, project)
    client.submit_run(initial, project, idempotency_key="hybrid-fixture-001")

    changed = initial.document()
    if changed_part == "route":
        changed["route_plan"]["decision"]["decided_at"] = "2026-09-01T18:02:59Z"
    elif changed_part == "pricing":
        changed["pricing"]["quote"]["assumptions"].append("A changed, still schema-valid assumption.")
    else:
        changed["updated_at"] = "2026-09-01T18:09:00Z"
    _bind_run_digests(changed)
    changed_run = AcceptedCloudClientRecord.from_document(changed, validator=validator)

    with pytest.raises(CloudClientIdempotencyConflict, match="another request"):
        client.submit_run(changed_run, project, idempotency_key="hybrid-fixture-001")


def test_run_rejects_a_supplied_digest_that_does_not_cover_its_canonical_request(
    validator: CloudClientContractValidator,
    records: dict[str, AcceptedCloudClientRecord],
    project_and_contents,
):
    project, _contents = project_and_contents
    changed = _run_for_project(validator, records, project).document()
    changed["request_digest"] = "sha256:" + "f" * 64
    changed["pricing"]["operation"]["request_digest"] = changed["request_digest"]
    changed["pricing_digest"] = jcs_digest(changed["pricing"])
    changed_run = AcceptedCloudClientRecord.from_document(changed, validator=validator)

    with pytest.raises(CloudClientContractError, match="canonical mutation request"):
        validator.validate_run(
            changed_run,
            session=records["session.connected"],
            capabilities=records["capabilities"],
            project=project,
        )


def test_changed_project_request_conflicts_under_the_same_scoped_key(
    client: InMemoryCloudClient,
    project_and_contents,
):
    project, contents = project_and_contents
    client.connect()
    client.put_project("project_cloud_algebra", project, contents, idempotency_key="project-put-idempotency-001")
    changed = project.document()
    changed["project"]["title"] = "Changed request"
    changed_project = AcceptedContractRecord.from_document(changed)
    with pytest.raises(CloudClientIdempotencyConflict, match="another request"):
        client.put_project(
            "project_cloud_algebra",
            changed_project,
            contents,
            idempotency_key="project-put-idempotency-001",
        )


def test_disconnect_and_restart_preserve_run_without_requesting_cancellation(
    client: InMemoryCloudClient,
    validator: CloudClientContractValidator,
    records: dict[str, AcceptedCloudClientRecord],
    project_and_contents,
):
    project, contents = project_and_contents
    client.connect()
    client.put_project("project_cloud_algebra", project, contents, idempotency_key="project-put-idempotency-001")
    run = _run_for_project(validator, records, project)
    accepted = client.submit_run(run, project, idempotency_key="hybrid-fixture-001")
    before = accepted.to_bytes()
    assert client.disconnect().document()["state"] == "disconnected"
    assert next(iter(client.store.runs.values())).to_bytes() == before
    restarted = client.restarted()
    assert restarted.current_session().document()["state"] == "disconnected"
    with pytest.raises(CloudClientStateError, match="disconnected"):
        restarted.get_run(run.record_id)
    restarted.connect()
    recovered = restarted.get_run(run.record_id)
    assert recovered.to_bytes() == before
    assert recovered.document()["cancellation_requested"] is False


def test_event_replay_is_exclusive_opaque_atomic_and_deduplicated(
    client: InMemoryCloudClient,
    validator: CloudClientContractValidator,
    records: dict[str, AcceptedCloudClientRecord],
):
    client.connect()
    client.seed_event_page(records["run-event-page.complete"])
    state = EventReplayState(operation_id="operation_hybrid_matrix")
    first_page = client.list_run_events("operation_hybrid_matrix", after=None)
    state, events = accept_event_page(validator, first_page, state)
    assert len(events) == 4
    assert state.cursor == "cursor-terminal-uninterpreted-value"
    replayed_state, replayed = accept_event_page(validator, first_page, state)
    assert replayed == ()
    assert replayed_state == state
    later = client.list_run_events("operation_hybrid_matrix", after=state.cursor)
    later_state, later_events = accept_event_page(validator, later, state)
    assert later_events == ()
    assert later_state == state
    assert later.document()["after_cursor"] == state.cursor
    assert later.document()["events"] == []


def test_event_replay_retains_state_and_timestamp_across_valid_page_boundaries(
    validator: CloudClientContractValidator,
    records: dict[str, AcceptedCloudClientRecord],
):
    events = records["run-event-page.complete"].document()["events"]
    first = _event_page(validator, records, after=None, events=events[:2], complete=False)
    state, accepted = accept_event_page(
        validator,
        first,
        EventReplayState(operation_id="operation_hybrid_matrix"),
    )
    assert len(accepted) == 2
    assert state.last_state == "running"
    assert state.last_occurred_at == "2026-09-01T18:04:00Z"

    second = _event_page(validator, records, after=state.cursor, events=events[2:], complete=True)
    state, accepted = accept_event_page(validator, second, state)
    assert len(accepted) == 2
    assert state.last_state == "succeeded"
    assert state.last_occurred_at == "2026-09-01T18:08:00Z"


def test_event_replay_rejects_invalid_state_transition_across_page_boundary(
    validator: CloudClientContractValidator,
    records: dict[str, AcceptedCloudClientRecord],
):
    complete = records["run-event-page.complete"]
    state, _accepted = accept_event_page(
        validator,
        complete,
        EventReplayState(operation_id="operation_hybrid_matrix"),
    )
    continuation = complete.document()["events"][1]
    continuation["event_id"] = "event_after_terminal_001"
    continuation["sequence"] = 5
    continuation["cursor"] = "cursor-after-terminal-uninterpreted-value"
    continuation["occurred_at"] = "2026-09-01T18:09:00Z"
    page = _event_page(validator, records, after=state.cursor, events=[continuation], complete=True)

    with pytest.raises(CloudClientContractError, match="state transition"):
        accept_event_page(validator, page, state)
    assert state.last_state == "succeeded"
    assert state.last_occurred_at == "2026-09-01T18:08:00Z"


def test_event_replay_rejects_decreasing_timestamp_across_page_boundary(
    validator: CloudClientContractValidator,
    records: dict[str, AcceptedCloudClientRecord],
):
    events = records["run-event-page.complete"].document()["events"]
    first = _event_page(validator, records, after=None, events=events[:2], complete=False)
    state, _accepted = accept_event_page(
        validator,
        first,
        EventReplayState(operation_id="operation_hybrid_matrix"),
    )
    continuation = copy.deepcopy(events[2:])
    continuation[0]["occurred_at"] = "2026-09-01T18:03:59Z"
    second = _event_page(validator, records, after=state.cursor, events=continuation, complete=True)

    with pytest.raises(CloudClientContractError, match="decreasing timestamp"):
        accept_event_page(validator, second, state)
    assert state.last_state == "running"
    assert state.last_occurred_at == "2026-09-01T18:04:00Z"


def test_reused_event_id_with_changed_content_fails_without_advancing_cursor(
    validator: CloudClientContractValidator,
    records: dict[str, AcceptedCloudClientRecord],
):
    initial = records["run-event-page.complete"]
    state, _events = accept_event_page(validator, initial, EventReplayState(operation_id="operation_hybrid_matrix"))
    tampered = initial.document()
    tampered["events"][2]["delivery_attempt"] = 2
    tampered_record = AcceptedCloudClientRecord.from_document(tampered, validator=validator)
    with pytest.raises(CloudClientContractError, match="identity changed"):
        accept_event_page(validator, tampered_record, state)
    assert state.cursor == "cursor-terminal-uninterpreted-value"
    assert state.last_sequence == 4


def test_artifact_content_is_verified_before_exposure(
    client: InMemoryCloudClient,
    records: dict[str, AcceptedCloudClientRecord],
):
    client.connect()
    page = records["run-event-page.complete"].document()
    metadata = page["events"][2]["payload"]["artifact"]
    client.seed_artifact(metadata, b"result\n")
    artifact = client.download_artifact("artifact_hybrid_result_001")
    assert artifact.content == b"result\n"
    artifact_key = next(iter(client.store.artifacts))
    client.store.artifacts[artifact_key] = (metadata, b"tampered")
    with pytest.raises(CloudClientContractError, match="size or digest"):
        client.download_artifact("artifact_hybrid_result_001")


def test_credential_value_detection_accepts_benign_bearer_prose_but_rejects_tokens(
    validator: CloudClientContractValidator,
    records: dict[str, AcceptedCloudClientRecord],
):
    metadata = records["run-event-page.complete"].document()["events"][2]["payload"]["artifact"]
    metadata["name"] = "Bearer category"
    validator.validate_artifact(metadata, b"result\n")

    metadata["name"] = "Bearer abcdefghijklmnopqrstuvwxyz"
    with pytest.raises(CloudClientCredentialError, match="credential-like value"):
        validator.validate_artifact(metadata, b"result\n")


def test_active_run_can_record_idempotent_cancellation_request_without_partial_pricing(
    client: InMemoryCloudClient,
    validator: CloudClientContractValidator,
    records: dict[str, AcceptedCloudClientRecord],
    project_and_contents,
):
    project, contents = project_and_contents
    client.connect()
    client.put_project("project_cloud_algebra", project, contents, idempotency_key="project-put-idempotency-001")
    value = _run_for_project(validator, records, project).document()
    value["operation_id"] = "operation_hybrid_active_001"
    value["route_plan"]["operation_id"] = value["operation_id"]
    value["idempotency_key_digest"] = idempotency_key_digest("active-run-idempotency-001")
    value["state"] = "running"
    value["pricing"] = None
    value["updated_at"] = "2026-09-01T18:04:00Z"
    value["terminal_at"] = None
    _bind_run_digests(value)
    active = AcceptedCloudClientRecord.from_document(value, validator=validator)
    client.submit_run(active, project, idempotency_key="active-run-idempotency-001")
    requested = client.cancel_run(value["operation_id"], idempotency_key="run-cancel-idempotency-001")
    replayed = client.cancel_run(value["operation_id"], idempotency_key="run-cancel-idempotency-001")
    assert requested.to_bytes() == replayed.to_bytes()
    assert requested.document()["state"] == "running"
    assert requested.document()["pricing"] is None
    assert requested.document()["cancellation_requested"] is True
    validator.validate_run(
        requested,
        session=records["session.connected"],
        capabilities=records["capabilities"],
        project=project,
    )


def test_project_content_fails_before_any_mutation(
    client: InMemoryCloudClient,
    project_and_contents,
):
    project, contents = project_and_contents
    client.connect()
    tampered = {**contents, "README.md": b"different"}
    with pytest.raises(CloudClientContractError, match="size or digest"):
        client.put_project("project_cloud_algebra", project, tampered, idempotency_key="project-put-idempotency-001")
    assert client.store.projects == {}
    assert client.store.mutations == {}


@pytest.mark.parametrize(
    ("actor_type", "actor_id", "tenant_id"),
    [
        ("service", "user_alice", "tenant_alpha"),
        ("user", "user_other", "tenant_alpha"),
        ("user", "user_alice", "tenant_other"),
    ],
)
def test_durable_resources_are_isolated_by_complete_session_scope(
    actor_type: str,
    actor_id: str,
    tenant_id: str,
    client: InMemoryCloudClient,
    validator: CloudClientContractValidator,
    records: dict[str, AcceptedCloudClientRecord],
    contract_records: dict[str, bytes],
    project_and_contents,
):
    project, contents = project_and_contents
    client.connect()
    client.put_project("project_cloud_algebra", project, contents, idempotency_key="project-put-idempotency-001")
    run = _run_for_project(validator, records, project)
    client.submit_run(run, project, idempotency_key="hybrid-fixture-001")
    client.seed_event_page(records["run-event-page.complete"])
    metadata = records["run-event-page.complete"].document()["events"][2]["payload"]["artifact"]
    client.seed_artifact(metadata, b"result\n")

    other_capability_value = records["capabilities"].document()
    other_capability_value["snapshot_id"] = "capability_snapshot_other"
    other_capability_value["actor"] = {"type": actor_type, "id": actor_id}
    other_capability_value["tenant_id"] = tenant_id
    other_capabilities = AcceptedCloudClientRecord.from_document(other_capability_value, validator=validator)
    other_session_value = records["session.connected"].document()
    other_session_value["actor"] = {"type": actor_type, "id": actor_id}
    other_session_value["tenant_id"] = tenant_id
    other_session_value["capability_snapshot_digest"] = jcs_digest(other_capability_value)
    other_session = AcceptedCloudClientRecord.from_document(other_session_value, validator=validator)
    other = InMemoryCloudClient(
        validator=validator,
        disconnected_session=records["session.disconnected"],
        connected_session=other_session,
        capabilities=other_capabilities,
        contract_records=contract_records,
        store=client.store,
    )
    other.connect()

    assert other.list_projects() == ()
    assert other.list_run_events(run.record_id, after=None).document()["events"] == []
    with pytest.raises(CloudClientStateError, match="run is unavailable"):
        other.get_run(run.record_id)
    with pytest.raises(CloudClientStateError, match="artifact is unavailable"):
        other.download_artifact("artifact_hybrid_result_001")


def test_runtime_module_has_no_transport_auth_provider_or_private_fixture_imports():
    source_path = ROOT / "src" / "reasoning_ledger" / "cloud_client.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
    assert imported_roots.isdisjoint({"httpx", "requests", "socket", "urllib", "reasoning_cloud"})
    assert "/internal/" not in source
    assert "oauth" not in source.casefold()
    assert "pkce" not in source.casefold()
