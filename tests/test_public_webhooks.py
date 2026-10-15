"""Outgoing bot subscription authority, capture, recovery, signing, and pinned HTTPS."""

import asyncio
import hashlib
import json
import ssl
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography import x509
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from reasoning_ledger.server import webhook_store as module
from reasoning_ledger.server import webhook_transport as transport_module
from reasoning_ledger.server.api import Config, create_app
from reasoning_ledger.server.backup import BackupError, backup, create_key, restore
from reasoning_ledger.server.bot_client import BotClientError
from reasoning_ledger.server.bot_store import BotStore
from reasoning_ledger.server.security import SecretBox
from reasoning_ledger.server.store import Store, StoreError
from reasoning_ledger.server.webhook_receiver import ReceiverError, ResearchReceiver, create_receiver
from reasoning_ledger.server.webhook_store import WebhookStore
from reasoning_ledger.server.webhook_transport import WebhookError, WebhookTransport, endpoint, public_address, signature, verify_signature
from reasoning_ledger.server.webhook_worker import WebhookWorker


@pytest.fixture
def fixture(tmp_path):
    store = Store(tmp_path / "server")
    owner = store.bootstrap(store._setup_token, "owner@example.org", "Owner", "fixture password long", "First")
    user, org = owner["user_id"], owner["organization_id"]
    project = store.create_entity(user, org, "projects", "Project")
    channel = store.create_entity(user, org, "channels", "Channel", project["id"])
    topics = [store.create_entity(user, org, "topics", kind, channel["id"], kind)["id"] for kind in ("alpha", "beta")]
    bots = BotStore(store)
    bot = bots.create(user, org, "Research bot", topics[:1], ["messages:read", "messages:write"])
    hooks = WebhookStore(store)
    return store, owner, topics, bots, bot, hooks


def create(fixture, **changes):
    _, owner, _, _, bot, hooks = fixture
    args = {"bot_id": bot["id"], "url": "https://receiver.example/hooks", "prefix": "!gcd", "client_id": "subscription-fixture-001"}
    return hooks.create(owner["user_id"], owner["organization_id"], **(args | changes))


def post(fixture, content="!gcd 24 36", client="message-fixture-001", topic=None):
    store, owner, topics, _, _, _ = fixture
    return store.post_message(owner["user_id"], owner["organization_id"], topic or topics[0], content, client)


def records(fixture, hook):
    _, owner, _, _, _, hooks = fixture
    return hooks.deliveries(owner["user_id"], owner["organization_id"], hook["id"], limit=100)


def due(store):
    with store.transaction(write=True) as db:
        db.execute("UPDATE bot_webhook_deliveries SET next_attempt_at=0 WHERE state='queued'")


def test_atomic_scoped_capture_excludes_history_bot_replies_assistants_and_replays(fixture):
    store, owner, topics, bots, bot, hooks = fixture
    post(fixture, client="before-subscription")
    hook = create(fixture)
    message = post(fixture)
    assert post(fixture)["id"] == message["id"]
    post(fixture, content="ordinary message", client="not-command")
    post(fixture, client="not-granted", topic=topics[1])
    bots.post(bot["token"], topics[0], "!gcd 30 50", "bot-feedback")
    with store.transaction(write=True) as db:
        store._post_message(
            db, owner["user_id"], owner["organization_id"], topics[0], "!gcd 60 80", "assistant-feedback", sender_kind="assistant"
        )
    assert len(records(fixture, hook)) == 1
    row = hooks.claim()
    body = json.loads(row["body"])
    assert body["message"]["id"] == message["id"] and body["id"] == row["id"]
    assert body["bot_id"] == bot["id"] and body["webhook_id"] == hook["id"]
    assert "client_id" not in body["message"] and "email" not in row["body"].decode()
    assert row["payload_digest"] == hashlib.sha256(row["body"]).hexdigest()
    hooks.finish(row, "accepted")
    assert records(fixture, hook)[0]["state"] == "accepted" and hooks.claim() is None
    with store.transaction() as db:
        assert db.execute("SELECT payload_json FROM bot_webhook_deliveries").fetchone()[0] is None


