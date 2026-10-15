"""Typed HTTP control client for unattended Reasoning operation."""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_CONTROL_URL = "http://127.0.0.1:9119"
TERMINAL_TURN_STATUSES = frozenset({"completed", "failed", "interrupted", "cancelled"})
TERMINAL_WORKFLOW_STATUSES = frozenset({"completed", "failed", "cancelled"})


@dataclass
class ControlError(RuntimeError):
    code: str
    detail: str
    http_status: int | None = None

    def __str__(self) -> str:
        return self.detail

    def payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "http_status": self.http_status,
        }


def _error_code(status: int, detail: str) -> str:
    lowered = detail.casefold()
    if status == 404:
        return "not_found"
    if status == 409 and any(word in lowered for word in ("busy", "running", "wait", "another")):
        return "busy"
    if status == 409:
        return "conflict"
    if status == 422:
        return "invalid_request"
    if status in (401, 403):
        return "unauthorized"
    if status >= 500:
        return "server_error"
    return "http_error"


class ReasoningControlClient:
    """Small synchronous client shared by the JSON CLI and control MCP."""

    def __init__(
        self,
        base_url: str = DEFAULT_CONTROL_URL,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ReasoningControlClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        raw: bool = False,
    ) -> Any:
        try:
            response = self._client.request(method, path, params=params, json=json)
        except httpx.TimeoutException as error:
            raise ControlError("timed_out", f"Reasoning request timed out: {error}") from error
        except httpx.HTTPError as error:
            raise ControlError("unavailable", f"Reasoning is unavailable: {error}") from error
        if not response.is_success:
            try:
                body = response.json()
                detail = str(body.get("detail") or body)
            except (ValueError, TypeError):
                detail = response.text.strip() or response.reason_phrase
            raise ControlError(
                _error_code(response.status_code, detail),
                detail,
                response.status_code,
            )
        if raw:
            return response
        try:
            return response.json()
        except ValueError as error:
            raise ControlError("invalid_response", "Reasoning returned non-JSON data") from error

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/api/health")

    def connections(self) -> dict[str, Any]:
        return self._request("GET", "/api/agent/connections")

    def project_list(self, *, include_archived: bool = False) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/projects",
            params={"include_archived": str(include_archived).lower()},
        )

    def project_create(self, *, name: str) -> dict[str, Any]:
        return self._request("POST", "/api/projects", json={"name": name})

    def chat_create(
        self,
        *,
        project_id: str,
        title: str | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/projects/{project_id}/chats",
            json={"title": title, "workspace_id": workspace_id},
        )

    def turn_start(
        self,
        *,
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
        payload = {
            key: value
            for key, value in {
                "message": message,
                "executor_id": executor_id,
                "model": model,
                "model_provider": model_provider,
                "effort": effort,
                "required_tool_calls": required_tool_calls,
                "timeout_seconds": timeout_seconds,
                "max_output_tokens": max_output_tokens,
                "max_tool_calls": max_tool_calls,
            }.items()
            if value is not None
        }
        result = self._request(
            "POST",
            f"/api/projects/{project_id}/chats/{chat_id}/messages",
            json=payload,
        )
        if result.get("steered"):
            raise ControlError(
                "busy",
                "the chat already had a running turn, so the message became a steer",
                409,
            )
        operation_id = result.get("operation_id")
        if not isinstance(operation_id, str):
            raise ControlError("invalid_response", "Reasoning did not return an operation id")
        return result

    def turn_steer(self, *, project_id: str, chat_id: str, message: str) -> dict[str, Any]:
        result = self._request(
            "POST",
            f"/api/projects/{project_id}/chats/{chat_id}/messages",
            json={"message": message},
        )
        if not result.get("steered"):
            raise ControlError("not_running", "the chat had no running turn to steer")
        return result

    def turn_interrupt(self, *, project_id: str, chat_id: str) -> dict[str, Any]:
        result = self._request(
            "POST",
            f"/api/projects/{project_id}/chats/{chat_id}/interrupt",
        )
        if not result.get("interrupted"):
            raise ControlError("not_running", "the chat had no running turn to interrupt")
        return result

    def turn_operation(
        self,
        *,
        project_id: str,
        chat_id: str,
        operation_id: str,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/projects/{project_id}/chats/{chat_id}/operations/{operation_id}",
        )

    def turn_read(
        self,
        *,
        project_id: str,
        chat_id: str,
        operation_id: str | None = None,
        tail: int = 200,
    ) -> dict[str, Any]:
        history = self._request(
            "GET",
            f"/api/projects/{project_id}/chats/{chat_id}/history",
            params={"tail": tail},
        )
        if operation_id is not None:
            history["messages"] = [
                entry
                for entry in history.get("messages", [])
                if entry.get("operation_id") == operation_id
            ]
            history["operation"] = self.turn_operation(
                project_id=project_id,
                chat_id=chat_id,
                operation_id=operation_id,
            )
        return history

    def turn_wait(
        self,
        *,
        project_id: str,
        chat_id: str,
        operation_id: str,
        timeout_seconds: float = 900.0,
        poll_seconds: float = 1.0,
    ) -> dict[str, Any]:
        if timeout_seconds <= 0 or poll_seconds <= 0:
            raise ControlError("invalid_request", "wait and poll intervals must be positive")
        deadline = time.monotonic() + timeout_seconds
        checkpoints: list[dict[str, Any]] = []
        previous: str | None = None
        while True:
            operation = self.turn_operation(
                project_id=project_id,
                chat_id=chat_id,
                operation_id=operation_id,
            )
            status = str(operation.get("status") or "unknown")
            if status != previous:
                checkpoints.append({"at": time.time(), "status": status})
                previous = status
            if status in TERMINAL_TURN_STATUSES:
                return {
                    "operation": operation,
                    "checkpoints": checkpoints,
                    "history": self.turn_read(
                        project_id=project_id,
                        chat_id=chat_id,
                        operation_id=operation_id,
                        tail=500,
                    ),
                }
            history = self.turn_read(project_id=project_id, chat_id=chat_id, tail=1)
            if history.get("pending_approvals"):
                raise ControlError(
                    "approval_needed",
                    "the turn is waiting for an interactive approval",
                    409,
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ControlError(
                    "timed_out",
                    f"operation {operation_id} did not finish within {timeout_seconds:g} seconds",
                )
            time.sleep(min(poll_seconds, remaining))

    def workflow_list(
        self,
        *,
        project_id: str,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/projects/{project_id}/workflows",
            params={"include_archived": str(include_archived).lower()},
        )

    def prompt_list(self, *, project_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/projects/{project_id}/agent/prompts",
        )

    def prompt_definition(
        self,
        *,
        project_id: str,
        prompt_id: str,
        revision_id: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/projects/{project_id}/agent/prompts/{prompt_id}",
            params=({"revision_id": revision_id} if revision_id is not None else None),
        )

    def prompt_create(
        self,
        *,
        project_id: str,
        name: str,
        kind: str,
        template: str,
        description: str = "",
        variable_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/projects/{project_id}/agent/prompts",
            json={
                "name": name,
                "kind": kind,
                "template": template,
                "description": description,
                "variable_schema": variable_schema or {},
            },
        )

    def prompt_revise(
        self,
        *,
        project_id: str,
        prompt_id: str,
        template: str,
        parent_revision_id: str,
        name: str | None = None,
        kind: str | None = None,
        description: str | None = None,
        variable_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/projects/{project_id}/agent/prompts/{prompt_id}/revisions",
            json={
                "template": template,
                "parent_revision_id": parent_revision_id,
                "name": name,
                "kind": kind,
                "description": description,
                "variable_schema": variable_schema,
            },
        )

    def dataset_list(self, *, project_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/projects/{project_id}/datasets",
        )

    def dataset_definition(
        self,
        *,
        project_id: str,
        dataset_id: str,
        revision_id: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/projects/{project_id}/datasets/{dataset_id}",
            params=({"revision_id": revision_id} if revision_id is not None else None),
        )

    def dataset_create(
        self,
        *,
        project_id: str,
        name: str,
        description: str = "",
        cases: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/projects/{project_id}/datasets",
            json={"name": name, "description": description, "cases": cases},
        )

    def model_catalog(
        self,
        *,
        project_id: str,
        scope: str = "account",
        refresh: bool = False,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/model-prices",
            params={"scope": scope, "refresh": str(refresh).lower()},
        )

    def workflow_definition(
        self,
        *,
        project_id: str,
        workflow_id: str,
        revision_id: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/projects/{project_id}/workflows/{workflow_id}",
            params=({"revision_id": revision_id} if revision_id is not None else None),
        )

    def workflow_create(
        self,
        *,
        project_id: str,
        name: str,
        template_id: str | None = None,
        graph: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name, "template_id": template_id}
        if graph is not None:
            payload["graph"] = graph
        return self._request(
            "POST",
            f"/api/projects/{project_id}/workflows",
            json=payload,
        )

    def workflow_update(
        self,
        *,
        project_id: str,
        workflow_id: str,
        graph: dict[str, Any],
        expected_revision_id: str,
    ) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/api/projects/{project_id}/workflows/{workflow_id}",
            json={
                "graph": graph,
                "expected_revision_id": expected_revision_id,
            },
        )

    def workflow_preflight(
        self,
        *,
        project_id: str,
        workflow_id: str,
        refresh: bool = False,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/projects/{project_id}/workflows/{workflow_id}/preflight",
            params={"refresh": str(refresh).lower()},
        )

    def workflow_start(
        self,
        *,
        project_id: str,
        workflow_id: str,
        expected_revision_id: str | None = None,
        iterations: int | None = None,
        variables: dict[str, Any] | None = None,
        initial_prompt: str = "",
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/projects/{project_id}/workflows/{workflow_id}/runs",
            json={
                "iterations": iterations,
                "variables": variables or {},
                "initial_prompt": initial_prompt,
                "expected_revision_id": expected_revision_id,
            },
        )

    def workflow_read(self, *, project_id: str, run_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/projects/{project_id}/workflow-runs/{run_id}",
        )

    def workflow_wait(
        self,
        *,
        project_id: str,
        run_id: str,
        timeout_seconds: float = 1800.0,
        poll_seconds: float = 2.0,
    ) -> dict[str, Any]:
        if timeout_seconds <= 0 or poll_seconds <= 0:
            raise ControlError("invalid_request", "wait and poll intervals must be positive")
        deadline = time.monotonic() + timeout_seconds
        checkpoints: list[dict[str, Any]] = []
        previous: str | None = None
        while True:
            run = self.workflow_read(project_id=project_id, run_id=run_id)
            status = str(run.get("status") or "unknown")
            if status != previous:
                checkpoints.append({"at": time.time(), "status": status})
                previous = status
            if status in TERMINAL_WORKFLOW_STATUSES:
                return {"run": run, "checkpoints": checkpoints}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ControlError(
                    "timed_out",
                    f"workflow run {run_id} did not finish within {timeout_seconds:g} seconds",
                )
            time.sleep(min(poll_seconds, remaining))

    def workflow_cancel(self, *, project_id: str, run_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/projects/{project_id}/workflow-runs/{run_id}/cancel",
        )

    def ledger_status(self, *, project_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/projects/{project_id}/ledger/status")

    def artifact_list(self, *, project_id: str, path: str = "") -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/projects/{project_id}/artifacts",
            params={"path": path},
        )

    def artifact_read(self, *, project_id: str, path: str) -> dict[str, Any]:
        response = self._request(
            "GET",
            f"/api/projects/{project_id}/artifacts/{path.lstrip('/')}",
            raw=True,
        )
        content = response.content
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return {
                "path": path,
                "encoding": "base64",
                "content": base64.b64encode(content).decode("ascii"),
                "bytes": len(content),
                "media_type": response.headers.get("content-type"),
            }
        return {
            "path": path,
            "encoding": "utf-8",
            "content": text,
            "bytes": len(content),
            "media_type": response.headers.get("content-type"),
        }

    def usage(self, *, project_id: str, chat_id: str) -> dict[str, Any]:
        history = self.turn_read(project_id=project_id, chat_id=chat_id, tail=1)
        return {
            "project_id": project_id,
            "chat_id": chat_id,
            "usage": history.get("usage"),
            "model_provider": history.get("model_provider"),
            "active_model_provider": history.get("active_model_provider"),
        }

    def dispatch(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        actions = {
            "health": self.health,
            "connections": self.connections,
            "project-list": self.project_list,
            "project-create": self.project_create,
            "chat-create": self.chat_create,
            "turn-start": self.turn_start,
            "turn-wait": self.turn_wait,
            "turn-read": self.turn_read,
            "turn-steer": self.turn_steer,
            "turn-interrupt": self.turn_interrupt,
            "workflow-start": self.workflow_start,
            "workflow-read": self.workflow_read,
            "workflow-wait": self.workflow_wait,
            "workflow-cancel": self.workflow_cancel,
            "ledger-status": self.ledger_status,
            "artifact-list": self.artifact_list,
            "artifact-read": self.artifact_read,
            "usage": self.usage,
        }
        try:
            handler = actions[action]
        except KeyError as error:
            raise ControlError("unknown_action", f"unknown control action: {action}") from error
        try:
            return handler(**payload)
        except TypeError as error:
            raise ControlError("invalid_request", str(error)) from error
