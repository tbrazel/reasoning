"""Attachment row and begin-rate bounds remain atomic under concurrent writers."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from test_public_media import PNG, upload
from test_public_server import installation as installation
from test_public_server import structure

from reasoning_ledger.server import research_media as module
from reasoning_ledger.server.research_media import ResearchMedia
from reasoning_ledger.server.store import StoreError


def test_atomic_attachment_creation_limits(installation, monkeypatch):
    _, store, client, owner = installation
    topic = structure(client, owner["organization_id"])[2][0]["id"]
    media = ResearchMedia(store)
    monkeypatch.setattr(module, "MAX_PENDING_FILES", 2)

    def begin(index):
        try:
            return media.begin(owner["user_id"], owner["organization_id"], topic, f"{index}.png", "image/png", 1, "0" * 64)
        except StoreError as exc:
            assert exc.status == 413
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(value is not None for value in pool.map(begin, range(4))) == 2
    with store.transaction(write=True) as db:
        assert db.execute("SELECT COUNT(*) FROM research_media").fetchone()[0] == 2
        db.execute("UPDATE research_media SET created_at=0")
    assert begin(5) is not None
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM research_media").fetchone()[0] == 1


def test_complete_file_count_and_begin_rate_are_separate_bounds(installation, monkeypatch):
    _, store, client, owner = installation
    org, user = owner["organization_id"], owner["user_id"]
    topic = structure(client, org)[2][0]["id"]
    media = ResearchMedia(store)
    monkeypatch.setattr(module, "MAX_ORGANIZATION_FILES", 1)
    upload(media, owner, topic, PNG)
    with pytest.raises(StoreError, match="count allowance"):
        media.begin(user, org, topic, "tiny.png", "image/png", 1, "0" * 64)
    monkeypatch.setattr(module, "MAX_ORGANIZATION_FILES", 10000)
    monkeypatch.setattr(module, "BEGINS_PER_MINUTE", 2)
    media.begin(user, org, topic, "other.png", "image/png", 1, "0" * 64)
    with pytest.raises(StoreError, match="creation rate") as limited:
        media.begin(user, org, topic, "more.png", "image/png", 1, "0" * 64)
    assert limited.value.status == 429
