"""Built-in harness declarations kept separate from adapter implementations."""

from __future__ import annotations

from ..agents.contracts import PromptFidelity
from .contracts import (
    HarnessCapabilities,
    HarnessTransport,
    ReasoningVisibility,
    SessionMode,
    WorkspaceAccess,
)


def codex_app_server_capabilities() -> HarnessCapabilities:
    """Capabilities exercised by the current persistent Codex chat path."""

    return HarnessCapabilities(
        harness_id="codex",
        display_name="Codex app-server",
        adapter_version="1",
        transport=HarnessTransport.LOCAL_PROCESS,
        prompt_fidelity=PromptFidelity.CONTROLLED_INPUT,
        session_modes=(SessionMode.FRESH, SessionMode.RESUME),
        workspace_access=(WorkspaceAccess.NONE, WorkspaceAccess.READ_ONLY, WorkspaceAccess.READ_WRITE),
        reasoning_efforts=("none", "low", "medium", "high", "xhigh", "max", "ultra"),
        reasoning_visibility=ReasoningVisibility.STREAMED_SUMMARY,
        supports_tools=True,
        supports_skills=True,
        supports_structured_output=True,
        supports_mcp=True,
        supports_approvals=True,
        supports_interrupt=True,
        supports_steering=True,
        supports_subagents=True,
        supports_context_compaction=True,
        reports_usage=True,
        reports_cost=False,
    )


def reasoning_native_capabilities() -> HarnessCapabilities:
    """Capabilities exercised by the current direct-model execution loop."""

    return HarnessCapabilities(
        harness_id="reasoning-native",
        display_name="Reasoning Native",
        adapter_version="1",
        transport=HarnessTransport.IN_PROCESS,
        prompt_fidelity=PromptFidelity.WIRE_EXACT,
        session_modes=(SessionMode.FRESH, SessionMode.RESUME),
        workspace_access=(WorkspaceAccess.NONE,),
        reasoning_efforts=("none", "low", "medium", "high", "xhigh", "max"),
        reasoning_visibility=ReasoningVisibility.STREAMED_SUMMARY,
        supports_tools=True,
        supports_skills=True,
        supports_structured_output=True,
        supports_mcp=False,
        supports_interrupt=True,
        supports_checkpoints=True,
        supports_context_compaction=True,
        reports_usage=True,
        reports_cost=True,
    )
