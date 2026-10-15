from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from reasoning_ledger.cloud_client import CloudClientContractError, CloudClientContractValidator, ContractRecordReference
from reasoning_ledger.cloud_product_policy import CloudProductPolicyValidator
from reasoning_ledger.collaboration import CollaborationContractValidator
from reasoning_ledger.desktop_cloud_http import (
    DesktopCloudHttpConfiguration,
    DesktopCloudHttpError,
    DesktopCloudHttpLocalSessionProvider,
    DesktopCloudHttpProductPolicyClient,
    DesktopCloudHttpProvider,
    DesktopCloudHttpTransport,
)
from reasoning_ledger.harnesses.desktop_hybrid import AcceptedContractRecord

ROOT = Path(__file__).resolve().parents[1]
CLOUD_FIXTURES = ROOT / "conformance" / "cloud-client" / "v1alpha1" / "fixtures" / "positive"
POLICY_FIXTURES = ROOT / "conformance" / "cloud-product-policy" / "v1alpha1" / "fixtures" / "positive"
PLATFORM_FIXTURES = ROOT / "conformance" / "platform" / "v1alpha1" / "fixtures" / "positive"


class _TokenSource:
    def __init__(self) -> None:
        self._token = "header-only-access-material-0001"

    def access_token(self) -> str:
        return self._token


def _configuration() -> DesktopCloudHttpConfiguration:
    return DesktopCloudHttpConfiguration("https://desktop-api.example.org")


def _json_response(payload: bytes, *, status: int = 200, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, content=payload, headers={"Content-Type": "application/json", **(headers or {})})


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://desktop-api.example.org",
        "https://127.0.0.1",
        "https://localhost",
        "https://api.test",
        "https://user@desktop-api.example.org",
        "https://desktop-api.example.org/path",
        "https://desktop-api.example.org?fallback=https://other.invalid",
        "https://desktop-api.example.org/#fragment",
    ],
)
def test_endpoint_configuration_rejects_noncanonical_or_local_origins(endpoint: str) -> None:
    with pytest.raises(DesktopCloudHttpError, match="endpoint|host|address"):
        DesktopCloudHttpConfiguration(endpoint)


def test_provider_confines_bearer_to_header_and_accepts_canonical_session() -> None:
    token_source = _TokenSource()
    session = (CLOUD_FIXTURES / "session.disconnected.json").read_bytes()
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return _json_response(session)

    transport = DesktopCloudHttpTransport(_configuration(), token_source, transport=httpx.MockTransport(handler))
    provider = DesktopCloudHttpProvider(transport, validator=CloudClientContractValidator(ROOT))
    try:
        accepted = provider.current_session()
        assert accepted.document()["state"] == "disconnected"
        assert len(observed) == 1
        request = observed[0]
        assert request.url == httpx.URL("https://desktop-api.example.org/desktop/v1/session")
        assert request.headers["authorization"] == f"Bearer {token_source._token}"
        assert token_source._token not in str(request.url)
        assert token_source._token not in repr(provider)
        assert token_source._token not in repr(transport)
    finally:
        transport.close()


def test_live_provider_requires_explicit_local_connect_and_preserves_disconnect() -> None:
    connected = (CLOUD_FIXTURES / "session.connected.json").read_bytes()
    disconnected = (CLOUD_FIXTURES / "session.disconnected.json").read_bytes()
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        return _json_response(disconnected if request.url.path.endswith("/disconnect") else connected)

    transport = DesktopCloudHttpTransport(_configuration(), _TokenSource(), transport=httpx.MockTransport(handler))
    provider = DesktopCloudHttpLocalSessionProvider(transport, validator=CloudClientContractValidator(ROOT))
    try:
        assert provider.current_session().document()["state"] == "disconnected"
        assert requests == []
        assert provider.connect().document()["state"] == "connected"
        assert provider.current_session().document()["state"] == "connected"
        assert provider.disconnect().document()["state"] == "disconnected"
        assert provider.current_session().document()["state"] == "disconnected"
        assert requests == [
            ("POST", "/desktop/v1/session/connect"),
            ("GET", "/desktop/v1/session"),
            ("POST", "/desktop/v1/session/disconnect"),
        ]
    finally:
        transport.close()


