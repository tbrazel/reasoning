"""Owner-scoped developer bot grants and exact, once-only GitHub issue approvals.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

SCHEMA = (
    """CREATE TABLE bot_developer_permissions (
        bot_id TEXT PRIMARY KEY REFERENCES bots(id), repositories_json TEXT NOT NULL,
        requesters_json TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE bot_issue_approvals (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES bot_runs(id), bot_id TEXT NOT NULL REFERENCES bots(id),
        owner_id TEXT NOT NULL REFERENCES users(id), repository TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
        document_sha256 TEXT NOT NULL, client_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
        created_at INTEGER NOT NULL, decided_at INTEGER, consumed_at INTEGER, issue_url TEXT,
        UNIQUE(run_id,client_id))""",
)