def test_edit_before_webhook_delivery_preserves_captured_original_bytes(fixture):
    store, owner, topics, _, _, hooks = fixture
    hook = create(fixture)
    message = post(fixture)
    store.edit_message(owner["user_id"], owner["organization_id"], topics[0], message["id"], "Corrected non-command", 0)
    delivery = hooks.claim()
    assert delivery is not None
    assert json.loads(delivery["body"])["message"]["content"] == "!gcd 24 36"
    assert len(records(fixture, hook)) == 1
    hooks.finish(delivery, "accepted")


def test_subscription_exact_creation_recovery_metadata_and_secret_encryption(fixture):
    store, owner, _, _, _, hooks = fixture
    hook = create(fixture)
    assert create(fixture) == hook
    listed = hooks.list(owner["user_id"], owner["organization_id"])[0]
    assert "signing_secret" not in listed and "ciphertext" not in listed and "client_id" not in listed
    with store.transaction() as db:
        ciphertext = db.execute("SELECT ciphertext FROM bot_webhooks").fetchone()[0]
        assert hook["signing_secret"].encode() not in ciphertext
    with pytest.raises(InvalidTag):
        store._box.open_webhook(ciphertext, organization_id="f" * 32, webhook_id=hook["id"])
    with pytest.raises(InvalidTag):
        store._box.open_webhook(ciphertext, organization_id=owner["organization_id"], webhook_id="f" * 32)
    with pytest.raises(StoreError) as error:
        create(fixture, prefix="!other")
    assert error.value.status == 409
    with pytest.raises(StoreError):
        create(fixture, client_id="second-subscription-command")
    hooks.revoke(owner["user_id"], owner["organization_id"], hook["id"])
    with pytest.raises(StoreError):
        create(fixture)
    replacement = create(fixture, client_id="replacement-subscription-command")
    assert replacement["id"] != hook["id"] and replacement["signing_secret"] != hook["signing_secret"]


@pytest.mark.parametrize("change", ["grant", "scope", "revoke", "sponsor", "expired"])
def test_queued_captures_are_fenced_after_authority_loss_even_if_grants_return(fixture, change):
    store, owner, topics, bots, bot, hooks = fixture
    hook = create(fixture)
    post(fixture)
    user, org = owner["user_id"], owner["organization_id"]
    if change == "grant":
        bots.update(user, org, bot["id"], "Bot", topics[1:], ["messages:read"])
        bots.update(user, org, bot["id"], "Bot", topics[:1], ["messages:read"])
    elif change == "scope":
        bots.update(user, org, bot["id"], "Bot", topics[:1], ["messages:write"])
        bots.update(user, org, bot["id"], "Bot", topics[:1], ["messages:read"])
    elif change == "revoke":
        bots.revoke(user, org, bot["id"])
    elif change == "expired":
        with store.transaction(write=True) as db:
            db.execute("UPDATE bots SET expires_at=0 WHERE id=?", (bot["id"],))
        bots.rotate(user, org, bot["id"])
    else:
        invite = store.invite(user, org, "second@example.org", "admin", 3600)
        second = store.accept_invitation(invite["token"], user_id=None, name="Second", password="fixture password long")["user_id"]
        store.change_member(user, org, second, "owner")
        store.change_member(second, org, user, "member")
        store.change_member(second, org, user, "admin")
    assert hooks.claim() is None and records(fixture, hook)[0]["state"] == "cancelled"


def test_revocation_fences_inflight_and_does_not_assert_remote_cancellation(fixture):
    _, owner, _, _, _, hooks = fixture
    hook = create(fixture)
    post(fixture)
    row = hooks.claim()
    worker = WebhookWorker(hooks)
    assert worker.authorized(row)
    hooks.revoke(owner["user_id"], owner["organization_id"], hook["id"])
    assert not worker.authorized(row)
    hooks.finish(row, "accepted")
    assert records(fixture, hook)[0]["state"] == "unknown"