def test_service_error_and_redirect_do_not_expose_response_or_location() -> None:
    token_source = _TokenSource()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            content=f"leaked {token_source._token}".encode(),
            headers={"Location": f"https://other.invalid/?token={token_source._token}"},
        )

    transport = DesktopCloudHttpTransport(_configuration(), token_source, transport=httpx.MockTransport(handler))
    provider = DesktopCloudHttpProvider(transport, validator=CloudClientContractValidator(ROOT))
    try:
        with pytest.raises(DesktopCloudHttpError) as raised:
            provider.current_session()
        assert str(raised.value) == "Desktop Cloud service returned HTTP 302"
        assert token_source._token not in repr(raised.value)
    finally:
        transport.close()


def test_provider_rejects_duplicate_json_and_declared_oversize() -> None:
    responses = iter(
        (
            _json_response(b'{"schema_id":"a","schema_id":"b"}'),
            _json_response(b"{}", headers={"Content-Length": "1048577"}),
        )
    )
    transport = DesktopCloudHttpTransport(
        _configuration(),
        _TokenSource(),
        transport=httpx.MockTransport(lambda _request: next(responses)),
    )
    provider = DesktopCloudHttpProvider(transport, validator=CloudClientContractValidator(ROOT))
    try:
        with pytest.raises(CloudClientContractError, match="duplicate"):
            provider.current_session()
        with pytest.raises(DesktopCloudHttpError, match="exceeds"):
            provider.current_session()
    finally:
        transport.close()


