"""Human feedback and immutable provenance for conversation branches."""

SCHEMA = (
    """CREATE TABLE chat_reactions (
        organization_id TEXT NOT NULL REFERENCES organizations(id), topic_id TEXT NOT NULL,
        target_key TEXT NOT NULL, user_id TEXT NOT NULL REFERENCES users(id),
        emoji TEXT NOT NULL CHECK(emoji IN ('up','down','upside_down')),
        model TEXT, created_at INTEGER NOT NULL,
        PRIMARY KEY(organization_id,target_key,user_id,emoji),
        FOREIGN KEY(organization_id,topic_id) REFERENCES topics(organization_id,id)
    )""",
    "CREATE INDEX chat_reaction_model ON chat_reactions(organization_id,model,topic_id)",
    """CREATE TABLE topic_forks (
        topic_id TEXT PRIMARY KEY REFERENCES topics(id), organization_id TEXT NOT NULL,
        source_topic_id TEXT NOT NULL, source_message_id TEXT NOT NULL,
        through_sequence INTEGER NOT NULL, created_by TEXT NOT NULL REFERENCES users(id),
        client_id TEXT NOT NULL, name TEXT NOT NULL, created_at INTEGER NOT NULL,
        UNIQUE(organization_id,created_by,client_id),
        FOREIGN KEY(organization_id,source_topic_id) REFERENCES topics(organization_id,id),
        FOREIGN KEY(organization_id,source_message_id) REFERENCES messages(organization_id,id)
    )""",
    """CREATE TABLE fork_message_sources (
        organization_id TEXT NOT NULL, message_id TEXT PRIMARY KEY REFERENCES messages(id),
        source_message_id TEXT NOT NULL, source_run_id TEXT, source_model TEXT,
        FOREIGN KEY(organization_id,source_message_id) REFERENCES messages(organization_id,id)
    )""",
)
