"""Personal favorites and authorized bot routes before a topic exists.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import pytest
from test_public_server import installation as installation
from test_public_server import invited, structure

from reasoning_ledger.server.bot_runtime import BotRuntime
from reasoning_ledger.server.bot_store import BotStore
from reasoning_ledger.server.topic_forks import TopicForks


def test_favorites_are_personal_persisted_and_not_organization_settings(installation):
    config, store, client, owner = installation
    _, member_client, _ = invited(installation)
    endpoint = "/api/auth/model-favorites"
    first = client.put(endpoint, json={"model_id": "provider/research", "favorite": True})
    assert first.status_code == 200 and first.json() == {"user_id": owner["user_id"], "models": ["provider/research"]}
    assert member_client.get(endpoint).json()["models"] == []
    client.put(endpoint, json={"model_id": "provider/other", "favorite": True})
    client.put(endpoint, json={"model_id": "provider/research", "favorite": True})
    from fastapi.testclient import TestClient

    from reasoning_ledger.server.api import create_app

    second_device = TestClient(create_app(config), base_url=config.public_url)
    second_device.headers["Authorization"] = "Bearer " + owner["access_token"]
    assert second_device.get(endpoint).json()["models"] == ["provider/other", "provider/research"]
    client.put(endpoint, json={"model_id": "provider/research", "favorite": False})
    assert second_device.get(endpoint).json()["models"] == ["provider/other"]
    assert client.put(endpoint, json={"model_id": "bad model", "favorite": True}).status_code == 400
    assert client.put(endpoint, json={"model_id": "provider/one", "favorite": "yes"}).status_code == 422
    second_device.headers.clear()
    assert second_device.get(endpoint).status_code == 401
    # A member's preference change cannot overwrite the owner's preferences.
    assert member_client.put(endpoint, json={"model_id": "member/choice", "favorite": True}).status_code == 200
    assert client.get(endpoint).json()["models"] == ["provider/other"]


@pytest.mark.parametrize("following", [False, True])
def test_creation_lists_only_following_bots_and_revalidates_effort(installation, following):
    _, store, client, owner = installation
    _, channel, topics = structure(client, owner["organization_id"])
    bots = BotStore(store)
    bot = bots.create(owner["user_id"], owner["organization_id"], "My CLI", [topics[1]["id"]],
                      ["messages:read", "messages:write", "knowledge:read", "knowledge:write"], personal=True,
                      public_access=following)
    BotRuntime(bots).advertise(bot["token"], "codex", ["example"], False, False,
                              {"efforts": {"example": ["high", "xhigh"]}})
    path = "/api/organizations/" + owner["organization_id"]
    choices = client.get(path + "/channels/" + channel["id"] + "/bot-models")
    assert choices.status_code == 200
    model = f"bot/{bot['id']}/example"
    assert [item["id"] for item in choices.json()] == ([model] if following else [])
    body = {"name": "Bot first", "channel_id": channel["id"], "kind": "beta", "default_model": model, "default_effort": "high"}
    created = client.post(path + "/topics", json=body)
    assert created.status_code == (201 if following else 403), created.text
    if following:
        assert created.json()["default_model"] == model and created.json()["default_effort"] == "high"
        assert client.get(path + "/topics/" + created.json()["id"] + "/bot-models").json()[0]["id"] == model
        assert client.post(path + "/topics", json=body | {"default_effort": "max"}).status_code == 400
        bots.revoke(owner["user_id"], owner["organization_id"], bot["id"], personal=True)
        assert client.post(path + "/topics", json=body).status_code == 403


def test_creation_does_not_offer_owner_only_or_private_project_routes_to_other_users(installation):
    _, store, client, owner = installation
    _, member_client, member = invited(installation)
    _, channel, _ = structure(client, owner["organization_id"])
    bots = BotStore(store)
    bot = bots.create(owner["user_id"], owner["organization_id"], "Owner only", [],
                      ["messages:read", "messages:write"], personal=True, public_access=True)
    runtime = BotRuntime(bots)
    runtime.advertise(bot["token"], "codex", ["example"], False, False)
    endpoint = f"/api/organizations/{owner['organization_id']}/channels/{channel['id']}/bot-models"
    assert member_client.get(endpoint).json() == []
    private = store.create_entity(owner["user_id"], owner["organization_id"], "projects", "Private",
                                  visibility="private", owner_id=owner["user_id"], member_ids=[])
    private_channel = store.create_entity(owner["user_id"], owner["organization_id"], "channels", "Hidden", private["id"])
    assert member_client.get(endpoint.replace(channel["id"], private_channel["id"])).status_code == 404
    # Access checks run at actual creation, not just in the browser's picker.
    response = member_client.post(f"/api/organizations/{owner['organization_id']}/topics", json={
        "name": "Unauthorized", "channel_id": channel["id"], "kind": "beta", "default_model": f"bot/{bot['id']}/example"})
    assert response.status_code == 403


def test_fork_keeps_initial_model_and_effort_without_starting_inference(installation):
    _, store, client, owner = installation
    _, channel, _ = structure(client, owner["organization_id"])
    topic = store.create_entity(owner["user_id"], owner["organization_id"], "topics", "Effort",
        channel["id"], "beta", default_model="provider/research", default_effort="xhigh")
    message = store.post_message(owner["user_id"], owner["organization_id"], topic["id"], "A branch point", "fork-effort-message")
    fork = TopicForks(store).create(owner["user_id"], owner["organization_id"], topic["id"], message["id"], "Branch", "fork-effort")
    assert fork["topic"]["default_model"] == "provider/research" and fork["topic"]["default_effort"] == "xhigh"
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM llm_requests").fetchone()[0] == 0
