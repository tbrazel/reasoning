"""Credential-separated project agents with durable local RPC replay journals."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ..agents.contracts import AgentRunSpec, PreparedAgentRun, PreparedTool, PromptBinding, RunBudget
from ..agents.native import ReasoningNativeExecutor
from ..deployment.host import InstallError, atomic_write
from ..deployment.private import locked_directory, protected_parent, read_private, write_document
from .research_context import encoded
from .security import digest

MAX_JOURNAL = 96 * 1024 * 1024


class WorkerError(Exception):
    """Static diagnostic: never expose a credential or untrusted HTTP body."""


def origin(value):
    if not isinstance(value, str) or "?" in value or "#" in value or any(ord(char) <= 32 for char in value):
        raise WorkerError("worker server must be an HTTPS origin or HTTP loopback")
    parts = urlsplit(value)
    if (
        parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path not in {"", "/"}
        or not parts.hostname
        or parts.scheme not in {"https", "http"}
        or parts.scheme == "http"
        and parts.hostname not in {"127.0.0.1", "localhost", "::1"}
    ):
        raise WorkerError("worker server must be an HTTPS origin or HTTP loopback")
    return value.rstrip("/")


class Transport:
    def __init__(self, config, *, transport=None):
        self.origin = origin(config["server"])
        credential = config["token"]
        if not isinstance(credential, str) or not re.fullmatch(r"rnaw_[A-Za-z0-9_-]{43}", credential):
            raise WorkerError("configuration requires a separate enrolled work-worker credential")
        self.client = httpx.Client(
            base_url=self.origin,
            headers={"authorization": "Bearer " + credential},
            timeout=httpx.Timeout(1810, connect=10),
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )

    def close(self):
        self.client.close()

    def post(self, path, body):
        try:
            seconds = (
                1810 if path.endswith("/inference") else 45 if path.endswith(("/model-info", "/source_search", "/source_fetch")) else 15
            )
            timeout = httpx.Timeout(seconds, connect=10)
            with self.client.stream("POST", "/api/work-worker/" + path, json=body, timeout=timeout) as response:
                if response.status_code != 200:
                    raise WorkerError(f"worker request refused (HTTP {response.status_code}); inspect its scoped server record")
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_JOURNAL:
                        raise WorkerError("worker response exceeded its wire allowance; inspect retained server outcome")
                return json.loads(data)
        except (httpx.HTTPError, ValueError, UnicodeError) as exc:
            raise WorkerError("worker reply unavailable; retry with the retained journal, not a new operation identity") from exc


class Journal:
    def __init__(self, directory):
        self.directory = directory

    def saved(self, name):
        path = self.directory / (name + ".json")
        return json.loads(read_private(path, maximum=MAX_JOURNAL)) if path.exists() or path.is_symlink() else None

    def save(self, name, body):
        path = self.directory / (name + ".json")
        protected_parent(path)
        if path.exists() or path.is_symlink():
            read_private(path, maximum=MAX_JOURNAL)
        data = encoded(body).encode()
        if len(data) > MAX_JOURNAL:
            raise WorkerError("worker journal exceeds its wire allowance")
        atomic_write(path, data)

    def rpc(self, transport, identity, path, body):
        checksum = digest(encoded({"path": path, "body": body}))
        previous = self.saved(identity)
        if previous and previous["sha256"] != checksum:
            raise WorkerError("replayed agent request differs from the retained operation")
        if previous and "response" in previous:
            return previous["response"]
        self.save(identity, {"sha256": checksum, "path": path, "body": body})  # retain identity before crossing the network
        response = transport.post(path, body)
        if not (path.endswith("/inference") and response.get("state") == "running"):
            self.save(identity, {"sha256": checksum, "response": response})
        return response


class Assignment:
    def __init__(self, transport, journal, row, *, heartbeat_interval=15):
        self.transport, self.journal, self.row = transport, journal, row
        self.lease = {key: row[key] for key in ("attempt_id", "fence", "lease")}
        self.prefix = f"jobs/{row['id']}/"
        self.stopped, self.closed = threading.Event(), threading.Event()
        self.heartbeat_interval = heartbeat_interval
        self.call_sequence, self.tool_sequence = 0, 0
        self.outputs = {}
        self.compactor = None

    def heartbeat(self):
        try:
            reply = self.transport.post(self.prefix + "heartbeat", self.lease)
            if reply["cancel_requested"]:
                self.stopped.set()
        except WorkerError:
            self.stopped.set()  # loss of authority is not permission to keep spending

    def keepalive(self):
        while not self.closed.wait(self.heartbeat_interval):
            self.heartbeat()

    def complete(self, payload, *, timeout):
        if self.compactor is not None:
            payload = self.compactor.transform(payload)
        return self._submit(payload)

    def _submit(self, payload, *, purpose="work"):
        if self.stopped.is_set():
            raise WorkerError("assignment cancelled or its lease could not be renewed")
        self.call_sequence += 1
        identity = f"call-{self.call_sequence}"
        body = self.lease | {"client_id": identity, "payload": payload}
        if purpose != "work":
            body["purpose"] = purpose
        try:
            reply = self.journal.rpc(self.transport, identity, self.prefix + "inference", body)
        except WorkerError:
            self.stopped.set()
            raise
        if reply["state"] != "complete":
            if reply["state"] in {"running", "submission_unknown"}:
                self.stopped.set()
            hint = {
                "running": "inference is still running on the server",
                "failed": "inference failed on the server",
                "submission_unknown": "inference has a retained intent without a confirmed submission receipt",
            }.get(reply["state"], "inference did not complete")
            raise WorkerError(hint + "; inspect the retained call and accounting before reopening")
        return reply["response"]

    def invoke(self, name, arguments):
        if self.stopped.is_set():
            raise WorkerError("assignment cancelled or its lease could not be renewed")
        if not re.fullmatch(r"[a-z_]+", name):
            raise WorkerError("unrecognized scoped tool")
        self.tool_sequence += 1
        identity = f"tool-{self.tool_sequence}"
        # Model arguments never replace transport-owned identity or lease fields.
        if set(arguments) & {"client_id", "attempt_id", "fence", "lease"}:
            raise WorkerError("tool arguments cannot supply execution authority")
        body = dict(arguments) | self.lease | {"client_id": identity}
        reply = self.journal.rpc(self.transport, identity, self.prefix + "tools/" + name, body)
        result = reply["result"]
        if name == "compute_submit":
            # Queuing is journaled independently from waiting. A lost worker
            # reply resumes the same computation, never a fresh paid submission.
            final = self.journal.saved(identity + "-execution")
            if final is None:
                while result["status"] not in {"succeeded", "failed", "cancelled"}:
                    if result["status"] == "outcome_unknown" or self.stopped.is_set():
                        self.stopped.set()
                        raise WorkerError("computation outcome is unresolved; retain its identity and reservation for inspection")
                    if self.stopped.wait(1):
                        raise WorkerError("assignment stopped while waiting for its computation")
                    try:
                        result = self.transport.post(self.prefix + "computations/" + result["compute_job_id"] + "/wait", self.lease)
                    except WorkerError:
                        self.stopped.set()
                        raise
                self.journal.save(identity + "-execution", result)
            else:
                result = final
        # Keep only references actually supplied by server tools, not model-authored IDs.
        from .work_contracts import VersionReference

        references = [result.get(key) for key in ("reference", "bibliography_reference", "result_reference")]
        references += [item.get("reference") for item in result.get("items", []) if isinstance(item, dict)]
        if isinstance(result.get("result"), dict):
            references += result["result"].get("outputs", [])
        for item in references:
            if isinstance(item, dict):
                reference = VersionReference.model_validate(item)
                self.outputs[(reference.kind, reference.id, str(reference.revision))] = reference.model_dump()
        return result

    def emit(self, event):
        if self.stopped.is_set():
            return
        phase = event.kind.value
        tool = event.data.get("name")
        summary = f"{phase}: {tool}" if tool else phase
        body = self.lease | {"client_id": f"event-{event.sequence}", "phase": phase, "summary": summary[:400]}
        try:
            self.journal.rpc(self.transport, f"event-{event.sequence}", self.prefix + "progress", body)
        except WorkerError:
            self.stopped.set()

    def execute(self, packet, model):
        limits = packet["specification"]["resources"]
        if model not in limits["allowed_models"]:
            raise WorkerError("selected model is not granted to this task")
        binding = PromptBinding("work", packet["prompt_sha256"])
        prepared = PreparedAgentRun(
            run_id=self.row["id"],
            spec=AgentRunSpec(
                executor_id="reasoning-native",
                model=model,
                system_prompt=binding,
                user_prompt=binding,
                budget=RunBudget(max_tool_calls=None, timeout_seconds=float("inf")),
                output_schema=packet.get("output_schema"),
            ),
            system_prompt=packet["prompt"]["system_prompt"],
            user_prompt=encoded(
                {
                    "specification": packet["specification"],
                    "compaction": packet["compaction"],
                    "authorization_revision": packet["authorization_revision"],
                    "controller": packet.get("controller"),
                    "output_schema": packet.get("output_schema"),
                }
            ),
            prompt_hashes={"system": packet["prompt_sha256"], "task": packet["specification_sha256"]},
            tools=tuple(
                PreparedTool(tool["function"]["name"], tool["function"]["description"], tool["function"]["parameters"])
                for tool in packet["tools"]
            ),
        )
        self.heartbeat()
        thread = threading.Thread(target=self.keepalive, daemon=True)
        thread.start()
        try:
            from .work_compaction import ContextCompactor

            mode = self.journal.saved("context-maintenance")
            if mode is None:
                mode = {"enabled": not any(self.journal.saved(name) for name in ("call-1", "input-0", "event-1"))}
                self.journal.save("context-maintenance", mode)
            input_length = 16000
            if mode["enabled"]:
                info = self.journal.rpc(self.transport, "model-info", self.prefix + "model-info", self.lease | {"model": model})
                if type(info.get("context_length")) is not int or info["context_length"] <= 0:
                    raise WorkerError("model information did not report a positive context size")
                self.compactor = ContextCompactor(self, packet, info["context_length"])
                input_length = min(16000, max(1, info["context_length"] // (12 * max(1, len(packet["specification"]["inputs"])))))
            inputs = []
            for index, reference in enumerate(packet["specification"]["inputs"]):
                if self.stopped.is_set():
                    raise WorkerError("assignment stopped before reading its scoped inputs")
                inputs.append(
                    self.journal.rpc(
                        self.transport,
                        f"input-{index}",
                        self.prefix + "inputs/read",
                        self.lease | {"reference": reference, "offset": 0, "length": input_length},
                    )
                )
            prepared = replace(prepared, user_prompt=encoded({**json.loads(prepared.user_prompt), "assigned_inputs": inputs}))
            executor = ReasoningNativeExecutor(
                self,
                tool_invoker=self.invoke,
                rate_limit_retry_delays=(),
                max_output_continuations=None,
                continue_reasoning_only=True,
            )
            result = executor.execute(prepared, self.emit, cancelled=self.stopped.is_set)
            self.journal.save("agent-result", {"status": result.status, "output": result.output_text, "error": result.error})
            if self.stopped.is_set():
                raise WorkerError("assignment stopped; retained outcomes require server inspection")
            # The full answer stays in the private journal and provider record;
            # a job summary is bounded and never promotes acceptance to complete.
            summary = result.output_text[:16000] or result.error or "Agent returned no result."
            body = self.lease | {
                "outcome": "result" if result.status == "completed" else "failed",
                "summary": summary,
                "outputs": list(self.outputs.values()),
            }
            return self.journal.rpc(self.transport, "completion", self.prefix + "result", body)
        finally:
            self.closed.set()
            thread.join(timeout=20)


def run_once(transport, state, model=None):
    journal = Journal(state)
    pending = journal.saved("pending")
    if pending is None:
        pending = {"claim_id": uuid.uuid4().hex}
        journal.save("pending", pending)
    if "row" not in pending:
        row = transport.post("claim", {"claim_id": pending["claim_id"]})
        if row is None:
            journal.save("pending", {"claim_id": uuid.uuid4().hex})
            return None
        if not row.get("dispatch"):
            raise WorkerError("claim reply was lost after assignment; inspect or fence that assignment before a new claim")
        pending["row"] = row
        journal.save("pending", pending)
    row = pending["row"]
    if (
        not isinstance(row.get("id"), str)
        or not re.fullmatch(r"[a-f0-9]{32}", row["id"])
        or type(row.get("fence")) is not int
        or row["fence"] < 1
    ):
        raise WorkerError("server returned an invalid assignment identity")
    directory = state / (row["id"] + "-" + str(row["fence"]))
    with locked_directory(directory):
        attempt = Journal(directory)
        completed = attempt.saved("completion")
        if completed:
            response = (
                completed["response"]
                if "response" in completed
                else attempt.rpc(transport, "completion", completed["path"], completed["body"])
            )
            journal.save("pending", {"claim_id": uuid.uuid4().hex})
            return response
        packet = attempt.saved("packet")
        if packet is None:
            packet = row["packet"]
            attempt.save("packet", packet)
        if (
            digest(encoded({key: value for key, value in packet.items() if key != "sha256"})) != packet["sha256"]
            or digest(encoded(packet["prompt"])) != packet["prompt_sha256"]
            or digest(encoded(packet["specification"])) != packet["specification_sha256"]
        ):
            raise WorkerError("assignment packet does not match its pinned digests")
        selected = model or next(iter(packet["specification"]["resources"]["allowed_models"]), None)
        if selected is None:
            raise WorkerError("assignment needs an explicit model grant")
        response = Assignment(transport, attempt, row).execute(packet, selected)
    journal.save("pending", {"claim_id": uuid.uuid4().hex})
    return response


def state_directory(value=None):
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    path = Path(value) if value else root / "reasoning" / "work-worker"
    if not path.is_absolute() or ".." in path.parts:
        raise WorkerError("worker state must use an absolute path without parent traversal")
    missing = []
    parent = path.parent
    while not parent.exists() and not parent.is_symlink():
        missing.append(parent)
        parent = parent.parent
    protected_parent(parent / "boundary")
    for parent in reversed(missing):
        parent.mkdir(mode=0o700)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, help="private XDG state directory override")
    commands = parser.add_subparsers(dest="command", required=True)
    configure = commands.add_parser("configure", help="privately store an enrolled work-worker credential")
    configure.add_argument("--server", required=True)
    run = commands.add_parser("run", help="run scoped project assignments; does not need a provider key")
    run.add_argument("--once", action="store_true")
    run.add_argument("--model", help="choose a model within each assignment's explicit grants")
    args = parser.parse_args(argv)
    try:
        with locked_directory(state_directory(args.state)) as state:
            config_path = state / "worker.json"
            if args.command == "configure":
                server = origin(args.server)
                credential = getpass.getpass("Enrolled work-worker credential (hidden): ")
                config = {"server": server, "token": credential}
                check = Transport(config)
                check.close()
                write_document(config_path, config, new=True)
                print("Worker configured. No provider key is stored on this machine.")
                return 0
            config = json.loads(read_private(config_path))
            transport = Transport(config)
            try:
                while True:
                    response = run_once(transport, state, args.model)
                    print("Assignment retained for review." if response else "No authorized assignment available.", flush=True)
                    if args.once:
                        return 0
                    if response is None:
                        time.sleep(5)
            finally:
                transport.close()
    except WorkerError as exc:
        print(f"reasoning-work-worker: {exc}. Retain private state before retrying.", flush=True)
        return 2
    except (InstallError, OSError, ValueError, KeyError):
        print("reasoning-work-worker: stopped; retain private state and inspect the scoped server job before retrying.", flush=True)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
