"""Real invitation/outbox transactions with bounded HTTPS provider fixtures."""

import asyncio
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import pytest
from public_schema_fixture import remove_collaboration_schema

from reasoning_ledger.server import cli
from reasoning_ledger.server.api import Config, create_app
from reasoning_ledger.server.backup import BackupError, backup, create_key, restore
from reasoning_ledger.server.mail import MAIL_ENDPOINT, MailConfig, MailStore, MailWorker, Resend
from reasoning_ledger.server.migrations import MIGRATIONS
from reasoning_ledger.server.store import Store, StoreError

CONFIG = MailConfig("invites@example.org", "re_private_sending_credential")
PROVIDER_ID = "a1111111-2222-3333-4444-555555555555"
PASSWORD = "private test account password"


@pytest.fixture
def installation(tmp_path):
    store = Store(tmp_path / "state")
    owner = store.bootstrap(store._setup_token, "owner@example.org", "Owner", PASSWORD, "Algebra")
    mail = MailStore(store, CONFIG, "https://chat.example.org")
    return store, owner, mail


def enqueue(installation, *, email="member@example.org", role="member", lifetime=3600, client="request_" + "a" * 24):
    _, owner, mail = installation
    return mail.enqueue(owner["user_id"], owner["organization_id"], email, role, lifetime, client)


def mail_row(store):
    with store.transaction() as db:
        return dict(db.execute("SELECT * FROM invitation_mail").fetchone())


def test_encrypted_atomic_outbox_survives_restart_and_lost_creation_reply(installation):
    store, owner, mail = installation
    invite = enqueue(installation)
    row = mail_row(store)
    assert row["state"] == "queued" and invite["token"].encode() not in row["ciphertext"]
    assert CONFIG.api_key.encode() not in row["ciphertext"]
    reopened = Store(store.data_dir)
    recovered = MailStore(reopened, CONFIG, mail.public_url)
    assert enqueue((reopened, owner, recovered)) == invite
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM invitations").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM invitation_mail").fetchone()[0] == 1
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    metadata = store.invitations(owner["user_id"], owner["organization_id"])
    assert metadata[0]["email_state"] == "queued" and "token" not in metadata[0]
    assert invite["token"] not in json.dumps(metadata)


def test_concurrent_creation_replay_is_one_invitation(installation):
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: enqueue(installation), range(2)))
    assert results[0] == results[1]


@pytest.mark.parametrize("change", ["email", "role", "lifetime", "sender", "credential", "origin"])
def test_replay_refuses_changed_request_or_provider_account(installation, change):
    store, owner, mail = installation
    enqueue(installation)
    values = (
        {"email": "other@example.org"}
        if change == "email"
        else {"role": "viewer"}
        if change == "role"
        else {"lifetime": 60}
        if change == "lifetime"
        else {}
    )
    if change in {"sender", "credential", "origin"}:
        config = (
            replace(CONFIG, sender="other@example.org")
            if change == "sender"
            else replace(CONFIG, api_key="re_rotated_credential")
            if change == "credential"
            else CONFIG
        )
        mail = MailStore(store, config, "https://other.example.org" if change == "origin" else mail.public_url)
    with pytest.raises(StoreError) as error:
        enqueue((store, owner, mail), **values)
    assert error.value.status == 409


def test_creation_rolls_back_if_outbox_cannot_be_persisted(installation, monkeypatch):
    store, _, _ = installation

    def fail(*_args, **_kwargs):
        raise ValueError("fixture encryption failed")

    monkeypatch.setattr(store._box, "seal_mail", fail)
    with pytest.raises(ValueError):
        enqueue(installation)
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM invitations").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM invitation_mail").fetchone()[0] == 0


