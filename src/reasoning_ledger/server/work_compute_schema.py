"""Atomic work-to-compute linkage and immutable server execution receipts."""

SCHEMA = (
    """CREATE TABLE work_computations (
        compute_job_id TEXT PRIMARY KEY REFERENCES compute_jobs(id),
        organization_id TEXT NOT NULL, project_id TEXT NOT NULL, program_id TEXT NOT NULL,
        task_id TEXT NOT NULL REFERENCES work_tasks(id), task_epoch INTEGER NOT NULL,
        job_id TEXT NOT NULL REFERENCES work_jobs(id), fence INTEGER NOT NULL,
        effect_id TEXT NOT NULL UNIQUE REFERENCES work_effects(id), client_id TEXT NOT NULL,
        request_sha256 TEXT NOT NULL, source_json TEXT,
        UNIQUE(task_id,task_epoch,client_id),
        FOREIGN KEY(organization_id,program_id) REFERENCES work_programs(organization_id,id),
        FOREIGN KEY(organization_id,project_id) REFERENCES projects(organization_id,id)
    )""",
    "CREATE INDEX work_compute_job ON work_computations(job_id,compute_job_id)",
    """CREATE TABLE work_compute_receipts (
        compute_job_id TEXT PRIMARY KEY REFERENCES work_computations(compute_job_id),
        organization_id TEXT NOT NULL, project_id TEXT NOT NULL, body_json TEXT NOT NULL,
        sha256 TEXT NOT NULL, created_at INTEGER NOT NULL,
        FOREIGN KEY(organization_id,project_id) REFERENCES projects(organization_id,id)
    )""",
    *tuple(
        f"""CREATE TRIGGER work_compute_{table}_{operation.lower()} BEFORE {operation} ON work_compute_{table}
            BEGIN SELECT RAISE(ABORT,'work compute records are immutable'); END"""
        for table in ("receipts",)
        for operation in ("UPDATE", "DELETE")
    ),
    """CREATE TRIGGER work_computation_update BEFORE UPDATE ON work_computations
        BEGIN SELECT RAISE(ABORT,'work computation linkage is immutable'); END""",
    """CREATE TRIGGER work_computation_delete BEFORE DELETE ON work_computations
        BEGIN SELECT RAISE(ABORT,'work computation linkage is retained'); END""",
)
