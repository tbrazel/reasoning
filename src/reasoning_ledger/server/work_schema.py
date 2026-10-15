"""Durable versioned general-work contracts, execution dependencies and provenance."""

SCHEMA = (
    """CREATE TABLE work_programs (
        id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, project_id TEXT NOT NULL,
        owned_by TEXT NOT NULL REFERENCES users(id), client_id TEXT NOT NULL,
        head_revision INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'draft'
            CHECK(status IN ('draft','active','paused','blocked','complete','cancelled')),
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
        UNIQUE(organization_id,id), UNIQUE(organization_id,owned_by,client_id),
        FOREIGN KEY(organization_id,project_id) REFERENCES projects(organization_id,id)
    )""",
    "CREATE INDEX work_program_project ON work_programs(organization_id,project_id,id)",
    """CREATE TABLE work_specifications (
        organization_id TEXT NOT NULL, program_id TEXT NOT NULL, revision INTEGER NOT NULL,
        body_json TEXT NOT NULL, sha256 TEXT NOT NULL, reason TEXT NOT NULL,
        created_by TEXT NOT NULL REFERENCES users(id), created_at INTEGER NOT NULL,
        PRIMARY KEY(program_id,revision),
        FOREIGN KEY(organization_id,program_id) REFERENCES work_programs(organization_id,id)
    )""",
    """CREATE TABLE work_tasks (
        id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, program_id TEXT NOT NULL,
        parent_id TEXT, client_id TEXT NOT NULL, requested_by TEXT NOT NULL REFERENCES users(id),
        root_revision INTEGER NOT NULL, body_json TEXT NOT NULL, sha256 TEXT NOT NULL,
        role TEXT NOT NULL, kind TEXT NOT NULL, controller TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','ready','running','review','complete','blocked','cancelled','outcome_unknown')),
        epoch INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
        UNIQUE(organization_id,program_id,id), UNIQUE(program_id,requested_by,client_id),
        FOREIGN KEY(organization_id,program_id) REFERENCES work_programs(organization_id,id),
        FOREIGN KEY(program_id,root_revision) REFERENCES work_specifications(program_id,revision),
        FOREIGN KEY(organization_id,program_id,parent_id) REFERENCES work_tasks(organization_id,program_id,id)
    )""",
    "CREATE INDEX work_task_frontier ON work_tasks(organization_id,program_id,status,id)",
    "CREATE INDEX work_task_children ON work_tasks(program_id,parent_id,id)",
    """CREATE TABLE work_dependencies (
        organization_id TEXT NOT NULL, program_id TEXT NOT NULL, task_id TEXT NOT NULL, prerequisite_id TEXT NOT NULL,
        PRIMARY KEY(task_id,prerequisite_id), CHECK(task_id<>prerequisite_id),
        FOREIGN KEY(organization_id,program_id,task_id) REFERENCES work_tasks(organization_id,program_id,id),
        FOREIGN KEY(organization_id,program_id,prerequisite_id) REFERENCES work_tasks(organization_id,program_id,id)
    )""",
    "CREATE INDEX work_dependency_reverse ON work_dependencies(prerequisite_id,task_id)",
    """CREATE TABLE work_provenance (
        organization_id TEXT NOT NULL, program_id TEXT NOT NULL, owner_kind TEXT NOT NULL,
        owner_id TEXT NOT NULL, owner_revision INTEGER NOT NULL, input_kind TEXT NOT NULL,
        input_id TEXT NOT NULL, input_revision TEXT NOT NULL, input_sha256 TEXT NOT NULL,
        PRIMARY KEY(owner_kind,owner_id,owner_revision,input_kind,input_id,input_revision),
        FOREIGN KEY(organization_id,program_id) REFERENCES work_programs(organization_id,id)
    )""",
    "CREATE INDEX work_provenance_source ON work_provenance(organization_id,input_kind,input_id,input_revision)",
    """CREATE TABLE work_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, organization_id TEXT NOT NULL,
        program_id TEXT NOT NULL, task_id TEXT, actor_id TEXT REFERENCES users(id),
        event_json TEXT NOT NULL, created_at INTEGER NOT NULL,
        FOREIGN KEY(organization_id,program_id) REFERENCES work_programs(organization_id,id),
        FOREIGN KEY(organization_id,program_id,task_id) REFERENCES work_tasks(organization_id,program_id,id)
    )""",
    "CREATE INDEX work_event_page ON work_events(program_id,sequence)",
    """CREATE TABLE work_knowledge_relations (
        id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, program_id TEXT NOT NULL,
        created_by TEXT NOT NULL REFERENCES users(id), client_id TEXT NOT NULL, body_json TEXT NOT NULL,
        sha256 TEXT NOT NULL, source_kind TEXT NOT NULL, source_id TEXT NOT NULL, source_revision TEXT NOT NULL,
        target_kind TEXT NOT NULL, target_id TEXT NOT NULL, target_revision TEXT NOT NULL,
        relation TEXT NOT NULL, created_at INTEGER NOT NULL,
        UNIQUE(program_id,created_by,client_id),
        FOREIGN KEY(organization_id,program_id) REFERENCES work_programs(organization_id,id)
    )""",
    "CREATE INDEX work_relation_source ON work_knowledge_relations(program_id,source_kind,source_id,source_revision,id)",
    "CREATE INDEX work_relation_target ON work_knowledge_relations(program_id,target_kind,target_id,target_revision,id)",
    """CREATE TRIGGER work_relation_immutable_update BEFORE UPDATE ON work_knowledge_relations
        BEGIN SELECT RAISE(ABORT,'knowledge relations are immutable'); END""",
    """CREATE TRIGGER work_relation_immutable_delete BEFORE DELETE ON work_knowledge_relations
        BEGIN SELECT RAISE(ABORT,'knowledge relations are retained'); END""",
    """CREATE TRIGGER work_specification_immutable_update BEFORE UPDATE ON work_specifications
        BEGIN SELECT RAISE(ABORT,'work specifications are immutable'); END""",
    """CREATE TRIGGER work_specification_immutable_delete BEFORE DELETE ON work_specifications
        BEGIN SELECT RAISE(ABORT,'work specifications are retained'); END""",
    """CREATE TRIGGER work_task_specification_immutable BEFORE UPDATE ON work_tasks
        WHEN NEW.body_json<>OLD.body_json OR NEW.sha256<>OLD.sha256 OR NEW.root_revision<>OLD.root_revision
            OR NEW.program_id<>OLD.program_id OR NEW.organization_id<>OLD.organization_id
            OR NEW.parent_id IS NOT OLD.parent_id OR NEW.role<>OLD.role OR NEW.kind<>OLD.kind OR NEW.controller<>OLD.controller
        BEGIN SELECT RAISE(ABORT,'task specifications are immutable'); END""",
    """CREATE TRIGGER work_provenance_immutable_update BEFORE UPDATE ON work_provenance
        BEGIN SELECT RAISE(ABORT,'work provenance is immutable'); END""",
    """CREATE TRIGGER work_provenance_immutable_delete BEFORE DELETE ON work_provenance
        BEGIN SELECT RAISE(ABORT,'work provenance is retained'); END""",
)
