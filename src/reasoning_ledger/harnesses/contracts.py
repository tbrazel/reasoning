"""Stable control-plane contracts for replaceable agent harnesses.

The types in this module deliberately separate three compatibility axes:

* a harness owns an agent loop and its session semantics;
* an execution target owns files, processes, isolation, and network policy;
* an inference route owns a provider and model.

Keeping those axes independent lets the same Reasoning client drive local CLI
harnesses, an in-process reference harness, and remote sandbox services without
making any one deployment topology part of the durable project model.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from ..agents.contracts import (
    CompletionArtifactRequirement,
    ExecutorCapabilities,
    PreparedSkill,
    PreparedTool,
    PromptFidelity,
    RunBudget,
    SandboxPolicy,
)

JsonObject = dict[str, Any]
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max", "ultra")
_SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "credentials",
    "password",
    "private_key",
    "secret",
    "token",
}


class HarnessTransport(StrEnum):
    """How the Reasoning control plane reaches the harness implementation."""

    IN_PROCESS = "in_process"
    LOCAL_PROCESS = "local_process"
    REMOTE_SERVICE = "remote_service"


class ExecutionTargetKind(StrEnum):
    """Where the harness's tools and workspace operations execute."""

    HOST = "host"
    LOCAL_SANDBOX = "local_sandbox"
    REMOTE_SANDBOX = "remote_sandbox"
    STATELESS = "stateless"


class TargetIsolation(StrEnum):
    NONE = "none"
    PROCESS = "process"
    OS_SANDBOX = "os_sandbox"
    CONTAINER = "container"
    VM = "vm"


class WorkspaceAccess(StrEnum):
    NONE = "none"
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class NetworkAccess(StrEnum):
    DENIED = "denied"
    ALLOWLISTED = "allowlisted"
    UNRESTRICTED = "unrestricted"


class ReasoningVisibility(StrEnum):
    NONE = "none"
    SUMMARY = "summary"
    STREAMED_SUMMARY = "streamed_summary"


class SessionMode(StrEnum):
    FRESH = "fresh"
    RESUME = "resume"
    FORK = "fork"


class HarnessApprovalDecision(StrEnum):
    ACCEPT = "accept"
    ACCEPT_FOR_SESSION = "acceptForSession"
    DECLINE = "decline"


class HarnessSteerStatus(StrEnum):
    STEERED = "steered"
    NO_TURN = "no_turn"
    FAILED = "failed"


@dataclass(frozen=True)
class HarnessCapabilities:
    """Agent-loop features supplied by one concrete harness adapter."""

    harness_id: str
    display_name: str
    adapter_version: str
    transport: HarnessTransport
    prompt_fidelity: PromptFidelity
    protocol_version: str = "1"
    session_modes: tuple[SessionMode, ...] = (SessionMode.FRESH,)
    workspace_access: tuple[WorkspaceAccess, ...] = (WorkspaceAccess.NONE,)
    reasoning_efforts: tuple[str, ...] = ()
    reasoning_visibility: ReasoningVisibility = ReasoningVisibility.NONE
    supports_streaming: bool = True
    supports_tools: bool = False
    supports_skills: bool = False
    supports_structured_output: bool = False
    supports_mcp: bool = False
    supports_approvals: bool = False
    supports_interrupt: bool = False
    supports_steering: bool = False
    supports_checkpoints: bool = False
    supports_subagents: bool = False
    supports_context_compaction: bool = False
    reports_usage: bool = False
    reports_cost: bool = False
    model_patterns: tuple[str, ...] = ("*",)
    model_options: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_identifier(self.harness_id, "harness_id")
        if not self.display_name.strip():
            raise ValueError("display_name is required")
        _validate_unique(self.session_modes, "session_modes")
        _validate_unique(self.workspace_access, "workspace_access")
        _validate_unique(self.reasoning_efforts, "reasoning_efforts", allow_empty=True)
        if not self.adapter_version.strip():
            raise ValueError("adapter_version is required")
        if self.protocol_version != "1":
            raise ValueError("unsupported harness protocol_version")
        if SessionMode.FRESH not in self.session_modes:
            raise ValueError("every harness must support fresh sessions")
        invalid_efforts = set(self.reasoning_efforts).difference(_REASONING_EFFORTS)
        if invalid_efforts:
            raise ValueError(f"unknown reasoning efforts: {', '.join(sorted(invalid_efforts))}")
        if self.reasoning_visibility is ReasoningVisibility.STREAMED_SUMMARY and not self.supports_streaming:
            raise ValueError("streamed reasoning summaries require streaming support")


