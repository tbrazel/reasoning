from __future__ import annotations

# Ruff 0.3.4 and 0.15.x classify the src-layout package differently.
# ruff: noqa: I001

import base64
import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from reasoning_ledger.cloud_client import (
    AcceptedCloudClientRecord,
    CloudClientContractError,
    CloudClientContractValidator,
    CloudClientCredentialError,
    CloudClientStateError,
    InMemoryCloudClient,
    byte_digest,
    canonical_run_request_digest,
    jcs_digest,
)
from reasoning_ledger.desktop_cloud import DesktopCloudController, DesktopCloudRuntimeError
from reasoning_ledger.harnesses.desktop_hybrid import AcceptedContractRecord, HybridPlanningError
from reasoning_ledger.webapp import build_app

ROOT = Path(__file__).resolve().parents[1]
CLOUD_FIXTURES = ROOT / "conformance" / "cloud-client" / "v1alpha1" / "fixtures" / "positive"
PLATFORM_FIXTURES = ROOT / "conformance" / "platform" / "v1alpha1" / "fixtures" / "positive"


def _records(validator: CloudClientContractValidator) -> dict[str, AcceptedCloudClientRecord]:
    return {
        path.stem: AcceptedCloudClientRecord.from_bytes(path.read_bytes(), validator=validator) for path in CLOUD_FIXTURES.glob("*.json")
    }


def _provider(
    validator: CloudClientContractValidator,
) -> tuple[InMemoryCloudClient, dict[str, AcceptedCloudClientRecord]]:
    records = _records(validator)
    descriptors = (
        PLATFORM_FIXTURES / "harness-provider.local.json",
        PLATFORM_FIXTURES / "inference-route.hosted.json",
        PLATFORM_FIXTURES / "compute-target.hosted.json",
    )
    client = InMemoryCloudClient(
        validator=validator,
        disconnected_session=records["session.disconnected"],
        connected_session=records["session.connected"],
        capabilities=records["capabilities"],
        contract_records={byte_digest(path.read_bytes()): path.read_bytes() for path in descriptors},
    )
    return client, records


def _project() -> tuple[AcceptedContractRecord, dict[str, bytes]]:
    value = json.loads((PLATFORM_FIXTURES / "project-bundle.local-unlinked.json").read_text(encoding="utf-8"))
    contents = {"README.md": b"problem\n", "inputs/cyclic5.json": b'{"x":1}'}
    for descriptor in value["contents"]:
        payload = contents[descriptor["path"]]
        descriptor["size_bytes"] = len(payload)
        descriptor["digest"] = byte_digest(payload)
    manifest = value["transfer_manifests"][0]
    for descriptor, transfer_input in zip(value["contents"], manifest["disclosure"]["inputs"], strict=True):
        transfer_input["size_bytes"] = descriptor["size_bytes"]
        transfer_input["digest"] = descriptor["digest"]
    manifest["disclosure_digest"] = jcs_digest(manifest["disclosure"])
    manifest["approval"]["approved_disclosure_digest"] = manifest["disclosure_digest"]
    return AcceptedContractRecord.from_document(value), contents


def _run(
    validator: CloudClientContractValidator,
    fixture: AcceptedCloudClientRecord,
    project: AcceptedContractRecord,
) -> AcceptedCloudClientRecord:
    value = fixture.document()
    value["project_binding"]["bundle_digest"] = jcs_digest(project.document())
    value["route_plan_digest"] = jcs_digest(value["route_plan"])
    value["request_digest"] = canonical_run_request_digest(value)
    value["pricing"]["operation"]["request_digest"] = value["request_digest"]
    value["pricing_digest"] = jcs_digest(value["pricing"])
    return AcceptedCloudClientRecord.from_document(value, validator=validator)


def _changed_run(
    validator: CloudClientContractValidator,
    run: AcceptedCloudClientRecord,
    change,
) -> AcceptedCloudClientRecord:
    value = run.document()
    change(value)
    value["route_plan_digest"] = jcs_digest(value["route_plan"])
    value["request_digest"] = canonical_run_request_digest(value)
    value["pricing"]["operation"]["request_digest"] = value["request_digest"]
    value["pricing_digest"] = jcs_digest(value["pricing"])
    return AcceptedCloudClientRecord.from_document(value, validator=validator)


