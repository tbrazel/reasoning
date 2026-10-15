"""Explicit synthetic, zero-priced hosted run from a Desktop connection.

The only uploaded file is the fixed sentence below. No local project file is
read, and the advertised fake descriptors must match the pinned synthetic
route before any mutation. One click creates one independent immutable project
and run, so provider idempotency and Desktop reconnect state remain exact.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from .cloud_client import (
    AcceptedCloudClientRecord,
    canonical_run_request_digest,
    idempotency_key_digest,
    jcs_digest,
)
from .desktop_cloud import DesktopCloudController, DesktopCloudRuntimeError
from .harnesses.desktop_hybrid import AcceptedContractRecord

_VERSION = "1.0.0-alpha.2"
_SUFFIX = re.compile(r"[0-9a-f]{32}\Z")
_CONTENT = b"Reasoning synthetic hosted-provider check. No local project data.\n"
_IMAGE = {
    "repository": "fixture.invalid/reasoning/fake-compute",
    "digest": "sha256:" + "1" * 64,
    "capabilities_digest": "sha256:" + "2" * 64,
    "attestation_digest": "sha256:" + "3" * 64,
}
_PINNED_DESCRIPTOR_DIGESTS = {
    "harness": "sha256:14f1c8c864a07e9b908ece50644f00d96ba63819288631893982181b82e24cdb",
    "inference": "sha256:1fb99dfb286e8c84f1f61e23837e09349a992adce1da434eda4077960112f3b8",
    "compute": "sha256:affc90e0f262356f1a5e900fbb37b49668a6df459a33ee5b3e734c0469e97a49",
}


@dataclass(frozen=True)
class DesktopFakeDemo:
    cloud_project_id: str
    project: AcceptedContractRecord
    run: AcceptedCloudClientRecord
    project_idempotency_key: str
    run_idempotency_key: str
    contents: dict[str, bytes]


def _timestamp(now: datetime) -> str:
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise DesktopCloudRuntimeError("synthetic run clock is unavailable")
    return now.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_desktop_fake_demo(
    session: dict,
    capabilities: dict,
    options: dict,
    *,
    suffix: str,
    now: datetime,
) -> DesktopFakeDemo:
    """Build and locally validate one canonical fake-only mutation."""
    if not isinstance(suffix, str) or _SUFFIX.fullmatch(suffix) is None:
        raise DesktopCloudRuntimeError("synthetic run identity is invalid")
    if session.get("state") != "connected" or capabilities.get("snapshot_id") is None:
        raise DesktopCloudRuntimeError("synthetic run requires a connected session")
    if {"route_plan": _VERSION, "pricing": _VERSION} not in capabilities.get("route_pricing_pairs", []):
        raise DesktopCloudRuntimeError("synthetic run requires the advertised fake pricing pair")
    try:
        harness = next(item for item in options["harnesses"] if item["harness_id"] == "reasoning-fake")
        inference = next(item for item in options["inference_routes"] if item["route_id"] == "route_cloud_fake_free")
        compute = next(item for item in options["compute_targets"] if item["target_id"] == "target_cloud_fake_compute")
        actor = session["actor"]
        tenant_id = session["tenant_id"]
        snapshot_digest = session["capability_snapshot_digest"]
        if (
            actor.get("type") != "user"
            or harness["location"] != "hosted"
            or inference["location"] != "hosted"
            or compute["location"] != "hosted"
            or inference["provider"] != "provider_reasoning_fake"
            or "reasoning/fake-echo-v1" not in inference["model_options"]
            or "none" not in inference["reasoning_efforts"]
            or compute["executor"]["target_kind"] != "fake"
        ):
            raise ValueError("fake-only route changed")
    except (KeyError, TypeError, ValueError, StopIteration) as error:
        raise DesktopCloudRuntimeError("advertised fake-only route is unavailable") from error

    instant = _timestamp(now)
    cloud_project_id = f"project_demo_{suffix}"
    project_id = cloud_project_id
    bundle_id = f"project_bundle_demo_{suffix}"
    operation_id = f"operation_demo_{suffix}"
    transfer_id = f"transfer_demo_{suffix}"
    revision_digest = jcs_digest({"synthetic_content": _CONTENT.decode("ascii")})
    content_digest = "sha256:" + hashlib.sha256(_CONTENT).hexdigest()
    disclosure = {
        "inputs": [{
            "artifact_id": f"artifact_demo_input_{suffix}",
            "digest": content_digest,
            "size_bytes": len(_CONTENT),
            "media_type": "text/plain",
            "purpose": "both",
        }],
        "retained_cloud_data": [
            {"kind": "outputs", "retention": "account_policy", "purpose": "Return the synthetic result."},
            {"kind": "logs", "retention": "operation_lifetime", "purpose": "Diagnose this fake run."},
            {"kind": "operation_metadata", "retention": "indefinite_audit", "purpose": "Preserve run lineage."},
            {"kind": "usage", "retention": "indefinite_audit", "purpose": "Settle the zero-priced run."},
            {"kind": "provenance", "retention": "indefinite_audit", "purpose": "Attribute the fake route."},
        ],
        "omitted_data_classes": [
            "unlisted_project_files", "local_harness_state", "authentication_material", "local_paths",
        ],
    }
    disclosure_digest = jcs_digest(disclosure)
    project_document = {
        "schema_id": "reasoning.platform.project-bundle",
        "schema_version": "1.0.0-alpha.1",
        "bundle_id": bundle_id,
        "project": {
            "project_id": project_id,
            "title": "Synthetic Cloud check",
            "origin": "local",
            "revision_id": f"revision_demo_{suffix}",
            "revision_digest": revision_digest,
            "created_at": instant,
        },
        "linkage": {"state": "unlinked"},
        "transfer_manifests": [{
            "transfer_id": transfer_id,
            "operation_id": operation_id,
            "binding": {
                "actor": actor,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "project_revision_digest": revision_digest,
            },
            "mode": "one_step",
            "disclosure": disclosure,
            "disclosure_digest": disclosure_digest,
            "approval": {
                "approval_id": f"approval_demo_{suffix}",
                "actor": actor,
                "approved_at": instant,
                "scope": "project_transfer",
                "approved_disclosure_digest": disclosure_digest,
            },
            "future_transfers_authorized": False,
        }],
        "contents": [{
            "path": "README.md",
            "digest": content_digest,
            "size_bytes": len(_CONTENT),
            "media_type": "text/plain",
            "class": "project_file",
        }],
        "export": {
            "created_at": instant,
            "created_by": actor,
            "format": "reasoning-project-bundle+json",
            "security_material": "excluded",
            "server_policy": "excluded",
        },
    }
    project = AcceptedContractRecord.from_document(project_document)
    route = {
        "schema_id": "reasoning.platform.route-plan",
        "schema_version": _VERSION,
        "route_plan_id": f"route_plan_demo_{suffix}",
        "operation_id": operation_id,
        "actor": actor,
        "tenant_id": tenant_id,
        "persistence": {
            "location": "local",
            "project_bundle_id": bundle_id,
            "project_id": project_id,
            "project_revision_digest": revision_digest,
            "transfer_manifest_id": transfer_id,
        },
        "harness": {
            "harness_id": harness["harness_id"],
            "descriptor_digest": jcs_digest(harness),
            "location": "hosted",
        },
        "inference": {
            "route_id": inference["route_id"],
            "route_digest": jcs_digest(inference),
            "provider": inference["provider"],
            "model": "reasoning/fake-echo-v1",
            "reasoning_effort": "none",
            "location": "hosted",
        },
        "compute": {
            "target_id": compute["target_id"],
            "target_digest": jcs_digest(compute),
            "location": "hosted",
            "executor_contract_version": "1.0.0-alpha.1",
            "image": _IMAGE,
        },
        "decision": {"source": "user", "decided_at": instant, "compatibility": "passed", "policy": "passed"},
        "pricing_lifecycle_id": f"pricing_demo_{suffix}",
        "pricing_contract_version": _VERSION,
    }
    AcceptedContractRecord.from_document(route)
    run_key = f"synthetic-demo-run-{suffix}"
    run_document = {
        "schema_id": "reasoning.cloud-client.run",
        "schema_version": "1.0.0-alpha.1",
        "operation_id": operation_id,
        "actor": actor,
        "tenant_id": tenant_id,
        "capability_snapshot_digest": snapshot_digest,
        "cloud_project_id": cloud_project_id,
        "idempotency_key_digest": idempotency_key_digest(run_key),
        "request_digest": "sha256:" + "0" * 64,
        "state": "queued",
        "cancellation_requested": False,
        "project_binding": {
            "project_bundle_id": bundle_id,
            "project_id": project_id,
            "project_revision_digest": revision_digest,
            "bundle_digest": jcs_digest(project_document),
            "transfer_manifest_id": transfer_id,
        },
        "route_plan": route,
        "route_plan_digest": jcs_digest(route),
        "pricing": None,
        "pricing_digest": None,
        "created_at": instant,
        "updated_at": instant,
        "terminal_at": None,
    }
    run_document["request_digest"] = canonical_run_request_digest(run_document)
    return DesktopFakeDemo(
        cloud_project_id=cloud_project_id,
        project=project,
        run=AcceptedCloudClientRecord.from_document(run_document),
        project_idempotency_key=f"synthetic-demo-project-{suffix}",
        run_idempotency_key=run_key,
        contents={"README.md": _CONTENT},
    )


def submit_desktop_fake_demo(cloud: DesktopCloudController, *, opt_in: bool) -> dict:
    if opt_in is not True:
        raise DesktopCloudRuntimeError("synthetic Cloud transfer requires explicit opt-in")
    if not isinstance(cloud, DesktopCloudController):
        raise DesktopCloudRuntimeError("synthetic Cloud controller is unavailable")
    demo = build_desktop_fake_demo(
        cloud.session(), cloud.capabilities(), cloud.route_options(),
        suffix=uuid4().hex, now=datetime.now(UTC),
    )
    route = demo.run.document()["route_plan"]
    observed = {
        "harness": route["harness"]["descriptor_digest"],
        "inference": route["inference"]["route_digest"],
        "compute": route["compute"]["target_digest"],
    }
    if observed != _PINNED_DESCRIPTOR_DIGESTS:
        raise DesktopCloudRuntimeError("hosted fake route differs from the pinned synthetic policy")
    cloud.put_project(
        demo.cloud_project_id, demo.project, demo.contents,
        idempotency_key=demo.project_idempotency_key, opt_in=True,
    )
    return cloud.submit_run(demo.run, demo.project, idempotency_key=demo.run_idempotency_key)


__all__ = ["DesktopFakeDemo", "build_desktop_fake_demo", "submit_desktop_fake_demo"]