def test_provider_submission_uses_exact_origin_fragment_and_no_organization_keys(installation):
    store, owner, mail = installation
    organization_secret = "organization_only_OpenRouter_secret"
    store.add_key(owner["user_id"], owner["organization_id"], "openrouter", "Organization inference", organization_secret)
    invite = enqueue(installation)
    calls = []

    def handler(request):
        assert str(request.url) == MAIL_ENDPOINT
        assert request.headers["authorization"] == "Bearer " + CONFIG.api_key
        assert request.headers["idempotency-key"] == "reasoning-invitation/" + invite["id"]
        message = json.loads(request.content)
        assert message["from"] == CONFIG.sender and message["to"] == [invite["email"]]
        assert "https://chat.example.org/#invite=" + invite["token"] in message["text"]
        assert set(message) == {"from", "to", "subject", "text"}
        assert organization_secret not in request.content.decode() and organization_secret not in str(request.headers)
        calls.append(message)
        return httpx.Response(200, json={"id": PROVIDER_ID})

    worker = MailWorker(mail, Resend(CONFIG, transport=httpx.MockTransport(handler)))
    assert asyncio.run(worker.execute_once())
    assert not asyncio.run(worker.execute_once())
    assert len(calls) == 1 and mail_row(store)["state"] == "accepted"
    assert mail_row(store)["provider_id"] == PROVIDER_ID
    assert store.invitations(owner["user_id"], owner["organization_id"])[0]["email_state"] == "accepted"
    # The mailed fragment is the actual invitation authority, not a synthetic receipt.
    member = store.accept_invitation(invite["token"], user_id=None, name="Member", password=PASSWORD)
    assert member["organization_id"] == owner["organization_id"]
    assert mail_row(store)["ciphertext"] is None
    assert len(store.members(owner["user_id"], owner["organization_id"])) == 2


@pytest.mark.parametrize(
    "status,state",
    [
        (400, "rejected"),
        (401, "rejected"),
        (403, "rejected"),
        (422, "rejected"),
        (429, "rejected"),
        (302, "unknown"),
        (408, "unknown"),
        (409, "unknown"),
        (500, "unknown"),
    ],
)
def test_provider_failure_is_safe_and_never_automatically_resubmitted(installation, status, state):
    store, _, mail = installation
    invite = enqueue(installation)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text=CONFIG.api_key + invite["token"], headers={"Location": "https://untrusted.example.org"})

    worker = MailWorker(mail, Resend(CONFIG, transport=httpx.MockTransport(handler)))
    assert asyncio.run(worker.execute_once()) and not asyncio.run(worker.execute_once())
    mail.recover()
    assert not asyncio.run(worker.execute_once())
    row = mail_row(store)
    assert row["state"] == state and len(calls) == 1
    assert CONFIG.api_key not in (row["error_code"] or "") and invite["token"] not in (row["error_code"] or "")
    assert enqueue(installation)["id"] == invite["id"]


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"id":"bad"}', b'{"id":42}', b"x" * 4097])
def test_malformed_success_is_uncertain(installation, body):
    store, _, mail = installation
    enqueue(installation)
    worker = MailWorker(mail, Resend(CONFIG, transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))))
    asyncio.run(worker.execute_once())
    assert mail_row(store)["state"] == "unknown"


def test_lost_provider_reply_does_not_retry_after_restart(installation):
    store, _, mail = installation
    enqueue(installation)
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("private upstream details", request=request)

    worker = MailWorker(mail, Resend(CONFIG, transport=httpx.MockTransport(handler)))
    asyncio.run(worker.execute_once())
    assert mail_row(store)["state"] == "unknown"
    fresh = MailStore(Store(store.data_dir), CONFIG, mail.public_url)
    fresh.recover()
    assert fresh.claim() is None and len(calls) == 1


def test_crash_after_claim_is_held_unknown_not_requeued(installation):
    store, _, mail = installation
    enqueue(installation)
    assert mail.claim() is not None
    fresh = MailStore(Store(store.data_dir), CONFIG, mail.public_url)
    fresh.recover()
    assert mail_row(store)["state"] == "unknown" and fresh.claim() is None


@pytest.mark.parametrize("reason", ["revoked", "accepted", "expired", "removed", "downgraded", "config_changed"])
def test_invalid_authority_is_not_sent_and_recoverable_credential_is_erased(installation, reason):
    store, owner, mail = installation
    actor = owner
    if reason in {"removed", "downgraded"}:
        grant = store.invite(owner["user_id"], owner["organization_id"], "admin@example.org", "admin", 3600)
        actor = store.accept_invitation(grant["token"], user_id=None, name="Admin", password=PASSWORD)
    invite = enqueue((store, actor, mail))
    if reason == "revoked":
        store.revoke_invitation(owner["user_id"], owner["organization_id"], invite["id"])
    elif reason == "accepted":
        store.accept_invitation(invite["token"], user_id=None, name="Member", password=PASSWORD)
    elif reason == "expired":
        with store.transaction(write=True) as db:
            db.execute("UPDATE invitations SET expires_at=1 WHERE id=?", (invite["id"],))
    elif reason in {"removed", "downgraded"}:
        store.change_member(owner["user_id"], owner["organization_id"], actor["user_id"], None if reason == "removed" else "member")
    else:
        mail = MailStore(store, replace(CONFIG, sender="changed@example.org"), mail.public_url)
    assert mail.claim() is None
    assert mail_row(store)["state"] == "cancelled" and mail_row(store)["ciphertext"] is None


