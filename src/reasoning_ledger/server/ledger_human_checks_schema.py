"""Revision-pinned human judgments, separate from mathematical verification."""

SCHEMA = (
    """CREATE TABLE ledger_human_checks (
        id TEXT PRIMARY KEY, organization_id TEXT NOT NULL REFERENCES organizations(id),
        object_id TEXT NOT NULL, revision_id TEXT NOT NULL, record_path TEXT NOT NULL,
        record_sha256 TEXT NOT NULL, actor_id TEXT NOT NULL REFERENCES users(id),
        created_at INTEGER NOT NULL, note TEXT NOT NULL,
        UNIQUE(organization_id,object_id,revision_id,actor_id),
        FOREIGN KEY(organization_id,record_path) REFERENCES mathematical_records(organization_id,path)
    )""",
    "CREATE INDEX ledger_human_checks_subject ON ledger_human_checks(organization_id,object_id,revision_id)",
    """CREATE TRIGGER ledger_human_checks_update BEFORE UPDATE ON ledger_human_checks
        BEGIN SELECT RAISE(ABORT,'human checks are immutable'); END""",
    """CREATE TRIGGER ledger_human_checks_delete BEFORE DELETE ON ledger_human_checks
        BEGIN SELECT RAISE(ABORT,'human check history is retained'); END""",
)