def test_unknown_outcomes_retry_same_id_and_immutable_bytes_at_most_five_times(fixture):
    store, _, _, _, _, hooks = fixture
    hook = create(fixture)
    post(fixture)
    first = None
    for attempt in range(1, 6):
        due(store)
        row = hooks.claim()
        first = first or row
        assert row["attempts"] == attempt and row["id"] == first["id"] and row["body"] == first["body"]
        hooks.finish(row, "unknown", "receiver_retryable")
        if attempt < 5:
            assert hooks.claim() is None and records(fixture, hook)[0]["state"] == "queued"
    assert records(fixture, hook)[0]["state"] == "unknown"
    assert hooks.claim() is None


@pytest.mark.parametrize("ending", ["refused", "revoked"])
def test_prior_uncertainty_survives_later_refusal_or_queue_revocation(fixture, ending):
    store, owner, _, _, _, hooks = fixture
    hook = create(fixture)
    post(fixture)
    first = hooks.claim()
    hooks.finish(first, "unknown", "transport_uncertain")
    if ending == "refused":
        due(store)
        second = hooks.claim()
        hooks.finish(second, "failed", "receiver_rejected")
    else:
        hooks.revoke(owner["user_id"], owner["organization_id"], hook["id"])
    assert records(fixture, hook)[0]["state"] == "unknown"


def test_restart_recovers_sending_without_generating_another_identity(fixture):
    store, _, _, _, _, hooks = fixture
    hook = create(fixture)
    post(fixture)
    old = hooks.claim()
    hooks = WebhookStore(Store(store.data_dir))
    hooks.recover()
    assert records(fixture, hook)[0]["state"] == "queued" and hooks.claim() is None
    due(store)
    new = hooks.claim()
    assert new["id"] == old["id"] and new["body"] == old["body"] and new["attempts"] == 2
    hooks.finish(old, "accepted")
    assert records(fixture, hook)[0]["state"] == "sending"
    hooks.finish(new, "accepted")


def test_queue_overflow_is_counted_without_breaking_human_chat(fixture):
    _, owner, _, _, _, hooks = fixture
    create(fixture)
    for index in range(102):
        assert post(fixture, client=f"message-{index}")["id"]
    listed = hooks.list(owner["user_id"], owner["organization_id"])[0]
    assert listed["pending"] == 100 and listed["dropped"] == 2


def test_delivery_ttl_integrity_and_durable_rate_limits(fixture, monkeypatch):
    store, _, _, _, _, hooks = fixture
    hook = create(fixture)
    post(fixture)
    with store.transaction(write=True) as db:
        db.execute("UPDATE bot_webhook_deliveries SET payload_digest=?", ("0" * 64,))
    assert hooks.claim() is None and records(fixture, hook)[0]["error_code"] == "capture_integrity"
    post(fixture, client="too-old")
    instant = int(module.time.time()) + module.LIFETIME + 1
    monkeypatch.setattr(module.time, "time", lambda: instant)
    assert hooks.claim() is None and records(fixture, hook)[0]["error_code"] == "delivery_expired"
    for index in range(11):
        post(fixture, client=f"quota-{index}")
    for _ in range(10):
        hooks.finish(hooks.claim(), "accepted")
    assert hooks.claim() is None and records(fixture, hook)[0]["error_code"] == "delivery_quota"
    assert WebhookStore(Store(store.data_dir)).claim() is None
    monkeypatch.setattr(module.time, "time", lambda: instant + 61)
    assert hooks.claim() is not None


def test_backup_restore_revokes_webhook_secrets_and_all_pending_delivery(fixture, tmp_path):
    store, owner, _, _, _, hooks = fixture
    create(fixture)
    post(fixture)
    row = hooks.claim()
    post(fixture, client="queued-during-backup")
    key, archive, target = tmp_path / "backup.key", tmp_path / "backup.rnb", tmp_path / "restored"
    create_key(key)
    result = backup(store.data_dir, archive, key)
    restored = restore(archive, key, result["sha256"], target, source_stopped=True)
    assert restored["webhooks_revoked"] == 1
    rebuilt = WebhookStore(Store(target))
    assert rebuilt.claim() is None
    listed = rebuilt.list(owner["user_id"], owner["organization_id"])[0]
    assert not listed["enabled"] and listed["disabled_at"] is not None
    with rebuilt.store.transaction() as db:
        assert db.execute("SELECT ciphertext FROM bot_webhooks").fetchone()[0] is None
        states = {r[0]: r[1] for r in db.execute("SELECT id,state FROM bot_webhook_deliveries")}
        assert states[row["id"]] == "unknown" and "cancelled" in states.values()


