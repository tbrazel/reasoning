import json

import pytest

from reasoning_ledger.agents import (
    AgentEventKind,
    AgentRunSpec,
    ContextPolicy,
    PromptBinding,
    RunBudget,
    RunStore,
    SessionPolicy,
)


def _spec() -> AgentRunSpec:
    return AgentRunSpec(
        executor_id="reasoning-native",
        model="openai/gpt-oss-20b",
        system_prompt=PromptBinding("student", "rev_system"),
        user_prompt=PromptBinding("weekly-work", "rev_user", {"week": 1}),
        context_policy=ContextPolicy(mode="selected", max_tokens=1200),
        budget=RunBudget(max_cost_usd=0.10, max_tool_calls=3),
    )


def test_run_spec_validates_budget_and_session():
    assert _spec().model == "openai/gpt-oss-20b"
    with pytest.raises(ValueError, match="positive"):
        RunBudget(timeout_seconds=0)
    with pytest.raises(ValueError, match="session_id"):
        SessionPolicy(mode="resume")
    normalized = AgentRunSpec(
        **{**_spec().__dict__, "model_provider": " openrouter "}
    )
    assert normalized.model_provider == "openrouter"
    with pytest.raises(ValueError, match="model_provider"):
        AgentRunSpec(**{**_spec().__dict__, "model_provider": "bad provider"})
    required = AgentRunSpec(
        **{**_spec().__dict__, "completion_tool_calls": {"arxiv_read": 2}}
    )
    assert required.completion_tool_calls == {"arxiv_read": 2}
    with pytest.raises(ValueError, match="positive integers"):
        AgentRunSpec(
            **{**_spec().__dict__, "completion_tool_calls": {"arxiv_read": 0}}
        )


def test_run_store_records_events_checkpoints_and_artifacts(tmp_path):
    store = RunStore(tmp_path / "runs")
    run_id = store.create(_spec(), run_id="run_test")

    event = store.append_event(run_id, AgentEventKind.RUN_STARTED, {"executor": "native"})
    assert event.sequence == 1
    assert store.append_event(run_id, AgentEventKind.MESSAGE_DELTA, {"text": "hi"}).sequence == 2

    checkpoint_id = store.checkpoint(run_id, {"next_node": "advisor"})
    assert store.load_checkpoint(run_id, checkpoint_id) == {"next_node": "advisor"}

    source = tmp_path / "paper.txt"
    source.write_text("result", encoding="utf-8")
    artifact = store.add_artifact(run_id, source, kind="research-note", media_type="text/plain")
    assert artifact.sha256
    assert (tmp_path / "runs" / run_id / "artifacts" / f"{artifact.artifact_id}-paper.txt").read_text() == "result"

    events = store.events(run_id)
    assert [entry["sequence"] for entry in events] == [1, 2, 3, 4]
    assert events[-1]["kind"] == "artifact.created"
    assert store.events(run_id, after=2)[0]["kind"] == "checkpoint.created"

    manifest = store.update_manifest(run_id, status="completed")
    assert manifest["status"] == "completed"
    assert json.loads((tmp_path / "runs" / run_id / "manifest.json").read_text())["spec"]["executor_id"] == "reasoning-native"


def test_run_ids_are_immutable(tmp_path):
    store = RunStore(tmp_path / "runs")
    store.create(_spec(), run_id="run_same")
    with pytest.raises(ValueError, match="already exists"):
        store.create(_spec(), run_id="run_same")