def _queued_run(
    validator: CloudClientContractValidator,
    fixture: AcceptedCloudClientRecord,
    project: AcceptedContractRecord,
) -> AcceptedCloudClientRecord:
    value = fixture.document()
    value["project_binding"]["bundle_digest"] = jcs_digest(project.document())
    value["state"] = "queued"
    value["pricing"] = None
    value["pricing_digest"] = None
    value["terminal_at"] = None
    value["updated_at"] = value["created_at"]
    value["route_plan_digest"] = jcs_digest(value["route_plan"])
    value["request_digest"] = canonical_run_request_digest(value)
    return AcceptedCloudClientRecord.from_document(value, validator=validator)


def test_queued_run_submission_and_read_never_invent_terminal_pricing(tmp_path: Path) -> None:
    validator = CloudClientContractValidator(ROOT)
    provider, records = _provider(validator)
    project, contents = _project()
    run = _queued_run(validator, records["run.succeeded"], project)
    controller = DesktopCloudController(
        (tmp_path / "queued-cloud-state").resolve(), provider=provider, validator=validator
    )
    try:
        controller.connect()
        controller.put_project(
            "project_cloud_algebra", project, contents,
            idempotency_key="project-put-idempotency-001", opt_in=True,
        )
        returned = controller.submit_run(run, project, idempotency_key="hybrid-fixture-001")
        assert returned["state"] == "queued"
        assert returned["pricing"] is None
        observed = controller.get_run(run.record_id)
        assert observed["state"] == "queued"
        assert observed["pricing"] is None
        assert controller._planner is not None
        pending_route = controller._planner.inspect_pending_plan(
            AcceptedContractRecord.from_document(observed["route_plan"]), project
        )
        assert pending_route.pricing_state == "pending"
        assert pending_route.dispatch_permitted is False
        with pytest.raises(HybridPlanningError, match="authorization is unknown"):
            controller._planner.reauthorize_connector_route(pending_route)
    finally:
        controller.close()


def test_without_injected_provider_desktop_starts_disconnected_and_refuses_mutations(tmp_path: Path) -> None:
    controller = DesktopCloudController((tmp_path / "cloud-state").resolve())
    try:
        assert controller.session()["state"] == "disconnected"
        assert controller.readiness() == {
            "status": "disconnected",
            "provider_injected": False,
            "provider_boundary": "post_authentication",
            "cloud_client_version": "1.0.0-alpha.1",
            "bundle_id": "reasoning-cloud-client-v1alpha1",
            "bundle_digest": "sha256:58f45ca5f9e5a53382b40444399e4cc792d297ead59b6387fd482ab72e0ad6b7",
        }
        with pytest.raises(CloudClientStateError, match="no post-authentication"):
            controller.connect()
        with pytest.raises(CloudClientStateError, match="no post-authentication"):
            controller.put_project(
                "project_cloud_algebra",
                _project()[0],
                _project()[1],
                idempotency_key="project-put-idempotency-001",
                opt_in=True,
            )
    finally:
        controller.close()


