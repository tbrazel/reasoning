"""Canonical organization collaboration and contributed-runner boundary."""

from __future__ import annotations

import copy
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from .cloud_client import jcs_digest, require_credential_free

JsonObject = dict[str, Any]

COLLABORATION_VERSION = "1.0.0-alpha.1"
MAX_COLLABORATION_RECORD_BYTES = 1_048_576

_SCHEMA_FILES = {
    "reasoning.collaboration.bootstrap": "bootstrap.schema.json",
    "reasoning.collaboration.message-page": "message-page.schema.json",
    "reasoning.collaboration.operation": "operation.schema.json",
    "reasoning.collaboration.runner": "runner.schema.json",
    "reasoning.collaboration.runner-lease": "runner-lease.schema.json",
    "reasoning.collaboration.topic": "topic.schema.json",
}
_RECORD_ID_FIELDS = {
    "reasoning.collaboration.bootstrap": "account_id",
    "reasoning.collaboration.message-page": "topic_id",
    "reasoning.collaboration.operation": "operation_id",
    "reasoning.collaboration.runner": "runner_id",
    "reasoning.collaboration.runner-lease": "lease_id",
    "reasoning.collaboration.topic": "topic_id",
}


class CollaborationContractError(ValueError):
    """A collaboration or contributed-runner record is invalid."""


