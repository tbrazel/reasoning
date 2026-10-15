"""Scoped compute submission, shared reservations and server-owned execution evidence."""

import base64
import hashlib
import json
import time

from .artifact_chunks import full, read
from .compute_store import CHUNK_BYTES, TERMINAL
from .research_context import encoded
from .security import digest
from .store import StoreError, _id, _row
from .work_contracts import VersionReference, WorkSpecification
from .work_inputs import bind, require_assigned


def authorized(db, store, row):
    """Recheck the sponsoring work authorization without inventing a new lease."""
    link = db.execute("SELECT * FROM work_computations WHERE compute_job_id=?", (row["id"],)).fetchone()
    if link is None:
        return True
    from .work_jobs import WorkJobs

    job = _row(db.execute("SELECT * FROM work_jobs WHERE id=?", (link["job_id"],)).fetchone())
    try:
        worker = _row(db.execute("SELECT * FROM work_workers WHERE id=?", (job["worker_id"],)).fetchone())
        if worker["revoked_at"] is not None:
            return False
        store._require(db, worker["issued_by"], row["organization_id"])
        _, root = WorkJobs(store)._authorized_task(db, job)
        store._project_access(db, worker["issued_by"], row["organization_id"], root["project_id"], write=True)
        if root["status"] != "active" or job["cancel_requested"]:
            return False
        if job["status"] != "leased" or job["fence"] != link["fence"] or job["lease_expires_at"] <= int(time.time()):
            return False
        scopes = [("program", root["id"], db.execute("SELECT body_json FROM work_specifications WHERE program_id=? AND revision=?",
                                                      (root["id"], root["head_revision"])).fetchone()[0])]
        scopes += [("task", item["id"], item["body_json"]) for item in db.execute(
            "SELECT t.id,t.body_json FROM work_task_ancestors a JOIN work_tasks t ON t.id=a.ancestor_id WHERE a.task_id=?",
            (link["task_id"],),
        )]
        for kind, identity, body in scopes:
            limits = WorkSpecification.model_validate_json(body).resources
            if json.loads(row["image_json"])["id"] not in limits.allowed_images:
                return False
            if "compute_submit" not in limits.allowed_tools or limits.deadline_at is not None and limits.deadline_at <= int(time.time()):
                return False
            usage = WorkJobs._usage(db, kind, identity)
            if limits.cpu_seconds is not None and usage["cpu_seconds"] > limits.cpu_seconds:
                return False
            if limits.budget_microusd is not None and usage["microusd"] > limits.budget_microusd:
                return False
        return True
    except StoreError:
        return False


def cancel_due(compute, stopped=lambda: False):
    """Release abandoned queued requests without claiming stopped containers."""
    from .work_jobs import WorkJobs

    with compute.store.transaction() as db:
        rows = db.execute(
            "SELECT c.compute_job_id,c.program_id FROM work_computations c JOIN compute_jobs q ON q.id=c.compute_job_id "
            "JOIN work_jobs j ON j.id=c.job_id JOIN work_programs p ON p.id=c.program_id "
            "WHERE q.status='queued' AND (j.status<>'leased' OR j.cancel_requested=1 OR p.status='cancelled') "
            "ORDER BY q.created_at,q.id LIMIT 128"
        ).fetchall()
    for entry in rows:
        if stopped():
            return
        with compute.store.transaction(write=True) as db:
            WorkJobs._program_lock(compute.store, db, entry["program_id"])
            row = compute._job(db, db.execute("SELECT organization_id FROM compute_jobs WHERE id=?",
                                             (entry["compute_job_id"],)).fetchone()[0], entry["compute_job_id"])
            if row["status"] == "queued" and not authorized(db, compute.store, row):
                compute._request_cancel(db, row["organization_id"], row)


