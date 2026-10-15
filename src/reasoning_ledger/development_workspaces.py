"""Explicit, owner-private repository grants for development chats."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class DevelopmentWorkspaceError(ValueError):
    """A development workspace definition is invalid or unavailable."""


_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_PYTHON_CANDIDATES = (
    Path(".venv/bin/python"),
    Path("venv/bin/python"),
    Path(".env/bin/python"),
    Path("env/bin/python"),
    Path(".conda/bin/python"),
)
_WORKSPACE_OUTPUT_LIMIT = 100_000


def _absolute_path(path: Path) -> Path:
    """Make an interpreter path absolute without dereferencing venv symlinks."""

    return Path(os.path.abspath(path.expanduser()))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")




def _slug(value: str) -> str:
    identifier = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (identifier or "repository")[:64].rstrip("-")


def _validated_repository(raw_path: Any) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        raise DevelopmentWorkspaceError("repository path is required")
    path = Path(text).expanduser().resolve()
    home = Path.home().resolve()
    if path.parent == path or path == home:
        raise DevelopmentWorkspaceError("choose a repository, not a filesystem or home root")
    if not path.is_dir():
        raise DevelopmentWorkspaceError(f"repository directory does not exist: {path}")
    if not (path / ".git").exists():
        raise DevelopmentWorkspaceError(f"directory is not a Git repository: {path}")
    return path


def _python_environment(value: dict[str, Any], repository: Path) -> dict[str, Any]:
    """Describe a workspace interpreter without launching repository code."""

    configured = str(value.get("python_path") or "").strip()
    if configured == "bundled":
        return {
            "mode": "bundled",
            "path": None,
            "available": True,
            "label": "Reasoning Python",
            "detail": "Explicitly uses the Python runtime bundled with Reasoning.",
        }
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            candidate = repository / candidate
        candidate = _absolute_path(candidate)
        return {
            "mode": "configured",
            "path": str(candidate),
            "available": candidate.is_file() and os.access(candidate, os.X_OK),
            "label": candidate.parent.parent.name or candidate.name,
            "detail": "Configured workspace Python interpreter.",
        }
    for relative in _PYTHON_CANDIDATES:
        candidate = _absolute_path(repository / relative)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return {
                "mode": "detected",
                "path": str(candidate),
                "available": True,
                "label": relative.parent.parent.name,
                "detail": f"Detected {relative.as_posix()}.",
            }
    if any((repository / marker).exists() for marker in ("uv.lock", "pyproject.toml")):
        expected = _absolute_path(repository / ".venv" / "bin" / "python")
        return {
            "mode": "detected",
            "path": str(expected),
            "available": False,
            "label": ".venv",
            "detail": "Python project detected, but its environment has not been created.",
        }
    return {
        "mode": "bundled",
        "path": None,
        "available": True,
        "label": "Reasoning Python",
        "detail": "No workspace environment was detected; using Reasoning Python.",
    }


def _workspace_contract(value: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Read Reasoning-owned setup commands without launching repository code."""

    environment = value.get("environment_config")
    if environment is None:
        return None, None
    if not isinstance(environment, dict):
        return None, "Invalid saved workspace environment configuration."

    commands: dict[str, list[str] | None] = {}
    for action in ("check", "setup"):
        raw = environment.get(action)
        if raw is None and action == "setup":
            commands[action] = None
            continue
        if not isinstance(raw, list) or not raw or not all(isinstance(part, str) and part and "\x00" not in part for part in raw):
            return None, f"Invalid saved {action} command."
        commands[action] = list(raw)

    def timeout(name: str, default: int, maximum: int) -> int:
        raw = environment.get(name, default)
        if not isinstance(raw, int) or isinstance(raw, bool) or not 1 <= raw <= maximum:
            raise DevelopmentWorkspaceError(
                f"Invalid saved {name.replace('_', ' ')}; choose a value between 1 and {maximum}."
            )
        return raw

    try:
        check_timeout = timeout("check_timeout_seconds", 120, 900)
        setup_timeout = timeout("setup_timeout_seconds", 1800, 7200)
    except DevelopmentWorkspaceError as error:
        return None, str(error)
    label = str(environment.get("label") or "Workspace environment").strip()
    if not label or len(label) > 120:
        return None, "Invalid saved workspace environment label."
    normalized = {
        "label": label,
        "check": commands["check"],
        "setup": commands["setup"],
        "check_timeout_seconds": check_timeout,
        "setup_timeout_seconds": setup_timeout,
    }
    fingerprint = hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {"fingerprint": fingerprint, **normalized}, None


