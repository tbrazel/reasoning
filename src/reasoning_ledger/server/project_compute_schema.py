"""Project compute provenance without requiring a synthetic chat topic."""

from .schema_operations import PROJECT_COMPUTE

SCHEMA = (
    PROJECT_COMPUTE,
    "ALTER TABLE compute_jobs ADD COLUMN project_id TEXT REFERENCES projects(id)",
    """UPDATE compute_jobs SET project_id=(SELECT c.project_id FROM topics t
        JOIN channels c ON c.organization_id=t.organization_id AND c.id=t.channel_id
        WHERE t.organization_id=compute_jobs.organization_id AND t.id=compute_jobs.topic_id)""",
    "CREATE INDEX compute_project_page ON compute_jobs(organization_id,project_id,created_at,id)",
    "CREATE INDEX compute_worker_activity ON compute_jobs(organization_id,worker_id,finished_at)",
)