def settle(db, store, row):
    """Settle from the enrolled compute worker's retained outcome, never model text."""
    link = db.execute("SELECT * FROM work_computations WHERE compute_job_id=?", (row["id"],)).fetchone()
    if link is None or row["status"] not in TERMINAL:
        return
    from .work_jobs import WorkJobs
    from .work_store import WorkStore

    if store._postgres:
        store._postgres.lock(db.native, f"work-program:{link['program_id']}")
    old = db.execute("SELECT 1 FROM work_compute_receipts WHERE compute_job_id=?", (row["id"],)).fetchone()
    if old:
        return
    body = {
        "schema": "reasoning.compute-execution.v1", "compute_job_id": row["id"],
        "image": json.loads(row["image_json"]), "program_digest": row["program_digest"],
        "parameters_sha256": digest(row["parameters_json"]),
        "source": json.loads(link["source_json"]) if link["source_json"] else None,
        "status": row["status"], "exit_code": row["exit_code"], "error_code": row["error_code"],
        "elapsed_ms": row["elapsed_ms"], "used_cpu_seconds": row["used_cpu_seconds"],
        "worker_id": row["worker_id"], "attempt_id": row["attempt_id"],
        "artifacts": [dict(item) for item in db.execute(
            "SELECT id,name,size_bytes,digest FROM compute_artifacts WHERE job_id=? AND committed_at IS NOT NULL ORDER BY name",
            (row["id"],),
        )],
        "attestation": "Enrolled compute worker execution outcome; not a proof or test-policy certificate.",
        "cost_accounting": "CPU quota only. Infrastructure charges are not reported here and remain subject to operator/provider budgets.",
    }
    payload = encoded(body)
    db.execute("INSERT INTO work_compute_receipts VALUES(?,?,?,?,?,?)",
               (row["id"], link["organization_id"], link["project_id"], payload, digest(payload), int(time.time())))
    task = _row(db.execute("SELECT * FROM work_tasks WHERE id=?", (link["task_id"],)).fetchone())
    root = _row(db.execute("SELECT * FROM work_programs WHERE id=?", (link["program_id"],)).fetchone())
    bind(db, WorkStore(store), task, root, VersionReference(kind="compute_result", id=row["id"], revision=1, sha256=digest(payload)),
         "server_compute_receipt")
    effect = _row(db.execute("SELECT * FROM work_effects WHERE id=?", (link["effect_id"],)).fetchone())
    if effect["state"] not in {"intent", "unknown"}:
        raise StoreError(409, "linked compute accounting was replaced outside its server receipt")
    receipt = encoded({"source": "server_compute", "compute_job_id": row["id"], "sha256": digest(payload)})
    db.execute("UPDATE work_effects SET state='settled',actual_microusd=NULL,actual_cpu_seconds=?,receipt_json=?,finished_at=? WHERE id=?",
               (row["used_cpu_seconds"], receipt, int(time.time()), effect["id"]))
    WorkJobs._account_delta(db, effect, 0, row["used_cpu_seconds"])
    WorkStore._event(db, link["organization_id"], link["program_id"], None,
                     {"type": "computation_finished", "compute_job_id": row["id"], "status": row["status"]}, link["task_id"])