def test_contract_resolution_and_project_collection_preserve_exact_record_bytes() -> None:
    descriptor = (PLATFORM_FIXTURES / "compute-target.hosted.json").read_bytes()
    project = (PLATFORM_FIXTURES / "project-bundle.local-unlinked.json").read_bytes()
    reference = ContractRecordReference(
        schema_id="reasoning.platform.compute-target",
        schema_version="1.0.0-alpha.1",
        record_id="target_cloud_research_cpu",
        digest=f"sha256:{hashlib.sha256(descriptor).hexdigest()}",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contracts/resolve"):
            assert json.loads(request.content) == {
                "digest": reference.digest,
                "record_id": reference.record_id,
                "schema_id": reference.schema_id,
                "schema_version": reference.schema_version,
            }
            return _json_response(descriptor)
        wrapper = json.dumps(
            {"records": [base64.b64encode(project).decode("ascii")]},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return _json_response(wrapper)

    transport = DesktopCloudHttpTransport(_configuration(), _TokenSource(), transport=httpx.MockTransport(handler))
    provider = DesktopCloudHttpProvider(transport, validator=CloudClientContractValidator(ROOT))
    try:
        assert provider.resolve_contract(reference).to_bytes() == descriptor
        assert provider.list_projects()[0].to_bytes() == project
    finally:
        transport.close()


def test_product_policy_client_uses_same_bounded_authenticated_transport() -> None:
    account_policy = (POLICY_FIXTURES / "account-policy.json").read_bytes()
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return _json_response(account_policy)

    transport = DesktopCloudHttpTransport(_configuration(), _TokenSource(), transport=httpx.MockTransport(handler))
    client = DesktopCloudHttpProductPolicyClient(transport, validator=CloudProductPolicyValidator(ROOT))
    try:
        accepted = client.account_policy()
        assert accepted.to_bytes() == account_policy
        assert observed[0].url.path == "/desktop/v1/account-policy"
        assert observed[0].headers["authorization"].startswith("Bearer ")
    finally:
        transport.close()


def test_collaboration_bootstrap_and_topics_are_canonical_and_bearer_bound() -> None:
    bootstrap = {
        "schema_id": "reasoning.collaboration.bootstrap",
        "schema_version": "1.0.0-alpha.1",
        "account_id": "account_alpha",
        "actor": {"type": "user", "id": "user_alpha"},
        "default_organization_id": "organization_personal_alpha",
        "default_workspace_id": "workspace_personal_alpha",
        "organizations": [{
            "organization_id": "organization_personal_alpha",
            "name": "Ada's workspace",
            "kind": "personal",
            "role_ids": ["owner"],
            "permissions": ["chat.read", "chat.write", "organization.read", "workspace.read"],
            "workspaces": [{
                "workspace_id": "workspace_personal_alpha",
                "name": "My workspace",
                "status": "active",
                "created_at": "2026-09-23T12:00:00Z",
            }],
        }],
    }
    topic = {
        "schema_id": "reasoning.collaboration.topic",
        "schema_version": "1.0.0-alpha.1",
        "organization_id": "organization_personal_alpha",
        "workspace_id": "workspace_personal_alpha",
        "topic_id": "topic_alpha",
        "title": "First shared topic",
        "state": "open",
        "created_by": {"type": "user", "id": "user_alpha"},
        "created_at": "2026-09-23T12:00:00Z",
        "updated_at": "2026-09-23T12:00:00Z",
    }
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        if request.url.path.endswith("/collaboration/bootstrap"):
            return _json_response(json.dumps(bootstrap).encode())
        return _json_response(json.dumps({"topics": [topic]}).encode())

    transport = DesktopCloudHttpTransport(_configuration(), _TokenSource(), transport=httpx.MockTransport(handler))
    provider = DesktopCloudHttpProvider(
        transport,
        validator=CloudClientContractValidator(ROOT),
        collaboration_validator=CollaborationContractValidator(ROOT),
    )
    try:
        assert provider.collaboration_bootstrap() == bootstrap
        assert provider.collaboration_topics(
            "organization_personal_alpha", "workspace_personal_alpha"
        ) == (topic,)
        assert all(request.headers["authorization"].startswith("Bearer ") for request in observed)
        assert observed[1].url.path.endswith(
            "/organizations/organization_personal_alpha/workspaces/workspace_personal_alpha/topics"
        )
    finally:
        transport.close()


def test_submitted_run_remains_addressable_after_disconnect_and_reconnect() -> None:
    disconnected = (CLOUD_FIXTURES / "session.disconnected.json").read_bytes()
    connected = (CLOUD_FIXTURES / "session.connected.json").read_bytes()
    run_bytes = (CLOUD_FIXTURES / "run.succeeded.json").read_bytes()
    project = AcceptedContractRecord.from_bytes(
        (PLATFORM_FIXTURES / "project-bundle.local-unlinked.json").read_bytes()
    )
    validator = CloudClientContractValidator(ROOT)
    run = validator.accept(run_bytes)
    connected_state = False
    retained_run: bytes | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal connected_state, retained_run
        path = request.url.path
        if path == "/desktop/v1/session":
            return _json_response(connected if connected_state else disconnected)
        if path.endswith("/session/connect"):
            connected_state = True
            return _json_response(connected)
        if path.endswith("/session/disconnect"):
            connected_state = False
            return _json_response(disconnected)
        if path == "/desktop/v1/runs" and request.method == "POST":
            assert request.headers["idempotency-key"] == "run-submit-reconnect-001"
            body = json.loads(request.content)
            assert base64.b64decode(body["run_bytes_base64"], validate=True) == run_bytes
            assert base64.b64decode(body["project_bytes_base64"], validate=True) == project.to_bytes()
            retained_run = run_bytes
            return _json_response(run_bytes)
        if path == "/desktop/v1/runs/operation_hybrid_matrix":
            assert retained_run is not None
            return _json_response(retained_run)
        raise AssertionError(f"unexpected private route: {request.method} {path}")

    transport = DesktopCloudHttpTransport(_configuration(), _TokenSource(), transport=httpx.MockTransport(handler))
    first = DesktopCloudHttpProvider(transport, validator=validator)
    try:
        assert first.current_session().document()["state"] == "disconnected"
        assert first.connect().document()["state"] == "connected"
        assert first.submit_run(run, project, idempotency_key="run-submit-reconnect-001").to_bytes() == run_bytes
        assert first.disconnect().document()["state"] == "disconnected"

        restarted = DesktopCloudHttpProvider(transport, validator=validator)
        assert restarted.current_session().document()["state"] == "disconnected"
        assert restarted.connect().document()["state"] == "connected"
        assert restarted.get_run("operation_hybrid_matrix").to_bytes() == run_bytes
    finally:
        transport.close()