def _strict_object(payload: bytes) -> JsonObject:
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_COLLABORATION_RECORD_BYTES:
        raise CollaborationContractError("collaboration record is empty or exceeds its limit")

    def unique(pairs: list[tuple[str, Any]]) -> JsonObject:
        result: JsonObject = {}
        for key, value in pairs:
            if key in result:
                raise CollaborationContractError("collaboration record contains a duplicate key")
            result[key] = value
        return result

    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                CollaborationContractError("collaboration record contains a non-finite number")
            ),
        )
    except CollaborationContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise CollaborationContractError("collaboration record is not strict UTF-8 JSON") from error
    if not isinstance(document, dict):
        raise CollaborationContractError("collaboration record must be one JSON object")
    return document


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise CollaborationContractError(f"{label} is not a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CollaborationContractError(f"{label} is not a timestamp") from error
    if parsed.tzinfo is None:
        raise CollaborationContractError(f"{label} is not offset-aware")
    return parsed


class CollaborationContractValidator:
    """Validate exact records and cross-field collaboration invariants."""

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.contract_root = self.repository_root / "contracts" / "collaboration" / "v1alpha1"
        self._schemas: dict[str, JsonObject] = {}
        self._registry = Registry()
        for path in sorted(self.contract_root.glob("*.schema.json")):
            document = _strict_object(path.read_bytes())
            Draft202012Validator.check_schema(document)
            schema_id = document.get("$id")
            if not isinstance(schema_id, str) or schema_id in self._schemas:
                raise CollaborationContractError("collaboration schema identity is missing or duplicated")
            self._schemas[schema_id] = document
            self._registry = self._registry.with_resource(schema_id, Resource.from_contents(document))
        expected = {f"https://schemas.reasoning.invalid/collaboration/v1alpha1/{name}" for name in _SCHEMA_FILES.values()}
        expected.add("https://schemas.reasoning.invalid/collaboration/v1alpha1/common.schema.json")
        if set(self._schemas) != expected:
            raise CollaborationContractError("collaboration schema set is incomplete")

    def accept(self, payload: bytes) -> AcceptedCollaborationRecord:
        document = _strict_object(payload)
        self.validate(document)
        return AcceptedCollaborationRecord._from_validated(document, self)

    def validate(self, document: Mapping[str, Any]) -> None:
        if not isinstance(document, Mapping):
            raise CollaborationContractError("collaboration record must be one mapping")
        value = copy.deepcopy(dict(document))
        schema_id = value.get("schema_id")
        filename = _SCHEMA_FILES.get(schema_id)
        if filename is None:
            raise CollaborationContractError("collaboration schema identity is unsupported")
        schema = self._schemas[f"https://schemas.reasoning.invalid/collaboration/v1alpha1/{filename}"]
        errors = sorted(
            Draft202012Validator(schema, registry=self._registry, format_checker=FormatChecker()).iter_errors(value),
            key=lambda error: tuple(str(item) for item in error.absolute_path),
        )
        if errors:
            path = "/".join(str(item) for item in errors[0].absolute_path) or "$"
            raise CollaborationContractError(f"collaboration record fails schema at {path}")
        try:
            require_credential_free(value)
        except ValueError as error:
            raise CollaborationContractError("credential material is forbidden in collaboration records") from error
        {
            "reasoning.collaboration.bootstrap": self._validate_bootstrap,
            "reasoning.collaboration.message-page": self._validate_message_page,
            "reasoning.collaboration.operation": self._validate_operation,
            "reasoning.collaboration.runner": self._validate_runner,
            "reasoning.collaboration.runner-lease": self._validate_lease,
            "reasoning.collaboration.topic": self._validate_topic,
        }[str(schema_id)](value)

    @staticmethod
    def _validate_bootstrap(document: JsonObject) -> None:
        if document["actor"]["type"] != "user":
            raise CollaborationContractError("collaboration bootstrap requires a user actor")
        organizations = document["organizations"]
        organization_ids = [item["organization_id"] for item in organizations]
        if len(set(organization_ids)) != len(organization_ids):
            raise CollaborationContractError("collaboration organizations are duplicated")
        personal = [item for item in organizations if item["kind"] == "personal"]
        if len(personal) != 1 or personal[0]["organization_id"] != document["default_organization_id"]:
            raise CollaborationContractError("default organization must be the account's one personal organization")
        default_workspaces = [
            item
            for item in personal[0]["workspaces"]
            if item["workspace_id"] == document["default_workspace_id"] and item["status"] == "active"
        ]
        if len(default_workspaces) != 1:
            raise CollaborationContractError("default workspace must be active in the personal organization")
        workspace_ids: list[str] = []
        for organization in organizations:
            if organization["role_ids"] != sorted(organization["role_ids"]):
                raise CollaborationContractError("collaboration role identifiers must be sorted")
            if organization["permissions"] != sorted(organization["permissions"]):
                raise CollaborationContractError("collaboration permissions must be sorted")
            workspace_ids.extend(item["workspace_id"] for item in organization["workspaces"])
        if len(set(workspace_ids)) != len(workspace_ids):
            raise CollaborationContractError("workspace identity is not globally unique")

    @staticmethod
    def _validate_topic(document: JsonObject) -> None:
        if _timestamp(document["updated_at"], "topic update") < _timestamp(document["created_at"], "topic creation"):
            raise CollaborationContractError("topic update precedes creation")

    @staticmethod
    def _validate_message_page(document: JsonObject) -> None:
        messages = document["messages"]
        ids = [item["message_id"] for item in messages]
        sequences = [item["sequence"] for item in messages]
        if len(set(ids)) != len(ids) or sequences != sorted(set(sequences)):
            raise CollaborationContractError("message page identities or sequences are not strictly ordered")
        occurred = [_timestamp(item["occurred_at"], "message occurrence") for item in messages]
        if occurred != sorted(occurred):
            raise CollaborationContractError("message occurrence time decreases")
        if not messages and document["next_cursor"] != document["after_cursor"]:
            raise CollaborationContractError("empty message page changed its cursor")

    @staticmethod
    def _validate_runner(document: JsonObject) -> None:
        if document["owner"]["type"] != "user":
            raise CollaborationContractError("a contributed runner requires a user owner")
        capabilities = document["capabilities"]
        for field in ("harness_ids", "models", "reasoning_efforts"):
            if capabilities[field] != sorted(capabilities[field]):
                raise CollaborationContractError("runner capabilities must be sorted")
        if document["policy"]["workspace_ids"] != sorted(document["policy"]["workspace_ids"]):
            raise CollaborationContractError("runner workspace policy must be sorted")
        _timestamp(document["last_seen_at"], "runner heartbeat")

    @staticmethod
    def _validate_operation(document: JsonObject) -> None:
        if document["requested_by"]["type"] != "user":
            raise CollaborationContractError("shared inference requires a user requester")
        runner = document["runner"]
        if runner is not None and runner["owner"]["type"] != "user":
            raise CollaborationContractError("shared inference runner requires a user owner")
        if document["route"] == "contributed_codex" and document["usage"] is not None:
            raise CollaborationContractError("contributed Codex inference cannot report API spend")
        if _timestamp(document["updated_at"], "operation update") < _timestamp(
            document["created_at"], "operation creation"
        ):
            raise CollaborationContractError("operation update precedes creation")

    @staticmethod
    def _validate_lease(document: JsonObject) -> None:
        if document["request_digest"] != jcs_digest(document["request"]):
            raise CollaborationContractError("runner lease request digest does not bind its request")
        created = _timestamp(document["created_at"], "lease creation")
        expires = _timestamp(document["expires_at"], "lease expiration")
        if expires <= created:
            raise CollaborationContractError("runner lease does not expire after creation")


@dataclass(frozen=True, slots=True)
class AcceptedCollaborationRecord:
    """One immutable collaboration record accepted against the canonical family."""

    _document: JsonObject
    _validator: CollaborationContractValidator

    @classmethod
    def _from_validated(
        cls, document: JsonObject, validator: CollaborationContractValidator
    ) -> AcceptedCollaborationRecord:
        return cls(copy.deepcopy(document), validator)

    @classmethod
    def from_bytes(
        cls, payload: bytes, *, validator: CollaborationContractValidator | None = None
    ) -> AcceptedCollaborationRecord:
        return (validator or canonical_collaboration_validator()).accept(payload)

    @classmethod
    def from_document(
        cls, document: Mapping[str, Any], *, validator: CollaborationContractValidator | None = None
    ) -> AcceptedCollaborationRecord:
        selected = validator or canonical_collaboration_validator()
        selected.validate(document)
        return cls._from_validated(dict(document), selected)

    @property
    def schema_id(self) -> str:
        return str(self._document["schema_id"])

    @property
    def record_id(self) -> str:
        return str(self._document[_RECORD_ID_FIELDS[self.schema_id]])

    def document(self) -> JsonObject:
        return copy.deepcopy(self._document)

    def to_bytes(self) -> bytes:
        return json.dumps(self._document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _collaboration_repository_root() -> Path:
    if getattr(sys, "frozen", False):
        extraction_root = getattr(sys, "_MEIPASS", None)
        if not isinstance(extraction_root, str) or not extraction_root or not Path(extraction_root).is_absolute():
            raise CollaborationContractError("frozen Desktop collaboration root is unavailable")
        return Path(extraction_root)
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=2)
def _canonical_collaboration_validator(repository_root: Path) -> CollaborationContractValidator:
    return CollaborationContractValidator(repository_root)


def canonical_collaboration_validator() -> CollaborationContractValidator:
    return _canonical_collaboration_validator(_collaboration_repository_root())


__all__ = [
    "COLLABORATION_VERSION",
    "MAX_COLLABORATION_RECORD_BYTES",
    "AcceptedCollaborationRecord",
    "CollaborationContractError",
    "CollaborationContractValidator",
    "canonical_collaboration_validator",
]