@pytest.mark.parametrize(
    "url",
    [
        "http://receiver.example/hooks",
        "https://127.0.0.1/hooks",
        "https://169.254.169.254/",
        "https://[::1]/",
        "https://receiver.example:8443/",
        "https://user:secret@receiver.example/",
        "https://receiver.example/?secret=one",
        "https://receiver.example/#fragment",
        "https://2130706433/",
        "https://127.1/",
        "https://localhost/",
        "https://receiver.example\\@127.0.0.1/",
        "https://receiver.example/\r\nX-Header:evil",
        "https://receiver.example/space here",
    ],
)
def test_ambiguous_private_or_credentialled_endpoints_refused(url):
    with pytest.raises(ValueError):
        endpoint(url)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "169.254.169.254",
        "10.0.0.1",
        "100.64.0.1",
        "192.168.1.1",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fd00::1",
        "ff02::1",
        "::ffff:127.0.0.1",
        "64:ff9b::a00:1",
        "2002:7f00:1::",
        "2001::1",
        "fe80::1%eth0",
    ],
)
def test_nonpublic_and_transition_addresses_refused(address):
    assert not public_address(address)


def test_signatures_bind_timestamp_id_and_exact_bytes_and_reject_stale_replays():
    secret, identity, body = "rnwh_" + "a" * 43, "b" * 32, b'{"message":"hello"}'
    signed = signature(secret, identity, 1000, body)
    assert verify_signature(secret, identity, "1000", body, signed, now=1001)
    assert not verify_signature(secret, identity, "1000", body, signed, now=1301)
    assert not verify_signature(secret, identity, "1000", body + b" ", signed, now=1000)
    assert not verify_signature(secret, "c" * 32, "1000", body, signed, now=1000)
    assert not verify_signature(secret, identity, "1001", body, signed, now=1001)
    assert not verify_signature(secret, identity, "invalid", body, signed, now=1000)
    assert not verify_signature("bad secret", identity, "1000", body, signed, now=1000)
    box = SecretBox(b"a" * 32)
    cipher = box.seal_webhook(secret, organization_id="c" * 32, webhook_id=identity)
    assert box.open_webhook(cipher, organization_id="c" * 32, webhook_id=identity) == secret


def certificates(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "receiver.example")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("receiver.example")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "certificate.pem", tmp_path / "private.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    client_context = ssl.create_default_context(cafile=str(cert_path))
    return server_context, client_context


