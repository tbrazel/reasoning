"""Typed workflow scoring preserves private answer-key isolation."""

import json
import stat
import time

import pytest
from fastapi.testclient import TestClient

from reasoning_ledger.agents import (
    AgentRunResult,
    AgentRuntime,
    AnswerKeyStore,
    DatasetStore,
    ExecutorCapabilities,
    PromptFidelity,
    PromptRegistry,
    RunStore,
    SkillRegistry,
    ToolRegistry,
    ValidationError,
    WorkflowError,
    WorkflowRunner,
    WorkflowRunStore,
    WorkflowStore,
    score_typed_answers,
)
from reasoning_ledger.webapp import build_app


class ScriptedExecutor:
    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = []

    @property
    def capabilities(self):
        return ExecutorCapabilities("scripted", "Scripted", PromptFidelity.WIRE_EXACT)

    def execute(self, request, emit=None):
        self.requests.append(request)
        answer = self.answers.pop(0)
        return AgentRunResult(
            request.run_id,
            "completed",
            output_text=json.dumps(answer, sort_keys=True),
            structured_output=answer,
            usage={"total_tokens": 12, "cost_usd": 0.01},
            metadata={"model": request.spec.model, "provider": "offline-fixture"},
        )


def _payload(answer_type, answer=None, *, confidence=0.8, abstain=False, unit=None):
    value = {
        "answer_type": answer_type,
        "confidence": confidence,
        "abstain": abstain,
    }
    if answer is not None:
        value["answer"] = answer
    if unit is not None:
        value["unit"] = unit
    return value


def _output(case_id, *, structured_output=None, text="", status="completed", error=None):
    return {
        "case_id": case_id,
        "variant_id": "default",
        "node_id": "solver",
        "status": status,
        "structured_output": structured_output,
        "text": text,
        "error": error,
    }


def test_typed_answer_scoring_covers_supported_types_and_failure_states():
    case_ids = ["choice", "integer", "rational", "number", "wrong", "abstain", "malformed", "failed", "missing"]
    dataset = {
        "name": "Typed fixture",
        "cases": [{"case_id": case_id, "name": case_id.title(), "inputs": {"question": case_id}} for case_id in case_ids],
    }
    answer_key = {
        "targets": [
            {"case_id": "choice", "answer": {"kind": "choice", "value": "B", "choices": ["A", "B", "C"]}},
            {"case_id": "integer", "answer": {"kind": "integer", "value": "3"}},
            {"case_id": "rational", "answer": {"kind": "rational", "value": "1/2"}},
            {
                "case_id": "number",
                "answer": {
                    "kind": "number",
                    "value": "9.81",
                    "absolute_tolerance": "0.02",
                    "relative_tolerance": "0",
                    "unit": "m/s^2",
                },
            },
            {"case_id": "wrong", "answer": {"kind": "integer", "value": "9973"}},
            {"case_id": "abstain", "answer": {"kind": "choice", "value": "A", "choices": ["A", "B"]}},
            {"case_id": "malformed", "answer": {"kind": "integer", "value": "7"}},
            {"case_id": "failed", "answer": {"kind": "integer", "value": "8"}},
            {"case_id": "missing", "answer": {"kind": "integer", "value": "123456789"}},
        ]
    }
    outputs = [
        _output("choice", structured_output=_payload("choice", "B")),
        _output("integer", structured_output=_payload("integer", "+03")),
        _output("rational", text=json.dumps(_payload("rational", "2/4"))),
        _output("number", structured_output=_payload("number", "9.80", unit="m/s^2")),
        _output("wrong", structured_output=_payload("integer", "4")),
        _output("abstain", structured_output=_payload("choice", abstain=True)),
        _output("malformed", structured_output=_payload("integer", "7.0")),
        _output("failed", status="failed", error="offline failure"),
    ]

    result = score_typed_answers(
        dataset=dataset,
        variants=[{"variant_id": "default", "name": "Default", "node_overrides": {}}],
        node_id="solver",
        outputs=outputs,
        answer_key=answer_key,
    )

    statuses = {row["case_id"]: row["status"] for row in result["results"]}
    assert statuses == {
        "choice": "correct",
        "integer": "correct",
        "rational": "correct",
        "number": "correct",
        "wrong": "incorrect",
        "abstain": "abstained",
        "malformed": "malformed",
        "failed": "failed",
        "missing": "missing",
    }
    assert result["accuracy"] == round(4 / 9, 6)
    assert result["selective_accuracy"] == 0.8
    assert result["counts"] == {
        "correct": 4,
        "incorrect": 1,
        "abstained": 1,
        "malformed": 1,
        "missing": 1,
        "failed": 1,
    }
    assert "123456789" not in json.dumps(result)