def test_nonadmin_and_cross_organization_requests_fail(installation):
    store, owner, mail = installation
    invite = store.invite(owner["user_id"], owner["organization_id"], "member@example.org", "member", 3600)
    member = store.accept_invitation(invite["token"], user_id=None, name="Member", password=PASSWORD)
    for actor, org, status in [(member["user_id"], owner["organization_id"], 403), (owner["user_id"], "b" * 32, 404)]:
        with pytest.raises(StoreError) as error:
            mail.enqueue(actor, org, "next@example.org", "member", 3600, "stable_request_1234")
        assert error.value.status == status
        with pytest.raises(StoreError):
            mail.configuration(actor, org)


def test_daily_admission_limit_is_installation_wide_and_replays_are_free(installation):
    store, owner, mail = installation
    limited = MailStore(store, replace(CONFIG, daily_limit=1), mail.public_url)
    invite = enqueue((store, owner, limited))
    assert enqueue((store, owner, limited)) == invite
    with pytest.raises(StoreError) as error:
        enqueue((store, owner, limited), client="different_request_123")
    assert error.value.status == 429
    for i in range(1, 10):
        enqueue(installation, client=f"different_request_{i:04}")
    other = store.create_organization(owner["user_id"], "Other")
    mail.enqueue(owner["user_id"], other["id"], "next@example.org", "member", 3600, "different_request_999")
    limited = MailStore(store, replace(CONFIG, daily_limit=11), mail.public_url)
    with pytest.raises(StoreError) as error:
        limited.enqueue(owner["user_id"], other["id"], "next@example.org", "member", 3600, "different_request_998")
    assert error.value.status == 429


def test_entire_batch_queues_once_and_worker_automatically_paces_across_restart(installation, monkeypatch):
    store, owner, mail = installation
    clock = [2_000_000_000]
    monkeypatch.setattr("time.time", lambda: clock[0])
    addresses = [f"member{i}@example.org" for i in range(32)]
    batch = mail.enqueue_batch(owner["user_id"], owner["organization_id"], addresses, "member", 3600, "batch_request_0001")
    assert len(batch["results"]) == 32 and all(row["status"] == "queued" for row in batch["results"])
    assert batch["retry_after_seconds"] is None
    sent = []

    class Provider:
        async def send(self, row):
            sent.append(row["id"])
            return PROVIDER_ID

    worker = MailWorker(mail, Provider())
    for _ in range(10):
        assert asyncio.run(worker.execute_once())
    assert not asyncio.run(worker.execute_once()) and len(sent) == 10
    reopened = MailStore(Store(store.data_dir), CONFIG, mail.public_url)
    reopened.recover()
    worker = MailWorker(reopened, Provider())
    clock[0] += 59
    assert not asyncio.run(worker.execute_once())
    for remaining in (10, 10, 2):
        clock[0] += 60
        for _ in range(remaining):
            assert asyncio.run(worker.execute_once())
        assert not asyncio.run(worker.execute_once())
    assert len(sent) == len(set(sent)) == 32
    replay = reopened.enqueue_batch(owner["user_id"], owner["organization_id"], addresses, "member", 3600, "batch_request_0001")
    assert all(row["email_state"] == "accepted" for row in replay["results"])
    assert not asyncio.run(worker.execute_once())
    assert len(store.invitations(owner["user_id"], owner["organization_id"])) == 32


def test_delivery_quota_covers_verifications_other_organizations_and_uncertain_attempts(installation, monkeypatch):
    store, owner, mail = installation
    clock = [2_000_000_000]
    monkeypatch.setattr("time.time", lambda: clock[0])
    mail.verification.enqueue(owner["user_id"], "verification_request_0001")
    row = mail.claim()
    assert row["kind"] == "verification"
    mail.finish(row, "unknown", error="provider_uncertain")
    other = store.create_organization(owner["user_id"], "Other")
    for i in range(11):
        mail.enqueue(owner["user_id"], other["id"], f"other{i}@example.org", "member", 3600, f"other_request_{i:04}")
    for _ in range(9):
        row = mail.claim()
        assert row
        mail.finish(row, "rejected", error="provider_rejected")
    assert mail.claim() is None
    clock[0] += 60
    assert mail.claim() is not None
    with store.transaction() as db:
        assert db.execute("SELECT state FROM email_verifications").fetchone()[0] == "unknown"
        assert db.execute("SELECT COUNT(*) FROM invitation_mail WHERE state='rejected'").fetchone()[0] == 9