def test_connect_builds_exact_planner_without_transfer_and_put_requires_per_project_opt_in(tmp_path: Path) -> None:
    validator = CloudClientContractValidator(ROOT)
    provider, records = _provider(validator)
    controller = DesktopCloudController((tmp_path / "cloud-state").resolve(), provider=provider, validator=validator)
    project, contents = _project()
    original = project.to_bytes()
    store_before = copy.deepcopy(provider.store)
    try:
        session = controller.connect()
        assert session["state"] == "connected"
        assert provider.store == store_before
        assert project.to_bytes() == original

        options = controller.route_options()
        assert options["route_pricing_pairs"] == [
            {"route_plan": "1.0.0-alpha.1", "pricing": "1.0.0-alpha.1"},
            {"route_plan": "1.0.0-alpha.2", "pricing": "1.0.0-alpha.2"},
        ]
        assert options["harnesses"][0]["location"] == "local"
        assert options["inference_routes"][0]["location"] == "hosted"
        assert options["compute_targets"][0]["location"] == "hosted"
        assert provider.store.projects == {}

        inspection_project = project.document()
        inspection_project["transfer_manifests"] = []
        inspected = controller.inspect_route(
            AcceptedContractRecord.from_document(records["run.succeeded"].document()["route_plan"]),
            AcceptedContractRecord.from_document(records["run.succeeded"].document()["pricing"]),
            AcceptedContractRecord.from_document(inspection_project),
        )
        assert inspected.display()["combination"] == "lhh"
        assert inspected.display()["transfer_required"] is True
        assert provider.store.projects == {}

        with pytest.raises(DesktopCloudRuntimeError, match="explicit per-project"):
            controller.put_project(
                "project_cloud_algebra",
                project,
                contents,
                idempotency_key="project-put-idempotency-001",
                opt_in=False,
            )
        assert provider.store.projects == {}
        non_byte_contents = dict(contents)
        non_byte_contents["README.md"] = bytearray(contents["README.md"])
        with pytest.raises(CloudClientContractError, match="exact bytes"):
            controller.put_project(
                "project_cloud_algebra",
                project,
                non_byte_contents,
                idempotency_key="project-put-idempotency-001",
                opt_in=True,
            )
        assert provider.store.projects == {}
        credential_value = project.document()
        credential_value["project"]["title"] = "Bearer abcdefghijklmnopqrstuvwxyz"
        with pytest.raises(CloudClientCredentialError, match="credential-like"):
            controller.put_project(
                "project_cloud_algebra",
                AcceptedContractRecord.from_document(credential_value),
                contents,
                idempotency_key="project-put-idempotency-001",
                opt_in=True,
            )
        assert provider.store.projects == {}
        assert (
            controller.put_project(
                "project_cloud_algebra",
                project,
                contents,
                idempotency_key="project-put-idempotency-001",
                opt_in=True,
            )
            == project.document()
        )
        assert project.to_bytes() == original
        assert len(provider.store.projects) == 1
    finally:
        controller.close()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda value: value["route_plan"]["harness"].__setitem__("location", "hosted"), "harness location substitution"),
        (lambda value: value["route_plan"]["inference"].__setitem__("location", "local"), "inference location substitution"),
        (lambda value: value["route_plan"]["compute"].__setitem__("location", "local"), "compute location substitution"),
        (lambda value: value["route_plan"]["harness"].__setitem__("harness_id", "codex_other"), "unknown harness"),
    ],
)
def test_inspect_and_submit_agree_on_planner_substitutions_before_provider_dispatch(
    tmp_path: Path,
    monkeypatch,
    change,
    message: str,
) -> None:
    validator = CloudClientContractValidator(ROOT)
    provider, records = _provider(validator)
    project, contents = _project()
    run = _changed_run(validator, _run(validator, records["run.succeeded"], project), change)
    controller = DesktopCloudController((tmp_path / "cloud-state").resolve(), provider=provider, validator=validator)
    controller.connect()
    controller.put_project(
        "project_cloud_algebra",
        project,
        contents,
        idempotency_key="project-put-idempotency-001",
        opt_in=True,
    )
    dispatches: list[str] = []

    def unexpected_dispatch(*args, **kwargs):
        dispatches.append("submit_run")
        raise AssertionError("invalid planner input reached the provider")

    monkeypatch.setattr(provider, "submit_run", unexpected_dispatch)
    try:
        with pytest.raises((CloudClientContractError, HybridPlanningError), match=message) as inspected:
            controller.inspect_route(
                AcceptedContractRecord.from_document(run.document()["route_plan"]),
                AcceptedContractRecord.from_document(run.document()["pricing"]),
                project,
            )
        with pytest.raises((CloudClientContractError, HybridPlanningError), match=message) as submitted:
            controller.submit_run(run, project, idempotency_key="hybrid-fixture-001")
        assert str(inspected.value) == str(submitted.value)
        assert dispatches == []
    finally:
        controller.close()


