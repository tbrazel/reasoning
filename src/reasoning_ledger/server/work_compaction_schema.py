"""Distinguish paid context-maintenance calls from candidate/review output."""

SCHEMA = (
    "ALTER TABLE work_provider_calls ADD COLUMN purpose TEXT NOT NULL DEFAULT 'work' CHECK(purpose IN ('work','compaction'))",
    "CREATE INDEX work_provider_purpose ON work_provider_calls(job_id,fence,purpose,id)",
)
