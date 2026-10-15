"""Human message revision history; captured inference inputs remain independent."""

SCHEMA = (
    "ALTER TABLE messages ADD COLUMN revision INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE messages ADD COLUMN edited_at INTEGER",
    """CREATE TABLE message_revisions (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id TEXT NOT NULL, message_id TEXT NOT NULL, revision INTEGER NOT NULL,
        content TEXT NOT NULL, edited_by TEXT NOT NULL REFERENCES users(id), created_at INTEGER NOT NULL,
        UNIQUE(message_id,revision),
        FOREIGN KEY(organization_id,message_id) REFERENCES messages(organization_id,id)
    )""",
    "CREATE INDEX message_revision_updates ON message_revisions(organization_id,sequence)",
    """CREATE TRIGGER message_revision_immutable_update BEFORE UPDATE ON message_revisions
        BEGIN SELECT RAISE(ABORT,'message revisions are immutable'); END""",
    """CREATE TRIGGER message_revision_immutable_delete BEFORE DELETE ON message_revisions
        BEGIN SELECT RAISE(ABORT,'message revisions are immutable'); END""",
    """CREATE TABLE research_memory_edit_versions (
        organization_id TEXT NOT NULL, topic_id TEXT NOT NULL, revision INTEGER NOT NULL, edit_sequence INTEGER NOT NULL,
        PRIMARY KEY(organization_id,topic_id,revision),
        FOREIGN KEY(organization_id,topic_id,revision) REFERENCES research_memories(organization_id,topic_id,revision)
    )""",
    """CREATE TRIGGER memory_edit_version_immutable BEFORE UPDATE ON research_memory_edit_versions
        BEGIN SELECT RAISE(ABORT,'memory edit versions are immutable'); END""",
)