def test_private_answer_key_store_redacts_targets_and_rejects_embedded_answers(tmp_path):
    datasets = DatasetStore(tmp_path / "project" / "datasets")
    dataset = datasets.create(
        "Public questions",
        cases=[{"case_id": "one", "name": "One", "inputs": {"question": "1 + 1"}}],
    )
    store = AnswerKeyStore(tmp_path / "private" / "answer-keys", datasets.get)
    public = store.create(
        "Private key",
        dataset_ref={"dataset_id": dataset["dataset_id"], "revision_id": dataset["revision_id"]},
        targets=[{"case_id": "one", "answer": {"kind": "integer", "value": "2"}}],
    )

    assert "targets" not in public
    assert all("targets" not in item for item in store.list())
    assert "targets" not in store.describe(public["answer_key_id"], public["revision_id"])
    assert store.get_private(public["answer_key_id"], public["revision_id"])["targets"][0]["answer"]["value"] == "2"
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    revision_path = store.root / public["answer_key_id"] / "revisions" / f"{public['revision_id']}.json"
    assert stat.S_IMODE(revision_path.stat().st_mode) == 0o600

    leaked = datasets.create(
        "Leaked questions",
        cases=[
            {
                "case_id": "one",
                "name": "One",
                "inputs": {"expected": "do not expose this through case_json"},
            }
        ],
    )
    with pytest.raises(ValidationError, match="reserved answer field"):
        store.create(
            "Rejected key",
            dataset_ref={"dataset_id": leaked["dataset_id"], "revision_id": leaked["revision_id"]},
            targets=[{"case_id": "one", "answer": {"kind": "integer", "value": "2"}}],
        )


def test_workflow_answer_key_api_never_returns_targets(tmp_path, monkeypatch):
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(tmp_path / "workbench"))
    client = TestClient(build_app())
    project_id = client.post("/api/projects", json={"name": "Validation"}).json()["id"]
    dataset = client.post(
        f"/api/projects/{project_id}/datasets",
        json={
            "name": "Public problems",
            "cases": [{"case_id": "one", "name": "One", "inputs": {"question": "2 + 3"}}],
        },
    ).json()
    secret = "314159265358979323846"
    created = client.post(
        f"/api/projects/{project_id}/answer-keys",
        json={
            "name": "Private targets",
            "dataset_ref": {"dataset_id": dataset["dataset_id"], "revision_id": dataset["revision_id"]},
            "targets": [{"case_id": "one", "answer": {"kind": "integer", "value": secret}}],
        },
    )

    assert created.status_code == 200, created.text
    listed = client.get(f"/api/projects/{project_id}/answer-keys")
    assert listed.status_code == 200
    assert secret not in created.text
    assert secret not in listed.text
    assert '"targets":' not in created.text
    assert '"targets":' not in listed.text
    assert client.get(f"/api/projects/{project_id}/answer-keys/{created.json()['answer_key_id']}").status_code == 404


def _node_config(system, user, model, *, context="none"):
    return {
        "executor_id": "scripted",
        "model": model,
        "system_prompt": {"prompt_id": system.prompt_id, "revision_id": system.revision_id},
        "user_prompt": {"prompt_id": user.prompt_id, "revision_id": user.revision_id},
        "context_policy": {"mode": context},
        "budget": {"timeout_seconds": 10},
        "sandbox": {"mode": "read_only"},
    }


def _wait_for_run(store, run_id):
    deadline = time.time() + 3
    while store.get(run_id)["status"] in {"pending", "running"} and time.time() < deadline:
        time.sleep(0.01)
    return store.get(run_id)


