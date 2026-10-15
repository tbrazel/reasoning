"""MCP tools for controlling a running Reasoning server without its UI."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .control import ReasoningControlClient

INSTRUCTIONS = """Operate Reasoning as a durable research workbench.

Start bounded turns or workflows, retain their operation/run ids, and use the
bounded wait tools rather than holding a browser or SSH connection open. Read
the ledger and artifacts after each stage. A model-generated candidate is not
a verified result: require explicit hypotheses, a complete witness, attached
evidence, an independent check, and a clean ledger support closure before
calling it a counterexample. Treat failed, interrupted, timed-out, and
approval-needed operations as distinct terminal/recovery cases.
"""


def build_server(base_url: str):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as error:  # pragma: no cover - broken installation
        raise RuntimeError("MCP support requires the core 'mcp' dependency") from error

    server = FastMCP("reasoning-control", instructions=INSTRUCTIONS)

    def call(method: str, **kwargs: Any) -> dict[str, Any]:
        with ReasoningControlClient(base_url) as client:
            handler: Callable[..., dict[str, Any]] = getattr(client, method)
            return handler(**kwargs)

    @server.tool(description="Check server health and configured research executors.")
    def reasoning_status() -> dict[str, Any]:
        return {
            "health": call("health"),
            "connections": call("connections")["connections"],
        }

    @server.tool(description="List Reasoning projects, including their chats and workflows.")
    def reasoning_project_list(include_archived: bool = False) -> dict[str, Any]:
        return call("project_list", include_archived=include_archived)

    @server.tool(description="Create a durable research project.")
    def reasoning_project_create(name: str) -> dict[str, Any]:
        return call("project_create", name=name)

    @server.tool(description="Create a chat within a Reasoning project.")
    def reasoning_chat_create(project_id: str, title: str | None = None) -> dict[str, Any]:
        return call("chat_create", project_id=project_id, title=title)

    @server.tool(
        description=(
            "Start one bounded chat turn and return its durable operation id. "
            "Set model_provider explicitly for Codex routes."
        )
    )
    def reasoning_turn_start(
        project_id: str,
        chat_id: str,
        message: str,
        executor_id: str = "codex",
        model: str | None = None,
        model_provider: str | None = None,
        effort: str | None = None,
        required_tool_calls: dict[str, int] | None = None,
        timeout_seconds: int | None = None,
        max_output_tokens: int | None = None,
        max_tool_calls: int | None = None,
    ) -> dict[str, Any]:
        return call(
            "turn_start",
            project_id=project_id,
            chat_id=chat_id,
            message=message,
            executor_id=executor_id,
            model=model,
            model_provider=model_provider,
            effort=effort,
            required_tool_calls=required_tool_calls,
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
            max_tool_calls=max_tool_calls,
        )

    @server.tool(
        description=(
            "Wait a bounded time for one turn operation and return its terminal "
            "state, checkpoints, and transcript entries."
        )
    )
    def reasoning_turn_wait(
        project_id: str,
        chat_id: str,
        operation_id: str,
        timeout_seconds: float = 900.0,
        poll_seconds: float = 1.0,
    ) -> dict[str, Any]:
        return call(
            "turn_wait",
            project_id=project_id,
            chat_id=chat_id,
            operation_id=operation_id,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
        )

    @server.tool(description="Read a chat transcript or the entries belonging to one operation.")
    def reasoning_turn_read(
        project_id: str,
        chat_id: str,
        operation_id: str | None = None,
        tail: int = 200,
    ) -> dict[str, Any]:
        return call(
            "turn_read",
            project_id=project_id,
            chat_id=chat_id,
            operation_id=operation_id,
            tail=tail,
        )

    @server.tool(description="Steer the currently running chat turn; never starts a new turn.")
    def reasoning_turn_steer(project_id: str, chat_id: str, message: str) -> dict[str, Any]:
        return call(
            "turn_steer",
            project_id=project_id,
            chat_id=chat_id,
            message=message,
        )

    @server.tool(description="Interrupt the currently running chat turn.")
    def reasoning_turn_interrupt(project_id: str, chat_id: str) -> dict[str, Any]:
        return call("turn_interrupt", project_id=project_id, chat_id=chat_id)

    @server.tool(description="Start a project workflow and return its durable run id.")
    def reasoning_workflow_start(
        project_id: str,
        workflow_id: str,
        iterations: int = 1,
        variables: dict[str, Any] | None = None,
        initial_prompt: str = "",
    ) -> dict[str, Any]:
        return call(
            "workflow_start",
            project_id=project_id,
            workflow_id=workflow_id,
            iterations=iterations,
            variables=variables,
            initial_prompt=initial_prompt,
        )

    @server.tool(description="Wait a bounded time for one workflow run to become terminal.")
    def reasoning_workflow_wait(
        project_id: str,
        run_id: str,
        timeout_seconds: float = 1800.0,
        poll_seconds: float = 2.0,
    ) -> dict[str, Any]:
        return call(
            "workflow_wait",
            project_id=project_id,
            run_id=run_id,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
        )

    @server.tool(description="Read the derived ledger status for a project.")
    def reasoning_ledger_status(project_id: str) -> dict[str, Any]:
        return call("ledger_status", project_id=project_id)

    @server.tool(description="List project artifacts at one directory path.")
    def reasoning_artifact_list(project_id: str, path: str = "") -> dict[str, Any]:
        return call("artifact_list", project_id=project_id, path=path)

    @server.tool(description="Read one project artifact; binary content is returned as base64.")
    def reasoning_artifact_read(project_id: str, path: str) -> dict[str, Any]:
        return call("artifact_read", project_id=project_id, path=path)

    @server.tool(description="Read the latest token usage and provider route for a chat.")
    def reasoning_usage(project_id: str, chat_id: str) -> dict[str, Any]:
        return call("usage", project_id=project_id, chat_id=chat_id)

    return server


def serve(base_url: str) -> None:
    build_server(base_url).run()
