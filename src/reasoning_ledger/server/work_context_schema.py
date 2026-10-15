"""Pinned role material and retained internal compaction, without transcript copies."""

SCHEMA = (
    """CREATE TABLE work_task_prompts (
        organization_id TEXT NOT NULL, program_id TEXT NOT NULL, task_id TEXT PRIMARY KEY,
        body_json TEXT NOT NULL, sha256 TEXT NOT NULL,
        FOREIGN KEY(organization_id,program_id,task_id) REFERENCES work_tasks(organization_id,program_id,id)
    )""",
    """CREATE TRIGGER work_prompt_immutable_update BEFORE UPDATE ON work_task_prompts
        BEGIN SELECT RAISE(ABORT,'task prompts are immutable'); END""",
    """CREATE TRIGGER work_prompt_immutable_delete BEFORE DELETE ON work_task_prompts
        BEGIN SELECT RAISE(ABORT,'task prompts are retained'); END""",
    """CREATE TABLE work_compactions (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES work_jobs(id),
        fence INTEGER NOT NULL, client_id TEXT NOT NULL, body_json TEXT NOT NULL, sha256 TEXT NOT NULL,
        created_by TEXT NOT NULL REFERENCES work_workers(id), created_at INTEGER NOT NULL,
        UNIQUE(job_id,fence,client_id)
    )""",
    "CREATE INDEX work_compaction_latest ON work_compactions(job_id,sequence)",
    """CREATE TRIGGER work_compaction_immutable_update BEFORE UPDATE ON work_compactions
        BEGIN SELECT RAISE(ABORT,'work compactions are immutable'); END""",
    """CREATE TRIGGER work_compaction_immutable_delete BEFORE DELETE ON work_compactions
        BEGIN SELECT RAISE(ABORT,'work compactions are retained'); END""",
)