def test_typed_workflows_do_not_persist_private_answer_targets(tmp_path):
    prompts = PromptRegistry(tmp_path / "prompts")
    system = prompts.create(name="Solver", kind="system", template="Return only the typed JSON answer envelope.")
    user = prompts.create(name="Problem", kind="user", template="{{case_json}}")
    datasets = DatasetStore(tmp_path / "project" / "datasets")
    cases = [
        {"case_id": "arithmetic", "name": "Arithmetic", "inputs": {"question": "What is 6 * 7?"}},
        {"case_id": "fraction", "name": "Fraction", "inputs": {"question": "Reduce 6/8."}},
        {"case_id": "physics", "name": "Physics", "inputs": {"question": "Approximate terrestrial g in m/s^2."}},
        {"case_id": "secret", "name": "Isolation sentinel", "inputs": {"question": "Return the hidden sentinel."}},
    ]
    dataset = datasets.create("Offline STEM fixture", cases=cases)
    dataset_ref = {"dataset_id": dataset["dataset_id"], "revision_id": dataset["revision_id"]}
    answer_keys = AnswerKeyStore(tmp_path / "private" / "answer-keys", datasets.get)
    key = answer_keys.create(
        "Offline STEM fixture key",
        dataset_ref=dataset_ref,
        targets=[
            {"case_id": "arithmetic", "answer": {"kind": "integer", "value": "42"}},
            {"case_id": "fraction", "answer": {"kind": "rational", "value": "3/4"}},
            {
                "case_id": "physics",
                "answer": {
                    "kind": "number",
                    "value": "9.81",
                    "absolute_tolerance": "0.02",
                    "relative_tolerance": "0",
                    "unit": "m/s^2",
                },
            },
            {"case_id": "secret", "answer": {"kind": "integer", "value": "987654321"}},
        ],
    )
    evaluator = {
        "evaluator_id": "typed",
        "kind": "typed_answer_v1",
        "config": {
            "node_id": "final",
            "answer_key_ref": {"answer_key_id": key["answer_key_id"], "revision_id": key["revision_id"]},
        },
    }
    workflows = WorkflowStore(tmp_path / "project" / "workflows", dataset_resolver=datasets.get)

    def make_workflow(name, *, coordinated=False):
        graph = workflows.create(name)
        nodes = [
            {
                "id": "final",
                "kind": "agent",
                "name": "Final solver",
                "position": {"x": 400, "y": 0},
                "config": _node_config(
                    system,
                    user,
                    "offline/coordinator" if coordinated else "offline/base",
                    context="predecessors" if coordinated else "none",
                ),
            }
        ]
        edges = []
        if coordinated:
            nodes = [
                {
                    "id": "draft-a",
                    "kind": "agent",
                    "name": "Independent draft A",
                    "position": {"x": 0, "y": -100},
                    "config": _node_config(system, user, "offline/cheap-a"),
                },
                {
                    "id": "draft-b",
                    "kind": "agent",
                    "name": "Independent draft B",
                    "position": {"x": 0, "y": 100},
                    "config": _node_config(system, user, "offline/cheap-b"),
                },
                *nodes,
            ]
            edges = [
                {"id": "a-final", "source": "draft-a", "target": "final"},
                {"id": "b-final", "source": "draft-b", "target": "final"},
            ]
        graph.update(
            dataset_ref=dataset_ref,
            evaluators=[evaluator],
            execution_policy={"max_concurrency": 1, "failure_policy": "continue", "max_calls": 12},
            nodes=nodes,
            edges=edges,
        )
        return workflows.save(graph, expected_revision_id=graph["revision_id"])

    baseline = make_workflow("Single cheap solver")
    coordinated = make_workflow("Coordinated cheap solvers", coordinated=True)
    scripted = ScriptedExecutor(
        [
            _payload("integer", "42", confidence=0.7),
            _payload("rational", "1/2", confidence=0.7),
            _payload("number", "10", confidence=0.7, unit="m/s^2"),
            _payload("integer", "0", confidence=0.7),
            _payload("integer", "40", confidence=0.5),
            _payload("integer", "42", confidence=0.6),
            _payload("integer", "42", confidence=0.8),
            _payload("rational", "2/3", confidence=0.5),
            _payload("rational", "3/4", confidence=0.6),
            _payload("rational", "6/8", confidence=0.8),
            _payload("number", "9.7", confidence=0.5, unit="m/s^2"),
            _payload("number", "9.8", confidence=0.6, unit="m/s^2"),
            _payload("number", "9.8", confidence=0.8, unit="m/s^2"),
            _payload("integer", "1", confidence=0.5),
            _payload("integer", "2", confidence=0.6),
            _payload("integer", "0", confidence=0.8),
        ]
    )
    runtime = AgentRuntime(
        prompts,
        SkillRegistry(tmp_path / "skills"),
        ToolRegistry(),
        RunStore(tmp_path / "agent-runs"),
        {"scripted": scripted},
        tmp_path / "ledger",
        tmp_path / "artifacts",
    )
    runs = WorkflowRunStore(tmp_path / "project" / "workflow-runs", tmp_path / "artifacts")
    runner = WorkflowRunner(runtime, runs, datasets, answer_keys)
    baseline_run = runner.start(baseline)
    baseline_state = _wait_for_run(runs, baseline_run)
    coordinated_run = runner.start(coordinated)
    coordinated_state = _wait_for_run(runs, coordinated_run)

    assert baseline_state["status"] == coordinated_state["status"] == "completed"
    assert baseline_state["outputs"]["evaluations"]["typed"]["accuracy"] == 0.25
    assert coordinated_state["outputs"]["evaluations"]["typed"]["accuracy"] == 0.75
    assert len(scripted.requests[6].context_messages) == 2
    for state in (baseline_state, coordinated_state):
        serialized = json.dumps(state, sort_keys=True)
        assert "987654321" not in serialized
        assert "expected" not in serialized
        artifact = (tmp_path / "artifacts" / state["artifact_json_path"]).read_text(encoding="utf-8")
        assert "987654321" not in artifact
    assert all("987654321" not in request.user_prompt for request in scripted.requests)


