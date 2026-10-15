"""Private immutable media and bounded resumable upload chunks."""

SCHEMA = (
    """CREATE TABLE research_media (
        id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, project_id TEXT NOT NULL,
        topic_id TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES users(id),
        name TEXT NOT NULL, mime TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
        complete INTEGER NOT NULL DEFAULT 0 CHECK(complete IN (0,1)), preview_text TEXT NOT NULL DEFAULT '',
        vision_base64 TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL,
        FOREIGN KEY(organization_id,topic_id) REFERENCES topics(organization_id,id)
    )""",
    """CREATE TABLE research_media_chunks (
        media_id TEXT NOT NULL REFERENCES research_media(id) ON DELETE CASCADE,
        chunk_index INTEGER NOT NULL, data_base64 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
        PRIMARY KEY(media_id,chunk_index)
    )""",
    "CREATE INDEX research_media_scope ON research_media(organization_id,project_id,created_at)",
)
