"""Project-scoped immutable content, separate from small artifact version metadata."""

SCHEMA = (
    """CREATE TABLE research_blob_objects (
        organization_id TEXT NOT NULL, project_id TEXT NOT NULL, digest TEXT NOT NULL,
        total_characters INTEGER NOT NULL, size_bytes INTEGER NOT NULL,
        PRIMARY KEY(organization_id,project_id,digest),
        FOREIGN KEY(organization_id,project_id) REFERENCES projects(organization_id,id)
    )""",
    """CREATE TABLE research_blob_chunks (
        organization_id TEXT NOT NULL, project_id TEXT NOT NULL, digest TEXT NOT NULL,
        ordinal INTEGER NOT NULL, text TEXT NOT NULL, sha256 TEXT NOT NULL,
        PRIMARY KEY(organization_id,project_id,digest,ordinal),
        FOREIGN KEY(organization_id,project_id,digest) REFERENCES research_blob_objects(organization_id,project_id,digest)
    )""",
    """CREATE TABLE research_artifact_blobs (
        artifact_id TEXT NOT NULL, revision INTEGER NOT NULL, organization_id TEXT NOT NULL,
        project_id TEXT NOT NULL, digest TEXT NOT NULL,
        PRIMARY KEY(artifact_id,revision),
        FOREIGN KEY(artifact_id,revision) REFERENCES research_artifact_versions(artifact_id,revision),
        FOREIGN KEY(organization_id,project_id,digest) REFERENCES research_blob_objects(organization_id,project_id,digest)
    )""",
    """CREATE TRIGGER research_blob_immutable BEFORE UPDATE ON research_blob_objects
        BEGIN SELECT RAISE(ABORT,'artifact blob identity is immutable'); END""",
    """CREATE TRIGGER research_blob_retained BEFORE DELETE ON research_blob_objects
        BEGIN SELECT RAISE(ABORT,'artifact blobs are retained'); END""",
    """CREATE TRIGGER research_chunk_immutable BEFORE UPDATE ON research_blob_chunks
        BEGIN SELECT RAISE(ABORT,'artifact chunks are immutable'); END""",
    """CREATE TRIGGER research_chunk_retained BEFORE DELETE ON research_blob_chunks
        BEGIN SELECT RAISE(ABORT,'artifact chunks are retained'); END""",
    """CREATE TRIGGER research_blob_binding_immutable BEFORE UPDATE ON research_artifact_blobs
        BEGIN SELECT RAISE(ABORT,'artifact blob bindings are immutable'); END""",
    """CREATE TRIGGER research_blob_binding_retained BEFORE DELETE ON research_artifact_blobs
        BEGIN SELECT RAISE(ABORT,'artifact blob bindings are retained'); END""",
)