def test_concurrent_mail_claims_cannot_exceed_dispatch_allowance(installation):
    _, owner, mail = installation
    mail.enqueue_batch(owner["user_id"], owner["organization_id"], [f"member{i}@example.org" for i in range(20)],
                       "member", 3600, "batch_request_0001")
    with ThreadPoolExecutor(max_workers=4) as executor:
        rows = list(executor.map(lambda _: mail.claim(), range(20)))
    assert len({row["id"] for row in rows if row}) == 10
    mail.recover()
    assert mail.claim() is None  # recovery does not reset the durable quota


def test_daily_limited_batch_reports_real_wait_and_replays_retired_links(installation, monkeypatch):
    store, owner, mail = installation
    clock = [2_000_000_000]
    monkeypatch.setattr("time.time", lambda: clock[0])
    limited = MailStore(store, replace(CONFIG, daily_limit=2), mail.public_url)
    addresses = [f"member{i}@example.org" for i in range(4)]
    batch = limited.enqueue_batch(owner["user_id"], owner["organization_id"], addresses, "member", 3600, "batch_request_0001")
    assert [row["status"] for row in batch["results"]] == ["queued", "queued", "retryable", "retryable"]
    assert batch["retry_after_seconds"] == 86400
    store.revoke_invitation(owner["user_id"], owner["organization_id"], batch["results"][0]["invitation_id"])
    replay = limited.enqueue_batch(owner["user_id"], owner["organization_id"], addresses, "member", 3600, "batch_request_0001")
    assert replay["results"][0]["email_state"] == "cancelled"
    assert replay["results"][0]["invitation_id"] == batch["results"][0]["invitation_id"]
    clock[0] += 86400
    replay = limited.enqueue_batch(owner["user_id"], owner["organization_id"], addresses, "member", 3600, "batch_request_0001")
    assert all(row["status"] == "queued" for row in replay["results"])
    assert len(store.invitations(owner["user_id"], owner["organization_id"])) == 4


def test_startup_and_backup_refuse_wrong_key_and_tenant_ciphertext_swap(installation, tmp_path):
    store, owner, mail = installation
    enqueue(installation)
    other = store.create_organization(owner["user_id"], "Other")
    mail.enqueue(owner["user_id"], other["id"], "other@example.org", "member", 3600, "other_request_1234")
    with store.transaction(write=True) as db:
        rows = db.execute("SELECT invitation_id,ciphertext FROM invitation_mail").fetchall()
        db.execute("UPDATE invitation_mail SET ciphertext=? WHERE invitation_id=?", (rows[0]["ciphertext"], rows[1]["invitation_id"]))
    with pytest.raises(ValueError, match="cannot decrypt stored invitation email"):
        Store(store.data_dir)
    key = tmp_path / "backup.key"
    create_key(key)
    with pytest.raises(BackupError, match="cannot decrypt stored invitation email"):
        backup(store.data_dir, tmp_path / "backup.enc", key)
    # No provider key exists: the outbox alone still proves original encryption authority.
    (store.data_dir / "encryption.key").write_bytes(os.urandom(32))
    with pytest.raises(ValueError, match="cannot decrypt stored invitation email"):
        Store(store.data_dir)


def test_backup_restore_never_replays_external_mail(installation, tmp_path):
    store, _, mail = installation
    (store.data_dir / "mail.json").write_text(json.dumps(CONFIG.document()))
    (store.data_dir / "mail.json").chmod(0o600)
    enqueue(installation)
    assert mail.claim()
    key, archive = tmp_path / "backup.key", tmp_path / "backup.enc"
    create_key(key)
    result = backup(store.data_dir, archive, key)
    restored_path = tmp_path / "restored"
    restore(archive, key, result["sha256"], restored_path, source_stopped=True)
    restored = Store(restored_path)
    recovered = MailStore(restored, CONFIG, mail.public_url)
    assert recovered.claim() is None
    assert mail_row(restored)["state"] == "unknown" and mail_row(restored)["ciphertext"] is None
    assert not (restored_path / "mail.json").exists()


