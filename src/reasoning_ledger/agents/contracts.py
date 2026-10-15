"""Stable contracts between workflows and replaceable agent executors.

The workflow engine owns durable state and hands a fully prepared request to
an executor.  Executors never need to know how prompts, skills, or context
were stored; their prepared inputs carry the exact rendered values together
with the immutable identities needed for reproducibility.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

JsonObject = dict[str, Any]
EventSink = Callable[["AgentEvent"], None]
_MODEL_PROVIDER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class AgentEventKind(StrEnum):
    RUN_STARTED = "run.started"
    MESSAGE_DELTA = "message.delta"
    MESSAGE_COMPLETED = "message.completed"
    REASONING_DELTA = "reasoning.delta"
    TOOL_REQUESTED = "tool.requested"
    TOOL_COMPLETED = "tool.completed"
    ARTIFACT_CREATED = "artifact.created"
    CHECKPOINT_CREATED = "checkpoint.created"
    USAGE = "usage"
    RUN_COMPLETED = "run.completed"
    RUN_CANCELLED = "run.cancelled"
    RUN_FAILED = "run.failed"


class PromptFidelity(StrEnum):
    """How completely Reasoning controls the executor's model-facing prompt."""

    WIRE_EXACT = "wire_exact"
    CONTROLLED_INPUT = "controlled_input"
    OPAQUE = "opaque"


@dataclass(frozen=True)
class ExecutorCapabilities:
    executor_id: str
    display_name: str
    prompt_fidelity: PromptFidelity
    supports_tools: bool = False
    supports_skills: bool = False
    supports_sessions: bool = False
    supports_streaming: bool = True
    supports_structured_output: bool = False
    supports_mcp: bool = False
    model_patterns: tuple[str, ...] = ("*",)
    model_options: tuple[str, ...] = ()


@dataclass(frozen=True)
class PromptBinding:
    prompt_id: str
    revision_id: str
    variables: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillBinding:
    skill_id: str
    revision_id: str


@dataclass(frozen=True)
class ToolBinding:
    tool_id: str


@dataclass(frozen=True)
class ContextPolicy:
    """Selects prior material without baking it into a prompt revision."""

    mode: str = "none"
    max_tokens: int | None = None
    include_agent_outputs: tuple[str, ...] = ()
    include_artifact_kinds: tuple[str, ...] = ()
    summary_prompt: PromptBinding | None = None

    def __post_init__(self) -> None:
        if self.max_tokens is not None and self.max_tokens <= 0:
            raise ValueError("context max_tokens must be positive")


@dataclass(frozen=True)
class RunBudget:
    max_cost_usd: float | None = None
    max_output_tokens: int | None = None
    max_tool_calls: int | None = 24
    timeout_seconds: float = 900

    def __post_init__(self) -> None:
        if self.max_cost_usd is not None and self.max_cost_usd <= 0:
            raise ValueError("max_cost_usd must be positive")
        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if self.max_tool_calls is not None and self.max_tool_calls < 0:
            raise ValueError("max_tool_calls cannot be negative")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True)
class SandboxPolicy:
    mode: str = "read_only"
    working_directory: str | None = None
    writable_paths: tuple[str, ...] = ()
    network: bool = False


@dataclass(frozen=True)
class SessionPolicy:
    mode: str = "fresh"
    session_id: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"fresh", "resume", "fork"}:
            raise ValueError("session mode must be fresh, resume, or fork")
        if self.mode in {"resume", "fork"} and not self.session_id:
            raise ValueError(f"{self.mode} session mode needs session_id")


@dataclass(frozen=True)
class CompletionArtifactRequirement:
    """One rendered workflow artifact that must be fresh when a run finishes."""

    path: str
    previous_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.path.strip():
            raise ValueError("completion artifact path must be non-empty")


