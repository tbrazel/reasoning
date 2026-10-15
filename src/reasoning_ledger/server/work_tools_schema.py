"""Atomic local tool receipts and exact discovery bindings for scoped delegation."""

SCHEMA = (
    """CREATE TABLE work_tool_operations (
        task_id TEXT NOT NULL REFERENCES work_tasks(id), task_epoch INTEGER NOT NULL, client_id TEXT NOT NULL,
        job_id TEXT NOT NULL REFERENCES work_jobs(id), fence INTEGER NOT NULL,
        tool TEXT NOT NULL, request_sha256 TEXT NOT NULL, result_json TEXT NOT NULL, result_sha256 TEXT NOT NULL,
        worker_id TEXT NOT NULL REFERENCES work_workers(id), created_at INTEGER NOT NULL,
        PRIMARY KEY(task_id,task_epoch,client_id)
    )""",
    "CREATE INDEX work_tool_job ON work_tool_operations(job_id,fence,created_at)",
    """CREATE TABLE work_input_bindings (
        task_id TEXT NOT NULL REFERENCES work_tasks(id), input_kind TEXT NOT NULL, input_id TEXT NOT NULL,
        input_revision TEXT NOT NULL, input_sha256 TEXT NOT NULL, source_tool TEXT NOT NULL,
        PRIMARY KEY(task_id,input_kind,input_id,input_revision)
    )""",
    """CREATE TRIGGER work_tool_immutable_update BEFORE UPDATE ON work_tool_operations
        BEGIN SELECT RAISE(ABORT,'local work tool receipts are immutable'); END""",
    """CREATE TRIGGER work_tool_immutable_delete BEFORE DELETE ON work_tool_operations
        BEGIN SELECT RAISE(ABORT,'local work tool receipts are retained'); END""",
    """CREATE TRIGGER work_binding_immutable_update BEFORE UPDATE ON work_input_bindings
        BEGIN SELECT RAISE(ABORT,'work discovery bindings are immutable'); END""",
    """CREATE TRIGGER work_binding_immutable_delete BEFORE DELETE ON work_input_bindings
        BEGIN SELECT RAISE(ABORT,'work discovery bindings are retained'); END""",
)
