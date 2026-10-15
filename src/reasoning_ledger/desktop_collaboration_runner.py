"""Opt-in outbound runner that contributes a local Codex installation."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import threading
import time
import uuid
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

from .agents.credentials import ApiKeyStore
from .agents.native import OpenRouterClient
from .agents.openrouter_calls import OpenRouterCallStore
from .codex_client import CodexAppServerClient
from .desktop_cloud import DesktopCloudController


class DesktopCollaborationRunnerError(RuntimeError):
    """The local contributed runner could not be configured or executed."""


class DesktopCollaborationRunner:
    """Poll outbound for bounded leases and execute them with local Codex."""

    def __init__(self, cloud: DesktopCloudController, state_path: Path, workdir: Path, *, autostart: bool = True) -> None:
        if not state_path.is_absolute() or not workdir.is_absolute():
            raise DesktopCollaborationRunnerError("Desktop runner paths must be absolute")
        self._cloud = cloud
        self._state_path = state_path
        self._workdir = workdir
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._active = False
        self._active_job: dict[str, Any] | None = None
        self._last_error = ""
        self._api_keys = ApiKeyStore(workdir / "settings" / "api-keys.json")
        self._api_calls = OpenRouterCallStore(workdir / "usage" / "openrouter-calls")
        self._state = self._read_state()
        self._thread = threading.Thread(target=self._loop, name="reasoning-collaboration-runner", daemon=True)
        if autostart:
            self.start()

    def start(self) -> None:
        with self._lock:
            if self._stop.is_set():
                raise DesktopCollaborationRunnerError("Desktop collaboration runner is closed")
            if self._thread.ident is None:
                self._thread.start()

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "version": 1,
            "runner_id": f"runner_desktop_{uuid.uuid4().hex}",
            "organizations": {},
        }

    def _read_state(self) -> dict[str, Any]:
        self._state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self._state_path.exists():
            state = self._empty_state()
            self._write_state(state)
            return state
        try:
            value = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise DesktopCollaborationRunnerError("Desktop runner state is unreadable") from error
        if (
            not isinstance(value, dict)
            or set(value) != {"version", "runner_id", "organizations"}
            or value.get("version") != 1
            or not isinstance(value.get("runner_id"), str)
            or not str(value["runner_id"]).startswith("runner_desktop_")
            or not isinstance(value.get("organizations"), dict)
        ):
            raise DesktopCollaborationRunnerError("Desktop runner state has an unsupported shape")
        for organization_id, workspaces in value["organizations"].items():
            if not isinstance(organization_id, str) or not isinstance(workspaces, list) or workspaces != sorted(set(workspaces)):
                raise DesktopCollaborationRunnerError("Desktop runner organization policy is invalid")
        return value

    def _write_state(self, state: Mapping[str, Any]) -> None:
        payload = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(payload) > 65_536:
            raise DesktopCollaborationRunnerError("Desktop runner state is oversized")
        temporary = self._state_path.with_name(f".{self._state_path.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self._state_path)
            os.chmod(self._state_path, 0o600)
        finally:
            with suppress(FileNotFoundError):
                temporary.unlink()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread.ident is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise DesktopCollaborationRunnerError("Desktop collaboration runner did not stop")

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "runner_id": self._state["runner_id"],
                "enabled_organizations": sorted(self._state["organizations"]),
                "active": self._active,
                "active_job": dict(self._active_job) if self._active_job is not None else None,
                "api_route_ready": bool(self._api_keys.get("openrouter")),
                "last_error": self._last_error,
            }

    def configure(self, organization_id: str, workspace_ids: list[str], *, enabled: bool) -> dict[str, Any]:
        if (
            not isinstance(organization_id, str)
            or not isinstance(workspace_ids, list)
            or any(not isinstance(item, str) for item in workspace_ids)
        ):
            raise DesktopCollaborationRunnerError("Desktop runner configuration is invalid")
        accepted = sorted(set(workspace_ids))
        with self._lock:
            organizations = self._state["organizations"]
            if enabled:
                organizations[organization_id] = accepted
            else:
                organizations.pop(organization_id, None)
            self._write_state(self._state)
        self._wake.set()
        return self.status()

    @staticmethod
    def _command() -> list[str]:
        override = os.environ.get("REASONING_CODEX_COMMAND")
        if override:
            base = shlex.split(override)
        else:
            sibling = Path(sys.executable).with_name("codex")
            executable = str(sibling) if sibling.is_file() else shutil.which("codex") or "codex"
            base = [executable, "app-server"]
        return [
            *base,
            "-c",
            "tools.web_search=false",
            "-c",
            "sandbox_workspace_write.network_access=false",
        ]

    def _registration(self, workspace_ids: list[str]) -> dict[str, object]:
        api_ready = bool(self._api_keys.get("openrouter"))
        return {
            "display_name": "This Desktop · local Codex",
            "capabilities": {
                "harness_ids": ["codex.local", *(["openrouter.api"] if api_ready else [])],
                "models": ["gpt-5.6-sol", *(["openrouter/auto"] if api_ready else [])],
                "reasoning_efforts": ["high", "low", "medium"],
                "max_concurrency": 1,
            },
            "policy": {
                "workspace_ids": workspace_ids,
                "network_access": "unrestricted" if api_ready else "denied",
                "requires_local_approval": False,
            },
        }

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                organizations = {key: list(value) for key, value in self._state["organizations"].items()}
                runner_id = self._state["runner_id"]
            did_work = False
            for organization_id, workspace_ids in organizations.items():
                if self._stop.is_set():
                    break
                try:
                    self._cloud.collaboration_register_runner(
                        organization_id,
                        runner_id,
                        self._registration(workspace_ids),
                    )
                    lease = self._cloud.collaboration_claim_lease(organization_id, runner_id)
                    if lease is None:
                        continue
                    did_work = True
                    with self._lock:
                        self._active = True
                        self._last_error = ""
                        self._active_job = {
                            "organization_id": organization_id,
                            "workspace_id": lease["workspace_id"],
                            "topic_id": lease["topic_id"],
                            "operation_id": lease["operation_id"],
                            "prompt": lease["request"]["prompt"],
                            "model": lease["request"]["model"],
                            "route": ("metered_openrouter" if lease["request"]["harness_id"] == "openrouter.api" else "contributed_codex"),
                            "progress": "Accepting the shared request",
                        }
                    started = self._cloud.collaboration_start_lease(organization_id, runner_id, lease["lease_id"])
                    if started["state"] != "cancelled":
                        state, content, usage = self._execute(lease)
                        if state != "cancelled":
                            self._cloud.collaboration_finish_lease(
                                organization_id,
                                runner_id,
                                lease["lease_id"],
                                state=state,
                                content=content,
                                usage=usage,
                            )
                except Exception as error:
                    with self._lock:
                        self._last_error = f"{type(error).__name__}: {str(error)[:300]}"
                finally:
                    with self._lock:
                        self._active = False
                        self._active_job = None
            self._wake.wait(0.5 if did_work else 3.0)
            self._wake.clear()

    def _progress(self, value: str) -> None:
        with self._lock:
            if self._active_job is not None:
                self._active_job["progress"] = value[:500]

    def _execute(self, lease: Mapping[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
        request = lease.get("request")
        if not isinstance(request, Mapping):
            return "failed", "Local runner refused an invalid request.", None
        harness_id = request.get("harness_id")
        if harness_id == "openrouter.api":
            if request.get("network_access") != "unrestricted":
                return "failed", "Metered API runner policy refused this request.", None
            return self._execute_openrouter(lease, request)
        if harness_id != "codex.local" or request.get("network_access") != "denied":
            return "failed", "Local runner policy refused this request.", None
        prompt = request.get("prompt")
        model = request.get("model")
        effort = request.get("reasoning_effort")
        timeout = request.get("timeout_seconds")
        maximum = request.get("max_output_tokens")
        if (
            not isinstance(prompt, str)
            or not isinstance(model, str)
            or not isinstance(effort, str)
            or not isinstance(timeout, int)
            or not isinstance(maximum, int)
        ):
            return "failed", "Local runner refused an incomplete request.", None

        cancelled = threading.Event()
        heartbeat_stop = threading.Event()

        def heartbeat() -> None:
            while not heartbeat_stop.wait(5):
                try:
                    with self._lock:
                        progress = str((self._active_job or {}).get("progress") or "Local Codex is answering")
                    receipt = self._cloud.collaboration_heartbeat_lease(
                        lease["organization_id"],
                        self._state["runner_id"],
                        lease["lease_id"],
                        progress=progress,
                    )
                    if receipt["state"] == "cancelled":
                        cancelled.set()
                        return
                except Exception as error:
                    with self._lock:
                        self._last_error = f"Heartbeat: {type(error).__name__}"

        heartbeat_thread = threading.Thread(target=heartbeat, name="reasoning-runner-heartbeat", daemon=True)
        heartbeat_thread.start()

        finished = threading.Event()
        messages: list[str] = []
        failed = [False]
        holder: dict[str, CodexAppServerClient] = {}

        def notification(message: dict[str, Any]) -> None:
            method = message.get("method")
            params = message.get("params") or {}
            item = params.get("item") or {}
            if method == "item/completed" and item.get("type") == "agentMessage":
                text = item.get("text")
                if isinstance(text, str) and text:
                    messages.append(text)
                    self._progress("Local Codex produced an answer")
            elif method in {"turn/started", "turn/plan/updated", "item/started"}:
                self._progress("Local Codex is working visibly in a shared thread")
            if method == "turn/completed":
                failed[0] = (params.get("turn") or {}).get("status") != "completed"
                finished.set()

        def server_request(message: dict[str, Any]) -> None:
            client = holder.get("client")
            if client is None:
                return
            if str(message.get("method") or "").endswith("requestApproval"):
                client.respond(message["id"], {"decision": "decline"})
            elif message.get("method") == "mcpServer/elicitation/request":
                client.respond(message["id"], {"action": "decline", "content": {}, "_meta": None})
            else:
                client.respond_error(message["id"], -32601, "unsupported contributed-runner request")

        log_path = self._state_path.with_name("runner-codex.log")
        client = CodexAppServerClient(
            self._command(),
            on_notification=notification,
            on_server_request=server_request,
            stderr_path=log_path,
            client_name="reasoning-collaboration-runner",
        )
        holder["client"] = client
        try:
            self._progress("Starting a persistent local Codex thread")
            client.start(timeout=min(timeout, 30))
            thread = client.request(
                "thread/start",
                {
                    "model": model,
                    "cwd": str(self._workdir),
                    "approvalPolicy": "never",
                    "approvalsReviewer": "user",
                    "sandbox": "read-only",
                    "developerInstructions": (
                        "You are answering one shared Reasoning workspace request. "
                        "You may use the shell only for bounded, read-only calculations needed by the answer. "
                        "Do not inspect local files, access the network, request credentials, or disclose local state. "
                        "Return only the useful answer for the shared conversation."
                    ),
                    "ephemeral": False,
                    "serviceName": "reasoning-shared-workspace",
                },
                timeout=min(timeout, 30),
            )
            client.request(
                "turn/start",
                {
                    "threadId": thread["thread"]["id"],
                    "input": [{"type": "text", "text": prompt, "text_elements": []}],
                    "model": model,
                    "effort": effort,
                },
                timeout=min(timeout, 30),
            )
            deadline = time.monotonic() + timeout
            while not finished.wait(0.25):
                if cancelled.is_set():
                    return "cancelled", "Cancelled by a workspace member.", None
                if time.monotonic() >= deadline:
                    return "failed", f"Local Codex timed out after {timeout} seconds.", None
            content = "\n\n".join(messages).strip()
            if failed[0] or not content:
                return "failed", "Local Codex did not return a completed answer.", None
            return "succeeded", content[: min(262_144, maximum * 4)], None
        except Exception as error:
            return "failed", f"Local Codex could not complete the request ({type(error).__name__}).", None
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1)
            with suppress(Exception):
                client.close()

    def _execute_openrouter(self, lease: Mapping[str, Any], request: Mapping[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
        api_key = self._api_keys.get("openrouter")
        if not api_key:
            return "failed", "OpenRouter is not configured on the assigned Desktop.", None
        self._progress("Calling the metered OpenRouter API")
        started = time.monotonic()
        started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        model = str(request["model"])
        try:
            response = OpenRouterClient(api_key=api_key).complete(
                {
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Answer this shared Reasoning workspace request. Return only the useful "
                                "answer for the shared conversation; do not request secrets."
                            ),
                        },
                        {"role": "user", "content": request["prompt"]},
                    ],
                    "max_tokens": request["max_output_tokens"],
                    "reasoning": {"effort": request["reasoning_effort"]},
                },
                timeout=float(request["timeout_seconds"]),
            )
            choice = (response.get("choices") or [{}])[0]
            raw_content = (choice.get("message") or {}).get("content")
            if isinstance(raw_content, list):
                content = "".join(str(item.get("text") or "") for item in raw_content if isinstance(item, dict))
            else:
                content = raw_content if isinstance(raw_content, str) else ""
            if not content.strip():
                raise RuntimeError("provider returned no answer")
            raw_usage = response.get("usage") if isinstance(response.get("usage"), Mapping) else {}
            usage = {
                "provider": "openrouter",
                "prompt_tokens": raw_usage.get("prompt_tokens") if isinstance(raw_usage.get("prompt_tokens"), int) else None,
                "completion_tokens": raw_usage.get("completion_tokens") if isinstance(raw_usage.get("completion_tokens"), int) else None,
                "total_tokens": raw_usage.get("total_tokens") if isinstance(raw_usage.get("total_tokens"), int) else None,
                "cost_usd": raw_usage.get("cost") if type(raw_usage.get("cost")) in {int, float} else None,
            }
            self._api_calls.record(
                f"collaboration_{lease['workspace_id']}",
                {
                    "status": "completed",
                    "started_at": started_at,
                    "duration_ms": (time.monotonic() - started) * 1000,
                    "requested_model": model,
                    "resolved_model": response.get("model"),
                    "response_id": response.get("id"),
                    "finish_reason": choice.get("finish_reason"),
                    "usage": dict(raw_usage),
                },
            )
            self._progress("Metered API answer received")
            return "succeeded", content[: min(262_144, int(request["max_output_tokens"]) * 4)], usage
        except Exception as error:
            self._api_calls.record(
                f"collaboration_{lease['workspace_id']}",
                {
                    "status": "failed",
                    "started_at": started_at,
                    "duration_ms": (time.monotonic() - started) * 1000,
                    "requested_model": model,
                    "error": type(error).__name__,
                    "usage": {},
                },
            )
            return "failed", f"Metered API request failed ({type(error).__name__}).", None


__all__ = ["DesktopCollaborationRunner", "DesktopCollaborationRunnerError"]
