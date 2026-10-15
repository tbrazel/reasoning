from __future__ import annotations

import pytest

from reasoning_ledger.agents import PromptFidelity
from reasoning_ledger.harnesses import (
    ExecutionTargetCapabilities,
    ExecutionTargetDeployment,
    ExecutionTargetKind,
    HarnessAdapterContext,
    HarnessCapabilities,
    HarnessDeployment,
    HarnessDeploymentRegistry,
    HarnessSelectionCatalog,
    HarnessTransport,
    InferenceRoute,
    InferenceRouteDeployment,
    NetworkAccess,
    TargetIsolation,
    WorkspaceAccess,
)


class StubAdapter:
    def __init__(self, capabilities: HarnessCapabilities) -> None:
        self.capabilities = capabilities


def _harness() -> HarnessCapabilities:
    return HarnessCapabilities(
        harness_id="cloud-reference",
        display_name="Cloud reference",
        adapter_version="1",
        transport=HarnessTransport.REMOTE_SERVICE,
        prompt_fidelity=PromptFidelity.WIRE_EXACT,
        workspace_access=(WorkspaceAccess.NONE, WorkspaceAccess.READ_WRITE),
    )


def _route() -> InferenceRoute:
    return InferenceRoute(
        route_id="cloud-openrouter",
        display_name="Cloud OpenRouter",
        provider="openrouter",
        harness_ids=("cloud-reference",),
        model_patterns=("openrouter/*",),
    )


def _target() -> ExecutionTargetCapabilities:
    return ExecutionTargetCapabilities(
        target_id="cloud-vm",
        display_name="Cloud VM",
        kind=ExecutionTargetKind.REMOTE_SANDBOX,
        isolation=TargetIsolation.VM,
        workspace_access=(WorkspaceAccess.NONE, WorkspaceAccess.READ_WRITE),
        network_access=(NetworkAccess.DENIED,),
        supports_processes=True,
        durable_workspace=True,
    )


def test_registry_extends_catalog_with_independently_registered_targets():
    capability = _harness()
    registry = HarnessDeploymentRegistry(
        harnesses=(
            HarnessDeployment(
                capability,
                lambda _context: StubAdapter(capability),
                chat_modes=("development",),
            ),
        ),
        routes=(
            InferenceRouteDeployment(
                _route(),
                chat_modes=("development",),
            ),
        ),
        targets=(
            ExecutionTargetDeployment(
                _target(),
                chat_modes=("development",),
            ),
        ),
    )

    development = registry.extend_catalog(
        HarnessSelectionCatalog((), (), ()),
        chat_mode="development",
    )
    research = registry.extend_catalog(
        HarnessSelectionCatalog((), (), ()),
        chat_mode="research",
    )

    assert [item.harness_id for item in development.harnesses] == ["cloud-reference"]
    assert [item.route_id for item in development.routes] == ["cloud-openrouter"]
    assert [item.target_id for item in development.targets] == ["cloud-vm"]
    assert research == HarnessSelectionCatalog((), (), ())


def test_registry_factory_is_runtime_only_and_capability_checked():
    capability = _harness()
    contexts: list[HarnessAdapterContext] = []

    def factory(context: HarnessAdapterContext):
        contexts.append(context)
        return StubAdapter(capability)

    registry = HarnessDeploymentRegistry(
        harnesses=(HarnessDeployment(capability, factory),),
    )
    context = HarnessAdapterContext("project-1", "chat-1", "session-1")

    adapter = registry.adapter_for("cloud-reference", context)

    assert adapter.capabilities == capability
    assert contexts == [context]


def test_registry_rejects_route_without_registered_harness():
    with pytest.raises(ValueError, match="unregistered harnesses"):
        HarnessDeploymentRegistry(
            routes=(InferenceRouteDeployment(_route()),),
        )


def test_registry_rejects_adapter_capability_drift():
    capability = _harness()
    other = HarnessCapabilities(
        harness_id="other",
        display_name="Other",
        adapter_version="1",
        transport=HarnessTransport.REMOTE_SERVICE,
        prompt_fidelity=PromptFidelity.WIRE_EXACT,
    )
    registry = HarnessDeploymentRegistry(
        harnesses=(HarnessDeployment(capability, lambda _context: StubAdapter(other)),),
    )

    with pytest.raises(ValueError, match="different capability snapshot"):
        registry.adapter_for(
            "cloud-reference",
            HarnessAdapterContext("project-1", "chat-1", "session-1"),
        )