@pytest.mark.parametrize(
    "value",
    [
        "bad\n@example.org",
        "a@example.org,b@example.org",
        "Name <a@example.org>",
        "a@-example.org",
        ".a@example.org",
        "a..b@example.org",
        "a@example..org",
        "é@example.org",
    ],
)
def test_header_or_recipient_injection_is_refused(installation, value):
    with pytest.raises(StoreError):
        enqueue(installation, email=value)


def test_private_config_prompt_and_file_validation(tmp_path, monkeypatch, capsys):
    path = tmp_path / "mail.json"
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: CONFIG.api_key)
    assert cli.main(["--data-dir", str(tmp_path / "state"), "mail-config", "--sender", CONFIG.sender, "--output", str(path)]) == 0
    assert path.stat().st_mode & 0o777 == 0o600
    assert MailConfig.read(path) == CONFIG and CONFIG.api_key not in repr(CONFIG)
    assert CONFIG.api_key not in capsys.readouterr().out and not (tmp_path / "state").exists()
    with pytest.raises(SystemExit):
        cli.main(["--data-dir", str(tmp_path / "state"), "mail-config", "--sender", CONFIG.sender, "--output", str(path)])
    path.chmod(0o644)
    with pytest.raises(ValueError, match="invalid or unavailable private mail"):
        MailConfig.read(path)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "duplicate", "oversize", "unknown_provider", "extra"])
def test_invalid_private_mail_input_is_refused_without_echo(tmp_path, kind):
    path = tmp_path / "mail.json"
    value = CONFIG.document()
    if kind == "unknown_provider":
        value["provider"] = "untrusted"
    if kind == "extra":
        value["endpoint"] = "https://untrusted.example.org"
    body = json.dumps(value).encode()
    if kind == "duplicate":
        body = body[:-1] + b',"api_key":"private_duplicate"}'
    if kind == "oversize":
        body = b"x" * 8193
    path.write_bytes(body)
    path.chmod(0o600)
    if kind == "symlink":
        other = tmp_path / "original.json"
        path.rename(other)
        path.symlink_to(other)
    if kind == "hardlink":
        os.link(path, tmp_path / "duplicate.json")
    with pytest.raises(ValueError) as error:
        MailConfig.read(path)
    assert CONFIG.api_key not in str(error.value)


def test_schema_nine_upgrade_preserves_invitation_and_session(tmp_path):
    data = tmp_path / "old"
    data.mkdir(mode=0o700)
    with sqlite3.connect(data / "server.sqlite3") as db:
        for migration in MIGRATIONS[:9]:
            for statement in migration:
                db.execute(statement)
        db.execute("PRAGMA user_version=9")
    (data / "server.sqlite3").chmod(0o600)
    (data / "encryption.key").write_bytes(os.urandom(32))
    (data / "encryption.key").chmod(0o600)
    # A real schema-nine database upgrades without synthetic outbox rows.
    store = Store(data)
    owner = store.bootstrap(store._setup_token, "owner@example.org", "Owner", PASSWORD, "Algebra")
    invite = store.invite(owner["user_id"], owner["organization_id"], "member@example.org", "member", 3600)
    with store.transaction(write=True) as db:
        remove_collaboration_schema(db)
        for table in (
            "knowledge_writes",
            "knowledge_versions",
            "knowledge_entries",
            "user_presence",
            "bot_webhook_requests",
            "bot_webhook_deliveries",
            "bot_webhooks",
            "workflow_run_actions",
            "workflow_run_events",
            "workflow_node_attempts",
            "workflow_runs",
            "workflow_writes",
            "workflow_versions",
            "workflows",
        ):
            db.execute("DROP TABLE " + table)
        db.execute("DROP TABLE invitation_mail")
        db.execute("DROP INDEX invitation_org_identity")
        db.execute("PRAGMA user_version=9")
        db.execute("DROP TABLE model_impact_profiles")
        db.execute("ALTER TABLE llm_runs DROP COLUMN impact_profile_json")
        db.execute("ALTER TABLE topics DROP COLUMN default_model")
        db.execute("ALTER TABLE topics DROP COLUMN knowledge_policy_json")
        db.execute("DROP TABLE project_memberships")
        db.execute("ALTER TABLE projects DROP COLUMN visibility")
        db.execute("DROP INDEX message_org_identity")
    reopened = Store(data)
    assert reopened.session(owner["access_token"])["user_id"] == owner["user_id"]
    assert reopened.accept_invitation(invite["token"], user_id=None, name="Member", password=PASSWORD)
    with reopened.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM invitation_mail").fetchone()[0] == 0