@dataclass(frozen=True)
class ExecutionTargetCapabilities:
    """Security and persistence properties of a tool execution target."""

    target_id: str
    display_name: str
    kind: ExecutionTargetKind
    isolation: TargetIsolation
    workspace_access: tuple[WorkspaceAccess, ...] = (WorkspaceAccess.NONE,)
    network_access: tuple[NetworkAccess, ...] = (NetworkAccess.DENIED,)
    supports_processes: bool = False
    durable_workspace: bool = False

    def __post_init__(self) -> None:
        _validate_identifier(self.target_id, "target_id")
        if not self.display_name.strip():
            raise ValueError("display_name is required")
        _validate_unique(self.workspace_access, "workspace_access")
        _validate_unique(self.network_access, "network_access")


@dataclass(frozen=True)
class HarnessRequirements:
    """Features a chat turn or workflow node requires before it may run."""

    session_mode: SessionMode = SessionMode.FRESH
    workspace_access: WorkspaceAccess = WorkspaceAccess.NONE
    network_access: NetworkAccess = NetworkAccess.DENIED
    minimum_isolation: TargetIsolation = TargetIsolation.NONE
    reasoning_effort: str | None = None
    reasoning_visibility: ReasoningVisibility = ReasoningVisibility.NONE
    requires_streaming: bool = False
    requires_tools: bool = False
    requires_skills: bool = False
    requires_structured_output: bool = False
    requires_mcp: bool = False
    requires_approvals: bool = False
    requires_interrupt: bool = False
    requires_steering: bool = False
    requires_checkpoints: bool = False
    requires_subagents: bool = False
    requires_context_compaction: bool = False
    requires_usage: bool = False
    requires_cost: bool = False
    requires_processes: bool = False
    requires_durable_workspace: bool = False

    def __post_init__(self) -> None:
        if self.reasoning_effort is not None and self.reasoning_effort not in _REASONING_EFFORTS:
            raise ValueError(f"unknown reasoning effort: {self.reasoning_effort}")


@dataclass(frozen=True)
class HarnessPreflightIssue:
    code: str
    scope: str
    detail: str


@dataclass(frozen=True)
class HarnessPreflightResult:
    compatible: bool
    issues: tuple[HarnessPreflightIssue, ...] = ()

    def to_dict(self) -> JsonObject:
        return asdict(self)


