"""Scoped transactional delegation, knowledge, memory, workspaces and compute."""

from __future__ import annotations

import json
import time
from functools import lru_cache

from .knowledge_store import KnowledgeStore
from .research_context import encoded
from .security import digest
from .store import StoreError, _row
from .work_contracts import TaskCreate, VersionReference, WorkSpecification
from .work_inputs import bind, require_assigned
from .work_job_contracts import JobEnqueue
from .work_tool_contracts import (
    ApproachWrite,
    BoundInputRead,
    ChildCancel,
    ChildPage,
    ChildRead,
    ComputeOutputRead,
    ComputeRead,
    ComputeSubmit,
    Delegate,
    KnowledgeSearch,
    SourceCheck,
    SourceFetch,
    SourceSearch,
    WorkspacePage,
    WorkspacePatch,
    WorkspaceRead,
    WorkspaceWrite,
)

MODELS = {
    "work_delegate": Delegate,
    "work_children": ChildPage,
    "work_child_read": ChildRead,
    "work_child_cancel": ChildCancel,
    "knowledge_search": KnowledgeSearch,
    "approach_write": ApproachWrite,
    "input_read": BoundInputRead,
    "source_search": SourceSearch,
    "source_fetch": SourceFetch,
    "source_check": SourceCheck,
    "workspace_list": WorkspacePage,
    "workspace_read": WorkspaceRead,
    "workspace_write": WorkspaceWrite,
    "workspace_patch": WorkspacePatch,
    "compute_submit": ComputeSubmit,
    "compute_read": ComputeRead,
    "compute_cancel": ComputeRead,
    "compute_output_read": ComputeOutputRead,
}
DESCRIPTIONS = {
    "work_delegate": "Queue a scoped child, including subordinate coordinators, with contained resources and pinned inputs.",
    "work_children": "Page direct child headers without reading their transcripts.",
    "work_child_read": "Inspect one direct child result and bind its exact output references to this assignment.",
    "work_child_cancel": "Request cancellation of one direct child and its descendants; running work must report its outcome.",
    "knowledge_search": "Search this project and organization-wide knowledge; bind a bounded page of exact source revisions.",
    "approach_write": "Save structured approach memory, with obstructions and reopening conditions, in the existing project ledger.",
    "input_read": "Read a bounded slice of an assigned or explicitly discovered exact input version.",
    "source_search": "Discover public Crossref/arXiv metadata and retain exact project bibliography entries; metadata is not full text.",
    "source_fetch": "Capture public HTTPS text into an immutable project source snapshot; credentials and private networks are denied.",
    "source_check": "Check an exact cited excerpt against a server-captured source; this is not a proof or truth certificate.",
    "workspace_list": "Page and bind exact file heads in this assignment's isolated persistent branch workspace.",
    "workspace_read": "Read and bind an exact bounded file revision from this branch workspace.",
    "workspace_write": "Save a branch file in the shared versioned repository; replacing content requires its current SHA-256.",
    "workspace_patch": "Apply ordered bounded character-range edits with a current SHA-256; old revisions and provenance remain.",
    "compute_submit": "Run an inline program or exact assigned file in an approved image under shared CPU authorization.",
    "compute_read": "Inspect this assignment's computation and bind its immutable server execution receipt when finished.",
    "compute_cancel": "Request cancellation of this assignment's computation without claiming that its container has stopped.",
    "compute_output_read": "Read bounded exact output bytes through an assigned receipt; files are untrusted data, not certificates.",
}


