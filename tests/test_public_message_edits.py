"""Author edits preserve history and captured research inputs without execution."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_public_inference import QUOTES, enqueue, prepare
from test_public_server import installation as installation
from test_public_server import invited, structure

from reasoning_ledger.server.research_context import ResearchContext
from reasoning_ledger.server.store import StoreError


def test_message_edits_history_retry_updates_and_mentions(installation):
    _, store, client, owner = installation
    org = owner["organization_id"]
    topic = structure(client, org)[2][0]["id"]
    _, member, account = invited(installation)
    path = f"/api/organizations/{org}/topics/{topic}"
    original = {"content": "Original question", "client_id": "original-question"}
    message = client.post(path + "/messages", json=original).json()
    edit_path = path + "/messages/" + message["id"]
    assert member.patch(edit_path, json={"content": "Not mine", "revision": 0}).status_code == 403
    content = f"Corrected question @[{account['user_id']}]"
    edited = client.patch(edit_path, json={"content": content, "revision": 0})
    assert edited.status_code == 200, edited.text
    assert edited.json()["revision"] == 1 and edited.json()["edited_at"]
    assert client.patch(edit_path, json={"content": content, "revision": 0}).json() == edited.json()
    assert client.patch(edit_path, json={"content": "Stale overwrite", "revision": 0}).status_code == 409
    assert client.post(path + "/messages", json=original).json()["content"] == content
    assert client.post(path + "/messages", json={**original, "content": content}).status_code == 409
    history = member.get(edit_path + "/history").json()
    assert [(row["revision"], row["content"]) for row in history] == [(0, original["content"]), (1, content)]
    updates = client.get(path + "/message-updates").json()
    assert updates["messages"][0]["id"] == message["id"]
    assert updates["messages"][0]["content"] == content
    assert client.get(path + "/message-updates", params={"after": updates["after"]}).json()["messages"] == []
    with store.transaction() as db:
        assert db.execute("SELECT user_id FROM message_mentions WHERE message_id=?", (message["id"],)).fetchone()[0] == account["user_id"]
        assert db.execute("SELECT COUNT(*) FROM llm_requests").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM bot_runs").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM bot_webhook_deliveries").fetchone()[0] == 0
    for mutation in ("UPDATE message_revisions SET content='Forged' WHERE message_id=?",
                     "DELETE FROM message_revisions WHERE message_id=?"):
        with pytest.raises(sqlite3.IntegrityError, match="message revisions are immutable"), store.transaction(write=True) as db:
            db.execute(mutation, (message["id"],))


def test_message_edit_requires_current_topic_write_access_and_human_author(installation):
    _, store, client, owner = installation
    org, user = owner["organization_id"], owner["user_id"]
    topic = structure(client, org)[2][0]["id"]
    _, member, account = invited(installation)
    path = f"/api/organizations/{org}/topics/{topic}/messages"
    human = member.post(path, json={"content": "My prompt", "client_id": "member-prompt"}).json()
    assert client.patch(path + "/" + human["id"], json={"content": "Admin override", "revision": 0}).status_code == 403
    store.change_member(user, org, account["user_id"], "viewer")
    assert member.patch(path + "/" + human["id"], json={"content": "Lost permission", "revision": 0}).status_code == 403
    store.change_member(user, org, account["user_id"], None)
    assert member.get(path + "/" + human["id"] + "/history").status_code == 404
    assert member.get(f"/api/organizations/{org}/topics/{topic}/message-updates").status_code == 404
    with store.transaction(write=True) as db:
        bot = store._post_message(db, user, org, topic, "Assistant response", "assistant-response", sender_kind="assistant")
    assert client.patch(path + "/" + bot["id"], json={"content": "Forged response", "revision": 0}).status_code == 403
    other_topic = structure(client, org)[2][0]["id"]
    assert client.get(f"/api/organizations/{org}/topics/{other_topic}/messages/{human['id']}/history").status_code == 404


def test_message_edit_concurrent_drafts_have_one_winner(installation):
    _, store, client, owner = installation
    org, user = owner["organization_id"], owner["user_id"]
    topic = structure(client, org)[2][0]["id"]
    message = store.post_message(user, org, topic, "Original", "concurrent-message")

    def save(content):
        try:
            return store.edit_message(user, org, topic, message["id"], content, 0)["revision"]
        except StoreError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(save, ["First draft", "Second draft"])) == [1, 409]
    assert len(store.message_history(user, org, topic, message["id"])) == 2


def test_edit_preserves_accepted_context_and_historical_compaction(installation):
    _, state, client, owner, topics = prepare(installation)
    store, org, user, topic = state.store, owner["organization_id"], owner["user_id"], topics[1]["id"]
    message = store.messages(user, org, topic)[0]
    run = enqueue(state, owner, topic, models=[QUOTES[0].model])[0]
    claimed = state.claim()
    original_context = json.loads(claimed["context_json"])
    context = ResearchContext(store)
    with store.transaction() as db:
        snapshot = json.loads(db.execute("SELECT snapshot_json FROM research_contexts WHERE request_id=?",
                                        (claimed["request_id"],)).fetchone()[0])
    store.edit_message(user, org, topic, message["id"], "Different revised research question", 0)
    with store.transaction() as db:
        preserved = json.loads(db.execute("SELECT context_json FROM llm_requests WHERE id=?", (claimed["request_id"],)).fetchone()[0])
        rebuilt, _ = context.compile(db, org, topic, message["sequence"], QUOTES[:1], user=user,
                                     edit_cutoff=snapshot["message_edit_sequence"])
        current, current_snapshot = context.compile(db, org, topic, message["sequence"], QUOTES[:1], user=user)
    assert preserved == original_context
    assert message["content"] in json.dumps(rebuilt)
    assert "Different revised research question" not in json.dumps(rebuilt)
    assert "Different revised research question" in json.dumps(current)
    batches = list(context.batches(org, topic, 0, message["sequence"], edit_cutoff=snapshot["message_edit_sequence"]))
    assert batches[0][0]["text"] == message["content"]
    body = {key: [] for key in ("definitions", "claims", "proofs", "sources", "questions", "tasks")}
    context.save_memory(claimed, message["sequence"], body, [message["id"]])
    with store.transaction() as db:
        assert context.memory(db, org, topic, message["sequence"], snapshot["message_edit_sequence"])
        assert context.memory(db, org, topic, message["sequence"], current_snapshot["message_edit_sequence"]) is None
        assert db.execute("SELECT COUNT(*) FROM llm_runs").fetchone()[0] == 1
    assert state.run(user, org, run["id"])["status"] == "running"
    store.edit_message(user, org, topic, message["id"], "A third version", 1)
    with store.transaction() as db:
        historical, _ = context.compile(db, org, topic, message["sequence"], QUOTES[:1], user=user,
                                        edit_cutoff=current_snapshot["message_edit_sequence"])
    assert "Different revised research question" in json.dumps(historical)
    assert "A third version" not in json.dumps(historical)