@dataclass(frozen=True)
class AgentRunSpec:
    """User-authored, durable description attached to a workflow node."""

    executor_id: str
    model: str
    system_prompt: PromptBinding
    user_prompt: PromptBinding
    model_provider: str | None = None
    variables: Mapping[str, Any] = field(default_factory=dict)
    skills: tuple[SkillBinding, ...] = ()
    tools: tuple[ToolBinding, ...] = ()
    mcp_connections: tuple[str, ...] = ()
    context_policy: ContextPolicy = field(default_factory=ContextPolicy)
    output_schema: JsonObject | None = None
    budget: RunBudget = field(default_factory=RunBudget)
    sandbox: SandboxPolicy = field(default_factory=SandboxPolicy)
    session: SessionPolicy = field(default_factory=SessionPolicy)
    effort: str | None = None
    completion_markers: tuple[str, ...] = ()
    completion_artifacts: tuple[CompletionArtifactRequirement, ...] = ()
    completion_tool_calls: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.executor_id.strip():
            raise ValueError("executor_id is required")
        if not self.model.strip():
            raise ValueError("model is required")
        if self.model_provider is not None:
            provider = self.model_provider.strip()
            if not _MODEL_PROVIDER.fullmatch(provider):
                raise ValueError("model_provider is invalid")
            object.__setattr__(self, "model_provider", provider)
        if any(not marker.strip() for marker in self.completion_markers):
            raise ValueError("completion markers must be non-empty")
        for tool_id, minimum in self.completion_tool_calls.items():
            if not isinstance(tool_id, str) or not tool_id.strip():
                raise ValueError("completion tool ids must be non-empty strings")
            if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 1:
                raise ValueError("completion tool call minimums must be positive integers")


@dataclass(frozen=True)
class PreparedTool:
    name: str
    description: str
    input_schema: JsonObject


@dataclass(frozen=True)
class PreparedSkill:
    skill_id: str
    revision_id: str
    name: str
    instructions: str
    directory: str | None = None
    description: str = ""
    package_directory: str | None = None


@dataclass(frozen=True)
class PreparedAgentRun:
    """Resolved, immutable inputs passed across the executor boundary."""

    run_id: str
    spec: AgentRunSpec
    system_prompt: str
    user_prompt: str
    prompt_hashes: Mapping[str, str]
    context_messages: tuple[JsonObject, ...] = ()
    tools: tuple[PreparedTool, ...] = ()
    skills: tuple[PreparedSkill, ...] = ()
    mcp_servers: Mapping[str, JsonObject] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentEvent:
    run_id: str
    sequence: int
    kind: AgentEventKind
    at: str
    data: JsonObject = field(default_factory=dict)

    def to_dict(self) -> JsonObject:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        return payload


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
    kind: str
    path: str
    media_type: str | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class AgentRunResult:
    run_id: str
    status: str
    output_text: str = ""
    structured_output: Any = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    artifacts: tuple[ArtifactRef, ...] = ()
    session_id: str | None = None
    error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in {"completed", "failed", "cancelled"}:
            raise ValueError("unknown run result status")


@runtime_checkable
class AgentExecutor(Protocol):
    """A replaceable harness capable of executing a prepared agent run."""

    @property
    def capabilities(self) -> ExecutorCapabilities: ...

    def execute(
        self,
        request: PreparedAgentRun,
        emit: EventSink | None = None,
    ) -> AgentRunResult: ...


@runtime_checkable
class ToolInvoker(Protocol):
    def __call__(self, name: str, arguments: Mapping[str, Any]) -> Any: ...


def dataclass_dict(value: Any) -> JsonObject:
    """Serialize contract dataclasses without leaking Enum instances."""

    def clean(item: Any) -> Any:
        if isinstance(item, StrEnum):
            return item.value
        if isinstance(item, Path):
            return str(item)
        if isinstance(item, Mapping):
            return {str(key): clean(child) for key, child in item.items()}
        if isinstance(item, (tuple, list)):
            return [clean(child) for child in item]
        return item

    return clean(asdict(value))


EventIterator = Iterator[AgentEvent]
