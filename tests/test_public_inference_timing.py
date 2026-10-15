"""Retained wait attribution without live providers, prompts, or guessed timings.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import json
from types import SimpleNamespace

import pytest
from test_public_inference import QUOTES, enqueue, prepare
from test_public_server import installation as installation

from reasoning_ledger.server import inference_store, inference_timing, usage_store
from reasoning_ledger.server.openrouter import Usage
from reasoning_ledger.server.usage_store import UsageStore


def test_timing_json_aggregates_keep_numeric_types_on_postgresql():
    from reasoning_ledger.server.postgres import query

    for field in ("recorded_at_ms", "elapsed_ms"):
        expression = f"SUM(CAST(json_extract(event_json,'$.{field}') AS BIGINT))"
        assert query(expression) == f"SUM(CAST(reasoning_json_extract(event_json,'$.{field}') AS BIGINT))"


def test_timing_reads_postgresql_mapping_rows_by_position_not_column_iteration():
    from reasoning_ledger.server.postgres import Row

    class Result:
        def __init__(self, values):
            self.values = values

        def fetchone(self):
            return self.values

        def fetchall(self):
            return self.values

    class Database:
        def execute(self, query, parameters):
            assert parameters == ("organization", "run")
            if "FROM llm_events" in query:
                # PostgreSQL names both unaliased aggregates "min". Scalar
                # positions retain all three values even with duplicate names.
                return Result(Row(["min", "min", "sum"], [1000, 3000, 123]))
            return Result([Row(["started_at_ms", "data_json"], [4000, json.dumps({
                "status": "succeeded", "latency_ms": 9000, "time_to_response_headers_ms": 4000,
                "time_to_first_token_ms": 6000,
            })])])

    timing = inference_timing.run_timing(Database(), {"organization_id": "organization", "id": "run", "status": "succeeded"})
    assert timing == {"wait_phase": None, "queue_wait_ms": 2000, "preparation_ms": 1000,
                      "provider_elapsed_ms": 9000, "time_to_response_headers_ms": 4000,
                      "time_to_first_token_ms": 6000, "tool_execution_ms": 123, "provider_calls": 1}


def test_live_timing_separates_queue_preparation_provider_and_completed_tools(installation, monkeypatch):
    _, state, _, owner, topics = prepare(installation)
    clock = [1000.0]
    fake = SimpleNamespace(time=lambda: clock[0])
    for module in (inference_store, inference_timing, usage_store):
        monkeypatch.setattr(module, "time", fake)
    row = enqueue(state, owner, topics[1]["id"], models=["test/one"])[0]
    user, org, identity = owner["user_id"], owner["organization_id"], row["id"]
    assert state.run(user, org, identity)["timing"]["wait_phase"] == "local_queue"
    clock[0] += 2
    claimed = state.claim()
    assert state.run(user, org, identity)["timing"]["wait_phase"] == "preparing_context"
    clock[0] += 1
    ledger = UsageStore(state.store)
    call = ledger.begin_call(claimed, QUOTES[0])
    timing = state.run(user, org, identity)["timing"]
    assert timing["queue_wait_ms"] == 2000 and timing["preparation_ms"] == 1000
    assert timing["wait_phase"] == "provider_wait"
    clock[0] += 4
    ledger.response_headers(call)
    ledger.response_headers(call)
    timing = state.run(user, org, identity)["timing"]
    assert timing["time_to_response_headers_ms"] == timing["provider_elapsed_ms"] == 4000
    assert timing["wait_phase"] == "provider_wait"
    clock[0] += 2
    ledger.first_token(call)
    timing = state.run(user, org, identity)["timing"]
    assert timing["time_to_response_headers_ms"] == 4000
    assert timing["time_to_first_token_ms"] == 6000 and timing["wait_phase"] == "provider_stream"
    clock[0] += 3
    ledger.finish_call(call, status="succeeded", usage=Usage(8, 20, 10))
    with state.store.transaction(write=True) as db:
        state._event(db, org, identity, {"type": "agent", "phase": "tool_requested", "name": "ledger_read"})
    assert state.run(user, org, identity)["timing"]["wait_phase"] == "tool_execution"
    with state.store.transaction(write=True) as db:
        state._event(db, org, identity, {"type": "agent", "phase": "tool_completed", "elapsed_ms": 123,
                                       "result": {"private": "not timing output"}})
    state.finish(claimed, "succeeded", usage=Usage(8, 20, 10))
    timing = state.run(user, org, identity)["timing"]
    assert timing["wait_phase"] is None and timing["tool_execution_ms"] == 123
    assert timing["provider_elapsed_ms"] == 9000 and timing["provider_calls"] == 1
    assert "private" not in json.dumps(timing)
    before = ledger.call(user, org, call)
    ledger.response_headers(call)
    assert ledger.call(user, org, call) == before


def test_historical_missing_timing_is_unknown_not_reconstructed_from_rounded_seconds(installation):
    _, state, _, owner, topics = prepare(installation)
    row = enqueue(state, owner, topics[1]["id"], models=["test/one"])[0]
    claimed = state.claim()
    with state.store.transaction(write=True) as db:
        db.execute("UPDATE llm_events SET event_json=? WHERE run_id=?", (json.dumps({"type": "state"}), row["id"]))
    state.finish(claimed, "failed", no_charge=True, error="provider_network")
    timing = state.run(owner["user_id"], owner["organization_id"], row["id"])["timing"]
    assert timing == {"wait_phase": None, "queue_wait_ms": None, "preparation_ms": None,
                      "provider_elapsed_ms": None, "time_to_response_headers_ms": None,
                      "time_to_first_token_ms": None, "tool_execution_ms": None, "provider_calls": 0}


def test_timing_remains_topic_permission_scoped(installation):
    from test_public_server import invited

    _, state, _, owner, topics = prepare(installation)
    row = enqueue(state, owner, topics[1]["id"], models=["test/one"])[0]
    _, _, member = invited(installation)
    with state.store.transaction(write=True) as db:
        db.execute("UPDATE projects SET visibility='private' WHERE id IN "
                   "(SELECT project_id FROM channels WHERE id=?)", (topics[1]["channel_id"],))
    from reasoning_ledger.server.store import StoreError

    with pytest.raises(StoreError):
        state.run(member["user_id"], owner["organization_id"], row["id"])