@pytest.mark.parametrize(
    "response,state,code",
    [
        (b"HTTP/1.1 204 No Content\r\n\r\n", None, None),
        (b"HTTP/1.1 303 See Other\r\nLocation: http://169.254.169.254/\r\nContent-Length: 0\r\n\r\n", "failed", "receiver_rejected"),
        (b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n", "unknown", "receiver_retryable"),
        (b"HTTP/1.1 200 OK\r\nX-Oversized: " + b"a" * 35000 + b"\r\n\r\n", "unknown", "transport_uncertain"),
    ],
)
def test_real_tls_wire_delivery_preserves_hostname_pin_and_bounded_response(fixture, tmp_path, monkeypatch, response, state, code):
    hook = create(fixture)
    post(fixture)
    row = fixture[-1].claim()
    server_context, client_context = certificates(tmp_path)
    captures = []
    original_open = asyncio.open_connection

    async def run():
        async def receiver(reader, writer):
            head = await reader.readuntil(b"\r\n\r\n")
            headers = dict(line.split(b": ", 1) for line in head.split(b"\r\n")[1:] if b": " in line)
            body = await reader.readexactly(int(headers[b"Content-Length"]))
            assert verify_signature(
                hook["signing_secret"],
                headers[b"X-Reasoning-Delivery"].decode(),
                headers[b"X-Reasoning-Timestamp"].decode(),
                body,
                headers[b"X-Reasoning-Signature"].decode(),
            )
            captures.append((head, body))
            writer.write(response)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(receiver, "127.0.0.1", 0, ssl=server_context)
        port = server.sockets[0].getsockname()[1]

        async def resolved(host):
            assert host == "receiver.example"
            return ["93.184.216.34"]

        async def connected(host, target_port, **kwargs):
            assert host == "93.184.216.34" and target_port == 443
            assert kwargs["server_hostname"] == "receiver.example" and kwargs["ssl"].check_hostname
            return await original_open("127.0.0.1", port, **kwargs)

        monkeypatch.setattr(transport_module, "resolve", resolved)
        monkeypatch.setattr(asyncio, "open_connection", connected)
        monkeypatch.setenv("HTTPS_PROXY", "http://169.254.169.254/")
        try:
            sender = WebhookTransport(ssl_context=client_context)
            if state is None:
                await sender.send(row, lambda _row: True)
            else:
                with pytest.raises(WebhookError) as error:
                    await sender.send(row, lambda _row: True)
                assert (error.value.state, error.value.code) == (state, code)
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(run())

    assert len(captures) == 1 and captures[0][1] == row["body"]
    assert b"Host: receiver.example" in captures[0][0] and b"Authorization:" not in captures[0][0]


def test_receiver_persists_exact_reply_before_post_and_recovers_lost_ack_and_restart(fixture, tmp_path):
    store, _, _, bots, bot, hooks = fixture
    hook = create(fixture)
    post(fixture)
    row = hooks.claim()
    calls = []

    class Client:
        server = "https://chat.example.org"

        def identity(self):
            return bots.identity(bot["token"])

        def post(self, topic, content, client_id):
            calls.append((topic, content, client_id))
            result = bots.post(bot["token"], topic, content, client_id)
            if len(calls) == 1:
                raise BotClientError("synthetic lost acknowledgment")
            return result

    root = tmp_path / "receiver"
    root.mkdir(mode=0o700)
    client = Client()
    receiver = ResearchReceiver(client, root, hook["signing_secret"], hook["id"])
    timestamp = str(int(module.time.time()))
    signed = signature(hook["signing_secret"], row["id"], int(timestamp), row["body"])
    with pytest.raises(BotClientError):
        receiver.receive(row["body"], row["id"], timestamp, signed)
    with receiver.connect() as db:
        pending = db.execute("SELECT * FROM deliveries").fetchone()
        assert pending["done"] == 0 and pending["reply"] == calls[0][1]
    restarted = ResearchReceiver(client, root, hook["signing_secret"], hook["id"])
    assert restarted.recover() == {"recovered": 1}
    restarted.receive(row["body"], row["id"], timestamp, signed)
    assert len(calls) == 2 and calls[0] == calls[1]
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM messages WHERE sender_kind='bot'").fetchone()[0] == 1
    assert len(records(fixture, hook)) == 1
    with pytest.raises(ReceiverError):
        ResearchReceiver(client, root, "rnwh_" + "a" * 43, hook["id"])
    with pytest.raises(ReceiverError):
        restarted.receive(row["body"] + b" ", row["id"], timestamp, signed)


def test_receiver_http_checks_duplicate_headers_binding_and_payload_bounds(fixture, tmp_path):
    _, _, _, bots, bot, hooks = fixture
    hook = create(fixture)
    post(fixture)
    row = hooks.claim()

    class Client:
        server = "https://chat.example.org"

        def identity(self):
            return bots.identity(bot["token"])

        def post(self, topic, content, client_id):
            return bots.post(bot["token"], topic, content, client_id)

    root = tmp_path / "receiver"
    root.mkdir(mode=0o700)
    receiver = ResearchReceiver(Client(), root, hook["signing_secret"], hook["id"])
    timestamp = str(int(module.time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Reasoning-Delivery": row["id"],
        "X-Reasoning-Timestamp": timestamp,
        "X-Reasoning-Signature": signature(hook["signing_secret"], row["id"], int(timestamp), row["body"]),
    }

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_receiver(receiver)), base_url="http://127.0.0.1") as client:
            assert (await client.post("/hooks", headers=headers, content=row["body"])).status_code == 204
            assert (await client.post("/hooks", headers=headers, content=row["body"])).status_code == 204
            assert (await client.post("/hooks", headers=headers, content=b"a" * (128 * 1024 + 1))).status_code == 400
            assert (
                await client.post("/hooks", headers=list(headers.items()) + [("X-Reasoning-Delivery", row["id"])], content=row["body"])
            ).status_code == 400
            bad = json.loads(row["body"])
            bad["organization_id"] = "a" * 32
            body = json.dumps(bad).encode()
            supplied = signature(hook["signing_secret"], row["id"], int(timestamp), body)
            assert (await client.post("/hooks", headers=headers | {"X-Reasoning-Signature": supplied}, content=body)).status_code == 400

    asyncio.run(run())


