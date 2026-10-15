"""Deployment-time registration of harnesses, routes, and execution targets.

Durable projects contain only stable identifiers and capability snapshots.
Adapter factories, service clients, endpoint locations, credentials, and live
readiness belong to the serving deployment and stay in this in-memory
registry.  Execution targets are registered separately from harnesses so a
cloud deployment may vary container, VM, GPU, and stateless targets without
claiming that the agent loop itself supplies those security properties.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TypeVar

from .contracts import ExecutionTargetCapabilities, HarnessAdapter, HarnessCapabilities
from .selection import HarnessSelectionCatalog, InferenceRoute

_CHAT_MODES = {"research", "development"}
_Deployment = TypeVar("_Deployment")


@dataclass(frozen=True)
class HarnessAdapterContext:
    """Non-secret Reasoning identity supplied to a deployment adapter factory."""

    project_id: str
    chat_id: str
    session_id: str

    def __post_init__(self) -> None:
        if not self.project_id.strip() or not self.chat_id.strip() or not self.session_id.strip():
            raise ValueError("project_id, chat_id, and session_id are required")


HarnessAdapterFactory = Callable[[HarnessAdapterContext], HarnessAdapter]


@dataclass(frozen=True)
class HarnessDeployment:
    capabilities: HarnessCapabilities
    adapter_factory: HarnessAdapterFactory
    chat_modes: tuple[str, ...] = ("research", "development")

    def __post_init__(self) -> None:
        _validate_modes(self.chat_modes)


@dataclass(frozen=True)
class InferenceRouteDeployment:
    route: InferenceRoute
    chat_modes: tuple[str, ...] = ("research", "development")

    def __post_init__(self) -> None:
        _validate_modes(self.chat_modes)


@dataclass(frozen=True)
class ExecutionTargetDeployment:
    capabilities: ExecutionTargetCapabilities
    chat_modes: tuple[str, ...] = ("research", "development")

    def __post_init__(self) -> None:
        _validate_modes(self.chat_modes)


class HarnessDeploymentRegistry:
    """Immutable deployment extensions for selection and adapter resolution."""

    def __init__(
        self,
        *,
        harnesses: Iterable[HarnessDeployment] = (),
        routes: Iterable[InferenceRouteDeployment] = (),
        targets: Iterable[ExecutionTargetDeployment] = (),
    ) -> None:
        self._harnesses = _index(harnesses, lambda item: item.capabilities.harness_id, "harness")
        self._routes = _index(routes, lambda item: item.route.route_id, "inference route")
        self._targets = _index(targets, lambda item: item.capabilities.target_id, "execution target")

        known_harnesses = set(self._harnesses)
        unknown = {
            harness_id
            for deployment in self._routes.values()
            for harness_id in deployment.route.harness_ids
            if harness_id not in known_harnesses
        }
        if unknown:
            raise ValueError(
                "deployment routes reference unregistered harnesses: "
                + ", ".join(sorted(unknown))
            )

    def has_harness(self, harness_id: str) -> bool:
        return harness_id in self._harnesses

    def adapter_for(
        self,
        harness_id: str,
        context: HarnessAdapterContext,
    ) -> HarnessAdapter:
        try:
            deployment = self._harnesses[harness_id]
        except KeyError as error:
            raise KeyError(f"unregistered deployment harness: {harness_id}") from error
        adapter = deployment.adapter_factory(context)
        if adapter.capabilities != deployment.capabilities:
            raise ValueError(
                f"adapter factory for {harness_id} returned a different capability snapshot"
            )
        return adapter

    def extend_catalog(
        self,
        base: HarnessSelectionCatalog,
        *,
        chat_mode: str,
    ) -> HarnessSelectionCatalog:
        if chat_mode not in _CHAT_MODES:
            raise ValueError(f"unknown chat mode: {chat_mode}")
        harnesses = tuple(
            deployment.capabilities
            for deployment in self._harnesses.values()
            if chat_mode in deployment.chat_modes
        )
        included_harnesses = {item.harness_id for item in harnesses}
        routes = tuple(
            deployment.route
            for deployment in self._routes.values()
            if chat_mode in deployment.chat_modes
            and set(deployment.route.harness_ids).issubset(included_harnesses)
        )
        targets = tuple(
            deployment.capabilities
            for deployment in self._targets.values()
            if chat_mode in deployment.chat_modes
        )
        return HarnessSelectionCatalog(
            harnesses=base.harnesses + harnesses,
            routes=base.routes + routes,
            targets=base.targets + targets,
        )


def _validate_modes(chat_modes: tuple[str, ...]) -> None:
    if not chat_modes:
        raise ValueError("chat_modes cannot be empty")
    if len(chat_modes) != len(set(chat_modes)):
        raise ValueError("chat_modes cannot contain duplicates")
    unknown = set(chat_modes).difference(_CHAT_MODES)
    if unknown:
        raise ValueError("unknown chat modes: " + ", ".join(sorted(unknown)))


def _index(
    values: Iterable[_Deployment],
    key: Callable[[_Deployment], str],
    label: str,
) -> dict[str, _Deployment]:
    result: dict[str, _Deployment] = {}
    for value in values:
        identifier = key(value)
        if identifier in result:
            raise ValueError(f"duplicate deployment {label}: {identifier}")
        result[identifier] = value
    return result
