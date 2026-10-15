"""Exact task input assignments, including discoveries from authorized local tools."""

from .store import StoreError
from .work_contracts import WorkSpecification


def require_assigned(db, task, reference):
    specification = WorkSpecification.model_validate_json(task["body_json"])
    if reference in specification.inputs:
        return
    if db.execute(
        "SELECT 1 FROM work_input_bindings WHERE task_id=? AND input_kind=? AND input_id=? AND input_revision=? AND input_sha256=?",
        (task["id"], reference.kind, reference.id, str(reference.revision), reference.sha256),
    ).fetchone():
        return
    raise StoreError(404, "input is not assigned to this task")


def bind(db, work, task, root, reference, source):
    work._input(db, task["organization_id"], root["project_id"], reference)
    old = db.execute(
        "SELECT input_sha256 FROM work_input_bindings WHERE task_id=? AND input_kind=? AND input_id=? AND input_revision=?",
        (task["id"], reference.kind, reference.id, str(reference.revision)),
    ).fetchone()
    if old:
        if old["input_sha256"] != reference.sha256:
            raise StoreError(409, "discovered version digest changed")
        return
    db.execute(
        "INSERT INTO work_input_bindings VALUES(?,?,?,?,?,?)",
        (task["id"], reference.kind, reference.id, str(reference.revision), reference.sha256, source),
    )
    work._provenance(db, task["organization_id"], root["project_id"], root["id"], "discovery", task["id"], 1, [reference])
