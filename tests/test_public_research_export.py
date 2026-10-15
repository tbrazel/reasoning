"""Complete topic diagnostics preserve evidence while enforcing tenant/project access."""

import json

import pytest
from test_public_compute import compute as compute
from test_public_compute import enqueue
from test_public_research_agent import ResearchFixture, turn
from test_public_server import installation as installation
from test_public_server import invited

from reasoning_ledger.server.research_artifacts import ResearchArtifacts
from reasoning_ledger.server.research_context import SYSTEM_PROMPT
from reasoning_ledger.server.topic_diagnostics import pack_trace, preview_value, summarize_topic


def test_large_tool_results_are_explicit_previews_not_silent_data_loss():
    text = "Large source text " * 1000
    result = preview_value({"url": "https://example.org/source", "text": text, "nested": [text]})
    assert result["text"]["truncated"] and result["text"]["characters"] == len(text)
    assert len(result["text"]["preview"]) == 240 and len(result["text"]["sha256"]) == 64
    assert result["nested"][0] == result["text"] and len(json.dumps(result)) < 1000


def test_export_contains_all_messages_calls_and_events_without_operator_credentials(installation):
    fixture = ResearchFixture()
    state, owner, topics, run = turn(installation, fixture)
    client = installation[2]
    org, topic = owner["organization_id"], topics[1]["id"]
    for number in range(215):
        state.store.post_message(owner["user_id"], org, topic, f"Retained message {number}", f"export-message-{number}")
    response = client.get(f"/api/organizations/{org}/topics/{topic}/research-export")
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["schema"] == "reasoning.beta-topic-diagnostics.v1"
    assert len(value["messages"]) > 215 and value["messages"][-1]["content"] == "Retained message 214"
    assert len(value["provider_calls"]) == 2 and len(value["runs"]) == 1
    assert value["runs"][0]["id"] == run["id"] and value["runs"][0]["tool_receipts"]
    captured = [item["event"]["request"] for item in value["runs"][0]["events"]
                if item["event"].get("phase") == "provider_request"]
    assert captured == fixture.requests
    assert value["current_instructions"]["system_prompt"] == SYSTEM_PROMPT
    assert value["captured_contexts"] and value["continuation_policy"]["default_output_token_cap"] is None
    assert "sk-or-organization-test-secret" not in response.text
    assert "ciphertext" not in response.text and "access_token" not in response.text and "key_id" not in response.text
    assert json.loads(json.dumps(value)) == value
    universal = client.get(f"/api/organizations/{org}/topics/{topic}/export")
    assert universal.status_code == 200
    assert universal.json()["schema"] == "reasoning.topic-summary.v2"
    assert [item["content"] for item in universal.json()["messages"]] == [item["content"] for item in value["messages"]]
    assert universal.json()["energy_assumptions"]
    assert "latest_session" not in universal.json()["runs"][0]
    assert len(universal.content) < len(response.content)
    assert client.get(f"/api/organizations/{org}/topics/{topic}/export?detail=raw").json()["runs"] == value["runs"]


def test_prompt_is_readable_before_first_run_and_exports_enforce_project_scope(installation):
    _, store, client, owner = installation
    org = owner["organization_id"]
    project = store.create_entity(
        owner["user_id"], org, "projects", "Private research", visibility="private", owner_id=owner["user_id"]
    )
    channel = store.create_entity(owner["user_id"], org, "channels", "Research", project["id"])
    topic = store.create_entity(owner["user_id"], org, "topics", "Problem", channel["id"], "beta")
    root = f"/api/organizations/{org}/topics/{topic['id']}"
    prompt = client.get(root + "/research-instructions")
    assert prompt.status_code == 200 and prompt.json()["system_prompt"] == SYSTEM_PROMPT
    empty = client.get(root + "/research-export").json()
    assert empty["messages"] == [] and empty["runs"] == []
    _, foreign_client, _ = invited(installation, email="ordinary@example.org")
    assert foreign_client.get(root + "/research-export").status_code == 404
    assert foreign_client.get(root + "/research-instructions").status_code == 404
    for detail in ("summary", "full", "raw"):
        assert foreign_client.get(root + "/export?detail=" + detail).status_code == 404
    other = client.post("/api/organizations", json={"name": "Other", "credential_mode": "none"}).json()["id"]
    assert client.get(root.replace(org, other) + "/research-export").status_code == 404
    with store.transaction(write=True) as db:
        db.execute("UPDATE topics SET deleted_at=1 WHERE id=?", (topic["id"],))
    assert client.get(root + "/research-export").status_code == 404
    assert client.get(root + "/export").status_code == 404


