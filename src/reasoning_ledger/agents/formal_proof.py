"""Bounded local Lean checks with explicit toolchain and axiom provenance."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .tools import ToolRegistry

_DECLARATION = re.compile(r"^[A-Za-z_][A-Za-z0-9_'.]*(?:\.[A-Za-z_][A-Za-z0-9_']*)*$")
_MAX_SOURCE_BYTES = 200_000
_MAX_OUTPUT_BYTES = 64_000


class FormalProofError(RuntimeError):
    """A local formal-proof environment could not perform the requested check."""


def _binary(name: str) -> str | None:
    value = shutil.which(name)
    return str(Path(value).resolve()) if value else None


def _workspace(value: Path | str | None) -> Path | None:
    configured = value or os.environ.get("REASONING_LEAN_WORKSPACE")
    if not configured:
        return None
    path = Path(configured).expanduser().resolve()
    if not path.is_dir():
        raise FormalProofError(f"configured Lean workspace does not exist: {path}")
    if not any((path / name).is_file() for name in ("lakefile.toml", "lakefile.lean")):
        raise FormalProofError(f"configured Lean workspace has no lakefile: {path}")
    return path


def _environment(*, workspace: Path | str | None = None) -> dict[str, Any]:
    lean = _binary("lean")
    lake = _binary("lake")
    resolved_workspace = _workspace(workspace)
    return {
        "lean_available": lean is not None,
        "lake_available": lake is not None,
        "lean_executable": lean,
        "lake_executable": lake,
        "mathlib_workspace": str(resolved_workspace) if resolved_workspace else None,
        "mathlib_available": bool(resolved_workspace and lake),
        "network_setup": "disabled; Reasoning never runs lake update or cache downloads",
    }


def _bounded_output(value: str, scratch: Path) -> tuple[str, bool]:
    scrubbed = value.replace(str(scratch), "$SCRATCH")
    encoded = scrubbed.encode("utf-8", errors="replace")
    if len(encoded) <= _MAX_OUTPUT_BYTES:
        return scrubbed, False
    return encoded[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"), True


def _version(command: list[str], cwd: Path, environment: dict[str, str]) -> str | None:
    try:
        result = subprocess.run(
            [*command, "--version"],
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (result.stdout or result.stderr).strip()
    return text.splitlines()[0] if text else None


def register_formal_proof_tools(
    registry: ToolRegistry,
    *,
    workspace: Path | str | None = None,
) -> None:
    @registry.tool(
        name="lean_environment",
        description=(
            "Inspect the locally installed Lean and Lake executables and the explicitly configured mathlib "
            "workspace. This performs no downloads or package updates."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("proof:inspect",),
    )
    def lean_environment() -> dict[str, Any]:
        return _environment(workspace=workspace)

    @registry.tool(
        name="lean_check",
        description=(
            "Elaborate and kernel-check a bounded Lean source file with the installed core toolchain or an "
            "explicitly configured local mathlib workspace. Can append #print axioms audits. Runs no package "
            "downloads and reports the exact source hash, toolchain, diagnostics, and sorry status."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "source": {"type": "string", "minLength": 1, "maxLength": _MAX_SOURCE_BYTES},
                "environment": {"enum": ["auto", "core", "mathlib"]},
                "declarations": {
                    "type": "array",
                    "maxItems": 20,
                    "uniqueItems": True,
                    "items": {"type": "string", "pattern": _DECLARATION.pattern, "maxLength": 200},
                },
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
            },
            "required": ["source"],
            "additionalProperties": False,
        },
        risk="write",
        capabilities=("proof:check",),
        idempotent=False,
    )
    def lean_check(
        source: str,
        environment: str = "auto",
        declarations: list[str] | None = None,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        if "\x00" in source:
            raise ValueError("Lean source cannot contain NUL bytes")
        source_bytes = source.encode("utf-8")
        if len(source_bytes) > _MAX_SOURCE_BYTES:
            raise ValueError(f"Lean source exceeds the {_MAX_SOURCE_BYTES:,}-byte safety limit")
        declarations = declarations or []
        if any(not _DECLARATION.fullmatch(name) for name in declarations):
            raise ValueError("invalid Lean declaration name")

        installed = _environment(workspace=workspace)
        mode = environment
        if mode == "auto":
            mode = "mathlib" if installed["mathlib_available"] else "core"
        if mode == "mathlib":
            if not installed["mathlib_available"]:
                raise FormalProofError(
                    "mathlib checking needs Lake and an existing REASONING_LEAN_WORKSPACE with a lakefile"
                )
            command = [installed["lake_executable"], "env", "lean"]
            working_directory = Path(installed["mathlib_workspace"])
            display_command = ["lake", "env", "lean", "Proof.lean"]
        else:
            if not installed["lean_available"]:
                raise FormalProofError("Lean is not installed or is not on PATH")
            command = [installed["lean_executable"]]
            working_directory = None
            display_command = ["lean", "Proof.lean"]

        audit = "".join(f"\n#print axioms {name}\n" for name in declarations)
        checked_source = f"{source.rstrip()}\n{audit}"
        source_sha256 = hashlib.sha256(checked_source.encode("utf-8")).hexdigest()
        process_environment = dict(os.environ)
        process_environment.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "LEAN_ABORT_ON_PANIC": "1",
                "NO_COLOR": "1",
            }
        )
        if mode == "core":
            process_environment.pop("LEAN_PATH", None)
            process_environment.pop("LEAN_SRC_PATH", None)

        with tempfile.TemporaryDirectory(prefix="reasoning-lean-") as temporary:
            scratch = Path(temporary)
            source_path = scratch / "Proof.lean"
            source_path.write_text(checked_source, encoding="utf-8")
            run_cwd = working_directory or scratch
            version = _version(command, run_cwd, process_environment)
            started = time.monotonic()
            try:
                result = subprocess.run(
                    [*command, str(source_path)],
                    cwd=run_cwd,
                    env=process_environment,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds,
                    check=False,
                )
                timed_out = False
            except subprocess.TimeoutExpired as error:
                stdout = error.stdout.decode(errors="replace") if isinstance(error.stdout, bytes) else (error.stdout or "")
                stderr = error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes) else (error.stderr or "")
                result = subprocess.CompletedProcess(command, 124, stdout, stderr)
                timed_out = True
            except OSError as error:
                raise FormalProofError(f"could not start Lean: {error}") from error
            duration_ms = round((time.monotonic() - started) * 1000)
            stdout, stdout_truncated = _bounded_output(result.stdout or "", scratch)
            stderr, stderr_truncated = _bounded_output(result.stderr or "", scratch)

        diagnostics = f"{stdout}\n{stderr}".lower()
        uses_sorry = "uses 'sorry'" in diagnostics or "sorryax" in diagnostics
        kernel_accepted = result.returncode == 0 and not timed_out
        return {
            "status": "timed_out" if timed_out else ("accepted" if kernel_accepted else "rejected"),
            "kernel_accepted": kernel_accepted,
            "complete_without_sorry": kernel_accepted and not uses_sorry,
            "uses_sorry": uses_sorry,
            "environment": mode,
            "toolchain": version,
            "workspace": str(working_directory) if working_directory else None,
            "command": display_command,
            "return_code": result.returncode,
            "duration_ms": duration_ms,
            "source_sha256": source_sha256,
            "audited_declarations": declarations,
            "stdout": stdout,
            "stderr": stderr,
            "output_truncated": stdout_truncated or stderr_truncated,
            "network_setup": "disabled; installed dependencies only",
        }
