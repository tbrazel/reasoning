"""Domain-neutral role templates, strategy modifiers and immutable prompt material."""

from importlib.resources import files
from typing import get_args

from .research_context import encoded
from .security import digest
from .work_contracts import Role, Strategy


def _part(name):
    text = files("reasoning_ledger").joinpath("prompts", name + ".md").read_text(encoding="utf-8")
    return {"id": name, "revision": 1, "sha256": digest(text), "text": text}


def catalog():
    return {
        "schema_version": 1,
        "common": _part("work-common"),
        "roles": {role: _part("work-role-" + role) for role in get_args(Role)},
        "strategies": {strategy: _part("work-strategy-" + strategy) for strategy in get_args(Strategy)},
    }


def snapshot(specification):
    parts = [_part("work-common"), _part("work-role-" + specification.role)]
    parts += [_part("work-strategy-" + strategy) for strategy in specification.strategies]
    return {"schema_version": 1, "parts": parts, "system_prompt": "\n\n".join(part["text"].strip() for part in parts)}


def pin(db, org, program, task, specification):
    existing = db.execute("SELECT body_json,sha256 FROM work_task_prompts WHERE task_id=?", (task,)).fetchone()
    if existing:
        return dict(existing)
    payload = encoded(snapshot(specification))
    checksum = digest(payload)
    db.execute("INSERT INTO work_task_prompts VALUES(?,?,?,?,?)", (org, program, task, payload, checksum))
    return {"body_json": payload, "sha256": checksum}