def test_backup_refuses_damaged_webhook_captures_and_wrong_encryption_key(fixture, tmp_path):
    store, _, _, _, _, _ = fixture
    create(fixture)
    post(fixture)
    with store.transaction(write=True) as db:
        db.execute("UPDATE bot_webhook_deliveries SET payload_json='{}'")
    key = tmp_path / "backup.key"
    create_key(key)
    with pytest.raises(BackupError, match="webhook capture"):
        backup(store.data_dir, tmp_path / "archive", key)
    with store.transaction(write=True) as db:
        db.execute("UPDATE bot_webhooks SET ciphertext=?", (b"bad encrypted credential",))
    with pytest.raises(ValueError, match="webhook secrets"):
        Store(store.data_dir)


def test_public_app_lifespan_owns_webhook_worker_and_cleans_up_after_start_failure(fixture, monkeypatch):
    app = create_app(Config(fixture[0].data_dir, "http://127.0.0.1", inference_enabled=False))

    async def run():
        async with app.router.lifespan_context(app):
            assert app.state.webhook_worker.running
        assert not app.state.webhook_worker.running and not app.state.workflow_worker.running

    asyncio.run(run())
    app = create_app(Config(fixture[0].data_dir, "http://127.0.0.1", inference_enabled=False))

    def failure():
        raise ValueError("synthetic worker start failure")

    monkeypatch.setattr(app.state.webhook_worker, "start", failure)

    async def fail():
        with pytest.raises(ValueError, match="synthetic worker"):
            async with app.router.lifespan_context(app):
                pytest.fail("startup must not yield")
        assert not app.state.workflow_worker.running

    asyncio.run(fail())


def test_mixed_dns_answers_refused_before_any_connection(fixture, monkeypatch):
    create(fixture)
    post(fixture)
    row = fixture[-1].claim()

    async def resolved(_host):
        return ["93.184.216.34", "169.254.169.254"]

    async def forbidden(*args, **kwargs):
        pytest.fail("private DNS answers must refuse connection")

    monkeypatch.setattr(transport_module, "resolve", resolved)
    monkeypatch.setattr(asyncio, "open_connection", forbidden)

    async def run():
        with pytest.raises(WebhookError) as error:
            await WebhookTransport().send(row, lambda _row: True)
        assert error.value.code == "endpoint_not_public"

    asyncio.run(run())


def test_worker_exception_is_redacted_and_live_rechecks_are_authoritative(fixture):
    hook = create(fixture)
    post(fixture)

    class Transport:
        async def send(self, row, authorized):
            assert authorized(row)
            raise ValueError(row["secret"] + row["body"].decode())

    worker = WebhookWorker(fixture[-1], Transport())
    assert asyncio.run(worker.execute_once())
    assert records(fixture, hook)[0]["error_code"] == "worker_interrupted"


def test_dns_resolver_cancellation_kills_and_reaps_its_owned_process(monkeypatch):
    processes = []
    original = asyncio.create_subprocess_exec

    async def start(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(transport_module, "_RESOLVE", "import time; time.sleep(60)")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)

    async def run():
        task = asyncio.create_task(transport_module.resolve("receiver.example"))
        while not processes:
            await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert processes[0].returncode is not None

    asyncio.run(run())


