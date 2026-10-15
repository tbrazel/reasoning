"""Retained server-owned provider calls and incremental work-stream events."""

SCHEMA = (
    """CREATE TABLE work_provider_calls (
        id TEXT PRIMARY KEY, effect_id TEXT NOT NULL UNIQUE REFERENCES work_effects(id),
        organization_id TEXT NOT NULL, project_id TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES work_jobs(id),
        fence INTEGER NOT NULL, worker_id TEXT NOT NULL REFERENCES work_workers(id),
        model TEXT NOT NULL, key_id TEXT NOT NULL, request_json TEXT NOT NULL, request_sha256 TEXT NOT NULL,
        quote_json TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('running','complete','failed')),
        response_json TEXT, usage_json TEXT, provider_generation_id TEXT, error_code TEXT, diagnostics_json TEXT,
        started_at INTEGER NOT NULL, finished_at INTEGER,
        FOREIGN KEY(organization_id,project_id) REFERENCES projects(organization_id,id),
        FOREIGN KEY(organization_id,key_id) REFERENCES provider_keys(organization_id,id)
    )""",
    "CREATE INDEX work_provider_scope ON work_provider_calls(organization_id,project_id,started_at,id)",
    "CREATE INDEX work_provider_job ON work_provider_calls(job_id,fence,id)",
    """CREATE TABLE work_stream_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, organization_id TEXT NOT NULL,
        program_id TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES work_jobs(id), fence INTEGER NOT NULL,
        call_id TEXT REFERENCES work_provider_calls(id), client_id TEXT, event_json TEXT NOT NULL, created_at INTEGER NOT NULL,
        UNIQUE(job_id,fence,client_id),
        FOREIGN KEY(organization_id,program_id) REFERENCES work_programs(organization_id,id)
    )""",
    "CREATE INDEX work_stream_job ON work_stream_events(job_id,sequence)",
    "CREATE INDEX work_stream_program ON work_stream_events(program_id,sequence)",
    """CREATE TRIGGER work_provider_request_immutable BEFORE UPDATE ON work_provider_calls
        WHEN NEW.effect_id<>OLD.effect_id OR NEW.organization_id<>OLD.organization_id OR NEW.project_id<>OLD.project_id
            OR NEW.job_id<>OLD.job_id OR NEW.fence<>OLD.fence OR NEW.worker_id<>OLD.worker_id OR NEW.model<>OLD.model
            OR NEW.key_id<>OLD.key_id OR NEW.request_json<>OLD.request_json OR NEW.request_sha256<>OLD.request_sha256
            OR NEW.quote_json<>OLD.quote_json
        BEGIN SELECT RAISE(ABORT,'work provider input is immutable'); END""",
    """CREATE TRIGGER work_provider_terminal_immutable BEFORE UPDATE ON work_provider_calls
        WHEN OLD.state<>'running'
        BEGIN SELECT RAISE(ABORT,'work provider outcomes are retained'); END""",
    """CREATE TRIGGER work_provider_retained BEFORE DELETE ON work_provider_calls
        BEGIN SELECT RAISE(ABORT,'work provider calls are retained'); END""",
    """CREATE TRIGGER work_stream_immutable_update BEFORE UPDATE ON work_stream_events
        BEGIN SELECT RAISE(ABORT,'work stream events are immutable'); END""",
    """CREATE TRIGGER work_stream_immutable_delete BEFORE DELETE ON work_stream_events
        BEGIN SELECT RAISE(ABORT,'work stream events are retained'); END""",
)
