"""Durable public retrieval intents, bibliography provenance and immutable source captures."""

SCHEMA = (
    """CREATE TABLE work_source_requests (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES work_tasks(id), task_epoch INTEGER NOT NULL,
        client_id TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES work_jobs(id), fence INTEGER NOT NULL, tool TEXT NOT NULL,
        request_sha256 TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('reading','complete','failed')),
        result_json TEXT, result_sha256 TEXT, error_code TEXT, created_at INTEGER NOT NULL, finished_at INTEGER,
        UNIQUE(task_id,task_epoch,client_id)
    )""",
    "CREATE INDEX work_source_job ON work_source_requests(job_id,fence,id)",
    """CREATE TABLE work_source_captures (
        id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, project_id TEXT NOT NULL,
        request_id TEXT NOT NULL REFERENCES work_source_requests(id),
        url TEXT NOT NULL, raw_sha256 TEXT NOT NULL, text_digest TEXT NOT NULL, media_type TEXT NOT NULL,
        transformation TEXT NOT NULL, retrieved_at INTEGER NOT NULL, bibliography_id TEXT NOT NULL REFERENCES knowledge_entries(id),
        bibliography_revision INTEGER NOT NULL,
        FOREIGN KEY(organization_id,project_id,text_digest) REFERENCES research_blob_objects(organization_id,project_id,digest)
    )""",
    "CREATE INDEX work_source_project ON work_source_captures(organization_id,project_id,id)",
    """CREATE TRIGGER work_source_capture_immutable BEFORE UPDATE ON work_source_captures
        BEGIN SELECT RAISE(ABORT,'source captures are immutable'); END""",
    """CREATE TRIGGER work_source_capture_retained BEFORE DELETE ON work_source_captures
        BEGIN SELECT RAISE(ABORT,'source captures are retained'); END""",
    """CREATE TRIGGER work_source_request_terminal BEFORE UPDATE ON work_source_requests
        WHEN OLD.state<>'reading' BEGIN SELECT RAISE(ABORT,'source outcomes are immutable'); END""",
    """CREATE TRIGGER work_source_request_retained BEFORE DELETE ON work_source_requests
        BEGIN SELECT RAISE(ABORT,'source requests are retained'); END""",
)