@pytest.mark.parametrize("kind", ["alpha", "gamma"])
def test_all_topic_exports_are_complete_and_enforce_private_project_access(installation, kind):
    _, store, client, owner = installation
    org, user = owner["organization_id"], owner["user_id"]
    project = store.create_entity(user, org, "projects", "Private project", visibility="private", owner_id=user)
    channel = store.create_entity(user, org, "channels", "Private channel", project["id"])
    topic = store.create_entity(user, org, "topics", "Private topic", channel["id"], kind)
    for index in range(205):
        store.post_message(user, org, topic["id"], f"Message {index}", f"export-{index}")
    root = f"/api/organizations/{org}/topics/{topic['id']}"
    response = client.get(root + "/export")
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["schema"] == "reasoning.topic-summary.v2"
    assert len(value["messages"]) == 205 and value["messages"][-1]["content"] == "Message 204"
    assert value["current_instructions"] is None and value["continuation_policy"] is None
    assert response.headers["cache-control"] == "no-store"
    assert client.get(root + "/research-export").status_code == 400
    _, other_client, _ = invited(installation, email=f"outsider-{kind}@example.org")
    for detail in ("summary", "full", "raw"):
        assert other_client.get(root + "/export?detail=" + detail).status_code == 404
    other_org = client.post("/api/organizations", json={"name": "Other", "credential_mode": "none"}).json()["id"]
    for detail in ("summary", "full", "raw"):
        assert client.get(root.replace(org, other_org) + "/export?detail=" + detail).status_code == 404
    with store.transaction(write=True) as db:
        db.execute("UPDATE topics SET deleted_at=1 WHERE id=?", (topic["id"],))
    assert client.get(root + "/export").status_code == 404


def test_gamma_export_contains_workflow_history_compute_and_accessible_file_revisions(compute):
    _, store, _, client, owner, topics = compute
    org, user, topic = owner["organization_id"], owner["user_id"], topics[2]["id"]
    job = enqueue(compute)
    app = client.app.state
    definition = {"format": 1, "name": "Review", "nodes": [{"id": "review", "kind": "approval", "depends_on": [],
        "inputs": {}, "parameters": {"message": "Review evidence", "required_role": "admin"}}]}
    workflow = app.workflows.create(user, org, topic, "export-workflow-create", definition)
    run = app.workflow_runs.start(user, org, workflow["workflow_id"], 1, "export-workflow-run")
    artifacts = ResearchArtifacts(store)
    original = artifacts.write(user, org, topic, "proof.md", "First revision")
    artifacts.write(user, org, topic, "proof.md", "Second revision", original["sha256"])
    # A different project must not contribute files or workflow state.
    project = store.create_entity(user, org, "projects", "Unrelated")
    channel = store.create_entity(user, org, "channels", "Elsewhere", project["id"])
    unrelated = store.create_entity(user, org, "topics", "Other", channel["id"], "gamma")
    artifacts.write(user, org, unrelated["id"], "private.md", "Not part of this export")
    with store.transaction(write=True) as db:
        db.execute("UPDATE compute_jobs SET lease_hash='private-worker-lease' WHERE id=?", (job["id"],))
    response = client.get(f"/api/organizations/{org}/topics/{topic}/export?detail=raw")
    assert response.status_code == 200, response.text
    value = response.json()
    assert [item["id"] for item in value["workflows"]] == [workflow["workflow_id"]]
    assert value["workflow_versions"][0]["definition"] == definition
    assert value["workflow_runs"][0]["id"] == run["id"]
    assert value["workflow_runs"][0]["attempts"] and value["workflow_runs"][0]["events"]
    assert value["compute_jobs"][0]["id"] == job["id"] and value["compute_jobs"][0]["events"]
    assert [item["path"] for item in value["files"]] == ["proof.md"]
    assert [item["text"] for item in value["file_versions"]] == ["First revision", "Second revision"]
    assert "private-worker-lease" not in response.text and "lease_hash" not in response.text
    assert "Not part of this export" not in response.text


