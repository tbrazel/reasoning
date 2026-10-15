"""Retained controller transitions, exact acceptance subjects and wake frontiers."""

SCHEMA = (
    """CREATE TABLE work_provider_call_sequences (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, call_id TEXT NOT NULL UNIQUE REFERENCES work_provider_calls(id)
    )""",
    "INSERT INTO work_provider_call_sequences(call_id) SELECT id FROM work_provider_calls ORDER BY started_at,id",
    """CREATE TRIGGER work_provider_sequence_immutable BEFORE UPDATE ON work_provider_call_sequences
        BEGIN SELECT RAISE(ABORT,'provider call sequence is immutable'); END""",
    """CREATE TRIGGER work_provider_sequence_retained BEFORE DELETE ON work_provider_call_sequences
        BEGIN SELECT RAISE(ABORT,'provider call sequence is retained'); END""",
    """CREATE TABLE work_controllers (
        id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, program_id TEXT NOT NULL,
        target_task TEXT NOT NULL REFERENCES work_tasks(id), candidate_task TEXT NOT NULL REFERENCES work_tasks(id),
        client_id TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES users(id), root_revision INTEGER NOT NULL,
        root_controller INTEGER NOT NULL DEFAULT 0 CHECK(root_controller IN (0,1)),
        controller TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('running','waiting','blocked','complete','cancelled')),
        phase TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, round INTEGER NOT NULL DEFAULT 1,
        current_job TEXT REFERENCES work_jobs(id), result_json TEXT, review_json TEXT, error TEXT,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
        UNIQUE(program_id,created_by,client_id), UNIQUE(target_task),
        FOREIGN KEY(organization_id,program_id) REFERENCES work_programs(organization_id,id)
    )""",
    "CREATE INDEX work_controller_frontier ON work_controllers(program_id,status,updated_at,id)",
    "CREATE UNIQUE INDEX work_controller_root ON work_controllers(program_id) WHERE root_controller=1 AND status<>'cancelled'",
    """CREATE TABLE work_controller_steps (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, controller_id TEXT NOT NULL REFERENCES work_controllers(id),
        phase TEXT NOT NULL, round INTEGER NOT NULL, task_id TEXT NOT NULL REFERENCES work_tasks(id),
        job_id TEXT NOT NULL UNIQUE REFERENCES work_jobs(id), created_at INTEGER NOT NULL
    )""",
    "CREATE INDEX work_controller_step_page ON work_controller_steps(controller_id,sequence)",
    """CREATE TABLE work_checks (
        id TEXT PRIMARY KEY, controller_id TEXT NOT NULL REFERENCES work_controllers(id),
        client_id TEXT NOT NULL, criterion_id TEXT NOT NULL, check_kind TEXT NOT NULL,
        subject_json TEXT NOT NULL, passed INTEGER NOT NULL CHECK(passed IN (0,1)), source TEXT NOT NULL,
        body_json TEXT NOT NULL, sha256 TEXT NOT NULL, created_by TEXT REFERENCES users(id), created_at INTEGER NOT NULL,
        UNIQUE(controller_id,client_id)
    )""",
    "CREATE INDEX work_check_subject ON work_checks(controller_id,subject_json,criterion_id,check_kind,created_at,id)",
    """CREATE TABLE work_controller_wakes (
        program_id TEXT PRIMARY KEY REFERENCES work_programs(id), updated_at INTEGER NOT NULL
    )""",
    "CREATE INDEX work_controller_wake_frontier ON work_controller_wakes(updated_at,program_id)",
    """CREATE TRIGGER work_controller_step_immutable_update BEFORE UPDATE ON work_controller_steps
        BEGIN SELECT RAISE(ABORT,'controller steps are immutable'); END""",
    """CREATE TRIGGER work_controller_step_immutable_delete BEFORE DELETE ON work_controller_steps
        BEGIN SELECT RAISE(ABORT,'controller steps are retained'); END""",
    """CREATE TRIGGER work_check_immutable_update BEFORE UPDATE ON work_checks
        BEGIN SELECT RAISE(ABORT,'work checks are immutable'); END""",
    """CREATE TRIGGER work_check_immutable_delete BEFORE DELETE ON work_checks
        BEGIN SELECT RAISE(ABORT,'work checks are retained'); END""",
)


def wake(db, program):
    import time

    db.execute(
        "INSERT INTO work_controller_wakes VALUES(?,?) ON CONFLICT(program_id) DO UPDATE SET updated_at=excluded.updated_at",
        (program, int(time.time())),
    )