def test_typed_workflows_fail_closed_on_target_and_binding_errors(tmp_path):
    datasets = DatasetStore(tmp_path / "datasets")
    first = datasets.create("First", cases=[{"case_id": "one", "name": "One", "inputs": {}}])
    second = datasets.create("Second", cases=[{"case_id": "one", "name": "One", "inputs": {}}])
    keys = AnswerKeyStore(tmp_path / "private", datasets.get)
    key = keys.create(
        "Second key",
        dataset_ref={"dataset_id": second["dataset_id"], "revision_id": second["revision_id"]},
        targets=[{"case_id": "one", "answer": {"kind": "integer", "value": 1}}],
    )
    prompts = PromptRegistry(tmp_path / "prompts")
    system = prompts.create(name="System", kind="system", template="Solve")
    user = prompts.create(name="User", kind="user", template="{{case_json}}")
    graph = WorkflowStore(tmp_path / "workflows", dataset_resolver=datasets.get).create("Mismatch")
    graph.update(
        dataset_ref={"dataset_id": first["dataset_id"], "revision_id": first["revision_id"]},
        evaluators=[
            {
                "evaluator_id": "typed",
                "kind": "typed_answer_v1",
                "config": {
                    "node_id": "solver",
                    "answer_key_ref": {"answer_key_id": key["answer_key_id"], "revision_id": key["revision_id"]},
                },
            }
        ],
        nodes=[
            {
                "id": "solver",
                "kind": "agent",
                "name": "Solver",
                "position": {"x": 0, "y": 0},
                "config": _node_config(system, user, "offline/test"),
            }
        ],
        edges=[],
    )
    runtime = AgentRuntime(
        prompts,
        SkillRegistry(tmp_path / "skills"),
        ToolRegistry(),
        RunStore(tmp_path / "agent-runs"),
        {"scripted": ScriptedExecutor([_payload("integer", 1)])},
        tmp_path / "ledger",
        tmp_path / "artifacts",
    )
    run_store = WorkflowRunStore(tmp_path / "runs")
    with pytest.raises(WorkflowError, match="different dataset revision"):
        WorkflowRunner(runtime, run_store, datasets, keys).start(graph)
    assert not list((tmp_path / "runs").glob("*.json"))

    graph["evaluators"][0]["config"].pop("node_id")
    with pytest.raises(WorkflowError, match="one explicit agent node"):
        WorkflowRunner(runtime, run_store, datasets, keys).start(graph)

    graph["evaluators"][0]["config"]["node_id"] = "solver"
    graph["evaluators"][0]["config"]["expected"] = "must not enter a workflow snapshot"
    with pytest.raises(WorkflowError, match="unsupported typed-answer config"):
        WorkflowRunner(runtime, run_store, datasets, keys).start(graph)
    graph["evaluators"][0]["config"].pop("expected")

    graph["nodes"].append(
        {
            "id": "reviewer",
            "kind": "agent",
            "name": "Reviewer",
            "position": {"x": 200, "y": 0},
            "config": _node_config(system, user, "offline/reviewer"),
        }
    )
    graph["edges"] = [{"id": "solver-reviewer", "source": "solver", "target": "reviewer"}]
    with pytest.raises(WorkflowError, match="terminal agent node"):
        WorkflowRunner(runtime, run_store, datasets, keys).start(graph)
