"""Durable bot capabilities, goal turns, gateway receipts and reasoning settings.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

SCHEMA = (
    "ALTER TABLE bot_runtimes ADD COLUMN capabilities_json TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE bot_runs ADD COLUMN effort TEXT",
    "ALTER TABLE bot_runs ADD COLUMN gateway_json TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE bot_runs ADD COLUMN context_edit_sequence INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE agent_goals ADD COLUMN effort TEXT",
    "ALTER TABLE agent_goals ADD COLUMN bot_run_id TEXT REFERENCES bot_runs(id)",
    """CREATE TABLE bot_agent_tasks (
        id TEXT PRIMARY KEY, parent_run TEXT NOT NULL REFERENCES bot_runs(id), child_run TEXT NOT NULL REFERENCES bot_runs(id),
        role TEXT NOT NULL, objective TEXT NOT NULL, context TEXT NOT NULL, request_sha256 TEXT NOT NULL,
        goal_id TEXT REFERENCES agent_goals(id),
        UNIQUE(parent_run,request_sha256)
    )""",
    """CREATE TABLE bot_agent_handoffs (
        parent_run TEXT PRIMARY KEY REFERENCES bot_runs(id), next_run TEXT NOT NULL REFERENCES bot_runs(id)
    )""",
    """CREATE TABLE bot_compute_approvals (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES bot_runs(id), bot_id TEXT NOT NULL REFERENCES bots(id),
        organization_id TEXT NOT NULL REFERENCES organizations(id), owner_id TEXT NOT NULL REFERENCES users(id),
        parameters_json TEXT NOT NULL, request_sha256 TEXT NOT NULL, image_digest TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending','denied','submitted')),
        job_id TEXT REFERENCES compute_jobs(id), created_at INTEGER NOT NULL,
        UNIQUE(run_id,request_sha256)
    )""",
)