def _parse_command(raw: Any, *, required: bool, action: str) -> list[str] | None:
    text = str(raw or "").strip()
    if not text:
        if required:
            raise DevelopmentWorkspaceError(f"{action} command is required")
        return None
    if "\x00" in text:
        raise DevelopmentWorkspaceError(f"invalid {action} command")
    try:
        command = shlex.split(text)
    except ValueError as error:
        raise DevelopmentWorkspaceError(f"invalid {action} command: {error}") from error
    if not command:
        raise DevelopmentWorkspaceError(f"{action} command is required")
    return command


class DevelopmentWorkspaceStore:
    """Persist the exact repositories a user may select for coding chats.

    Merely detecting or registering a repository does not grant an agent
    access. A chat must pin its workspace ID when it is created.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._environment_active: set[str] = set()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {str(key): value for key, value in payload.items() if isinstance(key, str) and isinstance(value, dict)}

    def _write(self, values: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(values, output, ensure_ascii=False, indent=2, sort_keys=True)
                output.write("\n")
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _environment_status(self, value: dict[str, Any]) -> dict[str, Any]:
        contract, error = _workspace_contract(value)
        if error:
            return {
                "managed": True,
                "ready": False,
                "state": "invalid",
                "label": "Workspace environment",
                "detail": error,
                "check_available": False,
                "setup_available": False,
                "check_command": "",
                "setup_command": "",
                "check_timeout_seconds": 120,
                "setup_timeout_seconds": 1800,
                "updated_at": None,
                "output": "",
            }
        if contract is None:
            return {
                "managed": False,
                "ready": True,
                "state": "not_declared",
                "label": "Workspace environment",
                "detail": "No setup commands are configured in Reasoning.",
                "check_available": False,
                "setup_available": False,
                "check_command": "",
                "setup_command": "",
                "check_timeout_seconds": 120,
                "setup_timeout_seconds": 1800,
                "updated_at": None,
                "output": "",
            }
        result = value.get("environment_result")
        if not isinstance(result, dict) or result.get("fingerprint") != contract["fingerprint"]:
            result = None
        if result is None:
            state = "setup_required"
            detail = "Project setup has not been checked on this machine."
            ready = False
            updated_at = None
            output = ""
        else:
            state = str(result.get("state") or "setup_required")
            if state == "running" and value["workspace_id"] not in self._environment_active:
                state = "interrupted"
            ready = state == "ready"
            detail = str(result.get("detail") or ("Environment is ready." if ready else "Project setup is required."))
            updated_at = result.get("updated_at")
            output = str(result.get("output") or "")[-_WORKSPACE_OUTPUT_LIMIT:]
        return {
            "managed": True,
            "ready": ready,
            "state": state,
            "label": contract["label"],
            "detail": detail,
            "check_available": True,
            "setup_available": contract["setup"] is not None,
            "check_command": shlex.join(contract["check"]),
            "setup_command": shlex.join(contract["setup"]) if contract["setup"] is not None else "",
            "check_timeout_seconds": contract["check_timeout_seconds"],
            "setup_timeout_seconds": contract["setup_timeout_seconds"],
            "updated_at": updated_at,
            "output": output,
        }

    def _public(self, value: dict[str, Any]) -> dict[str, Any]:
        path = Path(str(value.get("path") or "")).expanduser()
        available = path.is_dir() and (path / ".git").exists()
        return {
            "workspace_id": value["workspace_id"],
            "name": value["name"],
            "path": str(path),
            "available": available,
            "python_environment": _python_environment(value, path.resolve()),
            "workspace_environment": self._environment_status(value),
            "created_at": value.get("created_at"),
            "updated_at": value.get("updated_at"),
        }

    def list(self) -> list[dict[str, Any]]:
        return sorted(
            (self._public(value) for value in self._load().values()),
            key=lambda value: (str(value["name"]).lower(), value["workspace_id"]),
        )

    def get(self, workspace_id: str) -> dict[str, Any]:
        if not _IDENTIFIER.fullmatch(workspace_id):
            raise DevelopmentWorkspaceError("invalid development workspace id")
        try:
            value = self._load()[workspace_id]
        except KeyError as exc:
            raise KeyError(f"unknown development workspace: {workspace_id}") from exc
        public = self._public(value)
        if not public["available"]:
            raise DevelopmentWorkspaceError(f"development workspace is unavailable: {public['path']}")
        return public

    def add(self, name: str, path: str) -> dict[str, Any]:
        normalized_name = str(name or "").strip()
        if not normalized_name:
            raise DevelopmentWorkspaceError("workspace name is required")
        if len(normalized_name) > 120:
            raise DevelopmentWorkspaceError("workspace name cannot exceed 120 characters")
        repository = _validated_repository(path)
        with self._lock:
            values = self._load()
            for value in values.values():
                if Path(str(value.get("path") or "")).expanduser().resolve() == repository:
                    return self._public(value)
            base = _slug(normalized_name)
            workspace_id = base
            counter = 2
            while workspace_id in values:
                suffix = f"-{counter}"
                workspace_id = f"{base[: 64 - len(suffix)]}{suffix}"
                counter += 1
            timestamp = _now()
            values[workspace_id] = {
                "workspace_id": workspace_id,
                "name": normalized_name,
                "path": str(repository),
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            self._write(values)
            return self._public(values[workspace_id])

    def delete(self, workspace_id: str) -> dict[str, Any]:
        if not _IDENTIFIER.fullmatch(workspace_id):
            raise DevelopmentWorkspaceError("invalid development workspace id")
        with self._lock:
            values = self._load()
            try:
                removed = values.pop(workspace_id)
            except KeyError as exc:
                raise KeyError(f"unknown development workspace: {workspace_id}") from exc
            self._write(values)
            return self._public(removed)

    def set_python(self, workspace_id: str, python_path: str | None) -> dict[str, Any]:
        """Select a workspace interpreter, or restore conservative auto-detection."""

        if not _IDENTIFIER.fullmatch(workspace_id):
            raise DevelopmentWorkspaceError("invalid development workspace id")
        configured = str(python_path or "").strip()
        if configured and configured != "bundled" and "\x00" in configured:
            raise DevelopmentWorkspaceError("invalid Python interpreter path")
        with self._lock:
            values = self._load()
            try:
                value = values[workspace_id]
            except KeyError as exc:
                raise KeyError(f"unknown development workspace: {workspace_id}") from exc
            if configured:
                value["python_path"] = configured
            else:
                value.pop("python_path", None)
            value["updated_at"] = _now()
            self._write(values)
            return self._public(value)

    def set_environment(
        self,
        workspace_id: str,
        *,
        label: str,
        check_command: str,
        setup_command: str | None = None,
        check_timeout_seconds: int = 120,
        setup_timeout_seconds: int = 1800,
    ) -> dict[str, Any]:
        """Save owner-private workspace setup commands in Reasoning settings."""

        if not _IDENTIFIER.fullmatch(workspace_id):
            raise DevelopmentWorkspaceError("invalid development workspace id")
        normalized_label = str(label or "").strip()
        if not normalized_label or len(normalized_label) > 120:
            raise DevelopmentWorkspaceError("workspace environment label must contain at most 120 characters")
        check = _parse_command(check_command, required=True, action="check")
        setup = _parse_command(setup_command, required=False, action="setup")
        if not isinstance(check_timeout_seconds, int) or isinstance(check_timeout_seconds, bool) or not 1 <= check_timeout_seconds <= 900:
            raise DevelopmentWorkspaceError("check timeout must be between 1 and 900 seconds")
        if not isinstance(setup_timeout_seconds, int) or isinstance(setup_timeout_seconds, bool) or not 1 <= setup_timeout_seconds <= 7200:
            raise DevelopmentWorkspaceError("setup timeout must be between 1 and 7200 seconds")
        with self._lock:
            values = self._load()
            try:
                value = values[workspace_id]
            except KeyError as exc:
                raise KeyError(f"unknown development workspace: {workspace_id}") from exc
            value["environment_config"] = {
                "label": normalized_label,
                "check": check,
                "setup": setup,
                "check_timeout_seconds": check_timeout_seconds,
                "setup_timeout_seconds": setup_timeout_seconds,
            }
            value.pop("environment_result", None)
            value["updated_at"] = _now()
            self._write(values)
            return self._public(value)

    def clear_environment(self, workspace_id: str) -> dict[str, Any]:
        """Remove Reasoning-owned workspace setup commands."""

        if not _IDENTIFIER.fullmatch(workspace_id):
            raise DevelopmentWorkspaceError("invalid development workspace id")
        with self._lock:
            values = self._load()
            try:
                value = values[workspace_id]
            except KeyError as exc:
                raise KeyError(f"unknown development workspace: {workspace_id}") from exc
            value.pop("environment_config", None)
            value.pop("environment_result", None)
            value["updated_at"] = _now()
            self._write(values)
            return self._public(value)

    def run_environment(self, workspace_id: str, action: str) -> dict[str, Any]:
        """Run one explicitly configured setup action and persist its diagnostic."""

        if action not in {"check", "setup"}:
            raise DevelopmentWorkspaceError("workspace environment action must be check or setup")
        if not _IDENTIFIER.fullmatch(workspace_id):
            raise DevelopmentWorkspaceError("invalid development workspace id")
        with self._lock:
            values = self._load()
            try:
                value = values[workspace_id]
            except KeyError as exc:
                raise KeyError(f"unknown development workspace: {workspace_id}") from exc
            repository = _validated_repository(value.get("path"))
            contract, error = _workspace_contract(value)
            if error:
                raise DevelopmentWorkspaceError(error)
            if contract is None:
                raise DevelopmentWorkspaceError("workspace setup commands are not configured in Reasoning")
            command = contract[action]
            if command is None:
                raise DevelopmentWorkspaceError("workspace does not declare an environment setup command")
            if workspace_id in self._environment_active:
                raise DevelopmentWorkspaceError("workspace environment action is already running")
            self._environment_active.add(workspace_id)
            value["environment_result"] = {
                "fingerprint": contract["fingerprint"],
                "state": "running",
                "detail": "Checking project setup…" if action == "check" else "Setting up project environment…",
                "updated_at": _now(),
                "output": "",
            }
            self._write(values)

        output: list[str] = []
        final_state = "setup_required"
        detail = "Project setup is required."
        public: dict[str, Any] | None = None
        try:
            actions = [action]
            if action == "setup":
                actions.append("check")
            for current in actions:
                current_command = contract[current]
                if current_command is None:
                    continue
                output.append(f"$ {shlex.join(current_command)}")
                try:
                    result = subprocess.run(
                        current_command,
                        cwd=repository,
                        env={**os.environ, "REASONING_WORKSPACE_ACTION": current},
                        capture_output=True,
                        text=True,
                        timeout=contract[f"{current}_timeout_seconds"],
                    )
                except FileNotFoundError:
                    detail = f"Command not found: {current_command[0]}"
                    output.append(detail)
                    break
                except subprocess.TimeoutExpired as error:
                    stdout = error.stdout.decode() if isinstance(error.stdout, bytes) else (error.stdout or "")
                    stderr = error.stderr.decode() if isinstance(error.stderr, bytes) else (error.stderr or "")
                    output.extend(part for part in (stdout, stderr) if part)
                    detail = f"{current.capitalize()} timed out after {contract[f'{current}_timeout_seconds']} seconds."
                    output.append(detail)
                    break
                output.extend(part for part in (result.stdout, result.stderr) if part)
                if result.returncode != 0:
                    detail = f"{current.capitalize()} failed with exit status {result.returncode}."
                    break
            else:
                final_state = "ready"
                detail = "Project environment check passed."
        except OSError as error:
            detail = f"Workspace environment action could not start: {error}"
            output.append(detail)
        finally:
            with self._lock:
                self._environment_active.discard(workspace_id)
                values = self._load()
                value = values.get(workspace_id)
                if value is not None:
                    value["environment_result"] = {
                        "fingerprint": contract["fingerprint"],
                        "state": final_state,
                        "detail": detail,
                        "updated_at": _now(),
                        "output": "\n".join(output)[-_WORKSPACE_OUTPUT_LIMIT:],
                    }
                    value["updated_at"] = _now()
                    self._write(values)
                    public = self._public(value)
        if public is not None:
            return public
        raise KeyError(f"unknown development workspace: {workspace_id}")



def detected_development_workspaces() -> list[dict[str, str]]:
    """Return likely source checkouts without registering or granting them."""
    candidates: list[tuple[str, Path]] = []
    configured = os.environ.get("REASONING_SOURCE_ROOT")
    if configured:
        candidates.append(("Reasoning", Path(configured)))
    candidates.extend(
        [
            ("Reasoning", Path(__file__).resolve().parents[2]),
            ("Reasoning", Path.home() / "src" / "reasoning"),
        ]
    )
    found: list[dict[str, str]] = []
    seen: set[Path] = set()
    for name, candidate in candidates:
        path = candidate.expanduser().resolve()
        if path in seen or not path.is_dir() or not (path / ".git").exists():
            continue
        seen.add(path)
        found.append({"name": name if "reasoning" in path.name.lower() else path.name, "path": str(path)})
    return found
