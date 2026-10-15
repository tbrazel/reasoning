"""Worker leases, fenced jobs, retained outputs and shared resource reservations."""

SCHEMA = (
    "ALTER TABLE work_programs ADD COLUMN execution_slots INTEGER NOT NULL DEFAULT 2 CHECK(execution_slots>=1)",
    "UPDATE work_programs SET execution_slots=(SELECT json_extract(v.body_json,'$.resources.max_concurrency') "
    "FROM work_specifications v WHERE v.program_id=work_programs.id AND v.revision=work_programs.head_revision)",
    """CREATE TABLE work_task_ancestors (
        organization_id TEXT NOT NULL, program_id TEXT NOT NULL, task_id TEXT NOT NULL, ancestor_id TEXT NOT NULL,
        depth INTEGER NOT NULL CHECK(depth>=0), PRIMARY KEY(task_id,ancestor_id),
        FOREIGN KEY(organization_id,program_id,task_id) REFERENCES work_tasks(organization_id,program_id,id),
        FOREIGN KEY(organization_id,program_id,ancestor_id) REFERENCES work_tasks(organization_id,program_id,id)
    )""",
    "CREATE INDEX work_ancestor_descendants ON work_task_ancestors(ancestor_id,task_id)",
    """WITH RECURSIVE ancestry(organization_id,program_id,task_id,ancestor_id,parent_id,depth) AS (
        SELECT organization_id,program_id,id,id,parent_id,0 FROM work_tasks
        UNION ALL SELECT a.organization_id,a.program_id,a.task_id,t.id,t.parent_id,a.depth+1
        FROM ancestry a JOIN work_tasks t ON t.id=a.parent_id
    ) INSERT INTO work_task_ancestors SELECT organization_id,program_id,task_id,ancestor_id,depth FROM ancestry""",
    """CREATE TABLE work_workers (
        id TEXT PRIMARY KEY, organization_id TEXT NOT NULL REFERENCES organizations(id),
        issued_by TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
        roles_json TEXT NOT NULL, max_parallel INTEGER NOT NULL CHECK(max_parallel>=1),
        human_slots INTEGER NOT NULL, review_slots INTEGER NOT NULL,
        created_at INTEGER NOT NULL, last_seen_at INTEGER, revoked_at INTEGER,
        UNIQUE(organization_id,id)
    )""",
    """CREATE TABLE work_worker_projects (
        organization_id TEXT NOT NULL, worker_id TEXT NOT NULL, project_id TEXT NOT NULL,
        PRIMARY KEY(worker_id,project_id),
        FOREIGN KEY(organization_id,worker_id) REFERENCES work_workers(organization_id,id),
        FOREIGN KEY(organization_id,project_id) REFERENCES projects(organization_id,id)
    )""",
    """CREATE TABLE work_jobs (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
        organization_id TEXT NOT NULL, program_id TEXT NOT NULL, task_id TEXT NOT NULL,
        task_epoch INTEGER NOT NULL, lane TEXT NOT NULL CHECK(lane IN ('human','review','research')),
        status TEXT NOT NULL CHECK(status IN ('queued','leased','complete','failed','cancelled','outcome_unknown')),
        fence INTEGER NOT NULL DEFAULT 0, worker_id TEXT, attempt_id TEXT, lease_hash TEXT, lease_expires_at INTEGER,
        cancel_requested INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, finished_at INTEGER,
        UNIQUE(task_id,task_epoch), UNIQUE(organization_id,id),
        FOREIGN KEY(organization_id,program_id,task_id) REFERENCES work_tasks(organization_id,program_id,id),
        FOREIGN KEY(organization_id,worker_id) REFERENCES work_workers(organization_id,id)
    )""",
    "CREATE INDEX work_job_queue ON work_jobs(organization_id,status,lane,sequence)",
    "CREATE INDEX work_job_lease ON work_jobs(status,lease_expires_at,id)",
    "CREATE INDEX work_job_worker ON work_jobs(worker_id,status,id)",
    "CREATE INDEX work_job_program ON work_jobs(program_id,status,id)",
    """CREATE TABLE work_scheduler_policies (
        organization_id TEXT PRIMARY KEY REFERENCES organizations(id),
        max_parallel INTEGER NOT NULL CHECK(max_parallel>=1), human_slots INTEGER NOT NULL CHECK(human_slots>=0),
        review_slots INTEGER NOT NULL CHECK(review_slots>=0), CHECK(human_slots+review_slots<max_parallel)
    )""",
    """CREATE TABLE work_claims (
        worker_id TEXT NOT NULL REFERENCES work_workers(id), claim_id TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES work_jobs(id),
        attempt_id TEXT NOT NULL, fence INTEGER NOT NULL, created_at INTEGER NOT NULL,
        PRIMARY KEY(worker_id,claim_id)
    )""",
    """CREATE TABLE work_results (
        job_id TEXT NOT NULL REFERENCES work_jobs(id), fence INTEGER NOT NULL, attempt_id TEXT NOT NULL,
        worker_id TEXT NOT NULL REFERENCES work_workers(id), body_json TEXT NOT NULL, sha256 TEXT NOT NULL,
        created_at INTEGER NOT NULL, PRIMARY KEY(job_id,fence)
    )""",
    """CREATE TABLE work_effects (
        id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES work_jobs(id), program_id TEXT NOT NULL REFERENCES work_programs(id),
        task_id TEXT NOT NULL REFERENCES work_tasks(id), fence INTEGER NOT NULL, client_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('inference','compute','local')), request_sha256 TEXT NOT NULL,
        model TEXT, image_id TEXT, tool TEXT, held_microusd INTEGER NOT NULL CHECK(held_microusd>=0),
        held_cpu_seconds INTEGER NOT NULL CHECK(held_cpu_seconds>=0), actual_microusd INTEGER, actual_cpu_seconds INTEGER,
        state TEXT NOT NULL CHECK(state IN ('intent','settled','rejected','unknown')),
        receipt_json TEXT, created_at INTEGER NOT NULL, finished_at INTEGER,
        UNIQUE(job_id,fence,client_id)
    )""",
    "CREATE INDEX work_effect_budget ON work_effects(program_id,state,id)",
    "CREATE INDEX work_effect_job ON work_effects(job_id,fence,state,id)",
    """CREATE TABLE work_effect_scopes (
        effect_id TEXT NOT NULL REFERENCES work_effects(id), scope_kind TEXT NOT NULL CHECK(scope_kind IN ('program','task')),
        scope_id TEXT NOT NULL, PRIMARY KEY(effect_id,scope_kind,scope_id)
    )""",
    "CREATE INDEX work_scope_budget ON work_effect_scopes(scope_kind,scope_id,effect_id)",
    """CREATE TABLE work_resource_accounts (
        scope_kind TEXT NOT NULL CHECK(scope_kind IN ('program','task')), scope_id TEXT NOT NULL,
        microusd INTEGER NOT NULL DEFAULT 0 CHECK(microusd>=0), cpu_seconds INTEGER NOT NULL DEFAULT 0 CHECK(cpu_seconds>=0),
        turns INTEGER NOT NULL DEFAULT 0 CHECK(turns>=0), tools INTEGER NOT NULL DEFAULT 0 CHECK(tools>=0),
        PRIMARY KEY(scope_kind,scope_id)
    )""",
    """CREATE TABLE work_dispatch_counts (
        scope_kind TEXT NOT NULL CHECK(scope_kind IN ('lane','project','program','branch')), scope_id TEXT NOT NULL,
        dispatched INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(scope_kind,scope_id)
    )""",
    """CREATE TABLE work_scheduler_clocks (
        organization_id TEXT PRIMARY KEY REFERENCES organizations(id), tick INTEGER NOT NULL DEFAULT 0
    )""",
    *tuple(
        f"""CREATE TRIGGER work_result_{operation.lower()} BEFORE {operation} ON work_results
            BEGIN SELECT RAISE(ABORT,'work results are immutable'); END"""
        for operation in ("UPDATE", "DELETE")
    ),
)
