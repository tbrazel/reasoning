"""Strict private HTTPS transport for the Desktop Cloud boundary.

This module adapts the canonical transport-neutral clients to one exact private
HTTP surface.  It owns no provider configuration, authentication flow, or
server implementation.  Credential material is obtained just in time and is
placed only in the Authorization request header.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Protocol, runtime_checkable
from urllib.parse import quote, urlparse

import httpx

from .cloud_client import (
    MAX_CLOUD_CLIENT_RECORD_BYTES,
    MAX_PROJECT_CONTENT_BYTES,
    AcceptedCloudClientRecord,
    CloudClientContractValidator,
    ContractRecordReference,
    VerifiedArtifact,
    canonical_cloud_client_validator,
    idempotency_key_digest,
    parse_strict_json_object,
    require_credential_free,
)
from .cloud_product_policy import (
    MAX_CLOUD_PRODUCT_POLICY_RECORD_BYTES,
    AcceptedCloudProductPolicyRecord,
    CloudProductPolicyValidator,
    digest_idempotency_key,
)
from .collaboration import (
    MAX_COLLABORATION_RECORD_BYTES,
    CollaborationContractValidator,
    canonical_collaboration_validator,
)
from .desktop_auth import AuthenticatedDesktopIdentity, DesktopPostAuthProviderFactory
from .desktop_cloud import DesktopAuthenticatedCloudBoundary, DesktopCloudProvider
from .harnesses.desktop_hybrid import AcceptedContractRecord

JsonObject = dict[str, object]

DESKTOP_CLOUD_HTTP_API_VERSION = "desktop-v1alpha1"
MAX_HTTP_COLLECTION_BYTES = 8_388_608
MAX_HTTP_ARTIFACT_BYTES = 67_108_864
MAX_HTTP_PROJECT_REQUEST_BYTES = 100_663_296
MAX_HTTP_ERROR_BYTES = 16_384

_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
_CONTENT_TYPE = re.compile(r"^application/json(?:\s*;\s*charset=utf-8)?$", re.IGNORECASE)
_FORBIDDEN_HOST_SUFFIXES = (".invalid", ".localhost", ".local", ".test", ".example")


class DesktopCloudHttpError(RuntimeError):
    """The private Desktop Cloud transport failed closed."""


@dataclass(frozen=True)
class DesktopCloudHttpConfiguration:
    """One immutable production service origin."""

    endpoint: str
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        parsed = urlparse(self.endpoint)
        host = parsed.hostname
        if (
            parsed.scheme != "https"
            or host is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or self.endpoint.endswith("/")
            or not 1.0 <= self.timeout_seconds <= 120.0
        ):
            raise DesktopCloudHttpError("Desktop Cloud endpoint must be one canonical HTTPS origin")
        normalized = host.rstrip(".").casefold()
        if normalized in {"localhost", "localhost.localdomain"} or normalized.endswith(_FORBIDDEN_HOST_SUFFIXES):
            raise DesktopCloudHttpError("Desktop Cloud endpoint cannot use a local or reserved host")
        try:
            address = ipaddress.ip_address(normalized)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise DesktopCloudHttpError("Desktop Cloud endpoint cannot use a non-global address")


@runtime_checkable
class DesktopBearerTokenSource(Protocol):
    """Returns the current short-lived access token without presenting it."""

    def access_token(self) -> str: ...


def _identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise DesktopCloudHttpError(f"{label} is invalid")
    require_credential_free(value)
    return value


def _encoded_path(value: str, label: str) -> str:
    return quote(_identifier(value, label), safe="")


def _canonical_json(value: object, *, maximum: int) -> bytes:
    require_credential_free(value)
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if not payload or len(payload) > maximum:
        raise DesktopCloudHttpError("Desktop Cloud request exceeds its accepted size")
    return payload


def _base64_bytes(value: bytes) -> str:
    if not isinstance(value, bytes):
        raise DesktopCloudHttpError("Desktop Cloud binary field requires exact bytes")
    return base64.b64encode(value).decode("ascii")


def _decode_base64(value: object, *, maximum: int, label: str) -> bytes:
    if not isinstance(value, str) or len(value) > ((maximum + 2) // 3) * 4:
        raise DesktopCloudHttpError(f"{label} is invalid or oversized")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as error:
        raise DesktopCloudHttpError(f"{label} is not canonical base64") from error
    if len(decoded) > maximum:
        raise DesktopCloudHttpError(f"{label} exceeds its accepted size")
    return decoded


class DesktopCloudHttpTransport:
    """Bounded, redirect-free HTTP exchange shared by both canonical clients."""

    def __init__(
        self,
        configuration: DesktopCloudHttpConfiguration,
        token_source: DesktopBearerTokenSource,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not isinstance(configuration, DesktopCloudHttpConfiguration) or not isinstance(
            token_source, DesktopBearerTokenSource
        ):
            raise DesktopCloudHttpError("Desktop Cloud HTTP dependencies are invalid")
        self._configuration = configuration
        self._token_source = token_source
        self._lock = threading.RLock()
        self._client = httpx.Client(
            base_url=configuration.endpoint,
            timeout=configuration.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            headers={
                "Accept-Encoding": "identity",
                "User-Agent": f"reasoning-desktop/{DESKTOP_CLOUD_HTTP_API_VERSION}",
            },
        )

    def close(self) -> None:
        with self._lock:
            self._client.cookies.clear()
            self._client.close()

    def _authorization(self) -> str:
        token = self._token_source.access_token()
        if (
            not isinstance(token, str)
            or not 16 <= len(token) <= 8192
            or not token.isascii()
            or any(character.isspace() or ord(character) < 0x21 or ord(character) > 0x7E for character in token)
        ):
            raise DesktopCloudHttpError("Desktop Cloud access token is unavailable")
        return f"Bearer {token}"

    def request(
        self,
        method: str,
        path: str,
        **kwargs: object,
    ) -> bytes:
        """Serialize exchanges so no response cookie can cross requests."""

        with self._lock:
            self._client.cookies.clear()
            try:
                return self._request(method, path, **kwargs)
            finally:
                self._client.cookies.clear()

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
        maximum: int,
        expected_content_type: str,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> bytes:
        if not path.startswith("/desktop/v1/") or "//" in path or "?" in path or "#" in path:
            raise DesktopCloudHttpError("Desktop Cloud request path is invalid")
        additional_headers = dict(headers or {})
        if {name.casefold() for name in additional_headers}.intersection(
            {"accept", "authorization", "cookie", "content-length", "content-type", "host"}
        ):
            raise DesktopCloudHttpError("Desktop Cloud request attempted to override a protected header")
        require_credential_free(dict(params or {}))
        request_headers = {
            "Accept": expected_content_type,
            "Authorization": self._authorization(),
            **additional_headers,
        }
        if body is not None:
            request_headers["Content-Type"] = content_type or "application/json"
        try:
            with self._client.stream(method, path, content=body, headers=request_headers, params=params) as response:
                if response.headers.get_list("set-cookie"):
                    raise DesktopCloudHttpError("Desktop Cloud service attempted to set a cookie")
                if not 200 <= response.status_code < 300:
                    # Consume only a small bounded prefix so connections close cleanly;
                    # never interpolate an untrusted body, URL, or credential.
                    consumed = 0
                    for chunk in response.iter_bytes():
                        consumed += len(chunk)
                        if consumed > MAX_HTTP_ERROR_BYTES:
                            break
                    raise DesktopCloudHttpError(f"Desktop Cloud service returned HTTP {response.status_code}")
                observed_type = response.headers.get("content-type", "").strip()
                content_encoding = response.headers.get("content-encoding", "identity").strip().casefold()
                if content_encoding not in {"", "identity"}:
                    raise DesktopCloudHttpError("Desktop Cloud response encoding is invalid")
                if expected_content_type == "application/json":
                    if _CONTENT_TYPE.fullmatch(observed_type) is None:
                        raise DesktopCloudHttpError("Desktop Cloud response content type is invalid")
                elif observed_type != expected_content_type:
                    raise DesktopCloudHttpError("Desktop Cloud response content type is invalid")
                length = response.headers.get("content-length")
                if length is not None:
                    try:
                        declared = int(length)
                    except ValueError as error:
                        raise DesktopCloudHttpError("Desktop Cloud response length is invalid") from error
                    if declared < 0 or declared > maximum:
                        raise DesktopCloudHttpError("Desktop Cloud response exceeds its accepted size")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > maximum:
                        raise DesktopCloudHttpError("Desktop Cloud response exceeds its accepted size")
                    chunks.append(chunk)
                if size == 0:
                    raise DesktopCloudHttpError("Desktop Cloud response is empty")
                return b"".join(chunks)
        except DesktopCloudHttpError:
            raise
        except httpx.HTTPError:
            # Do not retain an exception carrying the Authorization-bearing
            # request as a cause reachable from logs or crash reports.
            raise DesktopCloudHttpError("Desktop Cloud service is unavailable") from None


class DesktopCloudHttpProvider(DesktopCloudProvider):
    """Canonical Cloud session/client implementation over the private API."""

    def __init__(
        self,
        transport: DesktopCloudHttpTransport,
        *,
        validator: CloudClientContractValidator | None = None,
        collaboration_validator: CollaborationContractValidator | None = None,
    ) -> None:
        if not isinstance(transport, DesktopCloudHttpTransport):
            raise DesktopCloudHttpError("Desktop Cloud provider transport is invalid")
        self._transport = transport
        self._validator = validator or canonical_cloud_client_validator()
        self._collaboration_validator = collaboration_validator or canonical_collaboration_validator()

    def _json(self, method: str, path: str, **kwargs: object) -> JsonObject:
        payload = self._transport.request(
            method,
            path,
            maximum=MAX_COLLABORATION_RECORD_BYTES,
            expected_content_type="application/json",
            **kwargs,
        )
        return parse_strict_json_object(payload, maximum=MAX_COLLABORATION_RECORD_BYTES)

    def collaboration_bootstrap(self) -> JsonObject:
        document = self._json("GET", "/desktop/v1/collaboration/bootstrap")
        self._collaboration_validator.validate(document)
        return document

    @staticmethod
    def _member(document: object) -> JsonObject:
        if not isinstance(document, dict) or set(document) != {
            "account_id", "user_id", "email", "status", "role_ids", "self"
        }:
            raise DesktopCloudHttpError("Desktop collaboration member is invalid")
        _identifier(document.get("account_id"), "member account id")
        _identifier(document.get("user_id"), "member user id")
        if (
            document.get("email") is not None and not isinstance(document.get("email"), str)
        ) or document.get("status") != "active" or type(document.get("self")) is not bool:
            raise DesktopCloudHttpError("Desktop collaboration member is invalid")
        roles = document.get("role_ids")
        if not isinstance(roles, list) or not roles or roles != sorted(set(roles)) or any(
            not isinstance(role, str) for role in roles
        ):
            raise DesktopCloudHttpError("Desktop collaboration member roles are invalid")
        require_credential_free(document)
        return document

    def collaboration_members(self, organization_id: str) -> tuple[JsonObject, ...]:
        document = self._json(
            "GET",
            f"/desktop/v1/organizations/{_encoded_path(organization_id, 'organization id')}/members",
        )
        members = document.get("members")
        if set(document) != {"members"} or not isinstance(members, list) or len(members) > 10_000:
            raise DesktopCloudHttpError("Desktop collaboration member collection is invalid")
        return tuple(self._member(member) for member in members)

    def collaboration_invite_member(
        self, organization_id: str, email: str, role_id: str, *, idempotency_key: str
    ) -> JsonObject:
        idempotency_key_digest(idempotency_key)
        body = _canonical_json(
            {"email": email, "role_id": role_id}, maximum=MAX_COLLABORATION_RECORD_BYTES
        )
        document = self._json(
            "POST",
            f"/desktop/v1/organizations/{_encoded_path(organization_id, 'organization id')}/members",
            body=body,
            headers={"Idempotency-Key": idempotency_key},
        )
        return self._member(document)

    def collaboration_topics(self, organization_id: str, workspace_id: str) -> tuple[JsonObject, ...]:
        document = self._json(
            "GET",
            f"/desktop/v1/organizations/{_encoded_path(organization_id, 'organization id')}"
            f"/workspaces/{_encoded_path(workspace_id, 'workspace id')}/topics",
        )
        topics = document.get("topics")
        if set(document) != {"topics"} or not isinstance(topics, list) or len(topics) > 256:
            raise DesktopCloudHttpError("Desktop collaboration topic collection is invalid")
        for topic in topics:
            self._collaboration_validator.validate(topic)
            if topic["organization_id"] != organization_id or topic["workspace_id"] != workspace_id:
                raise DesktopCloudHttpError("Desktop collaboration topic changed scope")
        return tuple(topics)

    def collaboration_create_topic(
        self,
        organization_id: str,
        workspace_id: str,
        title: str,
        *,
        idempotency_key: str,
    ) -> JsonObject:
        idempotency_key_digest(idempotency_key)
        body = _canonical_json({"title": title}, maximum=MAX_COLLABORATION_RECORD_BYTES)
        document = self._json(
            "POST",
            f"/desktop/v1/organizations/{_encoded_path(organization_id, 'organization id')}"
            f"/workspaces/{_encoded_path(workspace_id, 'workspace id')}/topics",
            body=body,
            headers={"Idempotency-Key": idempotency_key},
        )
        self._collaboration_validator.validate(document)
        if document["organization_id"] != organization_id or document["workspace_id"] != workspace_id:
            raise DesktopCloudHttpError("Desktop collaboration topic changed scope")
        return document

    def collaboration_messages(
        self,
        organization_id: str,
        workspace_id: str,
        topic_id: str,
        *,
        after: str | None = None,
    ) -> JsonObject:
        params = {"after": after} if after is not None else None
        document = self._json(
            "GET",
            f"/desktop/v1/organizations/{_encoded_path(organization_id, 'organization id')}"
            f"/workspaces/{_encoded_path(workspace_id, 'workspace id')}"
            f"/topics/{_encoded_path(topic_id, 'topic id')}/messages",
            params=params,
        )
        self._collaboration_validator.validate(document)
        if (
            document["organization_id"] != organization_id
            or document["workspace_id"] != workspace_id
            or document["topic_id"] != topic_id
            or document["after_cursor"] != after
        ):
            raise DesktopCloudHttpError("Desktop collaboration messages changed scope")
        return document

    def _message(
        self,
        document: JsonObject,
        *,
        organization_id: str,
        workspace_id: str,
        topic_id: str,
    ) -> JsonObject:
        sequence = document.get("sequence")
        page = {
            "schema_id": "reasoning.collaboration.message-page",
            "schema_version": "1.0.0-alpha.1",
            "organization_id": organization_id,
            "workspace_id": workspace_id,
            "topic_id": topic_id,
            "after_cursor": None,
            "messages": [document],
            "next_cursor": str(sequence) if isinstance(sequence, int) else None,
            "complete": True,
        }
        self._collaboration_validator.validate(page)
        return document

    def collaboration_post_message(
        self,
        organization_id: str,
        workspace_id: str,
        topic_id: str,
        content: str,
        *,
        idempotency_key: str,
    ) -> JsonObject:
        idempotency_key_digest(idempotency_key)
        body = _canonical_json({"content": content}, maximum=MAX_COLLABORATION_RECORD_BYTES)
        document = self._json(
            "POST",
            f"/desktop/v1/organizations/{_encoded_path(organization_id, 'organization id')}"
            f"/workspaces/{_encoded_path(workspace_id, 'workspace id')}"
            f"/topics/{_encoded_path(topic_id, 'topic id')}/messages",
            body=body,
            headers={"Idempotency-Key": idempotency_key},
        )
        return self._message(
            document,
            organization_id=organization_id,
            workspace_id=workspace_id,
            topic_id=topic_id,
        )

    def collaboration_queue_inference(
        self,
        organization_id: str,
        workspace_id: str,
        topic_id: str,
        request: Mapping[str, object],
        *,
        idempotency_key: str,
    ) -> JsonObject:
        idempotency_key_digest(idempotency_key)
        body = _canonical_json(dict(request), maximum=MAX_COLLABORATION_RECORD_BYTES)
        document = self._json(
            "POST",
            f"/desktop/v1/organizations/{_encoded_path(organization_id, 'organization id')}"
            f"/workspaces/{_encoded_path(workspace_id, 'workspace id')}"
            f"/topics/{_encoded_path(topic_id, 'topic id')}/inference-requests",
            body=body,
            headers={"Idempotency-Key": idempotency_key},
        )
        if set(document) != {"operation_id", "state"} or document.get("state") != "queued":
            raise DesktopCloudHttpError("Desktop collaboration inference receipt is invalid")
        _identifier(document.get("operation_id"), "inference operation id")
        require_credential_free(document)
        return document

    def collaboration_operations(
        self, organization_id: str, workspace_id: str, topic_id: str
    ) -> tuple[JsonObject, ...]:
        document = self._json(
            "GET",
            f"/desktop/v1/organizations/{_encoded_path(organization_id, 'organization id')}"
            f"/workspaces/{_encoded_path(workspace_id, 'workspace id')}"
            f"/topics/{_encoded_path(topic_id, 'topic id')}/operations",
        )
        operations = document.get("operations")
        if set(document) != {"operations"} or not isinstance(operations, list) or len(operations) > 100:
            raise DesktopCloudHttpError("Desktop collaboration activity collection is invalid")
        for operation in operations:
            self._collaboration_validator.validate(operation)
            if (
                operation["organization_id"] != organization_id
                or operation["workspace_id"] != workspace_id
                or operation["topic_id"] != topic_id
            ):
                raise DesktopCloudHttpError("Desktop collaboration activity changed scope")
        return tuple(operations)

    def collaboration_cancel_inference(
        self, organization_id: str, workspace_id: str, topic_id: str, operation_id: str
    ) -> JsonObject:
        document = self._json(
            "POST",
            f"/desktop/v1/organizations/{_encoded_path(organization_id, 'organization id')}"
            f"/workspaces/{_encoded_path(workspace_id, 'workspace id')}"
            f"/topics/{_encoded_path(topic_id, 'topic id')}"
            f"/operations/{_encoded_path(operation_id, 'operation id')}/cancel",
            body=b"{}",
        )
        if set(document) != {"operation_id", "state"} or document.get("operation_id") != operation_id:
            raise DesktopCloudHttpError("Desktop collaboration cancellation receipt is invalid")
        if document.get("state") not in {
            "succeeded", "failed", "cancelled", "expired", "outcome_unknown"
        }:
            raise DesktopCloudHttpError("Desktop collaboration cancellation state is invalid")
        require_credential_free(document)
        return document

    def collaboration_register_runner(
        self,
        organization_id: str,
        runner_id: str,
        document: Mapping[str, object],
    ) -> JsonObject:
        body = _canonical_json(dict(document), maximum=MAX_COLLABORATION_RECORD_BYTES)
        returned = self._json(
            "PUT",
            f"/desktop/v1/organizations/{_encoded_path(organization_id, 'organization id')}"
            f"/runners/{_encoded_path(runner_id, 'runner id')}",
            body=body,
        )
        self._collaboration_validator.validate(returned)
        if returned["organization_id"] != organization_id or returned["runner_id"] != runner_id:
            raise DesktopCloudHttpError("Desktop collaboration runner changed identity")
        return returned

    def collaboration_claim_lease(self, organization_id: str, runner_id: str) -> JsonObject | None:
        wrapper = self._json(
            "POST",
            f"/desktop/v1/organizations/{_encoded_path(organization_id, 'organization id')}"
            f"/runners/{_encoded_path(runner_id, 'runner id')}/leases/claim",
            body=b"{}",
        )
        lease = wrapper.get("lease")
        if set(wrapper) != {"lease"} or (lease is not None and not isinstance(lease, dict)):
            raise DesktopCloudHttpError("Desktop collaboration lease response is invalid")
        if lease is not None:
            self._collaboration_validator.validate(lease)
            if lease["organization_id"] != organization_id or lease["runner_id"] != runner_id:
                raise DesktopCloudHttpError("Desktop collaboration lease changed identity")
        return lease

    def _lease_action(
        self,
        organization_id: str,
        runner_id: str,
        lease_id: str,
        action: str,
        body: bytes,
    ) -> JsonObject:
        document = self._json(
            "POST",
            f"/desktop/v1/organizations/{_encoded_path(organization_id, 'organization id')}"
            f"/runners/{_encoded_path(runner_id, 'runner id')}"
            f"/leases/{_encoded_path(lease_id, 'lease id')}/{action}",
            body=body,
        )
        if set(document) != {"lease_id", "operation_id", "state"} or document.get("lease_id") != lease_id:
            raise DesktopCloudHttpError("Desktop collaboration lease receipt is invalid")
        _identifier(document.get("operation_id"), "inference operation id")
        if document.get("state") not in {
            "running", "succeeded", "failed", "cancelled", "expired", "outcome_unknown"
        }:
            raise DesktopCloudHttpError("Desktop collaboration lease state is invalid")
        require_credential_free(document)
        return document

    def collaboration_start_lease(
        self, organization_id: str, runner_id: str, lease_id: str
    ) -> JsonObject:
        return self._lease_action(organization_id, runner_id, lease_id, "start", b"{}")

    def collaboration_heartbeat_lease(
        self, organization_id: str, runner_id: str, lease_id: str, *, progress: str
    ) -> JsonObject:
        body = _canonical_json({"progress": progress}, maximum=MAX_COLLABORATION_RECORD_BYTES)
        return self._lease_action(organization_id, runner_id, lease_id, "heartbeat", body)

    def collaboration_finish_lease(
        self,
        organization_id: str,
        runner_id: str,
        lease_id: str,
        *,
        state: str,
        content: str,
        usage: Mapping[str, object] | None = None,
    ) -> JsonObject:
        completion: JsonObject = {"state": state, "content": content}
        if usage is not None:
            completion["usage"] = dict(usage)
        body = _canonical_json(completion, maximum=MAX_COLLABORATION_RECORD_BYTES)
        document = self._lease_action(
            organization_id, runner_id, lease_id, "finish", body
        )
        if document.get("state") not in {"succeeded", "failed"}:
            raise DesktopCloudHttpError("Desktop collaboration completion state is invalid")
        return document

    def _cloud_record(self, method: str, path: str, **kwargs: object) -> AcceptedCloudClientRecord:
        payload = self._transport.request(
            method,
            path,
            maximum=MAX_CLOUD_CLIENT_RECORD_BYTES,
            expected_content_type="application/json",
            **kwargs,
        )
        return self._validator.accept(payload)

    def current_session(self) -> AcceptedCloudClientRecord:
        return self._cloud_record("GET", "/desktop/v1/session")

    def connect(self) -> AcceptedCloudClientRecord:
        return self._cloud_record("POST", "/desktop/v1/session/connect", body=b"{}")

    def disconnect(self) -> AcceptedCloudClientRecord:
        return self._cloud_record("POST", "/desktop/v1/session/disconnect", body=b"{}")

    def capabilities(self) -> AcceptedCloudClientRecord:
        return self._cloud_record("GET", "/desktop/v1/capabilities")

    def resolve_contract(self, reference: ContractRecordReference) -> AcceptedContractRecord:
        if not isinstance(reference, ContractRecordReference):
            raise DesktopCloudHttpError("Desktop Cloud contract reference is invalid")
        body = _canonical_json(asdict(reference), maximum=MAX_CLOUD_CLIENT_RECORD_BYTES)
        payload = self._transport.request(
            "POST",
            "/desktop/v1/contracts/resolve",
            body=body,
            maximum=MAX_CLOUD_CLIENT_RECORD_BYTES,
            expected_content_type="application/json",
        )
        record = AcceptedContractRecord.from_bytes(payload)
        if (
            record.schema_id != reference.schema_id
            or record.schema_version != reference.schema_version
            or record.record_id != reference.record_id
            or record.digest != reference.digest
        ):
            raise DesktopCloudHttpError("Desktop Cloud contract response changed identity")
        return record

    def list_projects(self) -> tuple[AcceptedContractRecord, ...]:
        payload = self._transport.request(
            "GET",
            "/desktop/v1/projects",
            maximum=MAX_HTTP_COLLECTION_BYTES,
            expected_content_type="application/json",
        )
        wrapper = parse_strict_json_object(payload, maximum=MAX_HTTP_COLLECTION_BYTES)
        if set(wrapper) != {"records"} or not isinstance(wrapper["records"], list) or len(wrapper["records"]) > 256:
            raise DesktopCloudHttpError("Desktop Cloud project collection is invalid")
        records = tuple(
            AcceptedContractRecord.from_bytes(
                _decode_base64(item, maximum=MAX_CLOUD_CLIENT_RECORD_BYTES, label="Desktop Cloud project record")
            )
            for item in wrapper["records"]
        )
        if any(record.schema_id != "reasoning.platform.project-bundle" for record in records):
            raise DesktopCloudHttpError("Desktop Cloud project collection contains a non-project record")
        return records

    def put_project(
        self,
        cloud_project_id: str,
        project: AcceptedContractRecord,
        contents: Mapping[str, bytes],
        *,
        idempotency_key: str,
    ) -> AcceptedContractRecord:
        idempotency_key_digest(idempotency_key)
        if not isinstance(project, AcceptedContractRecord) or project.schema_id != "reasoning.platform.project-bundle":
            raise DesktopCloudHttpError("Desktop Cloud project record is invalid")
        if not isinstance(contents, Mapping) or (
            sum(len(value) for value in contents.values() if isinstance(value, bytes)) > MAX_PROJECT_CONTENT_BYTES
        ):
            raise DesktopCloudHttpError("Desktop Cloud project contents are invalid or oversized")
        entries = []
        for path in sorted(contents):
            value = contents[path]
            require_credential_free(path)
            entries.append({"path": path, "bytes_base64": _base64_bytes(value)})
        body = _canonical_json(
            {"project_bytes_base64": _base64_bytes(project.to_bytes()), "contents": entries},
            maximum=MAX_HTTP_PROJECT_REQUEST_BYTES,
        )
        payload = self._transport.request(
            "PUT",
            f"/desktop/v1/projects/{_encoded_path(cloud_project_id, 'Cloud project id')}",
            body=body,
            maximum=MAX_CLOUD_CLIENT_RECORD_BYTES,
            expected_content_type="application/json",
            headers={"Idempotency-Key": idempotency_key},
        )
        return AcceptedContractRecord.from_bytes(payload)

    def submit_run(
        self,
        run: AcceptedCloudClientRecord,
        project: AcceptedContractRecord,
        *,
        idempotency_key: str,
    ) -> AcceptedCloudClientRecord:
        idempotency_key_digest(idempotency_key)
        if not isinstance(run, AcceptedCloudClientRecord) or not isinstance(project, AcceptedContractRecord):
            raise DesktopCloudHttpError("Desktop Cloud run request is invalid")
        body = _canonical_json(
            {
                "run_bytes_base64": _base64_bytes(run.to_bytes()),
                "project_bytes_base64": _base64_bytes(project.to_bytes()),
            },
            maximum=MAX_CLOUD_CLIENT_RECORD_BYTES * 3,
        )
        return self._cloud_record(
            "POST",
            "/desktop/v1/runs",
            body=body,
            headers={"Idempotency-Key": idempotency_key},
        )

    def get_run(self, operation_id: str) -> AcceptedCloudClientRecord:
        return self._cloud_record("GET", f"/desktop/v1/runs/{_encoded_path(operation_id, 'operation id')}")

    def list_run_events(self, operation_id: str, *, after: str | None) -> AcceptedCloudClientRecord:
        params = None
        if after is not None:
            params = {"after": _identifier(after, "event cursor")}
        return self._cloud_record(
            "GET",
            f"/desktop/v1/runs/{_encoded_path(operation_id, 'operation id')}/events",
            params=params,
        )

    def cancel_run(self, operation_id: str, *, idempotency_key: str) -> AcceptedCloudClientRecord:
        idempotency_key_digest(idempotency_key)
        return self._cloud_record(
            "POST",
            f"/desktop/v1/runs/{_encoded_path(operation_id, 'operation id')}/cancel",
            body=b"{}",
            headers={"Idempotency-Key": idempotency_key},
        )

    def download_artifact(self, artifact_id: str) -> VerifiedArtifact:
        encoded = _encoded_path(artifact_id, "artifact id")
        metadata_bytes = self._transport.request(
            "GET",
            f"/desktop/v1/artifacts/{encoded}/metadata",
            maximum=MAX_CLOUD_CLIENT_RECORD_BYTES,
            expected_content_type="application/json",
        )
        metadata = parse_strict_json_object(metadata_bytes)
        content = self._transport.request(
            "GET",
            f"/desktop/v1/artifacts/{encoded}/content",
            maximum=MAX_HTTP_ARTIFACT_BYTES,
            expected_content_type="application/octet-stream",
        )
        self._validator.validate_artifact(metadata, content)
        return VerifiedArtifact(metadata=metadata, content=content)


class DesktopCloudHttpLocalSessionProvider(DesktopCloudHttpProvider):
    """Keep Desktop opt-in disconnected until the user explicitly connects.

    The hosted service can project an authenticated session immediately, but
    Desktop must not silently connect merely because OAuth completed. All
    other operations still use the same strict, bearer-bound HTTP transport.
    """

    def __init__(
        self,
        transport: DesktopCloudHttpTransport,
        *,
        validator: CloudClientContractValidator | None = None,
    ) -> None:
        super().__init__(transport, validator=validator)
        self._session_lock = threading.RLock()
        self._locally_connected = False
        self._disconnected = AcceptedCloudClientRecord.from_document(
            {
                "schema_id": "reasoning.cloud-client.session",
                "schema_version": "1.0.0-alpha.1",
                "state": "disconnected",
            },
            validator=self._validator,
        )

    def current_session(self) -> AcceptedCloudClientRecord:
        with self._session_lock:
            return super().current_session() if self._locally_connected else self._disconnected

    def connect(self) -> AcceptedCloudClientRecord:
        with self._session_lock:
            record = super().connect()
            if record.schema_id != "reasoning.cloud-client.session" or record.document()["state"] != "connected":
                raise DesktopCloudHttpError("hosted service did not establish a connected session")
            self._locally_connected = True
            return record

    def disconnect(self) -> AcceptedCloudClientRecord:
        with self._session_lock:
            if not self._locally_connected:
                return self._disconnected
            record = super().disconnect()
            if self._validator.validate_session(record)["state"] != "disconnected":
                raise DesktopCloudHttpError("hosted service did not disconnect")
            self._locally_connected = False
            return record


class DesktopCloudHttpProductPolicyClient:
    """Canonical product-policy client over the same authenticated transport."""

    def __init__(self, transport: DesktopCloudHttpTransport, *, validator: CloudProductPolicyValidator) -> None:
        if not isinstance(transport, DesktopCloudHttpTransport) or not isinstance(validator, CloudProductPolicyValidator):
            raise DesktopCloudHttpError("Desktop product-policy HTTP dependencies are invalid")
        self._transport = transport
        self._validator = validator

    def account_policy(self) -> AcceptedCloudProductPolicyRecord:
        payload = self._transport.request(
            "GET",
            "/desktop/v1/account-policy",
            maximum=MAX_CLOUD_PRODUCT_POLICY_RECORD_BYTES,
            expected_content_type="application/json",
        )
        return self._validator.accept(payload)

    def change_optional_use(
        self,
        change: AcceptedCloudProductPolicyRecord,
        *,
        idempotency_key: str,
    ) -> AcceptedCloudProductPolicyRecord:
        digest_idempotency_key(idempotency_key)
        if not isinstance(change, AcceptedCloudProductPolicyRecord):
            raise DesktopCloudHttpError("Desktop optional-use change is invalid")
        payload = self._transport.request(
            "POST",
            "/desktop/v1/account-policy/optional-use-changes",
            body=change.to_bytes(),
            maximum=MAX_CLOUD_PRODUCT_POLICY_RECORD_BYTES,
            expected_content_type="application/json",
            headers={"Idempotency-Key": idempotency_key},
        )
        return self._validator.accept(payload)


class DesktopCloudHttpBoundaryFactory(DesktopPostAuthProviderFactory):
    """Builds the closed post-auth pair from one credential source."""

    def __init__(
        self,
        configuration: DesktopCloudHttpConfiguration,
        token_source: DesktopBearerTokenSource,
        *,
        cloud_validator: CloudClientContractValidator | None = None,
        product_policy_validator: CloudProductPolicyValidator | None = None,
        transport_factory: Callable[[], httpx.BaseTransport | None] | None = None,
    ) -> None:
        self._configuration = configuration
        self._token_source = token_source
        self._cloud_validator = cloud_validator or canonical_cloud_client_validator()
        self._policy_validator = product_policy_validator or CloudProductPolicyValidator(
            self._cloud_validator.repository_root,
            frozen_layout=self._cloud_validator.frozen_layout,
        )
        self._transport_factory = transport_factory

    def create(self, identity: AuthenticatedDesktopIdentity) -> DesktopAuthenticatedCloudBoundary:
        if not isinstance(identity, AuthenticatedDesktopIdentity):
            raise DesktopCloudHttpError("Desktop authenticated identity is invalid")
        transport = DesktopCloudHttpTransport(
            self._configuration,
            self._token_source,
            transport=self._transport_factory() if self._transport_factory is not None else None,
        )
        provider = DesktopCloudHttpLocalSessionProvider(transport, validator=self._cloud_validator)
        policy = DesktopCloudHttpProductPolicyClient(transport, validator=self._policy_validator)
        return DesktopAuthenticatedCloudBoundary(provider=provider, product_policy_client=policy)


__all__ = [
    "DESKTOP_CLOUD_HTTP_API_VERSION",
    "DesktopBearerTokenSource",
    "DesktopCloudHttpBoundaryFactory",
    "DesktopCloudHttpConfiguration",
    "DesktopCloudHttpError",
    "DesktopCloudHttpProductPolicyClient",
    "DesktopCloudHttpProvider",
    "DesktopCloudHttpLocalSessionProvider",
    "DesktopCloudHttpTransport",
]