def preflight_harness(
    harness: HarnessCapabilities,
    target: ExecutionTargetCapabilities,
    requirements: HarnessRequirements,
) -> HarnessPreflightResult:
    """Check one harness/target pair without inferring features from names."""

    issues: list[HarnessPreflightIssue] = []

    def issue(code: str, scope: str, detail: str) -> None:
        issues.append(HarnessPreflightIssue(code=code, scope=scope, detail=detail))

    if requirements.session_mode not in harness.session_modes:
        issue("session_mode", "harness", f"{harness.display_name} does not support {requirements.session_mode.value} sessions")
    if not _supports_workspace(harness.workspace_access, requirements.workspace_access):
        issue("harness_workspace_access", "harness", f"{harness.display_name} cannot provide {requirements.workspace_access.value} access")
    if not _supports_workspace(target.workspace_access, requirements.workspace_access):
        issue("target_workspace_access", "target", f"{target.display_name} cannot provide {requirements.workspace_access.value} access")
    if requirements.network_access not in target.network_access:
        issue("network_access", "target", f"{target.display_name} cannot enforce {requirements.network_access.value} network access")
    if _isolation_rank(target.isolation) < _isolation_rank(requirements.minimum_isolation):
        issue(
            "isolation",
            "target",
            f"{target.display_name} provides {target.isolation.value} isolation; {requirements.minimum_isolation.value} is required",
        )
    if requirements.reasoning_effort is not None and requirements.reasoning_effort not in harness.reasoning_efforts:
        issue("reasoning_effort", "harness", f"{harness.display_name} does not advertise {requirements.reasoning_effort} reasoning")
    if _reasoning_rank(harness.reasoning_visibility) < _reasoning_rank(requirements.reasoning_visibility):
        issue(
            "reasoning_visibility",
            "harness",
            f"{harness.display_name} exposes {harness.reasoning_visibility.value}; {requirements.reasoning_visibility.value} is required",
        )

    feature_checks = (
        (requirements.requires_streaming, harness.supports_streaming, "streaming", "streaming"),
        (requirements.requires_tools, harness.supports_tools, "tools", "tool calls"),
        (requirements.requires_skills, harness.supports_skills, "skills", "skills"),
        (requirements.requires_structured_output, harness.supports_structured_output, "structured_output", "structured output"),
        (requirements.requires_mcp, harness.supports_mcp, "mcp", "MCP"),
        (requirements.requires_approvals, harness.supports_approvals, "approvals", "approval callbacks"),
        (requirements.requires_interrupt, harness.supports_interrupt, "interrupt", "interrupts"),
        (requirements.requires_steering, harness.supports_steering, "steering", "mid-turn steering"),
        (requirements.requires_checkpoints, harness.supports_checkpoints, "checkpoints", "checkpoints"),
        (requirements.requires_subagents, harness.supports_subagents, "subagents", "subagents"),
        (
            requirements.requires_context_compaction,
            harness.supports_context_compaction,
            "context_compaction",
            "context compaction",
        ),
        (requirements.requires_usage, harness.reports_usage, "usage", "usage reporting"),
        (requirements.requires_cost, harness.reports_cost, "cost", "cost reporting"),
    )
    for required, supported, code, label in feature_checks:
        if required and not supported:
            issue(code, "harness", f"{harness.display_name} does not support {label}")
    if requirements.requires_processes and not target.supports_processes:
        issue("processes", "target", f"{target.display_name} does not support process execution")
    if requirements.requires_durable_workspace and not target.durable_workspace:
        issue("durable_workspace", "target", f"{target.display_name} does not provide durable workspace storage")
    return HarnessPreflightResult(compatible=not issues, issues=tuple(issues))


def legacy_executor_capabilities(
    capabilities: ExecutorCapabilities,
    *,
    transport: HarnessTransport,
    adapter_version: str = "legacy-1",
    workspace_access: tuple[WorkspaceAccess, ...] = (WorkspaceAccess.NONE,),
    reasoning_efforts: tuple[str, ...] = (),
    reasoning_visibility: ReasoningVisibility = ReasoningVisibility.NONE,
    supports_approvals: bool = False,
    supports_interrupt: bool = False,
    supports_steering: bool = False,
    supports_checkpoints: bool = False,
    supports_subagents: bool = False,
    supports_context_compaction: bool = False,
    reports_usage: bool = False,
    reports_cost: bool = False,
) -> HarnessCapabilities:
    """Describe an existing one-shot executor through the harness vocabulary."""

    session_modes = (SessionMode.FRESH, SessionMode.RESUME, SessionMode.FORK) if capabilities.supports_sessions else (SessionMode.FRESH,)
    return HarnessCapabilities(
        harness_id=capabilities.executor_id,
        display_name=capabilities.display_name,
        adapter_version=adapter_version,
        transport=transport,
        prompt_fidelity=capabilities.prompt_fidelity,
        session_modes=session_modes,
        workspace_access=workspace_access,
        reasoning_efforts=reasoning_efforts,
        reasoning_visibility=reasoning_visibility,
        supports_streaming=capabilities.supports_streaming,
        supports_tools=capabilities.supports_tools,
        supports_skills=capabilities.supports_skills,
        supports_structured_output=capabilities.supports_structured_output,
        supports_mcp=capabilities.supports_mcp,
        supports_approvals=supports_approvals,
        supports_interrupt=supports_interrupt,
        supports_steering=supports_steering,
        supports_checkpoints=supports_checkpoints,
        supports_subagents=supports_subagents,
        supports_context_compaction=supports_context_compaction,
        reports_usage=reports_usage,
        reports_cost=reports_cost,
        model_patterns=capabilities.model_patterns,
        model_options=capabilities.model_options,
    )