def unpack_trace(value):
    def unpack(item):
        if isinstance(item, dict):
            if "$ref" in item:
                return unpack(value["blobs"][item["$ref"]])
            if "$object" in item:
                return {key: unpack(child) for key, child in item["$object"]}
            return {key: unpack(child) for key, child in item.items()}
        return [unpack(child) for child in item] if isinstance(item, list) else item
    return unpack(value["data"])


def test_full_trace_roundtrip_preserves_user_reference_keys_unicode_and_types():
    text = "Repeated Unicode α 🧮 " * 100
    value = {"messages": [text, text], "weird": {"$ref": text, "$object": [["x", None]], "bool": False, "number": 0},
             "other": {"$ref": text, "$object": [["x", None]], "bool": False, "number": 0}}
    trace = pack_trace(value)
    assert trace["blobs"] and unpack_trace(trace) == value
    assert len(json.dumps(trace)) < len(json.dumps(value))


def test_full_trace_and_summary_are_scoped_and_keep_original_evidence(installation):
    fixture = ResearchFixture()
    state, owner, topics, _ = turn(installation, fixture)
    org, topic = owner["organization_id"], topics[1]["id"]
    client = installation[2]
    root = f"/api/organizations/{org}/topics/{topic}/export"
    raw = client.get(root + "?detail=raw").json()
    original = json.dumps(raw)
    summary = summarize_topic(raw)
    assert json.dumps(raw) == original
    assert summary["provider_calls"][0]["accounting_state"] == raw["provider_calls"][0]["accounting_state"]
    assert summary["runs"][0]["tool_receipts"] == raw["runs"][0]["tool_receipts"]
    trace = client.get(root + "?detail=full").json()
    decoded = unpack_trace(trace)
    assert decoded["messages"] == raw["messages"] and decoded["runs"] == raw["runs"]
    assert decoded["captured_contexts"] == raw["captured_contexts"]
    assert KEY_NOT_EXPORTED not in json.dumps(trace)
    assert client.get(root + "?detail=invalid").status_code == 422
    with state.store.transaction(write=True) as db:
        db.execute("UPDATE topics SET deleted_at=1 WHERE id=?", (topic,))
    for detail in ("summary", "full", "raw"):
        assert client.get(root + "?detail=" + detail).status_code == 404


def test_compact_tool_previews_omit_binary_payloads_but_full_traces_remain_exact():
    import hashlib

    from reasoning_ledger.server.topic_diagnostics import pack_trace, preview_value

    body = "QUJD" * 8192
    original = {"name": "media_chunk", "arguments": {"data_base64": body, "index": 3},
                "result": {"vision_base64": "QUJD", "image_url": {"url": "data:image/jpeg;base64," + body}},
                "source": {"id": "media-source", "sha256": "a" * 64}}
    preview = preview_value(original)
    assert preview["arguments"]["data_base64"]["encoded_characters"] == len(body)
    assert preview["arguments"]["data_base64"]["sha256"] == hashlib.sha256(body.encode()).hexdigest()
    assert preview["result"]["vision_base64"]["encoded_characters"] == 4
    assert "preview" not in preview["arguments"]["data_base64"]
    assert body[:240] not in json.dumps(preview)
    assert preview["source"] == original["source"]
    assert len(json.dumps(preview)) < 1000
    trace = pack_trace(original)
    assert original["arguments"]["data_base64"] == body
    assert body in json.dumps(trace)


KEY_NOT_EXPORTED = "sk-or-organization-test-secret"
