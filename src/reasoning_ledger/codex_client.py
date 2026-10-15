"""Minimal JSON-RPC client for a `codex app-server` subprocess.

Transport is newline-delimited JSON-RPC 2.0 over stdio. This module is
deliberately dumb: framing, request/response correlation, notification
and server-request dispatch, and process lifecycle. Codex session semantics
live in ``harnesses.codex.CodexAppServerHarnessAdapter``.

Callbacks run on the reader thread; they must not block. Server-initiated
requests (approvals, elicitations) are handed to ``on_server_request``
and MUST eventually be answered via :meth:`respond` or
:meth:`respond_error`, or the active turn hangs.
"""

from __future__ import annotations

import json
import subprocess
import threading
from contextlib import suppress
from pathlib import Path


class CodexClientError(RuntimeError):
    pass


_MISSING_RUNTIME_LIBRARY_ERROR = (
    "Codex could not start because a required local runtime library is missing. "
    "Repair or reinstall Codex and its Node.js/Homebrew runtime, then fully quit "
    "and reopen Reasoning."
)


def local_codex_runtime_error(
    stderr_path: Path | None,
    *,
    start_offset: int = 0,
) -> str | None:
    """Return a safe, actionable diagnosis for a broken local Codex runtime.

    Codex stderr can contain local paths, commands, prompts, or credentials.  Match
    only known loader failures and return fixed copy instead of exposing the log to
    the UI.  The original stderr remains available in Reasoning's local log file.
    """

    if stderr_path is None:
        return None
    try:
        with open(stderr_path, "rb") as handle:
            handle.seek(0, 2)
            end_offset = handle.tell()
            new_output_offset = min(max(0, start_offset), end_offset)
            handle.seek(max(new_output_offset, end_offset - 32 * 1024))
            stderr = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    lowered = stderr.lower()
    if (
        ("dyld[" in lowered and "library not loaded:" in lowered)
        or (
            "error while loading shared libraries" in lowered
            and "cannot open shared object file" in lowered
        )
    ):
        return _MISSING_RUNTIME_LIBRARY_ERROR
    return None


class CodexAppServerClient:
    def __init__(
        self,
        command: list[str],
        *,
        on_notification,
        on_server_request,
        stderr_path: Path | None = None,
        client_name: str = "reasoning-workbench",
        client_version: str = "0.1",
        env: dict[str, str] | None = None,
    ):
        self._command = command
        self._env = env
        self._on_notification = on_notification
        self._on_server_request = on_server_request
        self._stderr_path = stderr_path
        self._client_name = client_name
        self._client_version = client_version
        self._process: subprocess.Popen | None = None
        self._pending: dict[int, dict] = {}
        self._next_id = 0
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self, timeout: float = 30.0) -> dict:
        """Spawn the subprocess and run the initialize handshake."""
        try:
            stderr_offset = self._stderr_path.stat().st_size if self._stderr_path else 0
        except OSError:
            stderr_offset = 0
        stderr_handle = (
            open(self._stderr_path, "a", encoding="utf-8")  # noqa: SIM115
            if self._stderr_path
            else None
        )
        try:
            self._process = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr_handle or subprocess.DEVNULL,
                text=True,
                env=self._env,
            )
        finally:
            if stderr_handle is not None:
                stderr_handle.close()
        threading.Thread(target=self._reader, daemon=True).start()
        try:
            result = self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": self._client_name,
                        "title": None,
                        "version": self._client_version,
                    },
                    "capabilities": {
                        "experimentalApi": True,
                        "requestAttestation": False,
                    },
                },
                timeout=timeout,
            )
        except CodexClientError as error:
            if diagnosis := local_codex_runtime_error(
                self._stderr_path,
                start_offset=stderr_offset,
            ):
                raise CodexClientError(diagnosis) from error
            raise
        self._send({"method": "initialized"})
        return result

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        # Unblock any waiters.
        with self._state_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for waiter in pending:
            waiter["msg"] = {"error": {"message": "client closed"}}
            waiter["event"].set()

    # ------------------------------------------------------------------ io

    def _send(self, obj: dict) -> None:
        process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            raise CodexClientError("codex app-server is not running")
        with self._write_lock:
            process.stdin.write(json.dumps(obj) + "\n")
            process.stdin.flush()

    def _reader(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                with self._state_lock:
                    waiter = self._pending.pop(msg["id"], None)
                if waiter is not None:
                    waiter["msg"] = msg
                    waiter["event"].set()
            elif "id" in msg and "method" in msg:
                try:
                    self._on_server_request(msg)
                except Exception:  # noqa: BLE001 - never kill the reader
                    with suppress(Exception):
                        self.respond_error(msg["id"], -32603, "handler failed")
            elif "method" in msg:
                with suppress(Exception):  # never kill the reader
                    self._on_notification(msg)
        # Process exited: fail all pending requests.
        with self._state_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for waiter in pending:
            waiter["msg"] = {"error": {"message": "codex app-server exited"}}
            waiter["event"].set()

    # ----------------------------------------------------------- requests

    def request(self, method: str, params: dict | None = None, timeout: float = 120.0):
        with self._state_lock:
            self._next_id += 1
            rid = self._next_id
            waiter = {"event": threading.Event(), "msg": None}
            self._pending[rid] = waiter
        self._send({"id": rid, "method": method, "params": params or {}})
        if not waiter["event"].wait(timeout):
            with self._state_lock:
                self._pending.pop(rid, None)
            raise CodexClientError(f"{method} timed out after {timeout}s")
        msg = waiter["msg"]
        if "error" in msg:
            error = msg["error"]
            raise CodexClientError(
                f"{method} failed: {error.get('message', error)}"
            )
        return msg.get("result")

    def respond(self, request_id, result: dict) -> None:
        self._send({"id": request_id, "result": result})

    def respond_error(self, request_id, code: int, message: str) -> None:
        self._send({"id": request_id, "error": {"code": code, "message": message}})
