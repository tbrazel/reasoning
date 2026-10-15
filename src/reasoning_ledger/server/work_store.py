"""Authenticated immutable work specifications and indexed execution frontiers."""

from __future__ import annotations

import hashlib
import json
import time

from .research_context import encoded
from .store import StoreError, _id, _row
from .work_contracts import KnowledgeRelation, TaskCreate, WorkCreate, WorkRevision, WorkSpecification
from .work_prompts import pin


class WorkStore:
    def __init__(self, store):
        self.store = store

    def _program(self, db, user, org, project, program, *, write=False):
        self.store._project_access(db, user, org, project, write=write)
        return _row(
            db.execute("SELECT * FROM work_programs WHERE organization_id=? AND project_id=? AND id=?", (org, project, program)).fetchone()
        )

    @staticmethod
    def _view(db, row, revision=None):
        version = _row(
            db.execute(
                "SELECT revision,body_json,sha256,reason,created_by,created_at FROM work_specifications WHERE program_id=? AND revision=?",
                (row["id"], revision or row["head_revision"]),
            ).fetchone()
        )
        version["specification"] = json.loads(version.pop("body_json"))
        return row | {"version": version}

    @staticmethod
    def _task_view(row):
        result = dict(row)
        result["specification"] = json.loads(result.pop("body_json"))
        return result

    @staticmethod
    def _event(db, org, program, user, event, task=None):
        db.execute(
            "INSERT INTO work_events(organization_id,program_id,task_id,actor_id,event_json,created_at) VALUES(?,?,?,?,?,?)",
            (org, program, task, user, encoded(event), int(time.time())),
        )
        from .work_controller_schema import wake

        wake(db, program)

    @staticmethod
    def _input(db, org, project, ref):
        """Resolve exact project-safe revisions, never mutable head aliases.

        A project program cannot absorb a narrower topic or another project's
        private input, even when its human operator can read both projects.
        """
        if ref.kind == "work_spec":
            row = db.execute(
                "SELECT v.sha256,p.project_id FROM work_specifications v JOIN work_programs p ON p.id=v.program_id "
                "WHERE p.organization_id=? AND p.id=? AND v.revision=?",
                (org, ref.id, ref.revision),
            ).fetchone()
        elif ref.kind == "work_task":
            row = db.execute(
                "SELECT t.sha256,p.project_id FROM work_tasks t JOIN work_programs p ON p.id=t.program_id "
                "WHERE p.organization_id=? AND t.id=? AND ?=1",
                (org, ref.id, ref.revision),
            ).fetchone()
        elif ref.kind == "work_result":
            row = db.execute(
                "SELECT r.sha256,p.project_id FROM work_results r JOIN work_jobs j ON j.id=r.job_id "
                "JOIN work_programs p ON p.id=j.program_id WHERE p.organization_id=? AND r.job_id=? AND r.fence=?",
                (org, ref.id, ref.revision),
            ).fetchone()
        elif ref.kind == "research_artifact":
            row = db.execute(
                "SELECT v.sha256,a.project_id FROM research_artifact_versions v JOIN research_artifacts a ON a.id=v.artifact_id "
                "WHERE a.organization_id=? AND a.id=? AND v.revision=?",
                (org, ref.id, ref.revision),
            ).fetchone()
        elif ref.kind == "compute_result":
            row = db.execute(
                "SELECT sha256,project_id FROM work_compute_receipts WHERE organization_id=? AND compute_job_id=? AND ?=1",
                (org, ref.id, ref.revision),
            ).fetchone()
        elif ref.kind == "source_snapshot":
            row = db.execute(
                "SELECT text_digest AS sha256,project_id FROM work_source_captures WHERE organization_id=? AND id=? AND ?=1",
                (org, ref.id, ref.revision),
            ).fetchone()
        elif ref.kind == "knowledge":
            row = db.execute(
                "SELECT v.title,v.body,v.state,e.project_id,e.topic_id FROM knowledge_versions v "
                "JOIN knowledge_entries e ON e.id=v.entry_id AND e.organization_id=v.organization_id "
                "WHERE e.organization_id=? AND e.id=? AND v.revision=? "
                "AND NOT EXISTS (SELECT 1 FROM blog_deletions deleted WHERE deleted.organization_id=e.organization_id "
                "AND deleted.entry_id=e.id)",
                (org, ref.id, ref.revision),
            ).fetchone()
            if row:
                row = dict(row) | {"body_json": encoded({key: row[key] for key in ("title", "body", "state")})}
        else:
            row = db.execute(
                "SELECT body_json,project_id,topic_id FROM mathematical_records "
                "WHERE organization_id=? AND path=? AND record_type LIKE '%-revision'",
                (org, ref.id),
            ).fetchone()
            if row and json.loads(row["body_json"]).get("revision_id") != ref.revision:
                row = None
        row = dict(row) if row is not None else None
        if row is None or row["project_id"] not in {None, project} or row.get("topic_id") is not None:
            raise StoreError(404, "input version unavailable in this project")
        actual = row["sha256"] if "sha256" in row else hashlib.sha256(row["body_json"].encode("utf-8")).hexdigest()
        if actual != ref.sha256:
            raise StoreError(409, "input version digest does not match")

    @classmethod
    def _provenance(cls, db, org, project, program, kind, identity, revision, inputs):
        for ref in inputs:
            cls._input(db, org, project, ref)
            db.execute(
                "INSERT INTO work_provenance VALUES(?,?,?,?,?,?,?,?,?)",
                (org, program, kind, identity, revision, ref.kind, ref.id, str(ref.revision), ref.sha256),
            )

    @staticmethod
    def _policy(db, org, specification):
        policy = db.execute("SELECT model_policy_json,compute_policy_json FROM organizations WHERE id=?", (org,)).fetchone()
        resources = specification.resources
        if not set(resources.allowed_images) <= set(json.loads(policy["compute_policy_json"]).get("allowed_images", [])):
            raise StoreError(403, "work image authorization exceeds organization policy")

    def create(self, user, org, project, body: WorkCreate):
        with self.store.transaction(write=True, scope=f"organization-work:{org}") as db:
            self.store._project_access(db, user, org, project, write=True)
            old = db.execute(
                "SELECT * FROM work_programs WHERE organization_id=? AND owned_by=? AND client_id=?", (org, user, body.client_id)
            ).fetchone()
            if old:
                original = self._view(db, dict(old), 1)
                if old["project_id"] != project or original["version"]["sha256"] != body.specification.digest():
                    raise StoreError(409, "work request ID was used for different input")
                return self._view(db, dict(old))
            self._policy(db, org, body.specification)
            identity, now = _id(), int(time.time())
            db.execute(
                "INSERT INTO work_programs(id,organization_id,project_id,owned_by,client_id,head_revision,created_at,updated_at) "
                "VALUES(?,?,?,?,?,1,?,?)",
                (identity, org, project, user, body.client_id, now, now),
            )
            db.execute(
                "INSERT INTO work_specifications VALUES(?,?,?,?,?,?,?,?)",
                (org, identity, 1, body.specification.canonical(), body.specification.digest(), "Initial specification", user, now),
            )
            self._provenance(db, org, project, identity, "program", identity, 1, body.specification.inputs)
            db.execute("UPDATE work_programs SET execution_slots=? WHERE id=?", (body.specification.resources.max_concurrency, identity))
            self._event(db, org, identity, user, {"type": "created", "revision": 1})
            self.store._audit(db, org, user, "work.create", identity)
            return self._view(db, self._program(db, user, org, project, identity))

    def read(self, user, org, project, program, revision=None):
        with self.store.transaction() as db:
            return self._view(db, self._program(db, user, org, project, program), revision)

    def list(self, user, org, project, *, after="", limit=50):
        if not 1 <= limit <= 100:
            raise StoreError(400, "page size must be between 1 and 100")
        with self.store.transaction() as db:
            self.store._project_access(db, user, org, project)
            rows = list(
                db.execute(
                    "SELECT p.id,p.status,p.head_revision,p.created_at,p.updated_at,"
                    "substr(json_extract(v.body_json,'$.objective'),1,240) AS objective "
                    "FROM work_programs p JOIN work_specifications v ON v.program_id=p.id AND v.revision=p.head_revision "
                    "WHERE p.organization_id=? AND p.project_id=? AND p.id>? ORDER BY p.id LIMIT ?",
                    (org, project, after, limit + 1),
                )
            )
            return {"items": [dict(row) for row in rows[:limit]], "next": rows[limit - 1]["id"] if len(rows) > limit else None}

    def revise(self, user, org, project, program, body: WorkRevision):
        with self.store.transaction(write=True, scope=f"work-program:{program}") as db:
            row = self._program(db, user, org, project, program, write=True)
            if row["head_revision"] != body.expected_revision:
                retained = db.execute(
                    "SELECT sha256,reason,created_by FROM work_specifications WHERE program_id=? AND revision=?",
                    (program, body.expected_revision + 1),
                ).fetchone()
                if retained and (retained["sha256"], retained["reason"], retained["created_by"]) == (
                    body.specification.digest(), body.reason, user,
                ):
                    return self._view(db, row)
                raise StoreError(409, "work specification changed; read its current revision")
            if row["status"] in {"complete", "cancelled"}:
                raise StoreError(409, "reopen work before revising its specification")
            self._policy(db, org, body.specification)
            revision, now = row["head_revision"] + 1, int(time.time())
            db.execute(
                "INSERT INTO work_specifications VALUES(?,?,?,?,?,?,?,?)",
                (org, program, revision, body.specification.canonical(), body.specification.digest(), body.reason, user, now),
            )
            self._provenance(db, org, project, program, "program", program, revision, body.specification.inputs)
            changed = db.execute(
                "UPDATE work_programs SET head_revision=?,updated_at=?,execution_slots=? WHERE id=? AND head_revision=?",
                (revision, now, body.specification.resources.max_concurrency, program, body.expected_revision),
            )
            if changed.rowcount != 1:
                raise StoreError(409, "work specification changed concurrently")
            self._event(db, org, program, user, {"type": "revised", "revision": revision, "reason": body.reason})
            self.store._audit(db, org, user, "work.revise", program)
            return self._view(
                db, row | {"head_revision": revision, "updated_at": now, "execution_slots": body.specification.resources.max_concurrency}
            )

    def task_create(self, user, org, project, program, body: TaskCreate):
        with self.store.transaction(write=True, scope=f"work-program:{program}") as db:
            return self._task_create(db, user, org, project, program, body)

    def _task_create(self, db, user, org, project, program, body: TaskCreate, *, controller_generated=False):
        """Caller owns the program transaction, including any delegation receipt."""
        root = self._program(db, user, org, project, program, write=True)
        old = db.execute(
            "SELECT * FROM work_tasks WHERE program_id=? AND requested_by=? AND client_id=?", (program, user, body.client_id)
        ).fetchone()
        if old:
            dependencies = {row[0] for row in db.execute("SELECT prerequisite_id FROM work_dependencies WHERE task_id=?", (old["id"],))}
            if (
                old["sha256"] != body.specification.digest()
                or old["parent_id"] != body.parent_id
                or old["root_revision"] != body.expected_root_revision
                or dependencies != set(body.depends_on)
            ):
                raise StoreError(409, "task request ID was used for different input")
            return self._task_view(old)
        if root["head_revision"] != body.expected_root_revision:
            raise StoreError(409, "read the current root specification before assigning work")
        if root["status"] in {"complete", "cancelled"}:
            raise StoreError(409, "finished work cannot receive new assignments")
        self._policy(db, org, body.specification)
        root_spec = WorkSpecification.model_validate(self._view(db, root)["version"]["specification"])
        envelope = root_spec.resources
        if body.parent_id:
            parent = _row(
                db.execute(
                    "SELECT * FROM work_tasks WHERE organization_id=? AND program_id=? AND id=?", (org, program, body.parent_id)
                ).fetchone()
            )
            if not controller_generated and db.execute(
                "SELECT 1 FROM work_integration_state WHERE parent_task_id=?", (body.parent_id,)
            ).fetchone():
                raise StoreError(409, "this child set is pinned for integration; revise the controller before assigning another branch")
            if parent["status"] in {"complete", "cancelled", "outcome_unknown"}:
                raise StoreError(409, "this parent cannot delegate new work")
            parent_spec = WorkSpecification.model_validate_json(parent["body_json"])
            if not parent_spec.resources.contains(body.specification.resources):
                raise StoreError(403, "child authorization exceeds its parent")
        if not envelope.contains(body.specification.resources):
            raise StoreError(403, "task authorization exceeds the shared root")
        for dependency in body.depends_on:
            if not db.execute(
                "SELECT 1 FROM work_tasks WHERE organization_id=? AND program_id=? AND id=?", (org, program, dependency)
            ).fetchone():
                raise StoreError(404, "dependency unavailable in this work program")
        identity, now = _id(), int(time.time())
        spec = body.specification
        db.execute(
            "INSERT INTO work_tasks(id,organization_id,program_id,parent_id,client_id,requested_by,root_revision,body_json,"
            "sha256,role,kind,controller,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                identity,
                org,
                program,
                body.parent_id,
                body.client_id,
                user,
                body.expected_root_revision,
                spec.canonical(),
                spec.digest(),
                spec.role,
                spec.kind,
                spec.controller,
                now,
                now,
            ),
        )
        for dependency in body.depends_on:
            db.execute("INSERT INTO work_dependencies VALUES(?,?,?,?)", (org, program, identity, dependency))
        db.execute("INSERT INTO work_task_ancestors VALUES(?,?,?,?,0)", (org, program, identity, identity))
        if body.parent_id:
            db.execute(
                "INSERT INTO work_task_ancestors SELECT organization_id,program_id,?,ancestor_id,depth+1 "
                "FROM work_task_ancestors WHERE task_id=?",
                (identity, body.parent_id),
            )
        self._provenance(db, org, project, program, "task", identity, 1, spec.inputs)
        pin(db, org, program, identity, spec)
        self._event(db, org, program, user, {"type": "assigned", "root_revision": body.expected_root_revision}, identity)
        return self._task_view(db.execute("SELECT * FROM work_tasks WHERE id=?", (identity,)).fetchone())

    def tasks(self, user, org, project, program, *, after="", limit=50, status=None):
        if not 1 <= limit <= 100:
            raise StoreError(400, "page size must be between 1 and 100")
        with self.store.transaction() as db:
            self._program(db, user, org, project, program)
            parameters = [org, program, after]
            condition = ""
            if status:
                condition = " AND status=?"
                parameters.append(status)
            parameters.append(limit + 1)
            rows = list(
                db.execute(
                    "SELECT id,parent_id,role,kind,controller,status,epoch,root_revision,sha256,created_at,updated_at "
                    "FROM work_tasks WHERE organization_id=? AND program_id=? AND id>?" + condition + " ORDER BY id LIMIT ?",
                    parameters,
                )
            )
            return {"items": [dict(row) for row in rows[:limit]], "next": rows[limit - 1]["id"] if len(rows) > limit else None}

    def task_read(self, user, org, project, program, task):
        with self.store.transaction() as db:
            self._program(db, user, org, project, program)
            row = _row(
                db.execute("SELECT * FROM work_tasks WHERE organization_id=? AND program_id=? AND id=?", (org, program, task)).fetchone()
            )
            return self._task_view(row) | {
                "depends_on": [
                    item[0]
                    for item in db.execute(
                        "SELECT prerequisite_id FROM work_dependencies WHERE task_id=? ORDER BY prerequisite_id", (task,)
                    )
                ]
            }

    def relate(self, user, org, project, program, body: KnowledgeRelation):
        payload = encoded(body.model_dump(exclude={"client_id"}))
        checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self.store.transaction(write=True, scope=f"work-program:{program}") as db:
            self._program(db, user, org, project, program, write=True)
            old = db.execute(
                "SELECT * FROM work_knowledge_relations WHERE program_id=? AND created_by=? AND client_id=?",
                (program, user, body.client_id),
            ).fetchone()
            if old:
                if old["sha256"] != checksum:
                    raise StoreError(409, "relation request ID was used for different input")
                return self._relation_view(old)
            self._input(db, org, project, body.source)
            self._input(db, org, project, body.target)
            identity, now = _id(), int(time.time())
            db.execute(
                "INSERT INTO work_knowledge_relations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    org,
                    program,
                    user,
                    body.client_id,
                    payload,
                    checksum,
                    body.source.kind,
                    body.source.id,
                    str(body.source.revision),
                    body.target.kind,
                    body.target.id,
                    str(body.target.revision),
                    body.relation,
                    now,
                ),
            )
            refs = {(ref.kind, ref.id, ref.revision): ref for ref in (body.source, body.target)}
            self._provenance(db, org, project, program, "relation", identity, 1, refs.values())
            # These are knowledge annotations, not execution/proof dependencies.
            # In particular equivalence cycles are valid here.
            self._event(db, org, program, user, {"type": "knowledge_relation", "relation_id": identity})
            return self._relation_view(db.execute("SELECT * FROM work_knowledge_relations WHERE id=?", (identity,)).fetchone())

    @staticmethod
    def _relation_view(row):
        return {
            "id": row["id"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "sha256": row["sha256"],
            **json.loads(row["body_json"]),
        }

    def relations(self, user, org, project, program, *, kind, identity, revision, direction="out", after="", limit=50):
        if not 1 <= limit <= 100 or direction not in {"in", "out"}:
            raise StoreError(400, "invalid relationship page")
        column = "source" if direction == "out" else "target"
        with self.store.transaction() as db:
            self._program(db, user, org, project, program)
            rows = list(
                db.execute(
                    f"SELECT * FROM work_knowledge_relations WHERE program_id=? AND {column}_kind=? AND {column}_id=? "
                    f"AND {column}_revision=? AND id>? ORDER BY id LIMIT ?",
                    (program, kind, identity, str(revision), after, limit + 1),
                )
            )
            return {
                "items": [self._relation_view(row) for row in rows[:limit]],
                "next": rows[limit - 1]["id"] if len(rows) > limit else None,
            }