@dataclass(frozen=True)
class HarnessSessionSpec:
    """Reasoning-owned session request; adapter-private state stays opaque."""

    harness_id: str
    target: ExecutionTargetCapabilities
    model: str
    model_provider: str | None = None
    mode: SessionMode = SessionMode.FRESH
    source_session_id: str | None = None
    sandbox: SandboxPolicy = field(default_factory=SandboxPolicy)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_identifier(self.harness_id, "harness_id")
        if not self.model.strip():
            raise ValueError("model is required")
        if self.mode in {SessionMode.RESUME, SessionMode.FORK} and not self.source_session_id:
            raise ValueError(f"{self.mode.value} session mode requires source_session_id")
        _validate_no_secret_keys(self.metadata, "session metadata")


@dataclass(frozen=True)
class HarnessSession:
    session_id: str
    capabilities: HarnessCapabilities
    target: ExecutionTargetCapabilities
    model: str
    model_provider: str | None = None
    adapter_session_ref: str | None = None

    @property
    def harness_id(self) -> str:
        return self.capabilities.harness_id

    @property
    def target_id(self) -> str:
        return self.target.target_id

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("session_id is required")
        if not self.model.strip():
            raise ValueError("model is required")
        if self.adapter_session_ref is not None and not self.adapter_session_ref.strip():
            raise ValueError("adapter_session_ref cannot be empty")


@dataclass(frozen=True)
class HarnessTurnRequest:
    turn_id: str
    messages: tuple[Mapping[str, Any], ...]
    effort: str | None = None
    tools: tuple[PreparedTool, ...] = ()
    skills: tuple[PreparedSkill, ...] = ()
    mcp_connection_ids: tuple[str, ...] = ()
    output_schema: JsonObject | None = None
    budget: RunBudget = field(default_factory=RunBudget)
    prompt_hashes: Mapping[str, str] = field(default_factory=dict)
    completion_markers: tuple[str, ...] = ()
    completion_artifacts: tuple[CompletionArtifactRequirement, ...] = ()
    completion_tool_calls: Mapping[str, int] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.turn_id.strip():
            raise ValueError("turn_id is required")
        if not self.messages:
            raise ValueError("at least one message is required")
        if self.effort is not None and self.effort not in _REASONING_EFFORTS:
            raise ValueError(f"unknown reasoning effort: {self.effort}")
        _validate_unique(self.mcp_connection_ids, "mcp_connection_ids", allow_empty=True)
        for connection_id in self.mcp_connection_ids:
            _validate_identifier(connection_id, "mcp_connection_id")
        _validate_no_secret_keys(self.metadata, "turn metadata")


class HarnessEventKind(StrEnum):
    SESSION_CREATED = "session.created"
    TURN_STARTED = "turn.started"
    MESSAGE_DELTA = "message.delta"
    MESSAGE_COMPLETED = "message.completed"
    REASONING_DELTA = "reasoning.delta"
    REASONING_COMPLETED = "reasoning.completed"
    PROGRESS = "progress"
    TOOL_REQUESTED = "tool.requested"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    FILE_CHANGED = "file.changed"
    APPROVAL_REQUIRED = "approval.required"
    APPROVAL_RESOLVED = "approval.resolved"
    CHECKPOINT_CREATED = "checkpoint.created"
    USAGE = "usage"
    COST = "cost"
    TURN_COMPLETED = "turn.completed"
    TURN_FAILED = "turn.failed"
    TURN_INTERRUPTED = "turn.interrupted"
    SESSION_CLOSED = "session.closed"


@dataclass(frozen=True)
class HarnessEvent:
    session_id: str
    turn_id: str | None
    sequence: int
    kind: HarnessEventKind
    at: str
    data: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("event sequence must be positive")
        _validate_no_secret_keys(self.data, "event data")

    def to_dict(self) -> JsonObject:
        value = asdict(self)
        value["kind"] = self.kind.value
        return value


HarnessEventSink = Callable[[HarnessEvent], None]


