"""Per-chat agent task list and persistent goal.

A lightweight to-do list the agent maintains for a large piece of work:
a flat list of {title, status} items it sets wholesale (send the full
desired list each time — no ids, no partial-update races), the way
codex's plan tool works. Lives in one JSON file per chat, written by the
ledger MCP server (inside the codex sandbox) and read by the workbench
for the task dock. Distinct from the ledger: tasks are ephemeral
scaffolding for one chat, never part of the mathematical record.

Beside it lives the chat's single persistent *goal* — the result the
user asked for, which outlives individual turns: the workbench keeps
waking the model until the goal is marked completed, impossible (proved
so), or blocked on user input.
"""

from __future__ import annotations

import json
from pathlib import Path

TASK_STATUSES = ("pending", "in_progress", "completed")
GOAL_MARKS = ("completed", "impossible", "blocked")


class TaskError(ValueError):
    """A malformed task list handed to the task tool."""


def _normalize(tasks: object) -> list[dict]:
    if not isinstance(tasks, list):
        raise TaskError("tasks must be a list of {title, status} objects")
    normalized: list[dict] = []
    for entry in tasks:
        if not isinstance(entry, dict):
            raise TaskError("each task must be an object with title and status")
        title = entry.get("title")
        if not isinstance(title, str) or not title.strip():
            raise TaskError("each task needs a non-empty title")
        status = entry.get("status", "pending")
        if status not in TASK_STATUSES:
            raise TaskError(
                f"status must be one of {', '.join(TASK_STATUSES)} (got {status!r})"
            )
        normalized.append({"title": title.strip(), "status": status})
    return normalized


class GoalError(ValueError):
    """A malformed goal operation handed to the goal tool."""


class GoalStore:
    """The chat's single persistent goal, one JSON file beside the task
    list. A goal is {"text", "status", "reason"}: status "active" while
    pursued, or one of GOAL_MARKS once the model marks it. Marks resolve
    at the start of the next user-initiated turn — completed/impossible
    clear the goal, blocked returns to active — via resolve_marks()."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def load(self) -> dict | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        status = data.get("status")
        if status not in ("active", *GOAL_MARKS):
            status = "active"
        reason = data.get("reason")
        return {
            "text": text.strip(),
            "status": status,
            "reason": reason if isinstance(reason, str) and reason else None,
        }

    def _write(self, goal: dict) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(goal, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)
        return goal

    def set(self, text: object) -> dict:
        if not isinstance(text, str) or not text.strip():
            raise GoalError("a goal needs non-empty text")
        extant = self.load()
        if extant is not None:
            raise GoalError(
                f"a goal is already set: {extant['text']!r}. If new "
                "instructions from the user have made it irrelevant or "
                "superseded it, clear it first with goal(clear=true); "
                "otherwise keep working toward it."
            )
        return self._write({"text": text.strip(), "status": "active", "reason": None})

    def mark(self, status: object, reason: object = None) -> dict:
        goal = self.load()
        if goal is None:
            raise GoalError("no goal is set")
        if status not in GOAL_MARKS:
            raise GoalError(
                f"mark must be one of {', '.join(GOAL_MARKS)} (got {status!r})"
            )
        note = reason.strip() if isinstance(reason, str) else ""
        if status == "blocked" and not note:
            raise GoalError(
                "blocked needs a reason naming exactly what user input is required"
            )
        if status == "impossible" and not note:
            raise GoalError(
                "impossible needs a reason citing the impossibility proof"
            )
        goal["status"] = status
        goal["reason"] = note or None
        return self._write(goal)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def resolve_marks(self) -> dict | None:
        """Start-of-user-turn transitions: completed/impossible goals
        clear, a blocked one returns to active. Returns the goal as the
        new turn should see it."""
        goal = self.load()
        if goal is None:
            return None
        if goal["status"] in ("completed", "impossible"):
            self.clear()
            return None
        if goal["status"] == "blocked":
            goal["status"] = "active"
            goal["reason"] = None
            return self._write(goal)
        return goal


class TaskStore:
    """The task list for one chat, backed by a single JSON file."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        try:
            return _normalize(data)
        except TaskError:
            return []

    def set(self, tasks: object) -> list[dict]:
        """Replace the whole list (an empty list clears it). Returns the
        stored list. Raises TaskError on a malformed list."""
        normalized = _normalize(tasks)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)
        return normalized
