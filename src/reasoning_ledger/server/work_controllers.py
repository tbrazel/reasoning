"""Three durable, domain-neutral controllers with version-specific acceptance gates."""

from __future__ import annotations

import json
import time

from pydantic import ValidationError

from .research_context import encoded
from .security import digest
from .store import StoreError, _id, _row
from .work_contracts import TaskCreate, VersionReference, WorkSpecification
from .work_controller_contracts import ReviewVerdict
from .work_controller_schema import wake
from .work_job_contracts import JobEnqueue
from .work_jobs import WorkJobs


def review_schema():
    return ReviewVerdict.model_json_schema()


class WorkControllers:
    def __init__(self, store):
        self.store, self.jobs = store, WorkJobs(store)
        self.work = self.jobs.work

    @staticmethod
    def _task_ref(task):
        return VersionReference(kind="work_task", id=task["id"], revision=1, sha256=task["sha256"])

    @staticmethod
    def _last_call(db, job):
        return db.execute(
            "SELECT c.id,c.response_json,c.state FROM work_provider_calls c JOIN work_provider_call_sequences s ON s.call_id=c.id "
            "WHERE c.job_id=? AND c.fence=? AND c.purpose='work' ORDER BY s.sequence DESC LIMIT 1",
            (job["id"], job["fence"]),
        ).fetchone()

    @staticmethod
    def _result(db, job):
        row = db.execute("SELECT body_json,sha256 FROM work_results WHERE job_id=? AND fence=?", (job["id"], job["fence"])).fetchone()
        if row is None:
            return None
        return {
            "reference": {"kind": "work_result", "id": job["id"], "revision": job["fence"], "sha256": row["sha256"]},
            "body": json.loads(row["body_json"]),
        }

    def _view(self, db, row):
        result = dict(row)
        for key in ("result_json", "review_json"):
            value = result.pop(key)
            result[key.removesuffix("_json")] = json.loads(value) if value else None
        result["missing_checks"] = self._missing(db, row) if row["result_json"] else []
        from .work_integration import view

        result["integration"] = view(db, row["id"])
        return result

    def read(self, user, org, project, program, identity):
        with self.store.transaction() as db:
            self.work._program(db, user, org, project, program)
            row = _row(db.execute("SELECT * FROM work_controllers WHERE id=? AND program_id=?", (identity, program)).fetchone())
            return self._view(db, row)

    def start(self, user, org, project, program, body):
        with self.store.transaction(write=True, scope=f"work-program:{program}") as db:
            root = self.work._program(db, user, org, project, program, write=True)
            previous = db.execute(
                "SELECT * FROM work_controllers WHERE program_id=? AND created_by=? AND client_id=?", (program, user, body.client_id)
            ).fetchone()
            if previous:
                if previous["root_revision"] != body.expected_revision or (
                    body.target_task is not None
                    and previous["target_task"] != body.target_task
                    or body.target_task is None
                    and not previous["root_controller"]
                ):
                    raise StoreError(409, "controller request identity was used for different input")
                return self._view(db, previous)
            if root["head_revision"] != body.expected_revision or root["status"] != "active":
                raise StoreError(409, "activate the current program revision before starting its controller")
            if body.target_task is None:
                if db.execute(
                    "SELECT 1 FROM work_controllers WHERE program_id=? AND root_controller=1 AND status<>'cancelled'", (program,)
                ).fetchone():
                    raise StoreError(409, "this program already has a root controller")
                spec = WorkSpecification.model_validate(self.work._view(db, root)["version"]["specification"])
                role = {"parallel_integrate": "coordinator", "build_check_repair": "executor", "explore_review_revise": "worker"}[
                    spec.controller
                ]
                spec = spec.model_copy(update={"role": role})
                target = self.work._task_create(
                    db,
                    user,
                    org,
                    project,
                    program,
                    TaskCreate(
                        client_id="controller-target-" + digest(body.client_id)[:32],
                        expected_root_revision=body.expected_revision,
                        specification=spec,
                    ),
                )
            else:
                target = _row(db.execute("SELECT * FROM work_tasks WHERE id=? AND program_id=?", (body.target_task, program)).fetchone())
                if target["status"] != "pending":
                    raise StoreError(409, "a new controller target must be pending")
            flow = self._attach(db, root, user, target, body.client_id, root_controller=body.target_task is None)
            wake(db, program)
            return self._view(db, flow)

    def _attach(self, db, root, user, target, client_id, *, root_controller=False):
        identity, now = _id(), int(time.time())
        spec = WorkSpecification.model_validate(target["specification"] if "specification" in target else json.loads(target["body_json"]))
        db.execute(
            "INSERT INTO work_controllers(id,organization_id,program_id,target_task,candidate_task,client_id,created_by,root_revision,"
            "root_controller,controller,status,phase,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,'running','specification',?,?)",
            (
                identity,
                root["organization_id"],
                root["id"],
                target["id"],
                target["id"],
                client_id,
                user,
                root["head_revision"],
                int(root_controller),
                spec.controller,
                now,
                now,
            ),
        )
        flow = dict(db.execute("SELECT * FROM work_controllers WHERE id=?", (identity,)).fetchone())
        reviewer = self._make_task(
            db,
            root,
            flow,
            "specification",
            "specification_reviewer",
            [self._task_ref(target)],
            "Check that this assignment and its requested verification match its objective. Assess every criterion.",
        )
        self._step(db, root, flow, "specification", reviewer)
        return dict(db.execute("SELECT * FROM work_controllers WHERE id=?", (identity,)).fetchone())

    def _make_task(self, db, root, flow, phase, role, inputs, instruction):
        target = db.execute("SELECT body_json FROM work_tasks WHERE id=?", (flow["target_task"],)).fetchone()
        original = WorkSpecification.model_validate_json(target[0])
        resources = original.resources
        if role in {"critic", "specification_reviewer"}:
            resources = resources.model_copy(
                update={
                    "allowed_tools": [
                        name
                        for name in resources.allowed_tools
                        if name
                        in {
                            "input_read",
                            "knowledge_search",
                            "work_children",
                            "work_child_read",
                            "source_search",
                            "source_fetch",
                            "source_check",
                            "workspace_list",
                            "workspace_read",
                            "compute_output_read",
                        }
                    ]
                }
            )
        expanded = []
        for ref in inputs:
            if ref.kind == "work_result":
                result = db.execute("SELECT body_json FROM work_results WHERE job_id=? AND fence=?", (ref.id, ref.revision)).fetchone()
                if result:
                    expanded.extend(VersionReference.model_validate(item) for item in json.loads(result[0])["outputs"])
        pinned = {}
        for ref in [*original.inputs, *inputs, *expanded]:
            key = (ref.kind, ref.id, ref.revision)
            if key in pinned and pinned[key].sha256 != ref.sha256:
                raise StoreError(409, "controller input digest conflicts with its pinned version")
            pinned.setdefault(key, ref)
        specification = WorkSpecification.model_validate(
            original.model_dump()
            | {
                "role": role,
                "kind": "review" if role in {"critic", "specification_reviewer"} else original.kind,
                "objective": instruction[:8000],
                "inputs": list(pinned.values()),
                "resources": resources,
            }
        )
        return self.work._task_create(
            db,
            flow["created_by"],
            root["organization_id"],
            root["project_id"],
            root["id"],
            TaskCreate(
                client_id=f"controller-{flow['id']}-{phase}-{flow['round']}-{flow['version']}",
                expected_root_revision=root["head_revision"],
                parent_id=flow["target_task"],
                specification=specification,
            ),
            controller_generated=True,
        )

    def _change(self, db, flow, **changes):
        changes |= {"version": flow["version"] + 1, "updated_at": int(time.time())}
        db.execute(
            "UPDATE work_controllers SET " + ",".join(key + "=?" for key in changes) + " WHERE id=?", (*changes.values(), flow["id"])
        )
        flow.update(changes)
        self.work._event(
            db,
            flow["organization_id"],
            flow["program_id"],
            None,
            {
                "type": "controller_transition",
                "controller_id": flow["id"],
                "version": flow["version"],
                "phase": flow["phase"],
                "status": flow["status"],
                "error": flow["error"],
            },
            flow["target_task"],
        )

    def _step(self, db, root, flow, phase, task):
        job = self.jobs._enqueue(
            db,
            flow["created_by"],
            root["organization_id"],
            root["project_id"],
            root["id"],
            task["id"],
            JobEnqueue(expected_epoch=task["epoch"]),
        )
        db.execute(
            "INSERT INTO work_controller_steps(controller_id,phase,round,task_id,job_id,created_at) VALUES(?,?,?,?,?,?)",
            (flow["id"], phase, flow["round"], task["id"], job["id"], int(time.time())),
        )
        self._change(db, flow, phase=phase, status="running", current_job=job["id"], error=None)
        wake(db, root["id"])

    def _verdict(self, db, root, flow, job, result):
        try:
            verdict = ReviewVerdict.model_validate_json(result["body"]["summary"])
            target = WorkSpecification.model_validate_json(
                db.execute("SELECT body_json FROM work_tasks WHERE id=?", (flow["target_task"],)).fetchone()[0]
            )
            if {item.criterion_id for item in verdict.assessments} != {item.id for item in target.acceptance}:
                raise ValueError("review must assess the exact target criteria")
        except (ValidationError, ValueError, TypeError):
            # A malformed review is a repairable artifact, not approval and not
            # a reason to replay its possibly billed job.
            replacement = self._make_task(
                db,
                root,
                flow,
                flow["phase"],
                "specification_reviewer" if flow["phase"] == "specification" else "critic",
                [
                    VersionReference.model_validate(result["reference"]),
                    self._task_ref(db.execute("SELECT * FROM work_tasks WHERE id=?", (flow["target_task"],)).fetchone()),
                ],
                "Repair this review into the required JSON format. Assess each original criterion; do not invent evidence.",
            )
            self._step(db, root, flow, flow["phase"], replacement)
            return None
        call = self._last_call(db, job)
        try:
            if call is None or call["state"] != "complete":
                raise ValueError("review has no completed provider call")
            message = json.loads(call["response_json"])["choices"][0]["message"]["content"] if call else None
            retained = ReviewVerdict.model_validate_json(message)
            if retained != verdict:
                raise ValueError("review differs from its provider response")
        except (ValidationError, ValueError, TypeError, KeyError, IndexError):
            self._change(db, flow, status="blocked", error="Review lacks a matching retained provider attestation.")
            return None
        db.execute("UPDATE work_tasks SET status='complete',updated_at=? WHERE id=?", (int(time.time()), job["task_id"]))
        self._change(db, flow, review_json=encoded(result["reference"]))
        return verdict

    def _repair(self, db, root, flow, refs, summary):
        target = WorkSpecification.model_validate_json(
            db.execute("SELECT body_json FROM work_tasks WHERE id=?", (flow["target_task"],)).fetchone()[0]
        )
        role = target.role
        self._change(db, flow, round=flow["round"] + 1, result_json=None, review_json=None)
        task = self._make_task(db, root, flow, "repair", role, refs, target.objective + "\n\nRevise using the pinned review:\n" + summary)
        self._change(db, flow, candidate_task=task["id"])
        self._step(db, root, flow, "working", task)

    def _missing(self, db, flow):
        target = WorkSpecification.model_validate_json(
            db.execute("SELECT body_json FROM work_tasks WHERE id=?", (flow["target_task"],)).fetchone()[0]
        )
        found = {
            (row["criterion_id"], row["check_kind"])
            for row in db.execute(
                "SELECT criterion_id,check_kind,passed FROM work_checks WHERE controller_id=? AND subject_json=? AND passed=1",
                (flow["id"], flow["result_json"]),
            )
        }
        return [{"criterion_id": item.id, "check": item.check} for item in target.acceptance if (item.id, item.check) not in found]

    def _record(self, db, flow, client_id, criterion, kind, subject, passed, source, body, actor):
        text = encoded(body)
        checksum = digest(text)
        old = db.execute("SELECT sha256,id FROM work_checks WHERE controller_id=? AND client_id=?", (flow["id"], client_id)).fetchone()
        if old:
            if old["sha256"] != checksum:
                raise StoreError(409, "check identity was used for different evidence")
            return old["id"]
        identity = _id()
        db.execute(
            "INSERT INTO work_checks VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                identity,
                flow["id"],
                client_id,
                criterion,
                kind,
                encoded(subject),
                int(passed),
                source,
                text,
                checksum,
                actor,
                int(time.time()),
            ),
        )
        return identity

    def check(self, user, org, project, program, identity, body):
        with self.store.transaction(write=True, scope=f"work-program:{program}") as db:
            self.work._program(db, user, org, project, program, write=True)
            flow = _row(db.execute("SELECT * FROM work_controllers WHERE id=? AND program_id=?", (identity, program)).fetchone())
            old = db.execute(
                "SELECT * FROM work_checks WHERE controller_id=? AND client_id=?", (identity, "human-" + body.client_id)
            ).fetchone()
            payload = {"actor_id": user, **body.model_dump()}
            if old:
                if old["sha256"] != digest(encoded(payload)):
                    raise StoreError(409, "check replay differs from retained evidence")
                return {"id": old["id"]}
            if flow["version"] != body.expected_version or flow["status"] != "waiting" or flow["phase"] != "acceptance":
                raise StoreError(409, "read the waiting controller before checking its result")
            if flow["result_json"] != encoded(body.subject.model_dump()):
                raise StoreError(409, "check subject is not the current exact candidate result")
            spec = WorkSpecification.model_validate_json(
                db.execute("SELECT body_json FROM work_tasks WHERE id=?", (flow["target_task"],)).fetchone()[0]
            )
            criterion = next((item for item in spec.acceptance if item.id == body.criterion_id), None)
            if criterion is None or criterion.check != body.check:
                raise StoreError(403, "check kind does not match this criterion")
            for ref in [body.subject, *body.evidence]:
                self.work._input(db, org, project, ref)
            saved = self._record(
                db,
                flow,
                "human-" + body.client_id,
                body.criterion_id,
                body.check,
                body.subject.model_dump(),
                body.passed,
                "human_attestation",
                payload,
                user,
            )
            if not body.passed:
                root = self.work._program(db, flow["created_by"], org, project, program, write=True)
                self._repair(db, root, flow, [body.subject, *body.evidence], body.explanation)
            else:
                self._change(db, flow, status="running")
                wake(db, program)
            return {"id": saved}

    def _accept(self, db, root, flow):
        missing = self._missing(db, flow)
        if missing:
            self._change(
                db,
                flow,
                phase="acceptance",
                status="waiting",
                current_job=None,
                error="Required checks remain; reviewer approval is not a substitute.",
            )
            return
        for task in {flow["candidate_task"], flow["target_task"]}:
            db.execute("UPDATE work_tasks SET status='complete',updated_at=? WHERE id=?", (int(time.time()), task))
        self._change(db, flow, status="complete", phase="complete", current_job=None, error=None)
        self._finalize(db, root)
        wake(db, root["id"])

    @staticmethod
    def _finalize(db, root):
        if not db.execute(
            "SELECT 1 FROM work_controllers WHERE program_id=? AND root_controller=1 AND status='complete' AND root_revision=?",
            (root["id"], root["head_revision"]),
        ).fetchone():
            return
        if (
            db.execute(
                "SELECT 1 FROM work_controllers WHERE program_id=? AND status NOT IN ('complete','cancelled') LIMIT 1", (root["id"],)
            ).fetchone()
            or db.execute(
                "SELECT 1 FROM work_jobs WHERE program_id=? AND status IN ('queued','leased','outcome_unknown') LIMIT 1", (root["id"],)
            ).fetchone()
        ):
            return
        db.execute("UPDATE work_programs SET status='complete',updated_at=? WHERE id=?", (int(time.time()), root["id"]))

    def _children(self, db, root, flow):
        if db.execute("SELECT 1 FROM work_integration_state WHERE controller_id=? AND round=?", (flow["id"], flow["round"])).fetchone():
            self._integrate(db, root, flow)
            return
        children = db.execute(
            "SELECT t.* FROM work_tasks t WHERE t.parent_id=? "
            "AND NOT EXISTS(SELECT 1 FROM work_controller_steps s WHERE s.task_id=t.id AND s.controller_id=?) "
            "AND NOT EXISTS(SELECT 1 FROM work_controllers c WHERE c.target_task=t.id) ORDER BY t.id LIMIT 100",
            (flow["candidate_task"], flow["id"]),
        ).fetchall()
        for child in children:
            self._attach(db, root, flow["created_by"], child, "adopt-" + child["id"])
        counts = db.execute(
            "SELECT COUNT(*) AS total,COALESCE(SUM(CASE WHEN c.status='complete' THEN 1 ELSE 0 END),0) AS done "
            "FROM work_tasks t LEFT JOIN work_controllers c ON c.target_task=t.id WHERE t.parent_id=? "
            "AND NOT EXISTS(SELECT 1 FROM work_controller_steps s WHERE s.task_id=t.id AND s.controller_id=?) "
            "AND NOT EXISTS(SELECT 1 FROM work_integration_groups g JOIN work_controllers b ON b.id=g.child_controller "
            "WHERE g.controller_id=? AND b.target_task=t.id)",
            (flow["candidate_task"], flow["id"], flow["id"]),
        ).fetchone()
        if counts["total"] != counts["done"]:
            self._change(db, flow, phase="children", current_job=None)
            if len(children) == 100:
                wake(db, root["id"])
            return
        self._integrate(db, root, flow)

    def _integrate(self, db, root, flow):
        from .work_integration import advance

        refs = advance(self, db, root, flow)
        if refs is None:
            self._change(db, flow, phase="children", current_job=None)
            return
        refs.insert(0, VersionReference.model_validate(json.loads(flow["result_json"])))
        integration = self._make_task(
            db,
            root,
            flow,
            "integration",
            "coordinator",
            refs,
            "Integrate the exact accepted child results into the original objective. Preserve uncertainty and evidence.",
        )
        self._step(db, root, flow, "integration", integration)

    def _advance(self, db, root, flow):
        if flow["root_revision"] != root["head_revision"]:
            self._change(db, flow, status="blocked", error="Program specification changed; cancel/restart against the new revision.")
            return
        if flow["phase"] == "acceptance":
            self._accept(db, root, flow)
            return
        if flow["phase"] == "children":
            self._children(db, root, flow)
            return
        job = db.execute("SELECT * FROM work_jobs WHERE id=?", (flow["current_job"],)).fetchone()
        if job is None or job["status"] in {"queued", "leased"}:
            return
        if job["status"] != "complete":
            if job["status"] == "failed":
                call = db.execute(
                    "SELECT error_code,usage_json FROM work_provider_calls c JOIN work_provider_call_sequences s ON s.call_id=c.id "
                    "WHERE job_id=? AND fence=? ORDER BY s.sequence DESC LIMIT 1",
                    (job["id"], job["fence"]),
                ).fetchone()
                uncertain = db.execute(
                    "SELECT 1 FROM work_effects WHERE job_id=? AND state IN ('intent','unknown') LIMIT 1", (job["id"],)
                ).fetchone()
                if (
                    call
                    and call["usage_json"]
                    and not uncertain
                    and call["error_code"]
                    in {
                        "provider_network",
                        "provider_protocol",
                        "provider_incomplete",
                        "provider_stream_error",
                    }
                ):
                    previous = self._result(db, job)
                    assignment = WorkSpecification.model_validate_json(
                        db.execute("SELECT body_json FROM work_tasks WHERE id=?", (job["task_id"],)).fetchone()[0]
                    )
                    self._change(db, flow, round=flow["round"] + 1)
                    refs = [
                        *assignment.inputs,
                        self._task_ref(db.execute("SELECT * FROM work_tasks WHERE id=?", (flow["target_task"],)).fetchone()),
                    ]
                    if previous:
                        refs.append(VersionReference.model_validate(previous["reference"]))
                    replacement = self._make_task(db, root, flow, "provider-recovery", assignment.role, refs, assignment.objective)
                    if flow["phase"] == "working":
                        self._change(db, flow, candidate_task=replacement["id"])
                    self._step(db, root, flow, flow["phase"], replacement)
                    return
            self._change(
                db, flow, status="blocked", error="Assignment failed, cancelled or has an unknown outcome; inspect before retrying."
            )
            return
        result = self._result(db, job)
        if result is None:
            self._change(db, flow, status="blocked", error="Completed assignment has no retained result receipt.")
            return
        if flow["phase"] == "specification":
            verdict = self._verdict(db, root, flow, job, result)
            if verdict is None:
                return
            if verdict.decision != "approve":
                self._change(db, flow, status="blocked", error="Specification review requires human revision: " + verdict.summary)
                return
            task = db.execute("SELECT * FROM work_tasks WHERE id=?", (flow["candidate_task"],)).fetchone()
            self._step(db, root, flow, "working", task)
            return
        if flow["phase"] in {"working", "integration"}:
            changes = {"result_json": encoded(result["reference"])}
            if flow["phase"] == "integration":
                changes["candidate_task"] = job["task_id"]
            self._change(db, flow, **changes)
            if flow["controller"] == "parallel_integrate" and flow["phase"] == "working":
                self._children(db, root, flow)
                return
            phase = "checking" if flow["controller"] == "build_check_repair" and flow["phase"] == "working" else "review"
            task = self._make_task(
                db,
                root,
                flow,
                phase,
                "executor" if phase == "checking" else "critic",
                [
                    VersionReference.model_validate(result["reference"]),
                    self._task_ref(db.execute("SELECT * FROM work_tasks WHERE id=?", (flow["target_task"],)).fetchone()),
                ],
                "Check the pinned candidate against every original criterion. Use approved adapters where available. "
                "Return the review JSON; never claim tests/proofs were checked without retained verifier evidence.",
            )
            self._step(db, root, flow, phase, task)
            return
        verdict = self._verdict(db, root, flow, job, result)
        if verdict is None:
            return
        if verdict.decision == "blocked":
            self._change(db, flow, status="blocked", error=verdict.summary)
            return
        if verdict.decision == "revise":
            self._repair(
                db,
                root,
                flow,
                [VersionReference.model_validate(json.loads(flow["result_json"])), VersionReference.model_validate(result["reference"])],
                verdict.summary,
            )
            return
        if flow["phase"] == "checking":
            task = self._make_task(
                db,
                root,
                flow,
                "review",
                "critic",
                [VersionReference.model_validate(json.loads(flow["result_json"])), VersionReference.model_validate(result["reference"])],
                "Independently validate the candidate and checking result against the original criteria. Return review JSON.",
            )
            self._step(db, root, flow, "review", task)
            return
        specification = WorkSpecification.model_validate_json(
            db.execute("SELECT body_json FROM work_tasks WHERE id=?", (flow["target_task"],)).fetchone()[0]
        )
        for criterion in specification.acceptance:
            if criterion.check == "llm_review":
                self._record(
                    db,
                    flow,
                    "review-" + job["id"] + "-" + digest(criterion.id)[:24],
                    criterion.id,
                    "llm_review",
                    json.loads(flow["result_json"]),
                    True,
                    "server_model_review",
                    {
                        "review": result["reference"],
                        "verdict": verdict.model_dump(),
                        "provider_call_id": self._last_call(db, job)["id"],
                    },
                    None,
                )
        self._accept(db, root, flow)

    def tick(self, program):
        with self.store.transaction(write=True, scope=f"work-program:{program}") as db:
            root = db.execute("SELECT * FROM work_programs WHERE id=?", (program,)).fetchone()
            db.execute("DELETE FROM work_controller_wakes WHERE program_id=?", (program,))
            if root is None or root["status"] != "active":
                return 0
            rows = db.execute(
                "SELECT * FROM work_controllers WHERE program_id=? AND status='running' ORDER BY updated_at,id LIMIT 51", (program,)
            ).fetchall()
            for row in rows[:50]:
                flow = dict(row)
                db.execute("SAVEPOINT controller_transition")
                try:
                    self.work._program(db, flow["created_by"], root["organization_id"], root["project_id"], program, write=True)
                    self._advance(db, dict(root), flow)
                except StoreError as exc:
                    db.execute("ROLLBACK TO SAVEPOINT controller_transition")
                    flow = dict(row)
                    if exc.status not in {403, 404, 409}:
                        raise
                    self._change(
                        db, flow, status="blocked", error="Current authority, resource envelope or work state refused continuation."
                    )
                db.execute("RELEASE SAVEPOINT controller_transition")
                db.execute("UPDATE work_controllers SET updated_at=? WHERE id=?", (int(time.time()), flow["id"]))
            if len(rows) > 50:
                wake(db, program)
            self._finalize(db, dict(root))
            return min(len(rows), 50)

    def tick_due(self, stopped=lambda: False):
        with self.store.transaction() as db:
            programs = [
                row[0] for row in db.execute("SELECT program_id FROM work_controller_wakes ORDER BY updated_at,program_id LIMIT 50")
            ]
        for program in programs:
            if stopped():
                break
            self.tick(program)

    def history(self, user, org, project, program, identity, after=0, limit=50):
        if after < 0 or not 1 <= limit <= 100:
            raise StoreError(400, "invalid controller history page")
        with self.store.transaction() as db:
            self.work._program(db, user, org, project, program)
            if not db.execute("SELECT 1 FROM work_controllers WHERE id=? AND program_id=?", (identity, program)).fetchone():
                raise StoreError(404, "controller unavailable")
            rows = db.execute(
                "SELECT * FROM work_controller_steps WHERE controller_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                (identity, after, limit + 1),
            ).fetchall()
            return {"items": [dict(row) for row in rows[:limit]], "next": rows[limit - 1]["sequence"] if len(rows) > limit else None}

    def cancel(self, user, org, project, program, identity, body):
        with self.store.transaction(write=True, scope=f"work-program:{program}") as db:
            self.work._program(db, user, org, project, program, write=True)
            flow = _row(db.execute("SELECT * FROM work_controllers WHERE id=? AND program_id=?", (identity, program)).fetchone())
            if flow["version"] != body.expected_version or flow["status"] in {"complete", "cancelled"}:
                raise StoreError(409, "read the unfinished controller before cancelling")
            scope = "SELECT task_id FROM work_task_ancestors WHERE ancestor_id=?"
            db.execute(
                "UPDATE work_jobs SET cancel_requested=1 WHERE status='leased' AND task_id IN (" + scope + ")", (flow["target_task"],)
            )
            db.execute(
                "UPDATE work_jobs SET status='cancelled',finished_at=? WHERE status='queued' AND task_id IN (" + scope + ")",
                (int(time.time()), flow["target_task"]),
            )
            db.execute(
                "UPDATE work_tasks SET status='cancelled',updated_at=? WHERE status IN ('pending','ready') AND id IN (" + scope + ")",
                (int(time.time()), flow["target_task"]),
            )
            db.execute(
                "UPDATE work_controllers SET status='cancelled',version=version+1,error=?,updated_at=? "
                "WHERE id<>? AND status NOT IN ('complete','cancelled') AND target_task IN (" + scope + ")",
                (body.reason, int(time.time()), identity, flow["target_task"]),
            )
            self._change(db, flow, status="cancelled", error=body.reason)
            return self._view(db, flow)

    def resume(self, user, org, project, program, identity, body):
        with self.store.transaction(write=True, scope=f"work-program:{program}") as db:
            root = self.work._program(db, user, org, project, program, write=True)
            flow = _row(db.execute("SELECT * FROM work_controllers WHERE id=? AND program_id=?", (identity, program)).fetchone())
            if flow["version"] != body.expected_version or flow["status"] != "blocked":
                raise StoreError(409, "read the blocked controller before resuming")
            if root["status"] != "active" or root["head_revision"] != flow["root_revision"]:
                raise StoreError(409, "resume requires the pinned active program revision")
            prior = db.execute("SELECT task_id FROM work_jobs WHERE id=?", (flow["current_job"],)).fetchone()
            if prior is None:
                raise StoreError(409, "this controller requires cancellation and a new versioned assignment")
            job = db.execute(
                "SELECT j.* FROM work_jobs j JOIN work_tasks t ON t.id=j.task_id "
                "WHERE j.task_id=? AND j.task_epoch=t.epoch ORDER BY j.sequence DESC LIMIT 1",
                (prior["task_id"],),
            ).fetchone()
            if job is None or job["id"] == flow["current_job"] or job["status"] not in {"queued", "leased", "complete"}:
                raise StoreError(409, "reconcile, explicitly reopen and queue the failed task before resuming its controller")
            db.execute(
                "INSERT INTO work_controller_steps(controller_id,phase,round,task_id,job_id,created_at) VALUES(?,?,?,?,?,?)",
                (identity, flow["phase"], flow["round"], job["task_id"], job["id"], int(time.time())),
            )
            self._change(db, flow, status="running", current_job=job["id"], error=None)
            return self._view(db, flow)
