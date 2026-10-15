"""Deletion diagnoses and atomic subtree cancellation retaining execution evidence."""

import time


def stop_subtree(store, db, organization_id, topics):
    """Fence new effects; never assert an external process or unknown charge disappeared."""
    if not topics:
        return
    from .compute_store import ComputeStore
    from .inference_store import InferenceStore

    marks = ",".join("?" for _ in topics)
    args = (organization_id, *topics)
    inference = InferenceStore(store)
    for row in db.execute(inference._joined() + f"WHERE q.organization_id=? AND q.topic_id IN ({marks})", args).fetchall():
        inference._request_cancel(db, organization_id, row)
    # Cancellation/settlement needs no catalog or artifact filesystem access.
    compute = ComputeStore.__new__(ComputeStore)
    compute.store = store
    for row in db.execute(f"SELECT * FROM compute_jobs WHERE organization_id=? AND topic_id IN ({marks})", args).fetchall():
        compute._request_cancel(db, organization_id, row)
    now = int(time.time())
    db.execute(f"UPDATE topic_agent_threads SET status='cancelled',reason='topic_deleted' WHERE organization_id=? "
               f"AND topic_id IN ({marks}) AND status IN ('active','paused')", args)
    db.execute(f"UPDATE agent_goals SET status='cancelled' WHERE organization_id=? AND topic_id IN ({marks}) "
               "AND status IN ('active','paused','blocked')", args)
    db.execute(f"UPDATE workflow_runs SET cancel_requested=1 WHERE organization_id=? AND topic_id IN ({marks}) "
               "AND status IN ('running','awaiting_approval','outcome_unknown')", args)
    db.execute(f"UPDATE mathematical_reviews SET status='cancelled',finished_at=? WHERE organization_id=? "
               f"AND topic_id IN ({marks}) AND status='pending' AND reviewer_run_id IS NULL", (now, *args))
    db.execute(f"UPDATE research_proposals SET status='rejected',reviewed_at=? WHERE organization_id=? "
               f"AND topic_id IN ({marks}) AND status='pending'", (now, *args))
    runs = f"SELECT id FROM bot_runs WHERE organization_id=? AND topic_id IN ({marks})"
    for table in ("bot_execution_approvals", "bot_issue_approvals"):
        db.execute(f"UPDATE {table} SET status='denied',decided_at=? WHERE run_id IN ({runs}) "
                   "AND status IN ('pending','approved')", (now, *args))
    db.execute(f"UPDATE bot_compute_approvals SET status='denied' WHERE run_id IN ({runs}) AND status='pending'", args)
    db.execute(f"UPDATE bot_runs SET status=CASE WHEN status='queued' THEN 'cancelled' ELSE 'outcome_unknown' END, "
               f"error_code='stop_requested',lease_hash=NULL,updated_at=? WHERE organization_id=? AND topic_id IN ({marks}) "
               "AND status IN ('queued','running','waiting_approval')", (now, *args))


def workflow_blocked(db, run) -> bool:
    """A lagging parent summary is not active when every child is demonstrably terminal."""
    terminal = {"succeeded", "failed", "cancelled", "blocked"}
    nodes = db.execute("""SELECT a.* FROM workflow_node_attempts a WHERE a.organization_id=? AND a.run_id=?
        AND a.attempt=(SELECT MAX(b.attempt) FROM workflow_node_attempts b
          WHERE b.organization_id=a.organization_id AND b.run_id=a.run_id AND b.node_id=a.node_id)""",
        (run["organization_id"], run["id"])).fetchall()
    if not nodes:
        return True  # Missing evidence is not completion evidence.
    for node in nodes:
        if node["inference_run_id"]:
            child = db.execute("SELECT status,accounting_state FROM llm_runs WHERE id=? AND organization_id=?",
                               (node["inference_run_id"], run["organization_id"])).fetchone()
            if child is None or child["status"] in {"queued", "running"} or child["accounting_state"] not in {"settled", "released"}:
                return True
        elif node["compute_job_id"]:
            child = db.execute("SELECT status,accounted_at FROM compute_jobs WHERE id=? AND organization_id=?",
                               (node["compute_job_id"], run["organization_id"])).fetchone()
            if child is None or child["status"] not in terminal or child["accounted_at"] is None:
                return True
        elif node["bot_run_id"]:
            child = db.execute("SELECT status FROM bot_runs WHERE id=? AND organization_id=?",
                               (node["bot_run_id"], run["organization_id"])).fetchone()
            if child is None or child["status"] not in terminal:
                return True
        elif node["status"] not in terminal:
            if not (run["cancel_requested"] and node["status"] in {"pending", "awaiting_approval"}):
                return True
    return False