def test_provider_returned_run_is_revalidated_by_planner_and_submission_lineage(tmp_path: Path, monkeypatch) -> None:
    validator = CloudClientContractValidator(ROOT)
    provider, records = _provider(validator)
    project, contents = _project()
    run = _run(validator, records["run.succeeded"], project)
    controller = DesktopCloudController((tmp_path / "cloud-state").resolve(), provider=provider, validator=validator)
    controller.connect()
    controller.put_project(
        "project_cloud_algebra",
        project,
        contents,
        idempotency_key="project-put-idempotency-001",
        opt_in=True,
    )
    original_submit = provider.submit_run
    returned = _changed_run(
        validator,
        run,
        lambda value: value["route_plan"]["harness"].__setitem__("location", "hosted"),
    )

    def substituted_return(*args, **kwargs):
        original_submit(*args, **kwargs)
        return returned

    monkeypatch.setattr(provider, "submit_run", substituted_return)
    try:
        with pytest.raises(HybridPlanningError, match="harness location substitution"):
            controller.submit_run(run, project, idempotency_key="hybrid-fixture-001")
    finally:
        controller.close()

    restarted_provider = provider.restarted()
    restarted = DesktopCloudController(
        (tmp_path / "lineage-state").resolve(),
        provider=restarted_provider,
        validator=validator,
    )
    restarted.connect()
    restarted.put_project(
        "project_cloud_algebra",
        project,
        contents,
        idempotency_key="project-put-idempotency-001",
        opt_in=True,
    )
    original_restarted_submit = restarted_provider.submit_run
    changed_lineage = _changed_run(
        validator,
        run,
        lambda value: value.__setitem__("cancellation_requested", True),
    )

    def changed_lineage_return(*args, **kwargs):
        original_restarted_submit(*args, **kwargs)
        return changed_lineage

    monkeypatch.setattr(restarted_provider, "submit_run", changed_lineage_return)
    try:
        with pytest.raises(CloudClientContractError, match="canonical submission lineage"):
            restarted.submit_run(run, project, idempotency_key="hybrid-fixture-001")
    finally:
        restarted.close()


def test_credential_shaped_raw_mutation_keys_never_reach_the_provider(tmp_path: Path, monkeypatch) -> None:
    validator = CloudClientContractValidator(ROOT)
    provider, records = _provider(validator)
    project, contents = _project()
    run = _run(validator, records["run.succeeded"], project)
    controller = DesktopCloudController((tmp_path / "cloud-state").resolve(), provider=provider, validator=validator)
    controller.connect()
    provider_calls: list[str] = []

    def unexpected_provider_call(*args, **kwargs):
        provider_calls.append("called")
        raise AssertionError("credential-shaped input reached the provider")

    for method in ("current_session", "put_project", "submit_run", "cancel_run"):
        monkeypatch.setattr(provider, method, unexpected_provider_call)
    try:
        with pytest.raises(CloudClientCredentialError, match="credential-like"):
            controller.put_project(
                "project_cloud_algebra",
                project,
                contents,
                idempotency_key="sk-abcdefghijklmnop",
                opt_in=True,
            )
        with pytest.raises(CloudClientCredentialError, match="credential-like"):
            controller.submit_run(run, project, idempotency_key="sk-abcdefghijklmnop")
        with pytest.raises(CloudClientCredentialError, match="credential-like"):
            controller.cancel_run(run.record_id, idempotency_key="sk-abcdefghijklmnop")
        assert provider_calls == []
    finally:
        controller.close()


def test_disconnect_and_restart_resume_scoped_run_events_and_verified_artifacts(tmp_path: Path) -> None:
    validator = CloudClientContractValidator(ROOT)
    provider, records = _provider(validator)
    project, contents = _project()
    run = _run(validator, records["run.succeeded"], project)
    state_directory = (tmp_path / "cloud-state").resolve()

    first = DesktopCloudController(state_directory, provider=provider, validator=validator)
    first.connect()
    first.put_project(
        "project_cloud_algebra",
        project,
        contents,
        idempotency_key="project-put-idempotency-001",
        opt_in=True,
    )
    first.submit_run(run, project, idempotency_key="hybrid-fixture-001")
    provider.seed_event_page(records["run-event-page.complete"])
    artifact_metadata = records["run-event-page.complete"].document()["events"][2]["payload"]["artifact"]
    provider.seed_artifact(artifact_metadata, b"result\n")
    first_events = first.events(run.record_id)
    assert len(first_events["events"]) == 4
    assert first_events["next_cursor"] == "cursor-terminal-uninterpreted-value"
    assert first.disconnect()["state"] == "disconnected"
    assert next(iter(provider.store.runs.values())).document()["cancellation_requested"] is False
    first.close()

    restarted_provider = provider.restarted()
    second = DesktopCloudController(state_directory, provider=restarted_provider, validator=validator)
    try:
        assert second.session()["state"] == "disconnected"
        with pytest.raises(CloudClientStateError, match="disconnected"):
            second.get_run(run.record_id)
        second.connect()
        assert second.tracked_runs() == (run.record_id,)
        assert second.tracked_artifacts() == ("artifact_hybrid_result_001",)
        recovered = second.get_run(run.record_id)
        assert recovered == run.document()
        assert recovered["cancellation_requested"] is False
        replay = second.events(run.record_id)
        assert replay == {
            "operation_id": run.record_id,
            "after_cursor": "cursor-terminal-uninterpreted-value",
            "next_cursor": "cursor-terminal-uninterpreted-value",
            "events": [],
        }
        assert second.download_artifact("artifact_hybrid_result_001").content == b"result\n"

        artifact_key = next(iter(provider.store.artifacts))
        provider.store.artifacts[artifact_key] = (artifact_metadata, b"corrupt")
        with pytest.raises(CloudClientContractError, match="size or digest"):
            second.download_artifact("artifact_hybrid_result_001")
    finally:
        second.close()