@dataclass(frozen=True)
class HarnessTurnResult:
    session_id: str
    turn_id: str
    status: str
    output_text: str = ""
    structured_output: Any = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    cost: Mapping[str, Any] = field(default_factory=dict)
    checkpoint: Mapping[str, Any] | None = None
    error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in {"completed", "failed", "cancelled", "interrupted"}:
            raise ValueError("unknown turn result status")
        _validate_no_secret_keys(self.usage, "turn result usage")
        _validate_no_secret_keys(self.cost, "turn result cost")
        if self.checkpoint is not None:
            _validate_no_secret_keys(self.checkpoint, "turn result checkpoint")
        _validate_no_secret_keys(self.metadata, "turn result metadata")


@dataclass(frozen=True)
class HarnessApprovalRequest:
    """Provider-neutral reconnect state for an outstanding approval."""

    request_id: str
    kind: str
    command: str | None = None
    cwd: str | None = None
    reason: str | None = None
    created_at: float | None = None
    expires_at: float | None = None

    def to_dict(self) -> JsonObject:
        return asdict(self)


@runtime_checkable
class HarnessAdapter(Protocol):
    """Uniform session boundary implemented by local and remote harnesses."""

    @property
    def capabilities(self) -> HarnessCapabilities: ...

    def create_session(self, spec: HarnessSessionSpec) -> HarnessSession: ...

    def run_turn(
        self,
        session: HarnessSession,
        request: HarnessTurnRequest,
        emit: HarnessEventSink | None = None,
    ) -> HarnessTurnResult: ...

    def interrupt(self, session: HarnessSession, turn_id: str) -> bool: ...

    def close_session(self, session: HarnessSession) -> None: ...


@runtime_checkable
class InteractiveHarnessAdapter(HarnessAdapter, Protocol):
    """Optional controls advertised by persistent interactive harnesses."""

    def steer(
        self,
        session: HarnessSession,
        turn_id: str,
        messages: tuple[Mapping[str, Any], ...],
    ) -> HarnessSteerStatus: ...

    def inject_context(
        self,
        session: HarnessSession,
        turn_id: str,
        messages: tuple[Mapping[str, Any], ...],
    ) -> bool: ...

    def pending_approvals(self, session: HarnessSession) -> tuple[HarnessApprovalRequest, ...]: ...

    def resolve_approval(
        self,
        session: HarnessSession,
        request_id: str,
        decision: HarnessApprovalDecision,
    ) -> bool: ...


def _validate_identifier(value: str, label: str) -> None:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase stable identifier")


def _validate_unique(values: tuple[Any, ...], label: str, *, allow_empty: bool = False) -> None:
    if not values and not allow_empty:
        raise ValueError(f"{label} cannot be empty")
    if len(values) != len(set(values)):
        raise ValueError(f"{label} cannot contain duplicates")


def _validate_no_secret_keys(value: Any, label: str, *, path: str = "") -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
            location = f"{path}.{key}" if path else key
            if normalized in _SECRET_KEYS or normalized.endswith("_token") or normalized.endswith("_secret"):
                raise ValueError(f"{label} cannot contain credential-like key {location}")
            _validate_no_secret_keys(child, label, path=location)
    elif isinstance(value, list | tuple):
        for index, child in enumerate(value):
            _validate_no_secret_keys(child, label, path=f"{path}[{index}]")


def _supports_workspace(supported: tuple[WorkspaceAccess, ...], required: WorkspaceAccess) -> bool:
    levels = {WorkspaceAccess.NONE: 0, WorkspaceAccess.READ_ONLY: 1, WorkspaceAccess.READ_WRITE: 2}
    return any(levels[value] >= levels[required] for value in supported)


def _reasoning_rank(value: ReasoningVisibility) -> int:
    return {
        ReasoningVisibility.NONE: 0,
        ReasoningVisibility.SUMMARY: 1,
        ReasoningVisibility.STREAMED_SUMMARY: 2,
    }[value]


def _isolation_rank(value: TargetIsolation) -> int:
    return {
        TargetIsolation.NONE: 0,
        TargetIsolation.PROCESS: 1,
        TargetIsolation.OS_SANDBOX: 2,
        TargetIsolation.CONTAINER: 3,
        TargetIsolation.VM: 4,
    }[value]
