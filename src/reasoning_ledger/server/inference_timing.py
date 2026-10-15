"""Content-free attribution of local queue, provider, and research-tool waits.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json
import time


def run_timing(db, row) -> dict:
    """Report only retained measurements; missing historical timing stays null."""
    state = db.execute(
        "SELECT MIN(CASE WHEN json_extract(event_json,'$.status')='queued' THEN "
        "CAST(json_extract(event_json,'$.recorded_at_ms') AS BIGINT) END), "
        "MIN(CASE WHEN json_extract(event_json,'$.status')='running' THEN "
        "CAST(json_extract(event_json,'$.recorded_at_ms') AS BIGINT) END), "
        "SUM(CASE WHEN json_extract(event_json,'$.phase')='tool_completed' THEN "
        "CAST(json_extract(event_json,'$.elapsed_ms') AS BIGINT) END) "
        "FROM llm_events WHERE organization_id=? AND run_id=?",
        (row["organization_id"], row["id"]),
    ).fetchone()
    queued, started, tools = state[0], state[1], state[2]
    calls = db.execute(
        "SELECT i.started_at_ms,e.data_json FROM inference_calls i JOIN inference_call_events e "
        "ON e.sequence=(SELECT MAX(sequence) FROM inference_call_events WHERE call_id=i.id) "
        "WHERE i.organization_id=? AND i.run_id=? ORDER BY i.sequence",
        (row["organization_id"], row["id"]),
    ).fetchall()
    latest = json.loads(calls[-1]["data_json"]) if calls else {}
    active = row["status"] in {"queued", "running"}
    wait = "local_queue" if row["status"] == "queued" else "preparing_context" if active else None
    if calls and active:
        wait = "provider_stream" if latest.get("time_to_first_token_ms") is not None else "provider_wait"
        if latest.get("status") != "running":
            phase = db.execute(
                "SELECT json_extract(event_json,'$.phase') FROM llm_events WHERE organization_id=? AND run_id=? "
                "AND json_extract(event_json,'$.phase') IN ('tool_requested','tool_completed','provider_request','provider_response') "
                "ORDER BY sequence DESC LIMIT 1", (row["organization_id"], row["id"]),
            ).fetchone()
            wait = "tool_execution" if phase and phase[0] == "tool_requested" else "preparing_context"
    elapsed = latest.get("latency_ms")
    if calls and latest.get("status") == "running" and calls[-1]["started_at_ms"] is not None:
        elapsed = max(0, int(time.time() * 1000) - calls[-1]["started_at_ms"])
    return {
        "wait_phase": wait,
        "queue_wait_ms": max(0, started - queued) if queued is not None and started is not None else None,
        "preparation_ms": max(0, calls[0]["started_at_ms"] - started) if started is not None and calls
                          and calls[0]["started_at_ms"] is not None else None,
        "provider_elapsed_ms": elapsed,
        "time_to_response_headers_ms": latest.get("time_to_response_headers_ms"),
        "time_to_first_token_ms": latest.get("time_to_first_token_ms"),
        "tool_execution_ms": tools,
        "provider_calls": len(calls),
    }
