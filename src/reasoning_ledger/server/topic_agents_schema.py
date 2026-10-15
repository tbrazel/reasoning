"""Conversation-owned delegation and exactly-once result handoffs.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

SCHEMA = (
    """CREATE TABLE topic_agent_threads (
        root_run TEXT PRIMARY KEY REFERENCES llm_runs(id),
        organization_id TEXT NOT NULL, topic_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','paused','cancelled','complete')),
        reason TEXT,
        FOREIGN KEY(organization_id,topic_id) REFERENCES topics(organization_id,id)
    )""",
    "CREATE INDEX topic_agent_thread_scope ON topic_agent_threads(organization_id,topic_id,status)",
    """CREATE TABLE topic_agent_tasks (
        id TEXT PRIMARY KEY, root_run TEXT NOT NULL REFERENCES topic_agent_threads(root_run),
        parent_run TEXT NOT NULL REFERENCES llm_runs(id), child_run TEXT UNIQUE REFERENCES llm_runs(id),
        child_bot_run TEXT UNIQUE REFERENCES bot_runs(id),
        client_id TEXT NOT NULL, role TEXT NOT NULL, objective TEXT NOT NULL, context_json TEXT NOT NULL,
        request_sha256 TEXT NOT NULL, created_at INTEGER NOT NULL,
        UNIQUE(parent_run,client_id), CHECK((child_run IS NULL)<>(child_bot_run IS NULL))
    )""",
    "CREATE INDEX topic_agent_task_parent ON topic_agent_tasks(parent_run,id)",
    "CREATE INDEX topic_agent_task_root ON topic_agent_tasks(root_run,id)",
    """CREATE TABLE topic_agent_waits (
        id TEXT PRIMARY KEY, root_run TEXT NOT NULL REFERENCES topic_agent_threads(root_run),
        parent_run TEXT NOT NULL REFERENCES llm_runs(id),
        compute_id TEXT REFERENCES compute_jobs(id), review_id TEXT REFERENCES mathematical_reviews(id),
        proposal_id TEXT REFERENCES research_proposals(id),
        CHECK((CASE WHEN compute_id IS NOT NULL THEN 1 ELSE 0 END)+
              (CASE WHEN review_id IS NOT NULL THEN 1 ELSE 0 END)+
              (CASE WHEN proposal_id IS NOT NULL THEN 1 ELSE 0 END)=1),
        UNIQUE(parent_run,compute_id), UNIQUE(parent_run,review_id), UNIQUE(parent_run,proposal_id)
    )""",
    "CREATE INDEX topic_agent_wait_parent ON topic_agent_waits(parent_run,id)",
    "CREATE INDEX topic_agent_wait_root ON topic_agent_waits(root_run,id)",
    """CREATE TABLE topic_agent_handoffs (
        parent_run TEXT PRIMARY KEY REFERENCES llm_runs(id), next_run TEXT NOT NULL UNIQUE REFERENCES llm_runs(id)
    )""",
    """CREATE TABLE topic_agent_dispatch (
        organization_id TEXT PRIMARY KEY REFERENCES organizations(id), tick INTEGER NOT NULL DEFAULT 0
    )""",
)
