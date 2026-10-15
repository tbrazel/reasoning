from __future__ import annotations

import copy
from pathlib import Path

import pytest

from reasoning_ledger.cloud_client import jcs_digest
from reasoning_ledger.collaboration import (
    AcceptedCollaborationRecord,
    CollaborationContractError,
    CollaborationContractValidator,
)

ROOT = Path(__file__).resolve().parents[1]


def bootstrap() -> dict:
    return {
        "schema_id": "reasoning.collaboration.bootstrap",
        "schema_version": "1.0.0-alpha.1",
        "account_id": "account_alice",
        "actor": {"type": "user", "id": "user_alice"},
        "default_organization_id": "organization_personal_alice",
        "default_workspace_id": "workspace_personal_alice",
        "organizations": [
            {
                "organization_id": "organization_personal_alice",
                "name": "Alice's personal organization",
                "kind": "personal",
                "role_ids": ["owner"],
                "permissions": [
                    "chat.read",
                    "chat.write",
                    "compute.contribute",
                    "compute.use",
                    "workflow.read",
                    "workflow.run",
                    "workflow.write",
                    "workspace.read",
                    "workspace.write",
                ],
                "workspaces": [
                    {
                        "workspace_id": "workspace_personal_alice",
                        "name": "Personal workspace",
                        "status": "active",
                        "created_at": "2026-09-23T14:00:00Z",
                    }
                ],
            },
            {
                "organization_id": "organization_research_group",
                "name": "Research group",
                "kind": "shared",
                "role_ids": ["member"],
                "permissions": [
                    "chat.read",
                    "chat.write",
                    "compute.use",
                    "workflow.read",
                    "workflow.run",
                    "workflow.write",
                    "workspace.read",
                    "workspace.write",
                ],
                "workspaces": [
                    {
                        "workspace_id": "workspace_collective_project",
                        "name": "Collective project",
                        "status": "active",
                        "created_at": "2026-09-23T14:01:00Z",
                    }
                ],
            },
        ],
    }


@pytest.fixture
def validator() -> CollaborationContractValidator:
    return CollaborationContractValidator(ROOT)


def test_bootstrap_binds_one_default_personal_organization(validator: CollaborationContractValidator):
    accepted = AcceptedCollaborationRecord.from_document(bootstrap(), validator=validator)
    assert accepted.schema_id == "reasoning.collaboration.bootstrap"
    assert accepted.record_id == "account_alice"
    assert AcceptedCollaborationRecord.from_bytes(accepted.to_bytes(), validator=validator).document() == bootstrap()


@pytest.mark.parametrize("mutation", ["second_personal", "shared_default", "duplicate_workspace", "unsorted_permissions"])
def test_bootstrap_rejects_ambiguous_or_unstable_scope(
    validator: CollaborationContractValidator, mutation: str
):
    value = bootstrap()
    if mutation == "second_personal":
        value["organizations"][1]["kind"] = "personal"
    elif mutation == "shared_default":
        value["default_organization_id"] = "organization_research_group"
    elif mutation == "duplicate_workspace":
        value["organizations"][1]["workspaces"][0]["workspace_id"] = "workspace_personal_alice"
    else:
        value["organizations"][0]["permissions"].reverse()
    with pytest.raises(CollaborationContractError):
        validator.validate(value)


def test_topics_messages_runners_and_leases_are_credential_free_and_bound(
    validator: CollaborationContractValidator,
):
    topic = {
        "schema_id": "reasoning.collaboration.topic",
        "schema_version": "1.0.0-alpha.1",
        "organization_id": "organization_research_group",
        "workspace_id": "workspace_collective_project",
        "topic_id": "topic_shared_question",
        "title": "Shared question",
        "state": "open",
        "created_by": {"type": "user", "id": "user_alice"},
        "created_at": "2026-09-23T14:02:00Z",
        "updated_at": "2026-09-23T14:02:00Z",
    }
    validator.validate(topic)
    page = {
        "schema_id": "reasoning.collaboration.message-page",
        "schema_version": "1.0.0-alpha.1",
        "organization_id": topic["organization_id"],
        "workspace_id": topic["workspace_id"],
        "topic_id": topic["topic_id"],
        "after_cursor": None,
        "messages": [
            {
                "message_id": "message_first",
                "sequence": 1,
                "actor": {"type": "user", "id": "user_alice"},
                "kind": "human",
                "content": "Check this together.",
                "operation_id": None,
                "occurred_at": "2026-09-23T14:03:00Z",
            }
        ],
        "next_cursor": "cursor_1",
        "complete": True,
    }
    validator.validate(page)
    runner = {
        "schema_id": "reasoning.collaboration.runner",
        "schema_version": "1.0.0-alpha.1",
        "organization_id": topic["organization_id"],
        "runner_id": "runner_alice_desktop",
        "owner": {"type": "user", "id": "user_alice"},
        "display_name": "Alice's Desktop",
        "state": "available",
        "capabilities": {
            "harness_ids": ["codex"],
            "models": ["gpt-5.6-sol"],
            "reasoning_efforts": ["medium", "high"],
            "max_concurrency": 1,
        },
        "policy": {
            "workspace_ids": ["workspace_collective_project"],
            "network_access": "allowlisted",
            "requires_local_approval": True,
        },
        "last_seen_at": "2026-09-23T14:04:00Z",
    }
    with pytest.raises(CollaborationContractError, match="sorted"):
        validator.validate(runner)
    runner["capabilities"]["reasoning_efforts"] = ["high", "medium"]
    validator.validate(runner)
    request = {
        "harness_id": "codex",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "prompt": "Check this together.",
        "timeout_seconds": 900,
        "max_output_tokens": 12000,
        "network_access": "allowlisted",
    }
    lease = {
        "schema_id": "reasoning.collaboration.runner-lease",
        "schema_version": "1.0.0-alpha.1",
        "organization_id": topic["organization_id"],
        "workspace_id": topic["workspace_id"],
        "topic_id": topic["topic_id"],
        "runner_id": runner["runner_id"],
        "lease_id": "lease_shared_question",
        "operation_id": "operation_shared_question",
        "state": "offered",
        "request": request,
        "request_digest": jcs_digest(request),
        "created_at": "2026-09-23T14:05:00Z",
        "expires_at": "2026-09-23T14:10:00Z",
    }
    validator.validate(lease)
    operation = {
        "schema_id": "reasoning.collaboration.operation",
        "schema_version": "1.0.0-alpha.1",
        "organization_id": topic["organization_id"],
        "workspace_id": topic["workspace_id"],
        "topic_id": topic["topic_id"],
        "operation_id": lease["operation_id"],
        "requested_by": {"type": "user", "id": "user_alice"},
        "route": "contributed_codex",
        "prompt": request["prompt"],
        "model": request["model"],
        "reasoning_effort": request["reasoning_effort"],
        "state": "running",
        "runner": {
            "runner_id": runner["runner_id"],
            "display_name": runner["display_name"],
            "owner": runner["owner"],
        },
        "progress": "Local Codex is answering",
        "usage": None,
        "created_at": "2026-09-23T14:05:00Z",
        "updated_at": "2026-09-23T14:06:00Z",
    }
    validator.validate(operation)
    changed = copy.deepcopy(lease)
    changed["request"]["prompt"] = "Different request"
    with pytest.raises(CollaborationContractError, match="digest"):
        validator.validate(changed)
    secret = copy.deepcopy(lease)
    secret["request"]["prompt"] = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"
    secret["request_digest"] = jcs_digest(secret["request"])
    with pytest.raises(CollaborationContractError, match="Credential|credential"):
        validator.validate(secret)