def _inline_schema(value, definitions):
    if isinstance(value, list):
        return [_inline_schema(item, definitions) for item in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        target = value["$ref"].removeprefix("#/$defs/")
        return _inline_schema(definitions[target] | {key: item for key, item in value.items() if key != "$ref"}, definitions)
    return {key: _inline_schema(item, definitions) for key, item in value.items()}


@lru_cache(maxsize=1)
def _catalog_json():
    result = []
    for name, model in MODELS.items():
        schema = model.model_json_schema()
        for field in ("attempt_id", "fence", "lease", "client_id"):
            schema["properties"].pop(field, None)
        schema["required"] = [field for field in schema.get("required", []) if field in schema["properties"]]
        schema = _inline_schema(schema, schema.pop("$defs", {}))
        result.append({"type": "function", "function": {"name": name, "description": DESCRIPTIONS[name], "parameters": schema}})
    return encoded(result)


def catalog():
    return json.loads(_catalog_json())


class WorkTools:
    def __init__(self, jobs, compute=None):
        self.jobs, self.store, self.work = jobs, jobs.store, jobs.work
        self.knowledge = KnowledgeStore(self.store)
        from .work_workspaces import WorkWorkspaces

        self.workspaces = WorkWorkspaces(self)
        from .work_compute import WorkCompute

        self.computations = WorkCompute(self, compute)

    def _admit(self, db, job, task, root, tool, *, counted=False, replay=False):
        root_spec = db.execute(
            "SELECT body_json FROM work_specifications WHERE program_id=? AND revision=?", (root["id"], root["head_revision"])
        ).fetchone()[0]
        scopes = [("program", root["id"], WorkSpecification.model_validate_json(root_spec).resources)]
        scopes += [
            ("task", row["id"], WorkSpecification.model_validate_json(row["body_json"]).resources)
            for row in db.execute(
                "SELECT t.id,t.body_json FROM work_task_ancestors a JOIN work_tasks t ON t.id=a.ancestor_id "
                "WHERE a.task_id=? ORDER BY a.depth",
                (task["id"],),
            ).fetchall()
        ]
        for kind, identity, limits in scopes:
            if tool not in limits.allowed_tools:
                raise StoreError(403, "local work tool is not authorized")
            if tool not in {"work_child_cancel", "compute_cancel"} and (root["status"] != "active" or job["cancel_requested"]):
                raise StoreError(409, "work program is paused or cancelled")
            account = db.execute("SELECT * FROM work_resource_accounts WHERE scope_kind=? AND scope_id=?", (kind, identity)).fetchone()
            if (
                not replay
                and tool not in {"work_child_cancel", "compute_cancel"}
                and (
                    limits.deadline_at is not None
                    and limits.deadline_at <= int(time.time())
                    or limits.max_tool_calls is not None
                    and (account["tools"] if account else 0) >= limits.max_tool_calls + int(counted)
                    or limits.budget_microusd is not None
                    and (account["microusd"] if account else 0) > limits.budget_microusd
                    or limits.cpu_seconds is not None
                    and (account["cpu_seconds"] if account else 0) > limits.cpu_seconds
                )
            ):
                raise StoreError(409, "shared work resources cannot authorize this tool")
        return scopes

    def execute(self, credential, job_id, tool, body):
        if tool not in MODELS or not isinstance(body, MODELS[tool]):
            raise StoreError(400, "unknown or invalid local work tool")
        if tool in {"source_search", "source_fetch"}:
            raise StoreError(400, "public retrieval requires the asynchronous source service")
        program = self.jobs._job_scope(credential, job_id)
        request = encoded(body.model_dump(exclude={"attempt_id", "fence", "lease", "client_id"}))
        checksum = digest(request)
        # Compute services serialize quota admission before locking the work
        # program. This is also the completion lock order; do not invert it.
        compute_state = tool in {"compute_submit", "compute_read", "compute_cancel"}
        scope = "legacy-writes" if compute_state else f"work-program:{program}"
        with self.store.transaction(write=True, scope=scope) as db:
            if compute_state:
                self.jobs._program_lock(self.store, db, program)
            job = self.jobs._lease(db, credential, job_id, body)
            task, root = self.jobs._authorized_task(db, job)
            if db.execute(
                "SELECT 1 FROM work_source_requests WHERE task_id=? AND task_epoch=? AND client_id=?",
                (task["id"], task["epoch"], body.client_id),
            ).fetchone():
                raise StoreError(409, "tool request ID belongs to a retained source request")
            old = db.execute(
                "SELECT * FROM work_tool_operations WHERE task_id=? AND task_epoch=? AND client_id=?",
                (task["id"], task["epoch"], body.client_id),
            ).fetchone()
            if old:
                if old["tool"] != tool or old["request_sha256"] != checksum:
                    raise StoreError(409, "local tool request ID was used for different input")
                self._admit(db, job, task, root, tool, replay=True)
                return {"result": json.loads(old["result_json"]), "sha256": old["result_sha256"], "replayed": True}
            scopes = self._admit(db, job, task, root, tool)
            result = getattr(self, "_" + tool)(db, job, task, root, body)
            payload = encoded(result)
            result_digest = digest(payload)
            db.execute(
                "INSERT INTO work_tool_operations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task["id"],
                    task["epoch"],
                    body.client_id,
                    job_id,
                    job["fence"],
                    tool,
                    checksum,
                    payload,
                    result_digest,
                    job["worker_id"],
                    int(time.time()),
                ),
            )
            for kind, identity, _ in scopes:
                db.execute(
                    "INSERT INTO work_resource_accounts(scope_kind,scope_id,tools) VALUES(?,?,1) "
                    "ON CONFLICT(scope_kind,scope_id) DO UPDATE SET tools=work_resource_accounts.tools+1",
                    (kind, identity),
                )
            self.work._event(
                db,
                task["organization_id"],
                program,
                None,
                {
                    "type": "local_tool",
                    "job_id": job_id,
                    "worker_id": job["worker_id"],
                    "tool": tool,
                    "request_sha256": checksum,
                    "result_sha256": result_digest,
                },
                task["id"],
            )
            return {"result": result, "sha256": result_digest, "replayed": False}

    def _work_delegate(self, db, job, task, root, body):
        for reference in body.specification.inputs:
            require_assigned(db, task, reference)
        for dependency in body.depends_on:
            if not db.execute(
                "SELECT 1 FROM work_tasks WHERE id=? AND parent_id=? AND program_id=?", (dependency, task["id"], root["id"])
            ).fetchone():
                raise StoreError(404, "dependency is not a direct child of this assignment")
        identity = "delegated-" + digest(f"{task['id']}:{task['epoch']}:{body.client_id}")
        request = TaskCreate(
            client_id=identity,
            expected_root_revision=body.expected_root_revision,
            parent_id=task["id"],
            specification=body.specification,
            depends_on=body.depends_on,
        )
        child = self.work._task_create(db, task["requested_by"], task["organization_id"], root["project_id"], root["id"], request)
        queued = self.jobs._enqueue(
            db,
            task["requested_by"],
            task["organization_id"],
            root["project_id"],
            root["id"],
            child["id"],
            JobEnqueue(expected_epoch=child["epoch"]),
        )
        return {"task": child, "job": queued}

    @staticmethod
    def _work_children(db, job, task, root, body):
        rows = db.execute(
            "SELECT id,role,kind,status,epoch,sha256,updated_at FROM work_tasks "
            "WHERE program_id=? AND parent_id=? AND id>? ORDER BY id LIMIT ?",
            (root["id"], task["id"], body.after, body.limit + 1),
        ).fetchall()
        return {"items": [dict(row) for row in rows[: body.limit]], "next": rows[body.limit - 1]["id"] if len(rows) > body.limit else None}

    @staticmethod
    def _child(db, task, root, child):
        return _row(
            db.execute("SELECT * FROM work_tasks WHERE program_id=? AND parent_id=? AND id=?", (root["id"], task["id"], child)).fetchone()
        )

    def _work_child_read(self, db, job, task, root, body):
        child = self._child(db, task, root, body.child_id)
        latest = db.execute(
            "SELECT j.*,r.body_json AS result_json,r.sha256 AS result_sha256 FROM work_jobs j "
            "LEFT JOIN work_results r ON r.job_id=j.id AND r.fence=j.fence WHERE j.task_id=? AND j.task_epoch=?",
            (child["id"], child["epoch"]),
        ).fetchone()
        result = json.loads(latest["result_json"]) if latest and latest["result_json"] else None
        if result:
            bind(
                db,
                self.work,
                task,
                root,
                VersionReference(kind="work_result", id=latest["id"], revision=latest["fence"], sha256=latest["result_sha256"]),
                "work_child_read",
            )
            for reference in result["outputs"]:
                bind(db, self.work, task, root, VersionReference.model_validate(reference), "work_child_read")
        return {
            "task": self.work._task_view(child),
            "job": self.jobs._view(latest) if latest else None,
            "result": result,
            "result_sha256": latest["result_sha256"] if latest else None,
            "result_reference": {"kind": "work_result", "id": latest["id"], "revision": latest["fence"], "sha256": latest["result_sha256"]}
            if result
            else None,
        }

    def _work_child_cancel(self, db, job, task, root, body):
        self._child(db, task, root, body.child_id)
        descendants = "SELECT task_id FROM work_task_ancestors WHERE ancestor_id=?"
        now = int(time.time())
        db.execute("UPDATE work_jobs SET cancel_requested=1 WHERE task_id IN (" + descendants + ") AND status='leased'", (body.child_id,))
        queued = db.execute(
            "UPDATE work_jobs SET status='cancelled',cancel_requested=1,finished_at=? WHERE task_id IN ("
            + descendants
            + ") AND status='queued'",
            (now, body.child_id),
        ).rowcount
        db.execute(
            "UPDATE work_tasks SET status='cancelled',updated_at=? WHERE id IN (" + descendants + ") AND status IN ('pending','ready')",
            (now, body.child_id),
        )
        self.work._event(
            db,
            task["organization_id"],
            root["id"],
            None,
            {"type": "branch_cancel", "child_id": body.child_id, "reason": body.reason, "worker_id": job["worker_id"]},
            task["id"],
        )
        return {"child_id": body.child_id, "cancel_requested": True, "queued_cancelled": queued}

    def _knowledge_search(self, db, job, task, root, body):
        conditions, parameters = (
            ["e.organization_id=?", "e.topic_id IS NULL", "(e.project_id=? OR e.project_id IS NULL)", "e.id>?",
             "NOT EXISTS (SELECT 1 FROM blog_deletions deleted WHERE deleted.organization_id=e.organization_id "
             "AND deleted.entry_id=e.id)"],
            [task["organization_id"], root["project_id"], body.after],
        )
        if body.kind:
            conditions.append("e.kind=?")
            parameters.append(body.kind)
        if body.query.strip():
            conditions.append("(v.title LIKE ? ESCAPE '\\' OR v.body LIKE ? ESCAPE '\\')")
            query = "%" + body.query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            parameters.extend([query, query])
        rows = db.execute(
            "SELECT e.id,e.kind,v.title,v.body,v.state,v.revision FROM knowledge_entries e JOIN knowledge_versions v "
            "ON v.entry_id=e.id AND v.revision=e.head_revision WHERE " + " AND ".join(conditions) + " ORDER BY e.id LIMIT ?",
            (*parameters, body.limit + 1),
        ).fetchall()
        items = []
        for row in rows[: body.limit]:
            reference = VersionReference(
                kind="knowledge",
                id=row["id"],
                revision=row["revision"],
                sha256=digest(encoded({key: row[key] for key in ("title", "body", "state")})),
            )
            bind(db, self.work, task, root, reference, "knowledge_search")
            items.append(
                {
                    "reference": reference.model_dump(),
                    "kind": row["kind"],
                    "title": row["title"],
                    "state": row["state"],
                    "preview": row["body"][:300],
                }
            )
        return {"items": items, "next": rows[body.limit - 1]["id"] if len(rows) > body.limit else None}

    def _approach_write(self, db, job, task, root, body):
        for reference in body.evidence:
            require_assigned(db, task, reference)
            self.work._input(db, task["organization_id"], root["project_id"], reference)
        if body.entry_id:
            prior = self.knowledge._view(db, task["organization_id"], body.entry_id)
            if prior["project_id"] != root["project_id"] or prior["topic_id"] is not None or prior["kind"] != "ledger":
                raise StoreError(404, "approach memory is unavailable in this project")
            try:
                recognized = json.loads(prior["body"]).get("schema_type") == "work_approach"
            except (ValueError, AttributeError):
                recognized = False
            if not recognized:
                raise StoreError(403, "approach writes cannot replace unrelated knowledge")
        payload = body.model_dump(exclude={"attempt_id", "fence", "lease", "client_id", "entry_id", "expected_revision", "title"})
        payload.update(schema_type="work_approach", schema_version=1, task_id=task["id"], job_id=job["id"], fence=job["fence"])
        identity = "work-approach-" + digest(f"{task['id']}:{task['epoch']}:{body.client_id}")
        scope = {} if body.entry_id else {"scope_kind": "project", "scope_id": root["project_id"]}
        entry = self.knowledge._write(
            db,
            task["requested_by"],
            "bot",
            task["organization_id"],
            client_id=identity,
            kind="ledger",
            title=body.title,
            body=encoded(payload),
            state="note",
            **scope,
            entry_id=body.entry_id,
            expected_revision=body.expected_revision,
        )
        reference = VersionReference(
            kind="knowledge",
            id=entry["id"],
            revision=entry["revision"],
            sha256=digest(encoded({key: entry[key] for key in ("title", "body", "state")})),
        )
        bind(db, self.work, task, root, reference, "approach_write")
        self.work._provenance(
            db, task["organization_id"], root["project_id"], root["id"], "approach", entry["id"], entry["revision"], body.evidence
        )
        return {"reference": reference.model_dump(), "title": entry["title"], "state": entry["state"]}

    def _source_check(self, db, job, task, root, body):
        from .artifact_chunks import read_blob
        from .work_controllers import WorkControllers

        if task["role"] != "critic":
            raise StoreError(403, "source checks require an independent critic assignment")
        for ref in (body.subject, body.source):
            require_assigned(db, task, ref)
            self.work._input(db, task["organization_id"], root["project_id"], ref)
        candidate = json.loads(
            db.execute(
                "SELECT body_json FROM work_results WHERE job_id=? AND fence=?", (body.subject.id, body.subject.revision)
            ).fetchone()[0]
        )
        if body.statement not in candidate["summary"] or body.source.model_dump() not in candidate["outputs"]:
            raise StoreError(409, "source check must cite a statement and source retained by this candidate")
        excerpt = read_blob(db, task["organization_id"], root["project_id"], body.source.sha256, body.offset, len(body.quote))
        if excerpt["text"] != body.quote:
            raise StoreError(409, "quoted excerpt does not match the captured source at this offset")
        flow = _row(
            db.execute(
                "SELECT * FROM work_controllers WHERE program_id=? AND current_job=? AND result_json=?",
                (root["id"], job["id"], encoded(body.subject.model_dump())),
            ).fetchone()
        )
        target = WorkSpecification.model_validate_json(
            db.execute("SELECT body_json FROM work_tasks WHERE id=?", (flow["target_task"],)).fetchone()[0]
        )
        if not any(item.id == body.criterion_id and item.check == "source_check" for item in target.acceptance):
            raise StoreError(409, "criterion does not request source checking")
        payload = {
            "subject": body.subject.model_dump(),
            "source": body.source.model_dump(),
            "statement": body.statement,
            "quote": body.quote,
            "offset": body.offset,
            "scope": "exact_citation_fidelity_not_truth_or_proof",
            "worker_id": job["worker_id"],
        }
        check_id = WorkControllers(self.store)._record(
            db,
            flow,
            "source-" + body.client_id,
            body.criterion_id,
            "source_check",
            body.subject.model_dump(),
            True,
            "captured_source_excerpt",
            payload,
            task["requested_by"],
        )
        self.work._event(
            db, task["organization_id"], root["id"], None, {"type": "source_checked", "check_id": check_id, **payload}, task["id"]
        )
        return {"check_id": check_id, "passed": True, **payload}

    def _input_read(self, db, job, task, root, body):
        return self.jobs._input_read(db, job, task, root, body)

    def _workspace_list(self, db, job, task, root, body):
        return self.workspaces.page(db, job, task, root, body)

    def _workspace_read(self, db, job, task, root, body):
        return self.workspaces.read(db, job, task, root, body)

    def _workspace_write(self, db, job, task, root, body):
        return self.workspaces.write(db, job, task, root, body)

    def _workspace_patch(self, db, job, task, root, body):
        return self.workspaces.patch(db, job, task, root, body)

    def _compute_submit(self, db, job, task, root, body):
        return self.computations.submit(db, job, task, root, body)

    def _compute_read(self, db, job, task, root, body):
        return self.computations.read(db, job, task, root, body)

    def _compute_cancel(self, db, job, task, root, body):
        return self.computations.cancel(db, job, task, root, body)

    def _compute_output_read(self, db, job, task, root, body):
        return self.computations.output_read(db, job, task, root, body)