def test_transport_rechecks_authority_before_writing_any_message_bytes(fixture, monkeypatch):
    create(fixture, url="https://93.184.216.34/hooks")
    post(fixture)
    row = fixture[-1].claim()
    written = []

    class Writer:
        def write(self, value):
            written.append(value)

        def close(self):
            pass

        async def wait_closed(self):
            pass

    async def connected(*args, **kwargs):
        return None, Writer()

    monkeypatch.setattr(asyncio, "open_connection", connected)

    async def run():
        with pytest.raises(WebhookError) as error:
            await WebhookTransport().send(row, lambda _row: False)
        assert error.value.code == "authorization_changed" and written == []

    asyncio.run(run())
    unsafe = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    unsafe.check_hostname = False
    with pytest.raises(ValueError, match="verified TLS"):
        WebhookTransport(ssl_context=unsafe)


def test_terminal_history_and_daily_attempt_meter_remain_bounded(fixture, monkeypatch):
    store, _, _, _, _, hooks = fixture
    hook = create(fixture)
    instant = int(module.time.time())
    for index in range(102):
        monkeypatch.setattr(module.time, "time", lambda index=index: instant + index * 61)
        post(fixture, client="history-" + str(index))
        row = hooks.claim()
        assert row is not None
        hooks.finish(row, "accepted")
    assert len(records(fixture, hook)) == 100
    with store.transaction(write=True) as db:
        db.executemany("INSERT INTO bot_webhook_requests VALUES(?,?,?)", [(hook["organization_id"], hook["id"], instant)] * 500)
    post(fixture, client="daily-quota")
    assert hooks.claim() is None
    newest = records(fixture, hook)[0]
    assert newest["error_code"] == "delivery_quota" and newest["attempts"] == 0


def test_exclusive_worker_lock_and_lifespan_cleanup(fixture):
    first, second = WebhookWorker(fixture[-1]), WebhookWorker(fixture[-1])
    try:
        first.start()
        assert first.running
        with pytest.raises(BlockingIOError):
            second.start()
        assert first.running and not second.running
    finally:
        first.stop()
    assert not first.running
    second.start()
    second.stop()


def test_api_requires_current_human_admin_and_refuses_foreign_bot_identity(fixture):
    store, owner, topics, bots, bot, _ = fixture
    app = create_app(Config(store.data_dir, "http://127.0.0.1", inference_enabled=False))
    root = f"/api/organizations/{owner['organization_id']}/bot-webhooks"
    body = {"bot_id": bot["id"], "url": "https://receiver.example/hooks", "prefix": "!gcd", "client_id": "api-subscription-command"}
    foreign_org = store.create_organization(owner["user_id"], "Other")["id"]
    invite = store.invite(owner["user_id"], owner["organization_id"], "member@example.org", "member", 3600)
    member = store.accept_invitation(invite["token"], user_id=None, name="Member", password="fixture password long")

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            admin = {"Authorization": "Bearer " + owner["access_token"]}
            created = await client.post(root, headers=admin, json=body)
            assert created.status_code == 201, created.text
            result = created.json()
            assert (await client.post(root, headers=admin, json=body)).json() == result
            assert (await client.get(root, headers=admin)).json()[0]["id"] == result["id"]
            assert "signing_secret" not in (await client.get(root, headers=admin)).text
            assert (await client.get(root + "/status", headers=admin)).json() == {"worker_running": False}
            denied = {"Authorization": "Bearer " + member["access_token"]}
            for method, path, value in (
                ("GET", root, None),
                ("POST", root, body),
                ("DELETE", root + "/" + result["id"], None),
                ("GET", root + "/" + result["id"] + "/deliveries", None),
            ):
                assert (await client.request(method, path, headers=denied, json=value)).status_code == 403
                assert (
                    await client.request(method, path, headers={"Authorization": "Bearer " + bot["token"]}, json=value)
                ).status_code == 401
            foreign = f"/api/organizations/{foreign_org}/bot-webhooks"
            assert (await client.post(foreign, headers=admin, json=body)).status_code == 404
            assert (await client.get(foreign + "/" + result["id"] + "/deliveries", headers=admin)).status_code == 404
            assert (await client.post(root, headers=admin, json=body | {"signing_secret": "must-not-echo"})).status_code == 422

    asyncio.run(run())
