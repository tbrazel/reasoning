"""Task-branch workspace bindings into the shared versioned artifact repository."""

SCHEMA = (
    "CREATE INDEX work_controller_step_task ON work_controller_steps(task_id)",
    "CREATE UNIQUE INDEX research_artifact_project_id ON research_artifacts(organization_id,project_id,id)",
    """CREATE TABLE work_workspace_files (
        organization_id TEXT NOT NULL, project_id TEXT NOT NULL, program_id TEXT NOT NULL,
        scope_task TEXT NOT NULL REFERENCES work_tasks(id), path TEXT NOT NULL, artifact_id TEXT NOT NULL UNIQUE,
        PRIMARY KEY(program_id,scope_task,path),
        FOREIGN KEY(organization_id,program_id) REFERENCES work_programs(organization_id,id),
        FOREIGN KEY(organization_id,project_id,artifact_id) REFERENCES research_artifacts(organization_id,project_id,id)
    )""",
    """CREATE TRIGGER work_workspace_binding_immutable BEFORE UPDATE ON work_workspace_files
        BEGIN SELECT RAISE(ABORT,'workspace bindings are immutable'); END""",
    """CREATE TRIGGER work_workspace_binding_retained BEFORE DELETE ON work_workspace_files
        BEGIN SELECT RAISE(ABORT,'workspace bindings are retained'); END""",
)
