"""Canonical organization knowledge, private source links, and loss-tolerant imports."""

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from reasoning_ledger import organization_ledger as bridge
from reasoning_ledger.agents.project_tools import build_project_tool_registry
from reasoning_ledger.deployment.private import write_document
from reasoning_ledger.server.api import Config, create_app
from reasoning_ledger.server.bot_client import BotClient, BotClientError, configuration
from reasoning_ledger.server.bot_store import BotStore
from reasoning_ledger.store import LedgerStore, LedgerStoreError


@pytest.fixture
def organization(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    app = create_app(Config(tmp_path / "server", "https://math.example.org"))
    api = TestClient(app, base_url="https://math.example.org")
    store = app.state.store
    owner = store.bootstrap(store._setup_token, "owner@example.org", "Owner", "a long test password", "Research")
    user, org = owner["user_id"], owner["organization_id"]
    project = store.create_entity(user, org, "projects", "Algebra")
    channel = store.create_entity(user, org, "channels", "Research", project["id"])
    topic = store.create_entity(user, org, "topics", "Bot topic", channel["id"], "beta")
    other = store.create_entity(user, org, "topics", "Other topic", channel["id"], "gamma")
    bots = BotStore(store)
    bot = bots.create(user, org, "Workbench", [topic["id"]], ["messages:read", "knowledge:read", "knowledge:write"], 3600)
    config = tmp_path / "bot.json"
    write_document(config, configuration("https://math.example.org", bot["token"]), new=True)
    lost = {"next": False}

    def transport(request):
        response = api.request(
            request.method,
            request.url.raw_path.decode(),
            headers={
                "Authorization": request.headers["authorization"],
                "Content-Type": request.headers.get("content-type", "application/json"),
            },
            content=request.content,
        )
        if lost["next"] and request.method == "POST" and request.url.path == "/api/bot/knowledge":
            lost["next"] = False
            assert response.status_code == 201
            raise httpx.ReadTimeout("lost write receipt")
        return httpx.Response(
            response.status_code, headers={"content-type": response.headers.get("content-type", "")}, content=response.content
        )

    def factory(conf):
        return BotClient(conf, transport=httpx.MockTransport(transport))

    monkeypatch.setattr(bridge, "BotClient", factory)
    return api, app, owner, project, topic, other, bot, config, lost


def test_workbench_tools_use_canonical_ledger_and_fence_local_writes(organization, tmp_path):
    _api, app, owner, _project, _topic, _other, _bot, config, _lost = organization
    store = LedgerStore(tmp_path / "local-ledger")
    local, _revision = store.create_claim("Local source retained")
    link = bridge.link_ledger(store.root, config)
    assert link["local_records_retained"] is True
    registry = build_project_tool_registry(store, tmp_path / "artifacts")
    created = registry.invoke(
        "ledger_note", {"title": "Definition", "body": "$G$ is finite.", "state": "conjecture", "client_id": "stable-workbench-note"}
    )["entry"]
    assert app.state.knowledge.get(owner["user_id"], owner["organization_id"], created["id"])["body"] == "$G$ is finite."
    status = registry.invoke("ledger_status", {})
    assert status["authority"] == "organization" and status["entries"][0]["id"] == created["id"]
    assert registry.invoke("ledger_read", {"entry_id": created["id"]})["entry"]["revision"] == 1
    with pytest.raises(LedgerStoreError, match="canonical ledger"):
        store.create_claim("Must not create an independent authority")
    assert store.claim_head(local["claim_id"])["claim_id"] == local["claim_id"]
    with pytest.raises(BotClientError, match="human review"):
        bridge.linked_ledger(store.root).write(title="Proof", body="I assert it", client_id="wrong-promotion-request", state="established")
    assert configuration("https://math.example.org", _bot["token"])["token"] not in Path(link["link_file"]).read_text()
    moved = tmp_path / "moved-local-ledger"
    store.root.rename(moved)
    assert bridge.linked_ledger(moved).read(created["id"])["body"] == "$G$ is finite."
    with pytest.raises(LedgerStoreError, match="canonical ledger"):
        LedgerStore(moved).create_claim("Moving an archive cannot restore local authority")


def test_bot_exact_reads_preserve_revisions_and_scope_and_pagination(organization):
    api, app, owner, _project, topic, other, bot, config, _lost = organization
    ledger = bridge.OrganizationLedger(config, organization_id=owner["organization_id"])
    first = ledger.write(title="First", body="Original", client_id="first-ledger-write")
    revised = ledger.write(title="First", body="Revision", client_id="second-ledger-write", entry_id=first["id"], expected_revision=1)
    assert revised["revision"] == 2 and ledger.read(first["id"], 1)["body"] == "Original"
    ledger.write(title="Second", body="Other entry", client_id="third-ledger-write")
    page = ledger.listing(limit=1)
    next_page = ledger.listing(limit=1, before=page["next_cursor"])
    assert {page["entries"][0]["id"], next_page["entries"][0]["id"]} == {row["id"] for row in ledger.listing()["entries"]}
    assert ledger.listing(limit=1, before=next_page["next_cursor"])["entries"] == []
    hidden = app.state.knowledge.create(
        owner["user_id"],
        owner["organization_id"],
        kind="ledger",
        title="Ungrantable",
        body="Unrelated topic knowledge",
        state="note",
        scope_kind="topic",
        scope_id=other["id"],
        client_id="other-topic-knowledge",
    )
    visible = app.state.knowledge.create(
        owner["user_id"],
        owner["organization_id"],
        kind="ledger",
        title="Granted",
        body="Available topic knowledge",
        state="note",
        scope_kind="topic",
        scope_id=topic["id"],
        client_id="allowed-topic-knowledge",
    )
    assert visible["id"] in {entry["id"] for entry in ledger.listing()["entries"]}
    assert hidden["id"] not in {entry["id"] for entry in ledger.listing()["entries"]}
    response = api.get("/api/bot/knowledge/" + hidden["id"], headers={"Authorization": "Bearer " + bot["token"]})
    assert response.status_code == 404
    assert api.get("/api/bot/knowledge/" + visible["id"], headers={"Authorization": "Bearer " + bot["token"]}).status_code == 200


def test_bot_promotion_and_edits_to_reviewed_claims_require_human_review(organization):
    api, app, owner, _project, _topic, _other, bot, config, _lost = organization
    ledger = bridge.OrganizationLedger(config)
    entry = ledger.write(title="Conjecture", body="Original", client_id="bot-original-note")
    promoted = app.state.knowledge.revise(
        owner["user_id"],
        owner["organization_id"],
        entry["id"],
        kind="ledger",
        title="Conjecture",
        body="Human-reviewed statement",
        state="established",
        client_id="human-promotion-record",
        expected_revision=1,
    )
    base = {"kind": "ledger", "title": "Changed", "body": "Unreviewed new statement", "client_id": "bot-invalid-promotion"}
    headers = {"Authorization": "Bearer " + bot["token"]}
    assert (
        api.post("/api/bot/knowledge", headers=headers, json={**base, "state": "established", "scope_kind": "organization"}).status_code
        == 403
    )
    assert (
        api.put(
            "/api/bot/knowledge/" + entry["id"], headers=headers, json={**base, "state": "note", "expected_revision": promoted["revision"]}
        ).status_code
        == 403
    )
    assert ledger.read(entry["id"])["body"] == "Human-reviewed statement"
    assert ledger.write(title="Conjecture", body="Original", client_id="bot-original-note")["revision"] == 1


def test_direct_mcp_uses_shared_tools_and_keeps_goal_plumbing(organization, tmp_path, monkeypatch):
    from reasoning_ledger.mcp_server import build_server

    _api, _app, _owner, _project, _topic, _other, _bot, config, _lost = organization
    local = LedgerStore(tmp_path / "local")
    local.create_claim("Source")
    bridge.link_ledger(local.root, config)
    monkeypatch.setenv("REASONING_TASKS_PATH", str(tmp_path / "tasks.json"))
    monkeypatch.setenv("REASONING_GOAL_PATH", str(tmp_path / "goal.json"))
    server = build_server(local, {})
    tools = asyncio.run(server.list_tools())
    names = {tool.name for tool in tools}
    assert {"ledger_status", "ledger_read", "ledger_note", "ledger_revise", "tasks", "goal"} <= names
    assert not {"ledger_claim", "ledger_prove", "ledger_verify", "writer_start"} & names
    assert "organization's canonical research ledger" in server.instructions


def test_import_preserves_all_record_ids_revisions_and_authorship_after_lost_receipt(organization, tmp_path):
    _api, app, owner, _project, _topic, _other, _bot, config, lost = organization
    store = LedgerStore(tmp_path / "source")
    reference, _ = store.create_reference(author="Noether", title="Source", year=1921)
    claim, original = store.create_claim(
        "Original statement", reference_id=reference["reference_id"], author={"operator": "Ben", "agent": "Codex", "session": "s1"}
    )
    current = store.refine(claim["claim_id"], "Current statement")
    store.add_proof(claim["claim_id"], {"kind": "argument", "text": "Proof attempt", "format": "tex-prose-v1"})
    verification = store.add_verification(
        {"claim_id": claim["claim_id"], "revision_id": current["revision_id"]},
        verdict="established",
        authority="operator",
        evidence={"kind": "operator-assertion"},
    )
    snapshot = store.head_commit()
    before = {
        str(path.relative_to(store.root)): path.read_bytes()
        for parent in (store.claims_dir, store.references_dir)
        for path in parent.rglob("*.json")
    }
    lost["next"] = True
    ledger = bridge.OrganizationLedger(config)
    with pytest.raises(BotClientError):
        bridge.import_local_ledger(store, ledger, "algebra-source", tmp_path / "import-state")
    result = bridge.import_local_ledger(store, ledger, "algebra-source", tmp_path / "import-state")
    assert result["imported_records"] == len(before)
    assert bridge.import_local_ledger(store, ledger, "algebra-source", tmp_path / "import-state") == result
    rows = app.state.knowledge.list(owner["user_id"], owner["organization_id"])
    assert len(rows) == len(before)
    sources = [bridge.import_provenance(entry) for entry in rows]
    assert {source["source_path"] for source in sources} == set(before)
    assert all(source["source_commit"] == snapshot for source in sources)
    imported_original = next(source for source in sources if source["original_id"] == original["revision_id"])
    assert imported_original["record"] == original and imported_original["record"]["author"]["operator"] == "Ben"
    assert verification["verification_id"] in {source["original_id"] for source in sources}
    assert all(entry["state"] in {"note", "active"} for entry in rows)
    assert before == {
        str(path.relative_to(store.root)): path.read_bytes()
        for parent in (store.claims_dir, store.references_dir)
        for path in parent.rglob("*.json")
    }


def test_workbench_binding_and_entries_never_expose_credentials(organization, tmp_path, monkeypatch):
    from reasoning_ledger.webapp import build_app

    _api, _app, _owner, _project, _topic, _other, bot, config, _lost = organization
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(tmp_path / "workbench"))
    client = TestClient(build_app())
    project = client.post("/api/projects", json={"name": "Attached"}).json()
    project_id = project["id"]
    root = tmp_path / "workbench" / "projects" / project_id / "ledger"
    assert client.get(f"/api/projects/{project_id}/ledger/organization").json() == {"linked": False}
    bridge.link_ledger(root, config)
    response = client.get(f"/api/projects/{project_id}/ledger/organization")
    assert response.json()["linked"] is True
    assert bot["token"] not in response.text and str(config) not in response.text
    entries = client.get(f"/api/projects/{project_id}/ledger/organization/entries")
    assert entries.status_code == 200 and entries.json()["authority"] == "organization"