def test_injected_provider_descriptor_drift_fails_before_controller_readiness(tmp_path: Path) -> None:
    validator = CloudClientContractValidator(ROOT)
    provider, _records_by_name = _provider(validator)
    reference = provider._capabilities.document()["harnesses"][0]  # deterministic conformance provider inspection
    provider._contract_records[reference["digest"]] = (PLATFORM_FIXTURES / "inference-route.hosted.json").read_bytes()
    controller = DesktopCloudController((tmp_path / "cloud-state").resolve(), provider=provider, validator=validator)
    try:
        with pytest.raises(CloudClientContractError, match="descriptor digest"):
            controller.connect()
        assert controller.readiness()["status"] == "disconnected"
    finally:
        controller.close()


def test_durable_desktop_indexes_are_isolated_by_complete_session_scope(tmp_path: Path) -> None:
    validator = CloudClientContractValidator(ROOT)
    provider, records = _provider(validator)
    project, contents = _project()
    run = _run(validator, records["run.succeeded"], project)
    state_directory = (tmp_path / "cloud-state").resolve()
    first = DesktopCloudController(state_directory, provider=provider, validator=validator)
    first.connect()
    first.put_project(
        "project_cloud_algebra",
        project,
        contents,
        idempotency_key="project-put-idempotency-001",
        opt_in=True,
    )
    first.submit_run(run, project, idempotency_key="hybrid-fixture-001")
    first.disconnect()
    first.close()

    other_capability_value = records["capabilities"].document()
    other_capability_value["snapshot_id"] = "capability_snapshot_other"
    other_capability_value["actor"] = {"type": "user", "id": "user_other"}
    other_capabilities = AcceptedCloudClientRecord.from_document(other_capability_value, validator=validator)
    other_session_value = records["session.connected"].document()
    other_session_value["actor"] = {"type": "user", "id": "user_other"}
    other_session_value["capability_snapshot_digest"] = jcs_digest(other_capability_value)
    other_session = AcceptedCloudClientRecord.from_document(other_session_value, validator=validator)
    other_provider = InMemoryCloudClient(
        validator=validator,
        disconnected_session=records["session.disconnected"],
        connected_session=other_session,
        capabilities=other_capabilities,
        contract_records=provider._contract_records,
        store=provider.store,
    )
    second = DesktopCloudController(state_directory, provider=other_provider, validator=validator)
    try:
        second.connect()
        assert second.tracked_runs() == ()
        assert second.list_projects() == ()
        with pytest.raises(DesktopCloudRuntimeError, match="not tracked"):
            second.get_run(run.record_id)
    finally:
        second.close()