def test_worker_lock_excludes_other_process_owner_and_recovers_only_after_release(installation):
    store, _, mail = installation
    first = MailWorker(mail)
    first.start()
    try:
        assert first.running
        second = MailWorker(MailStore(Store(store.data_dir), CONFIG, mail.public_url))
        with pytest.raises(BlockingIOError):
            second.start()
        assert not second.running
    finally:
        first.stop()
    assert not first.running
    second.start()
    second.stop()


def test_server_discovers_default_mail_file_without_exposing_it(tmp_path, monkeypatch):
    data = tmp_path / "state"
    data.mkdir(mode=0o700)
    path = data / "mail.json"
    path.write_text(json.dumps(CONFIG.document()))
    path.chmod(0o600)
    started = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **_kwargs: started.append(app))
    assert cli.main(["--data-dir", str(data), "serve", "--public-url", "https://chat.example.org", "--no-inference-worker"]) == 0
    assert started[0].state.config.mail == CONFIG
    path.chmod(0o644)
    with pytest.raises(SystemExit):
        cli.main(["--data-dir", str(data), "serve", "--public-url", "https://chat.example.org"])
    assert len(started) == 1


@pytest.mark.parametrize(
    "url", ["https://chat.example.org\n", "https://chat.exa\nmple.org", "https://chat.example.org\\wrong", "https://chat.example.org\x7f"]
)
def test_public_origin_cannot_inject_mail_body_or_browser_authority(tmp_path, url):
    with pytest.raises(ValueError):
        Config(tmp_path / "state", url, mail=CONFIG)


def test_application_lifespan_runs_and_stops_real_mail_worker(installation):
    store, owner, mail = installation
    invite = enqueue(installation)
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": PROVIDER_ID})

    app = create_app(
        Config(store.data_dir, mail.public_url, inference_enabled=False, mail=CONFIG),
        mail_provider=Resend(CONFIG, transport=httpx.MockTransport(handler)),
    )

    async def run():
        async with app.router.lifespan_context(app):
            assert app.state.mail_worker.running
            for _ in range(100):
                if mail_row(store)["state"] == "accepted":
                    break
                await asyncio.sleep(0.05)
            assert mail_row(store)["state"] == "accepted"
        assert not app.state.mail_worker.running

    asyncio.run(run())
    assert len(sent) == 1 and invite["token"] in sent[0]["text"]
    assert store.invitations(owner["user_id"], owner["organization_id"])[0]["email_state"] == "accepted"


def test_real_asgi_create_retry_deliver_and_accept(installation, monkeypatch):
    import fastapi.dependencies.utils
    import fastapi.routing
    import starlette.concurrency

    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    for module in (fastapi.dependencies.utils, fastapi.routing, starlette.concurrency):
        monkeypatch.setattr(module, "run_in_threadpool", inline)
    store, owner, _ = installation
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": PROVIDER_ID})

    origin = "https://chat.example.org"
    app = create_app(
        Config(store.data_dir, origin, inference_enabled=False, mail=CONFIG),
        mail_provider=Resend(CONFIG, transport=httpx.MockTransport(handler)),
    )

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=origin) as client:
            headers = {"Authorization": "Bearer " + owner["access_token"]}
            root = "/api/organizations/" + owner["organization_id"]
            configuration = await client.get(root + "/invitation-mail", headers=headers)
            assert configuration.json() == {"enabled": True, "daily_limit": 100, "worker_running": False}
            body = {"email": "member@example.org", "send_email": True, "client_id": "stable_request_1234"}
            first = await client.post(root + "/invitations", headers=headers, json=body)
            assert first.status_code == 201
            assert (await client.post(root + "/invitations", headers=headers, json=body)).json() == first.json()
            assert await app.state.mail_worker.execute_once()
            metadata = (await client.get(root + "/invitations", headers=headers)).json()
            assert metadata[0]["email_state"] == "accepted" and "token" not in metadata[0]
            token = sent[0]["text"].split("#invite=")[1].splitlines()[0]
            assert token == first.json()["token"]
            joined = await client.post(
                "/api/invitations/accept", json={"token": token, "name": "Member", "password": PASSWORD, "transport": "bearer"}
            )
            assert joined.status_code == 200 and joined.json()["organization_id"] == owner["organization_id"]

    asyncio.run(run())
