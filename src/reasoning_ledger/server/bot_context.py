"""Authorized recent conversation for both alpha mentions and beta inference.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

from .research_context import source_message


def conversation(runtime, db, bot, run):
    runtime._run_access(db, bot, run)
    if (db.execute("SELECT 1 FROM topic_agent_tasks WHERE child_bot_run=?", (run["id"],)).fetchone()
            or db.execute("SELECT 1 FROM bot_agent_tasks WHERE child_run=?", (run["id"],)).fetchone()):
        return []  # Explicit delegated prompts carry only their assigned context.
    upper = run["context_through_sequence"]
    # Fork history consists of retained copied messages, not the source topic's
    # evolving transcript. Never follow a source topic past its fork boundary.
    rows = db.execute("SELECT m.*,u.name FROM messages m JOIN users u ON u.id=m.author_id "
                      "WHERE m.organization_id=? AND m.topic_id=? AND (? IS NULL OR m.sequence<=?) "
                      "ORDER BY m.sequence DESC LIMIT 20",
                      (run["organization_id"], run["topic_id"], upper, upper)).fetchall()
    cutoff = run["context_edit_sequence"]
    return [source_message(row, db, cutoff) for row in reversed(rows)]
