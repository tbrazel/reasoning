"""Durable organization research state; schema statements for migration 20."""

SCHEMA = (
    """CREATE TABLE research_topic_settings (
        organization_id TEXT NOT NULL, topic_id TEXT NOT NULL, tools_json TEXT NOT NULL,
        PRIMARY KEY(organization_id,topic_id),
        FOREIGN KEY(organization_id,topic_id) REFERENCES topics(organization_id,id)
    )""",
    """CREATE TABLE research_contexts (
        request_id TEXT PRIMARY KEY REFERENCES llm_requests(id), organization_id TEXT NOT NULL,
        topic_id TEXT NOT NULL, snapshot_json TEXT NOT NULL, created_at INTEGER NOT NULL
    )""",
    """CREATE TABLE research_memories (
        organization_id TEXT NOT NULL, topic_id TEXT NOT NULL, revision INTEGER NOT NULL,
        through_sequence INTEGER NOT NULL, body_json TEXT NOT NULL, source_digest TEXT NOT NULL,
        run_id TEXT REFERENCES llm_runs(id), created_at INTEGER NOT NULL,
        PRIMARY KEY(organization_id,topic_id,revision),
        FOREIGN KEY(organization_id,topic_id) REFERENCES topics(organization_id,id)
    )""",
    """CREATE TRIGGER research_memory_update BEFORE UPDATE ON research_memories
        BEGIN SELECT RAISE(ABORT,'research memory revisions are immutable'); END""",
    """CREATE TRIGGER research_memory_delete BEFORE DELETE ON research_memories
        BEGIN SELECT RAISE(ABORT,'research memory revisions are immutable'); END""",
    """CREATE TABLE research_pins (
        organization_id TEXT NOT NULL, topic_id TEXT NOT NULL, message_id TEXT NOT NULL,
        pinned_by TEXT NOT NULL REFERENCES users(id), created_at INTEGER NOT NULL,
        PRIMARY KEY(organization_id,topic_id,message_id),
        FOREIGN KEY(organization_id,message_id) REFERENCES messages(organization_id,id)
    )""",
    """CREATE TABLE research_sessions (
        run_id TEXT PRIMARY KEY REFERENCES llm_runs(id), organization_id TEXT NOT NULL,
        topic_id TEXT NOT NULL, state_json TEXT NOT NULL, updated_at INTEGER NOT NULL
    )""",
    """CREATE TABLE research_tool_receipts (
        id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES llm_runs(id),
        sequence INTEGER NOT NULL, name TEXT NOT NULL, arguments_json TEXT NOT NULL,
        result_json TEXT, status TEXT NOT NULL CHECK(status IN ('requested','completed','failed','uncertain')),
        created_at INTEGER NOT NULL, finished_at INTEGER, UNIQUE(run_id,sequence)
    )""",
    """CREATE TABLE agent_goals (
        id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, topic_id TEXT NOT NULL,
        owned_by TEXT NOT NULL REFERENCES users(id), client_id TEXT NOT NULL, objective TEXT NOT NULL, acceptance_json TEXT NOT NULL,
        model TEXT NOT NULL, status TEXT NOT NULL
            CHECK(status IN ('active','paused','blocked','complete','budget_limited','cancelled')),
        budget_microusd INTEGER NOT NULL, max_turns INTEGER NOT NULL, max_tool_calls INTEGER NOT NULL,
        deadline_at INTEGER NOT NULL, turns INTEGER NOT NULL DEFAULT 0, tool_calls INTEGER NOT NULL DEFAULT 0,
        run_id TEXT REFERENCES llm_runs(id), reason TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
        FOREIGN KEY(organization_id,topic_id) REFERENCES topics(organization_id,id), UNIQUE(organization_id,owned_by,client_id)
    )""",
    "CREATE UNIQUE INDEX agent_goal_live ON agent_goals(organization_id,topic_id) WHERE status IN ('active','paused','blocked')",
    """CREATE TABLE agent_goal_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, goal_id TEXT NOT NULL REFERENCES agent_goals(id),
        actor_id TEXT REFERENCES users(id), event_json TEXT NOT NULL, created_at INTEGER NOT NULL
    )""",
    """CREATE TABLE research_proposals (
        id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, topic_id TEXT NOT NULL,
        run_id TEXT NOT NULL REFERENCES llm_runs(id), requested_by TEXT NOT NULL REFERENCES users(id),
        body_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','reviewing','approved','rejected')),
        entry_id TEXT REFERENCES knowledge_entries(id), reviewed_by TEXT REFERENCES users(id),
        created_at INTEGER NOT NULL, reviewed_at INTEGER, result_json TEXT
    )""",
)