def blockers(db, organization_id: str, topics: list[str]) -> list[dict]:
    if not topics:
        return []
    marks, args = ",".join("?" for _ in topics), (organization_id, *topics)
    result = []
    sources = (
        ("compute", "compute_jobs", "status IN ('queued','running','outcome_unknown')",
         "Cancel the compute job; reconcile any unknown outcome."),
        ("bots", "bot_runs", "status IN ('queued','running','waiting_approval','outcome_unknown')",
         "Cancel the bot run or resolve its owner approval and unknown outcome."),
        ("agents", "agent_goals", "status='active'", "Stop or pause the topic goal."),
        ("reviews", "mathematical_reviews", "status IN ('pending','running')", "Finish or cancel the ledger review."),
    )
    for kind, table, predicate, action in sources:
        for row in db.execute(f"SELECT id,topic_id,status FROM {table} WHERE organization_id=? "
                              f"AND topic_id IN ({marks}) AND {predicate}", args):
            result.append({"kind": kind, **dict(row), "action": action})
    for row in db.execute("SELECT r.id,q.topic_id,r.status,r.accounting_state FROM llm_runs r JOIN llm_requests q "
                         "ON q.id=r.request_id AND q.organization_id=r.organization_id "
                         f"WHERE q.organization_id=? AND q.topic_id IN ({marks}) "
                         "AND (r.status IN ('queued','running') OR r.accounting_state='uncertain')", args):
        result.append({"kind": "inference", **dict(row), "action": "Cancel active inference; reconcile unknown provider charges."})
    for row in db.execute(f"SELECT * FROM workflow_runs WHERE organization_id=? AND topic_id IN ({marks}) "
                         "AND status IN ('running','awaiting_approval','outcome_unknown')", args):
        if workflow_blocked(db, row):
            result.append({"kind": "workflows", "id": row["id"], "topic_id": row["topic_id"], "status": row["status"],
                           "action": "Open this workflow and cancel or resolve its pending approval/unknown child outcome."})
    # A native coordinator with deferred children can be idle while the graph remains resumable.
    for row in db.execute("SELECT root_run,topic_id,status,reason FROM topic_agent_threads WHERE organization_id=? "
                         f"AND topic_id IN ({marks}) "
                         "AND status IN ('active','paused')", args):
        pending = db.execute("SELECT 1 FROM topic_agent_tasks a WHERE a.root_run=? AND NOT EXISTS "
                             "(SELECT 1 FROM topic_agent_handoffs h WHERE h.parent_run=a.parent_run) UNION ALL "
                             "SELECT 1 FROM topic_agent_waits w WHERE w.root_run=? AND NOT EXISTS "
                             "(SELECT 1 FROM topic_agent_handoffs h WHERE h.parent_run=w.parent_run) LIMIT 1",
                             (row["root_run"], row["root_run"])).fetchone()
        if pending:
            result.append({"kind": "agent_threads", "id": row["root_run"], "topic_id": row["topic_id"], "status": row["status"],
                           "action": "Use Stop agents in this beta topic; resolve any uncertain execution first."})
    return result