def test_loopback_controller_routes_expose_the_bounded_post_auth_boundary(tmp_path: Path, monkeypatch) -> None:
    validator = CloudClientContractValidator(ROOT)
    provider, records = _provider(validator)
    project, contents = _project()
    run = _run(validator, records["run.succeeded"], project)
    monkeypatch.setenv("REASONING_DESKTOP_RUNTIME", "1")
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(tmp_path / "workbench"))
    app = build_app(desktop_cloud_provider=provider)
    with TestClient(app) as client:
        assert client.get("/api/desktop/cloud/readiness").json()["status"] == "disconnected"
        assert client.get("/api/desktop/cloud/session").json()["state"] == "disconnected"
        assert client.get("/api/desktop/cloud/projects").status_code == 409
        assert client.post("/api/desktop/cloud/session/connect").json()["state"] == "connected"
        assert client.get("/api/desktop/cloud/routes").json()["harnesses"][0]["location"] == "local"

        put_payload = {
            "project": project.document(),
            "contents_base64": {path: base64.b64encode(content).decode("ascii") for path, content in contents.items()},
            "idempotency_key": "project-put-idempotency-001",
            "opt_in": True,
        }
        assert (
            client.put(
                "/api/desktop/cloud/projects/project_cloud_algebra",
                json={**put_payload, "access_token": "forbidden"},
            ).status_code
            == 422
        )
        assert provider.store.projects == {}
        response = client.put("/api/desktop/cloud/projects/project_cloud_algebra", json=put_payload)
        assert response.status_code == 200
        assert response.json() == project.document()
        submitted = client.post(
            "/api/desktop/cloud/runs",
            json={
                "run": run.document(),
                "project": project.document(),
                "idempotency_key": "hybrid-fixture-001",
            },
        )
        assert submitted.status_code == 200
        assert submitted.json()["operation_id"] == run.record_id

        provider.seed_event_page(records["run-event-page.complete"])
        metadata = records["run-event-page.complete"].document()["events"][2]["payload"]["artifact"]
        provider.seed_artifact(metadata, b"result\n")
        assert len(client.get(f"/api/desktop/cloud/runs/{run.record_id}/events").json()["events"]) == 4
        assert client.get("/api/desktop/cloud/artifacts").json() == {"artifact_ids": ["artifact_hybrid_result_001"]}
        artifact = client.get("/api/desktop/cloud/artifacts/artifact_hybrid_result_001").json()
        assert base64.b64decode(artifact["content_base64"], validate=True) == b"result\n"
        assert client.post("/api/desktop/cloud/session/disconnect").json()["state"] == "disconnected"


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        (
            "PUT",
            "/api/desktop/cloud/projects/project_cloud_algebra",
            b'{"project":{"schema_id":"first","schema_id":"second"},"contents_base64":{},'
            b'"idempotency_key":"abcdefghijklmnop","opt_in":true}',
        ),
        (
            "POST",
            "/api/desktop/cloud/runs",
            b'{"run":{"operation_id":"first","operation_id":"second"},"project":{},"idempotency_key":"abcdefghijklmnop"}',
        ),
        (
            "POST",
            "/api/desktop/cloud/routes/inspect",
            b'{"route_plan":{"route_plan_id":"first","route_plan_id":"second"},"pricing":{},"project":{}}',
        ),
        (
            "POST",
            "/api/desktop/cloud/routes/inspect",
            b'{"route_plan":{},"pricing":{"pricing_lifecycle_id":"first","pricing_lifecycle_id":"second"},"project":{}}',
        ),
        (
            "POST",
            "/api/desktop/cloud/runs/operation_hybrid_matrix/cancel",
            b'{"idempotency_key":"abcdefghijklmnop","idempotency_key":"qrstuvwxyzabcdef"}',
        ),
    ],
    ids=("project", "run", "route", "pricing", "controller-envelope"),
)
def test_loopback_rejects_nested_duplicate_keys_before_conversion_or_provider_call(
    tmp_path: Path,
    monkeypatch,
    method: str,
    path: str,
    payload: bytes,
) -> None:
    validator = CloudClientContractValidator(ROOT)
    provider, _records_by_name = _provider(validator)
    monkeypatch.setenv("REASONING_DESKTOP_RUNTIME", "1")
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(tmp_path / "workbench"))
    app = build_app(desktop_cloud_provider=provider)
    with TestClient(app) as client:
        assert client.post("/api/desktop/cloud/session/connect").status_code == 200
        provider_calls: list[str] = []

        def unexpected_provider_call(*args, **kwargs):
            provider_calls.append("current_session")
            raise AssertionError("duplicate-key input reached domain conversion")

        monkeypatch.setattr(provider, "current_session", unexpected_provider_call)
        response = client.request(method, path, content=payload, headers={"content-type": "application/json"})
        assert response.status_code == 422
        assert "duplicate JSON object key" in response.json()["detail"]
        assert provider_calls == []
