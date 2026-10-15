"""Reviewed reduction trees: bounded Python reads, pinned leaves and shared resources."""

import json

from .store import StoreError
from .work_contracts import TaskCreate, VersionReference, WorkSpecification
from .work_controller_schema import wake

FAN_IN = 8


def _references(rows):
    return [VersionReference.model_validate(json.loads(row[0])) for row in rows]


def _group(owner, db, root, flow, state, refs):
    original = WorkSpecification.model_validate_json(
        db.execute("SELECT body_json FROM work_tasks WHERE id=?", (flow["target_task"],)).fetchone()[0]
    )
    semantic = owner._task_ref(db.execute("SELECT * FROM work_tasks WHERE id=?", (flow["target_task"],)).fetchone())
    spec = WorkSpecification.model_validate(
        original.model_dump()
        | {
            "role": "coordinator",
            "kind": "collect",
            "controller": "explore_review_revise",
            "objective": "Integrate only the assigned accepted-result batch, preserving exact evidence, disagreements, "
            "uncertainties and omissions. Consult the pinned original task for context. "
            "This is an intermediate reduction, not a claim that the original objective is complete.",
            "acceptance": [
                {
                    "id": "scoped_batch",
                    "description": "Faithfully integrate this assigned batch without "
                    "unsupported conclusions or claims of full-project coverage.",
                    "check": "llm_review",
                }
            ],
            "inputs": [semantic, *refs],
            "resources": original.resources.model_copy(
                update={"allowed_tools": [name for name in original.resources.allowed_tools if name != "work_delegate"]}
            ),
        }
    )
    target = owner.work._task_create(
        db,
        flow["created_by"],
        root["organization_id"],
        root["project_id"],
        root["id"],
        TaskCreate(
            client_id=f"integration-{flow['id']}-{state['round']}-{state['level']}-{state['next_batch']}",
            expected_root_revision=root["head_revision"],
            parent_id=flow["target_task"],
            specification=spec,
        ),
        controller_generated=True,
    )
    child = owner._attach(db, root, flow["created_by"], target, "integrate-" + target["id"])
    db.execute(
        "INSERT INTO work_integration_groups VALUES(?,?,?,?,?)",
        (flow["id"], state["round"], state["level"], state["next_batch"], child["id"]),
    )


def advance(owner, db, root, flow):
    key = (flow["id"], flow["round"])
    state = db.execute("SELECT * FROM work_integration_state WHERE controller_id=? AND round=?", key).fetchone()
    if state is None:
        db.execute(
            "INSERT INTO work_integration_state(controller_id,round,parent_task_id,fan_in) VALUES(?,?,?,?)",
            (*key, flow["candidate_task"], FAN_IN),
        )
        # Pin the accepted leaf set in one database statement; do not materialize
        # every child's transcript/reference in a Python list or a task packet.
        inserted = db.execute(
            "INSERT INTO work_integration_inputs(controller_id,round,level,ordinal,reference_json) "
            "SELECT ?,?,0,ROW_NUMBER() OVER(ORDER BY t.id)-1,c.result_json "
            "FROM work_controllers c JOIN work_tasks t ON t.id=c.target_task "
            "WHERE t.parent_id=? AND c.status='complete' ORDER BY t.id",
            (*key, flow["candidate_task"]),
        ).rowcount
        db.execute(
            "UPDATE work_integration_state SET input_count=?,leaf_count=? WHERE controller_id=? AND round=?", (inserted, inserted, *key)
        )
        state = db.execute("SELECT * FROM work_integration_state WHERE controller_id=? AND round=?", key).fetchone()
    total = state["input_count"]
    if total <= state["fan_in"]:
        db.execute("UPDATE work_integration_state SET stage='ready' WHERE controller_id=? AND round=?", key)
        return _references(
            db.execute(
                "SELECT reference_json FROM work_integration_inputs WHERE controller_id=? AND round=? AND level=? ORDER BY ordinal LIMIT ?",
                (*key, state["level"], state["fan_in"]),
            ).fetchall()
        )
    if state["stage"] == "grouping":
        rows = db.execute(
            "SELECT reference_json FROM work_integration_inputs WHERE controller_id=? AND round=? "
            "AND level=? AND ordinal>=? ORDER BY ordinal LIMIT ?",
            (*key, state["level"], state["cursor"], state["fan_in"]),
        ).fetchall()
        if not rows:
            raise StoreError(409, "integration frontier has no remaining pinned batch")
        _group(owner, db, root, flow, state, _references(rows))
        next_cursor = state["cursor"] + len(rows)
        db.execute(
            "UPDATE work_integration_state SET cursor=?,next_batch=next_batch+1,stage=? WHERE controller_id=? AND round=?",
            (next_cursor, "waiting" if next_cursor == total else "grouping", *key),
        )
        wake(db, root["id"])
        return None
    pending = db.execute(
        "SELECT 1 FROM work_integration_groups g JOIN work_controllers c ON c.id=g.child_controller "
        "WHERE g.controller_id=? AND g.round=? AND g.level=? AND c.status<>'complete' LIMIT 1",
        (*key, state["level"]),
    ).fetchone()
    if pending:
        return None
    inserted = db.execute(
        "INSERT INTO work_integration_inputs(controller_id,round,level,ordinal,reference_json) "
        "SELECT ?,?,?,ROW_NUMBER() OVER(ORDER BY g.ordinal)-1,c.result_json "
        "FROM work_integration_groups g JOIN work_controllers c ON c.id=g.child_controller "
        "WHERE g.controller_id=? AND g.round=? AND g.level=? AND c.status='complete' ORDER BY g.ordinal",
        (*key, state["level"] + 1, *key, state["level"]),
    ).rowcount
    db.execute(
        "UPDATE work_integration_state SET level=level+1,cursor=0,next_batch=0,stage='grouping',input_count=? "
        "WHERE controller_id=? AND round=?",
        (inserted, *key),
    )
    wake(db, root["id"])
    return None


def view(db, identity):
    state = db.execute("SELECT * FROM work_integration_state WHERE controller_id=? ORDER BY round DESC LIMIT 1", (identity,)).fetchone()
    if state is None:
        return None
    return dict(state) | {"pinned_leaves": state["leaf_count"]}
