"""Narrow bot delegates and exactly-once result integration.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import json

from .security import digest
from .store import StoreError, _id


def delegate(runtime, credential, run_id, lease, role, objective, context="", bot_model=None):
    if role not in {"worker", "critic", "librarian", "formalizer", "verifier"}:
        raise StoreError(400, "select a supported delegate role")
    if not isinstance(objective, str) or not 1 <= len(objective.strip()) <= 8000 or len(context) > 24000:
        raise StoreError(400, "delegate needs a bounded objective and explicit relevant context")
    with runtime.store.transaction(write=True) as db:
        bot, run = runtime.leased(db, credential, run_id, lease)
        model = bot_model or f"bot/{bot['id']}/{run['model']}"
        fingerprint = digest(json.dumps([role, objective, context, model], ensure_ascii=False))
        prior = db.execute("SELECT id,child_run FROM bot_agent_tasks WHERE parent_run=? AND request_sha256=?",
                           (run_id, fingerprint)).fetchone()
        if prior:
            return dict(prior)
        child = runtime.enqueue(run["requested_by"], run["organization_id"], run["topic_id"], model,
                                "delegate-" + run_id + "-" + fingerprint[:24],
                                prompt=f"Assigned role: {role}.\n" + objective + "\nAssigned context:\n" + context,
                                through=run["context_through_sequence"], db=db)
        identity = _id()
        goal = db.execute("SELECT id FROM agent_goals WHERE bot_run_id=? UNION SELECT goal_id FROM bot_agent_tasks WHERE child_run=?",
                          (run_id, run_id)).fetchone()
        db.execute("INSERT INTO bot_agent_tasks VALUES(?,?,?,?,?,?,?,?)",
                   (identity, run_id, child["id"], role, objective, context, fingerprint, goal[0] if goal else None))
        return {"id": identity, "child_run": child["id"], "status": "queued",
                "instruction": "End this turn with a brief progress update. Results trigger integration automatically; do not poll."}


def tasks(runtime, credential, run_id, lease, task_id=None):
    with runtime.store.transaction() as db:
        runtime.leased(db, credential, run_id, lease)
        rows = [dict(row) for row in db.execute(
            "WITH RECURSIVE parents(id) AS (SELECT ? UNION SELECT h.parent_run FROM bot_agent_handoffs h JOIN parents p "
            "ON h.next_run=p.id) "
            "SELECT t.id,t.child_run,t.role,t.objective,r.status,r.output_text,r.error_code "
            "FROM bot_agent_tasks t JOIN bot_runs r ON r.id=t.child_run WHERE t.parent_run IN (SELECT id FROM parents) "
            "AND (? IS NULL OR t.id=?) ORDER BY t.id LIMIT 50", (run_id, task_id, task_id))]
        if task_id:
            if not rows:
                raise StoreError(404, "assigned bot task unavailable")
            seen = set()
            while rows[0]["child_run"] not in seen:
                seen.add(rows[0]["child_run"])
                leaf = db.execute("SELECT r.id,r.status,r.output_text,r.error_code FROM bot_agent_handoffs h "
                                  "JOIN bot_runs r ON r.id=h.next_run WHERE h.parent_run=?",
                                  (rows[0]["child_run"],)).fetchone()
                if leaf is None:
                    break
                rows[0].update(child_run=leaf["id"], status=leaf["status"], output_text=leaf["output_text"],
                               error_code=leaf["error_code"])
            return rows[0]
        return [{key: value for key, value in row.items() if key != "output_text"} for row in rows]


def dispatch(runtime, db, bot):
    rows = db.execute("SELECT p.* FROM bot_runs p "
                      "LEFT JOIN bot_agent_handoffs h ON h.parent_run=p.id "
                      "WHERE p.bot_id=? AND p.status='succeeded' AND h.parent_run IS NULL AND "
                      "(EXISTS(SELECT 1 FROM bot_agent_tasks t WHERE t.parent_run=p.id) OR "
                      "EXISTS(SELECT 1 FROM bot_compute_approvals a WHERE a.run_id=p.id)) ORDER BY p.created_at,p.id LIMIT 20",
                      (bot["id"],)).fetchall()
    for parent in rows:
        children = list(db.execute("SELECT c.* FROM bot_agent_tasks t JOIN bot_runs c ON c.id=t.child_run "
                                   "WHERE t.parent_run=? ORDER BY t.id", (parent["id"],)))
        for index, child in enumerate(children):
            seen = set()
            while child["id"] not in seen:
                seen.add(child["id"])
                following = db.execute("SELECT r.* FROM bot_agent_handoffs h JOIN bot_runs r ON r.id=h.next_run "
                                       "WHERE h.parent_run=?", (child["id"],)).fetchone()
                if following is None:
                    break
                child = following
            children[index] = child
        if any(child["status"] in {"queued", "running", "waiting_approval", "outcome_unknown"} for child in children):
            continue
        if any(db.execute("SELECT 1 FROM bot_agent_tasks t LEFT JOIN bot_agent_handoffs h ON h.parent_run=t.parent_run "
                          "WHERE t.parent_run=? AND h.parent_run IS NULL", (child["id"],)).fetchone() for child in children):
            continue
        computation = [dict(row) for row in db.execute(
            "SELECT a.id,a.status AS approval_status,a.job_id,j.status FROM bot_compute_approvals a "
            "LEFT JOIN compute_jobs j ON j.id=a.job_id WHERE a.run_id=? ORDER BY a.id", (parent["id"],))]
        if any(item["approval_status"] == "pending" or item["status"] in {"queued", "running", "outcome_unknown"}
               for item in computation):
            continue
        goal = db.execute("SELECT * FROM agent_goals WHERE bot_run_id=?", (parent["id"],)).fetchone()
        if goal and (goal["status"] != "active" or goal["turns"] >= goal["max_turns"]):
            continue
        try:
            summary = [{"run_id": child["id"], "status": child["status"], "error": child["error_code"],
                        "answer": child["output_text"]} for child in children]
            prompt = ("Integrate these completed delegated results into the original request. Check their evidence; "
                      "do not treat a worker's answer as verified.\nOriginal request:\n" + parent["prompt"]
                      + "\nDelegated results:\n" + json.dumps(summary, ensure_ascii=False))
            if computation:
                prompt += ("\nOwner-approved computation receipts:\n" + json.dumps(computation)
                           + "\nUse compute_result for completed jobs. Never invent results or rerun unknown execution.")
            if len(prompt) > 64000:
                prompt = "Integrate delegated work for request " + parent["id"] + ". Use agent_read to fetch exact child results."
            following = runtime.enqueue(parent["requested_by"], parent["organization_id"], parent["topic_id"],
                f"bot/{bot['id']}/{parent['model']}", "handoff-" + parent["id"], prompt=prompt,
                through=parent["context_through_sequence"], effort=parent["effort"], db=db)
            db.execute("INSERT INTO bot_agent_handoffs VALUES(?,?)", (parent["id"], following["id"]))
            if goal:
                db.execute("UPDATE agent_goals SET bot_run_id=?,turns=turns+1 WHERE id=?", (following["id"], goal["id"]))
        except StoreError:
            continue  # Access/offline/queue admission never broadens grants or replays inference.
