"""Goal continuation through the selected bot, without OpenRouter admission.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import json
import time

from .research_goals import ResearchGoals
from .store import StoreError


def dispatch(runtime, db, bot):
    now = int(time.time())
    goals = db.execute("SELECT * FROM agent_goals WHERE status='active' AND model LIKE ? ORDER BY created_at,id LIMIT 8",
                       ("bot/" + bot["id"] + "/%",)).fetchall()
    for goal in goals:
        if goal["deadline_at"] <= now or goal["turns"] >= goal["max_turns"] or goal["tool_calls"] >= goal["max_tool_calls"]:
            stop(db, goal, "budget_limited", "The explicitly configured goal time, turn, or tool limit was reached.")
            continue
        if goal["bot_run_id"]:
            run = db.execute("SELECT * FROM bot_runs WHERE id=?", (goal["bot_run_id"],)).fetchone()
            if run["status"] in {"queued", "running", "waiting_approval"}:
                continue
            if run["status"] != "succeeded":
                stop(db, goal, "blocked", "The bot turn failed or has an unknown outcome; owner review is required.")
                continue
            if db.execute("SELECT 1 FROM bot_compute_approvals WHERE run_id=? AND status='pending'",
                          (run["id"],)).fetchone():
                continue
            if db.execute("SELECT 1 FROM bot_agent_tasks t LEFT JOIN bot_agent_handoffs h ON h.parent_run=t.parent_run "
                          "WHERE t.parent_run=? AND h.parent_run IS NULL", (run["id"],)).fetchone():
                continue
        if db.execute("SELECT 1 FROM bot_runs WHERE organization_id=? AND topic_id=? "
                      "AND status IN ('queued','running','waiting_approval')", (goal["organization_id"], goal["topic_id"])).fetchone():
            continue
        if db.execute("SELECT 1 FROM compute_jobs WHERE organization_id=? AND topic_id=? "
                      "AND status IN ('queued','running','outcome_unknown')", (goal["organization_id"], goal["topic_id"])).fetchone():
            continue
        through = db.execute("SELECT MAX(sequence) FROM messages WHERE organization_id=? AND topic_id=?",
                             (goal["organization_id"], goal["topic_id"])).fetchone()[0]
        prompt = ("Continue the active goal. Objective: " + goal["objective"] + "\nAcceptance criteria:\n"
                  + json.dumps(json.loads(goal["acceptance_json"]), ensure_ascii=False)
                  + "\nRead recent conversation and canonical records. Work on the next concrete step. "
                  "Call goal_checkpoint to record progress. Complete only with evidence for every criterion.")
        try:
            run = runtime.enqueue(goal["owned_by"], goal["organization_id"], goal["topic_id"], goal["model"],
                                  "goal-" + goal["id"] + "-" + str(goal["turns"]), through=through, prompt=prompt,
                                  effort=goal["effort"], db=db)
            db.execute("UPDATE agent_goals SET bot_run_id=?,turns=turns+1,updated_at=? WHERE id=?",
                       (run["id"], now, goal["id"]))
            ResearchGoals.event(db, goal["id"], None, {"type": "turn", "bot_run_id": run["id"], "turn": goal["turns"] + 1})
        except StoreError:
            stop(db, goal, "blocked", "Bot admission failed; inspect its connection, permissions, model and effort.")


def stop(db, goal, status, reason):
    db.execute("UPDATE agent_goals SET status=?,reason=?,updated_at=? WHERE id=?",
               (status, reason, int(time.time()), goal["id"]))
    ResearchGoals.event(db, goal["id"], None, {"type": "status", "status": status, "reason": reason})


def checkpoint(runtime, credential, run_id, lease, summary, status="active"):
    if status not in {"active", "complete", "blocked"} or not isinstance(summary, str) or not 1 <= len(summary.strip()) <= 4000:
        raise StoreError(400, "checkpoint requires evidence and an active, complete or blocked state")
    with runtime.store.transaction(write=True) as db:
        _, run = runtime.leased(db, credential, run_id, lease)
        goal = db.execute("SELECT * FROM agent_goals WHERE bot_run_id=? AND status='active'", (run_id,)).fetchone()
        if not goal:
            raise StoreError(409, "this bot invocation has no active goal")
        if status == "complete" and db.execute(
            "SELECT 1 FROM bot_compute_approvals a JOIN bot_runs r ON r.id=a.run_id "
            "WHERE r.organization_id=? AND r.topic_id=? AND a.status='pending'",
            (run["organization_id"], run["topic_id"]),
        ).fetchone():
            raise StoreError(409, "owner compute approval is pending; completion is not permitted")
        if status == "complete" and db.execute("SELECT 1 FROM compute_jobs WHERE organization_id=? AND topic_id=? "
             "AND status IN ('queued','running','outcome_unknown')", (run["organization_id"], run["topic_id"])).fetchone():
            raise StoreError(409, "compute results are pending or unknown; completion is not permitted")
        if status == "complete" and db.execute("SELECT 1 FROM bot_agent_tasks t JOIN bot_runs r ON r.id=t.child_run "
                    "WHERE t.goal_id=? AND r.status IN ('queued','running','waiting_approval','outcome_unknown')",
                    (goal["id"],)).fetchone():
            raise StoreError(409, "delegated work is pending or unknown; completion is not permitted")
        db.execute("UPDATE agent_goals SET status=?,reason=?,updated_at=? WHERE id=?",
                   (status, summary.strip(), int(time.time()), goal["id"]))
        ResearchGoals.event(db, goal["id"], None, {"type": "checkpoint", "bot_run_id": run_id, "summary": summary, "status": status})
        return {"goal_id": goal["id"], "status": status, "summary": summary}