class WorkCompute:
    def __init__(self, tools, compute):
        self.tools, self.store, self.work, self.compute = tools, tools.store, tools.work, compute

    def submit(self, db, job, task, root, body):
        if self.compute is None:
            raise StoreError(409, "the installation has no configured compute service")
        source = body.source
        if source:
            require_assigned(db, task, source)
            self.work._input(db, task["organization_id"], root["project_id"], source)
            if read(db, source.id, source.revision, length=16000)["truncated"]:
                raise StoreError(413, "computation source exceeds the 16000-character runner transport allowance")
            program = full(db, source.id, source.revision)
        else:
            program = body.program
        params = self.compute.parameters(body.image_id, body.language, program, body.resources.model_dump())
        reserve, effect_id = params["resources"]["cpu"] * params["resources"]["wall_time_seconds"], _id()
        scopes = self.tools._admit(db, job, task, root, "compute_submit")
        for kind, identity, limits in scopes:
            if body.image_id not in limits.allowed_images:
                raise StoreError(403, "compute image is not authorized by this work assignment")
            usage = self.tools.jobs._usage(db, kind, identity)
            if limits.cpu_seconds is not None and usage["cpu_seconds"] + reserve > limits.cpu_seconds:
                raise StoreError(409, "shared work CPU authorization cannot cover this reservation")
        client_id = "work-" + digest(f"{task['id']}:{task['epoch']}:{body.client_id}")
        queued = self.compute._enqueue(
            db, task["requested_by"], task["organization_id"], None, client_id, params, project=root["project_id"]
        )
        db.execute(
            "INSERT INTO work_effects(id,job_id,program_id,task_id,fence,client_id,kind,request_sha256,image_id,"
            "held_microusd,held_cpu_seconds,state,created_at) VALUES(?,?,?,?,?,?,'compute',?,?,0,?,'intent',?)",
            (effect_id, job["id"], root["id"], task["id"], job["fence"], "compute-" + body.client_id,
             digest(encoded(params)), body.image_id, reserve, int(time.time())),
        )
        for kind, identity, _ in scopes:
            db.execute("INSERT INTO work_effect_scopes VALUES(?,?,?)", (effect_id, kind, identity))
            db.execute(
                "INSERT INTO work_resource_accounts(scope_kind,scope_id,cpu_seconds) VALUES(?,?,?) "
                "ON CONFLICT(scope_kind,scope_id) DO UPDATE SET cpu_seconds=work_resource_accounts.cpu_seconds+excluded.cpu_seconds",
                (kind, identity, reserve),
            )
        db.execute("INSERT INTO work_computations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            queued["id"], task["organization_id"], root["project_id"], root["id"], task["id"], task["epoch"], job["id"], job["fence"],
            effect_id, body.client_id, digest(encoded(params)), encoded(source.model_dump()) if source else None,
        ))
        self.work._provenance(db, task["organization_id"], root["project_id"], root["id"], "computation", queued["id"], 1,
                              [source] if source else [])
        return self.view(db, task, root, queued["id"])

    def view(self, db, task, root, identity):
        _row(db.execute(
            "SELECT compute_job_id FROM work_computations WHERE compute_job_id=? AND task_id=? AND task_epoch=? AND program_id=?",
            (identity, task["id"], task["epoch"], root["id"]),
        ).fetchone())
        self.compute._expire(db, task["organization_id"])
        row = self.compute._job(db, task["organization_id"], identity)
        receipt = db.execute("SELECT sha256,body_json FROM work_compute_receipts WHERE compute_job_id=?", (identity,)).fetchone()
        reference = VersionReference(kind="compute_result", id=identity, revision=1, sha256=receipt["sha256"]) if receipt else None
        if reference:
            bind(db, self.work, task, root, reference, "compute_read")
        return {"compute_job_id": identity, "status": row["status"], "exit_code": row["exit_code"], "error_code": row["error_code"],
                "used_cpu_seconds": row["used_cpu_seconds"], "reference": reference.model_dump() if reference else None,
                "artifacts": json.loads(receipt["body_json"])["artifacts"] if receipt else []}

    def read(self, db, job, task, root, body):
        return self.view(db, task, root, body.compute_job_id)

    def cancel(self, db, job, task, root, body):
        self.view(db, task, root, body.compute_job_id)
        row = self.compute._job(db, task["organization_id"], body.compute_job_id)
        self.compute._request_cancel(db, task["organization_id"], row)
        return self.view(db, task, root, body.compute_job_id)

    def output_read(self, db, job, task, root, body):
        """Resolve files through assigned immutable receipts, never guessed IDs."""
        if self.compute is None:
            raise StoreError(409, "the installation has no configured compute service")
        require_assigned(db, task, body.execution)
        self.work._input(db, task["organization_id"], root["project_id"], body.execution)
        receipt = json.loads(db.execute(
            "SELECT body_json FROM work_compute_receipts WHERE compute_job_id=?", (body.execution.id,),
        ).fetchone()[0])
        header = next((item for item in receipt["artifacts"] if item["id"] == body.artifact_id), None)
        if header is None:
            raise StoreError(404, "artifact is not retained in this execution receipt")
        meta = self.compute._artifact(db, task["organization_id"], body.execution.id, body.artifact_id)
        if meta["committed_at"] is None or any(meta[key] != header[key] for key in ("name", "size_bytes", "digest")):
            raise StoreError(409, "artifact metadata differs from its retained execution receipt")
        if body.offset > header["size_bytes"]:
            raise StoreError(400, "artifact byte offset exceeds the retained output")
        end = min(header["size_bytes"], body.offset + body.length)
        content = bytearray()
        if end > body.offset:
            first, last = body.offset // CHUNK_BYTES, (end - 1) // CHUNK_BYTES
            for index in range(first, last + 1):
                chunk = db.execute(
                    "SELECT size_bytes,digest FROM compute_artifact_chunks WHERE artifact_id=? AND chunk_index=?",
                    (body.artifact_id, index),
                ).fetchone()
                try:
                    data = self.compute._read_chunk(body.artifact_id, index)
                except (OSError, ValueError) as exc:
                    raise StoreError(409, "retained output chunk is unavailable or unsafe") from exc
                if chunk is None or len(data) != chunk["size_bytes"] or "sha256:" + hashlib.sha256(data).hexdigest() != chunk["digest"]:
                    raise StoreError(409, "retained output chunk integrity check failed")
                start = index * CHUNK_BYTES
                content.extend(data[max(0, body.offset - start):min(len(data), end - start)])
        try:
            text = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            text = None
        return {
            "reference": body.execution.model_dump(), "artifact": header,
            "offset": body.offset, "length": len(content), "total_bytes": header["size_bytes"],
            "base64": base64.b64encode(content).decode("ascii"), "text": text,
            "truncated": end < header["size_bytes"], "next_offset": end if end < header["size_bytes"] else None,
            "scope": "untrusted_execution_output_not_verification",
        }
