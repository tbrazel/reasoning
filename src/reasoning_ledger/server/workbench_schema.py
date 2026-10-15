"""Organization-owned mathematical records, artifacts, and review jobs."""

SCHEMA = (
    """CREATE TABLE mathematical_records (
        organization_id TEXT NOT NULL REFERENCES organizations(id), path TEXT NOT NULL,
        record_type TEXT NOT NULL, object_id TEXT NOT NULL, claim_id TEXT,
        project_id TEXT, topic_id TEXT, body_json TEXT NOT NULL,
        created_by TEXT NOT NULL REFERENCES users(id), created_at INTEGER NOT NULL,
        trust_state TEXT NOT NULL DEFAULT 'native' CHECK(trust_state IN ('native','imported')),
        PRIMARY KEY(organization_id,path),
        FOREIGN KEY(organization_id,project_id) REFERENCES projects(organization_id,id),
        FOREIGN KEY(organization_id,topic_id) REFERENCES topics(organization_id,id)
    )""",
    "CREATE INDEX mathematical_object ON mathematical_records(organization_id,object_id)",
    """CREATE TRIGGER mathematical_revision_update BEFORE UPDATE ON mathematical_records
        WHEN OLD.record_type NOT LIKE '%-head'
        BEGIN SELECT RAISE(ABORT,'mathematical revisions are immutable'); END""",
    """CREATE TRIGGER mathematical_record_delete BEFORE DELETE ON mathematical_records
        BEGIN SELECT RAISE(ABORT,'mathematical history is retained'); END""",
    """CREATE TABLE mathematical_requests (
        organization_id TEXT NOT NULL, actor_id TEXT NOT NULL, client_id TEXT NOT NULL,
        arguments_json TEXT NOT NULL, result_json TEXT NOT NULL,
        PRIMARY KEY(organization_id,actor_id,client_id)
    )""",
    """CREATE TABLE research_artifacts (
        id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, project_id TEXT NOT NULL,
        topic_id TEXT NOT NULL, path TEXT NOT NULL, head_revision INTEGER NOT NULL,
        UNIQUE(organization_id,project_id,path),
        FOREIGN KEY(organization_id,topic_id) REFERENCES topics(organization_id,id)
    )""",
    """CREATE TABLE research_artifact_versions (
        artifact_id TEXT NOT NULL REFERENCES research_artifacts(id), revision INTEGER NOT NULL,
        text TEXT NOT NULL, sha256 TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES users(id),
        run_id TEXT REFERENCES llm_runs(id), created_at INTEGER NOT NULL,
        PRIMARY KEY(artifact_id,revision)
    )""",
    """CREATE TRIGGER research_artifact_update BEFORE UPDATE ON research_artifact_versions
        BEGIN SELECT RAISE(ABORT,'artifact revisions are immutable'); END""",
    """CREATE TRIGGER research_artifact_delete BEFORE DELETE ON research_artifact_versions
        BEGIN SELECT RAISE(ABORT,'artifact revisions are retained'); END""",
    """CREATE TABLE mathematical_reviews (
        id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, topic_id TEXT NOT NULL,
        requested_by TEXT NOT NULL REFERENCES users(id), source_run_id TEXT NOT NULL REFERENCES llm_runs(id),
        reviewer_model TEXT NOT NULL, subject_json TEXT NOT NULL, input_json TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed','cancelled')),
        reviewer_run_id TEXT REFERENCES llm_runs(id), result_json TEXT, created_at INTEGER NOT NULL,
        finished_at INTEGER, UNIQUE(source_run_id,subject_json),
        FOREIGN KEY(organization_id,topic_id) REFERENCES topics(organization_id,id)
    )""",
    "ALTER TABLE research_topic_settings ADD COLUMN reviewer_model TEXT",
    "ALTER TABLE research_topic_settings ADD COLUMN context_policy_json TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE research_topic_settings ADD COLUMN tasks_json TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE research_topic_settings ADD COLUMN compute_allowance_cpu_seconds INTEGER NOT NULL DEFAULT 0",
)
