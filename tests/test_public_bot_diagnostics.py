"""Developer diagnostics disclose only allowlisted counters in one topic.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import json

import pytest

from reasoning_ledger.server.bot_diagnostics import collect
from reasoning_ledger.server.bot_runtime import BotRuntime
from reasoning_ledger.server.bot_store import BotStore
from reasoning_ledger.server.store import Store, StoreError


def test_developer_diagnostics_are_topic_scoped_and_do_not_read_or_forward_content(tmp_path):
    store = Store(tmp_path / "diagnostic-state")
    owner = store.bootstrap(store._setup_token, "owner@example.org", "Owner", "fixture password long", "First")
    user, org = owner["user_id"], owner["organization_id"]
    project = store.create_entity(user, org, "projects", "Project")
    channel = store.create_entity(user, org, "channels", "Channel", project["id"])
    topics = [store.create_entity(user, org, "topics", kind, channel["id"], kind)["id"] for kind in ("alpha", "beta")]
    topic = topics[1]
    secret = "private_message_token_and_host_secret"
    store.post_message(user, org, topic, secret, "diagnostic-local")
    store.post_message(user, org, topics[0], secret, "diagnostic-other")
    bots = BotStore(store)
    bot = bots.create(user, org, "Private bot name", [topic], ["messages:read", "messages:write"], personal=True)
    runtime = BotRuntime(bots)
    runtime.advertise(bot["token"], "codex", ["default"], False, False)
    message = store.post_message(user, org, topic, "@[" + bot["id"] + "] " + secret, "diagnostic-request")
    runtime.from_message(bot["token"], message["id"])
    with store.transaction(write=True) as db:
        db.execute("UPDATE bot_runs SET status=?,error_code=? WHERE bot_id=?", (secret, secret, bot["id"]))
    with store.transaction() as db:
        value = collect(db, org, topic)
        before = db.execute("SELECT status,error_code FROM bot_runs WHERE bot_id=?", (bot["id"],)).fetchone()
        missing = "f" * 32
        with pytest.raises(StoreError) as absent:
            collect(db, org, missing)
        assert absent.value.status == 404
        with pytest.raises(StoreError) as invalid:
            collect(db, org, topic + "' OR 1=1")
        assert invalid.value.status == 400
        after = db.execute("SELECT status,error_code FROM bot_runs WHERE bot_id=?", (bot["id"],)).fetchone()
    assert value["message_count"] == 2
    assert value["runs"]["bots"] == {"states": {"other": 1}, "errors": {"other": 1}}
    assert value["runs"]["inference"] == {"states": {}, "errors": {}}
    assert before[0] == after[0] == secret and before[1] == after[1] == secret
    body = json.dumps(value)
    assert secret not in body and bot["token"] not in body and "Private bot name" not in body
    assert org not in body and topic not in body
