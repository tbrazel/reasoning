"""Immutable blog deletion receipts; post revisions remain retained."""

SCHEMA = ("""
CREATE TABLE blog_deletions (
    organization_id TEXT NOT NULL,
    entry_id TEXT NOT NULL,
    deleted_by TEXT NOT NULL REFERENCES users(id),
    deleted_at INTEGER NOT NULL,
    expected_revision INTEGER NOT NULL,
    client_id TEXT NOT NULL,
    PRIMARY KEY (organization_id, entry_id),
    UNIQUE (organization_id, deleted_by, client_id),
    FOREIGN KEY (organization_id, entry_id) REFERENCES knowledge_entries(organization_id, id)
);
""",)
