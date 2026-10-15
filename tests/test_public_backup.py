"""Real SQLite/WAL, encrypted archives, filesystem refusal, and recovery fencing."""

import base64
import fcntl
import hashlib
import io
import json
import os
import sqlite3
import struct
from pathlib import Path

import pytest
from test_public_compute_worker import IMAGE

from reasoning_ledger.deployment.host import canonical
from reasoning_ledger.server import backup as module
from reasoning_ledger.server.backup import BackupError, backup, create_key, inspect_backup, restore
from reasoning_ledger.server.compute_catalog import DEFAULT_RESOURCES, ComputeImage
from reasoning_ledger.server.compute_scaler import ComputeScalers
from reasoning_ledger.server.compute_store import ComputeStore
from reasoning_ledger.server.security import private_file
from reasoning_ledger.server.store import Store, StoreError


@pytest.fixture
def installation(tmp_path):
    state = Store(tmp_path / "source")
    owner = state.bootstrap((state.data_dir / "setup-token").read_text(), "owner@example.org", "Owner", "fixture password long", "Org")
    user, org = owner["user_id"], owner["organization_id"]
    project = state.create_entity(user, org, "projects", "Project")
    channel = state.create_entity(user, org, "channels", "Channel", project["id"])
    topic = state.create_entity(user, org, "topics", "Topic", channel["id"], "gamma")
    key = state.add_key(user, org, "openrouter", "Fixture", "provider-secret-never-printed")
    state.post_message(user, org, topic["id"], "message secret", "message-1")
    compute = ComputeStore(state, {"fixture": ComputeImage.parse(IMAGE)})
    compute.set_policy(user, org, ["fixture"], 10000, 2)
    worker = compute.register_worker(user, org, "Fixture", ["fixture"], DEFAULT_RESOURCES)
    encryption = tmp_path / "backup-key"
    create_key(encryption)
    return state, owner, topic["id"], key, compute, worker, encryption


def snapshot(installation, tmp_path):
    state, _, _, _, _, _, key = installation
    archive = tmp_path / "backup.rnbk"
    result = backup(state.data_dir, archive, key)
    return archive, result["sha256"], key


def test_restore_revokes_scalers_and_preserves_original_enrollment_mapping(installation, tmp_path):
    state, owner, _, _, compute, _, encryption = installation
    scalers = ComputeScalers(compute)
    scoped = scalers.create(owner["user_id"], owner["organization_id"], "Recovery scaler", ["fixture"], DEFAULT_RESOURCES, 2)
    credential = "rnw_" + "a" * 43
    reply = scalers.enroll(scoped["token"], "b" * 32, "Recovery worker", ["fixture"], DEFAULT_RESOURCES, credential)
    archive, checksum, _ = snapshot(installation, tmp_path)
    target = tmp_path / "scaler-restored"
    result = restore(archive, encryption, checksum, target, source_stopped=True)
    assert result["scalers_revoked"] == 1 and result["workers_revoked"] == 2
    recovered = Store(target)
    restored = ComputeScalers(ComputeStore(recovered, {"fixture": ComputeImage.parse(IMAGE)}))
    for operation in (restored.identity, restored.snapshot):
        with pytest.raises(StoreError) as failure:
            operation(scoped["token"])
        assert failure.value.status == 401
    with recovered.transaction() as db:
        assert db.execute("SELECT worker_id FROM compute_enrollments").fetchone()[0] == reply["worker"]["id"]
        assert db.execute("SELECT COUNT(*) FROM compute_scaler_requests").fetchone()[0] == 0
    with pytest.raises(StoreError) as failure:
        restored.rotate(owner["user_id"], owner["organization_id"], scoped["id"])
    assert failure.value.status == 409


def test_restore_preserves_retirement_seal_but_does_not_grant_admin_revocations_receipt_access(installation, tmp_path):
    state, owner, _topic, _key, compute, worker, encryption = installation
    compute.drain_self(worker["token"])
    receipt = compute.retire_worker(worker["token"], True)
    other = compute.register_worker(owner["user_id"], owner["organization_id"], "Other", ["fixture"], DEFAULT_RESOURCES)
    archive, checksum, _ = snapshot(installation, tmp_path)
    target = tmp_path / "retirement-restored"
    restore(archive, encryption, checksum, target, source_stopped=True)
    recovered = ComputeStore(Store(target), {"fixture": ComputeImage.parse(IMAGE)})
    assert recovered.worker_retirement(worker["token"]) == receipt
    with pytest.raises(StoreError):
        recovered.claim(worker["token"])
    with pytest.raises(StoreError):
        recovered.worker_retirement(other["token"])


def artifact(installation, *, commit=True, incomplete=False, body=b"artifact secret\n" * 3000):
    _, owner, topic, _, compute, worker, _ = installation
    parameters = compute.parameters("fixture", "python", "print('fixture')", DEFAULT_RESOURCES)
    job = compute.enqueue(owner["user_id"], owner["organization_id"], topic, "compute-1", parameters)
    claim = compute.claim(worker["token"], "a" * 32)
    args = (worker["token"], job["id"], claim["attempt_id"], claim["lease"])
    record = compute.begin_artifact(*args, "output.txt", len(body), "sha256:" + hashlib.sha256(body).hexdigest())
    chunks = [body[index : index + module.CHUNK_BYTES] for index in range(0, len(body), module.CHUNK_BYTES)]
    for index, chunk in enumerate(chunks[:1] if incomplete else chunks):
        compute.artifact_chunk(*args, record["id"], index, base64.b64encode(chunk).decode())
    if commit:
        compute.commit_artifact(*args, record["id"])
    return job, record, body


def test_encrypted_round_trip_preserves_chat_keys_and_committed_artifacts(installation, tmp_path):
    state, owner, topic, key, _, worker, encryption = installation
    job, record, body = artifact(installation)
    invite = state.invite(owner["user_id"], owner["organization_id"], "new@example.org", "member", 3600)
    archive, digest, _ = snapshot(installation, tmp_path)
    encrypted = archive.read_bytes()
    for secret in (
        b"message secret",
        b"artifact secret",
        b"provider-secret-never-printed",
        state._setup_token.encode(),
        encryption.read_bytes(),
    ):
        assert secret not in encrypted
    assert encrypted.startswith(module.MAGIC)
    before = inspect_backup(archive, encryption, digest)
    assert before["verified"] and before["files"] == 5
    destination = tmp_path / "restored"
    result = restore(archive, encryption, digest, destination, source_stopped=True)
    assert result["sessions_revoked"] == result["invitations_revoked"] == result["workers_revoked"] == result["compute_jobs_held"] == 1
    assert not result["server_started"]
    restored = Store(destination)
    assert restored.initialized() and (destination / "encryption.key").read_bytes() == (state.data_dir / "encryption.key").read_bytes()
    assert (destination / "setup-token").read_bytes() != (state.data_dir / "setup-token").read_bytes()
    with restored.transaction() as db:
        assert db.execute("SELECT content FROM messages WHERE topic_id=?", (topic,)).fetchone()[0] == "message secret"
        row = db.execute("SELECT * FROM provider_keys WHERE id=?", (key["id"],)).fetchone()
        assert (
            restored._box.open(row["ciphertext"], organization_id=row["organization_id"], provider=row["provider"], key_id=row["id"])
            == "provider-secret-never-printed"
        )
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert db.execute("SELECT revoked_at FROM invitations WHERE id=?", (invite["id"],)).fetchone()[0]
        assert db.execute("SELECT action FROM audit_events ORDER BY sequence DESC LIMIT 1").fetchone()[0] == "installation.restore"
    compute = ComputeStore(restored, {"fixture": ComputeImage.parse(IMAGE)})
    with pytest.raises(StoreError) as revoked:
        compute.claim(worker["token"])
    assert revoked.value.status == 401
    assert compute.job(owner["user_id"], owner["organization_id"], job["id"])["status"] == "outcome_unknown"
    assert b"".join(compute._read_chunk(record["id"], index) for index in range(2)) == body
    assert all(path.stat().st_mode & 0o077 == 0 for path in destination.rglob("*"))
    # Authentication succeeds with the original password; restored tokens do not survive.
    assert restored.login("owner@example.org", "fixture password long", "fixture")["user_id"] == owner["user_id"]


def test_incomplete_uploads_survive_but_unreferenced_orphans_and_worker_state_do_not(installation, tmp_path):
    state, _, _, _, _, _, _ = installation
    _, record, _ = artifact(installation, commit=False, incomplete=True)
    private_file(state.data_dir / "artifacts" / record["id"] / "9", b"orphan secret")
    private_file(state.data_dir / "worker-state", b"worker secret")
    archive, digest, key = snapshot(installation, tmp_path)
    destination = tmp_path / "restored"
    result = restore(archive, key, digest, destination, source_stopped=True)
    assert result["files"] == 4 and result["compute_jobs_held"] == 1
    assert (destination / "artifacts" / record["id"] / "0").exists()
    assert not (destination / "artifacts" / record["id"] / "9").exists()
    assert not (destination / "worker-state").exists()


@pytest.mark.parametrize(
    "body", [b"", b"x", b"x" * module.CHUNK_BYTES, b"x" * (module.CHUNK_BYTES + 1)], ids=["empty", "one", "chunk", "chunk-plus-one"]
)
def test_artifact_boundary_lengths(installation, tmp_path, body):
    artifact(installation, body=body)
    archive, digest, key = snapshot(installation, tmp_path)
    assert inspect_backup(archive, key, digest)["verified"]


def test_live_wal_is_snapshotted_without_mutating_source_or_initializing_target(installation, tmp_path, monkeypatch):
    state, owner, topic, _, _, _, _ = installation
    # An open WAL reader keeps checkpointing from removing committed WAL content.
    reader = sqlite3.connect(state.database)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM messages").fetchone()
    state.post_message(owner["user_id"], owner["organization_id"], topic, "WAL-only message", "wal-only")
    assert Path(str(state.database) + "-wal").stat().st_size > 0
    original = module._snapshot

    def snapshot_then_write(*args):
        original(*args)
        state.post_message(owner["user_id"], owner["organization_id"], topic, "after-snapshot message", "after-snapshot")

    monkeypatch.setattr(module, "_snapshot", snapshot_then_write)
    try:
        archive, digest, key = snapshot(installation, tmp_path)
    finally:
        reader.close()
    assert inspect_backup(archive, key, digest)["verified"]
    destination = tmp_path / "restored"
    restore(archive, key, digest, destination, source_stopped=True)
    with sqlite3.connect(destination / "server.sqlite3") as db:
        contents = [row[0] for row in db.execute("SELECT content FROM messages")]
    assert contents == ["message secret", "WAL-only message"]
    with state.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 3


def test_restore_holds_queued_and_running_inference_without_releasing_cost_reservations(installation, tmp_path):
    state, owner, topic, key, compute, _, _ = installation
    with state.transaction(write=True) as db:
        for index, status in enumerate(("queued", "running", "succeeded")):
            request, run = f"request-{index}", f"run-{index}"
            db.execute(
                "INSERT INTO llm_requests VALUES(?,?,?,?,?,?,?,?,?)",
                (request, owner["organization_id"], topic, owner["user_id"], request, "{}", "[]", 0, 1),
            )
            db.execute(
                "INSERT INTO llm_runs(id,organization_id,request_id,model,key_id,quote_json,status,reserved_microusd,accounting_state) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    run,
                    owner["organization_id"],
                    request,
                    "fixture/model",
                    key["id"],
                    "{}",
                    status,
                    200,
                    "settled" if status == "succeeded" else "reserved",
                ),
            )
    compute.enqueue(
        owner["user_id"], owner["organization_id"], topic, "queued", compute.parameters("fixture", "python", "pass", DEFAULT_RESOURCES)
    )
    archive, digest, encryption = snapshot(installation, tmp_path)
    destination = tmp_path / "restored"
    result = restore(archive, encryption, digest, destination, source_stopped=True)
    assert result["inference_runs_held"] == 2 and result["compute_jobs_held"] == 1
    with sqlite3.connect(destination / "server.sqlite3") as db:
        assert db.execute("SELECT status,accounting_state,reserved_microusd FROM llm_runs ORDER BY id").fetchall() == [
            ("interrupted", "uncertain", 200),
            ("interrupted", "uncertain", 200),
            ("succeeded", "settled", 200),
        ]
        assert db.execute("SELECT COUNT(*) FROM llm_events").fetchone()[0] == 2
        assert db.execute("SELECT status,cancel_requested FROM compute_jobs").fetchone() == ("outcome_unknown", 1)


def test_restore_requires_explicit_original_host_fencing_before_reading_any_input(tmp_path):
    with pytest.raises(BackupError, match="original server and workers"):
        restore(tmp_path / "missing", tmp_path / "missing-key", "invalid", tmp_path / "destination")
    assert not (tmp_path / "destination").exists()


@pytest.mark.parametrize("existing", ["directory", "file", "symlink"])
def test_restore_never_overwrites_existing_destination(tmp_path, existing):
    destination = tmp_path / "destination"
    if existing == "directory":
        destination.mkdir()
    elif existing == "file":
        destination.write_bytes(b"keep")
    else:
        destination.symlink_to(tmp_path / "absent")
    with pytest.raises(BackupError, match="already exists"):
        restore(tmp_path / "missing", tmp_path / "missing-key", "invalid", destination, source_stopped=True)
    assert destination.exists() or destination.is_symlink()


def test_atomic_directory_publication_refuses_a_racing_empty_directory(tmp_path):
    source, target = tmp_path / "stage", tmp_path / "target"
    source.mkdir(mode=0o700)
    target.mkdir(mode=0o700)
    with pytest.raises(BackupError, match="already exists"):
        module._rename_noreplace(source, target)
    assert source.exists() and target.exists()


@pytest.mark.parametrize("mutation", ["header", "ciphertext", "length", "truncate", "trailing", "terminal"])
def test_corruption_and_truncation_are_rejected_before_target_publication(installation, tmp_path, mutation):
    archive, original, key = snapshot(installation, tmp_path)
    body = bytearray(archive.read_bytes())
    if mutation == "header":
        body[10] ^= 1
    elif mutation == "ciphertext":
        body[40] ^= 1
    elif mutation == "length":
        body[28:32] = struct.pack(">I", module.FRAME + 1)
    elif mutation == "truncate":
        body = body[: len(body) // 2]
    elif mutation == "terminal":
        body = body[:-20]
    else:
        body += b"trailing data"
    archive.write_bytes(body)
    expected = hashlib.sha256(body).hexdigest()
    with pytest.raises(BackupError):
        inspect_backup(archive, key, original)
    destination = tmp_path / "destination"
    with pytest.raises(BackupError):
        restore(archive, key, expected, destination, source_stopped=True)
    assert not destination.exists() and not list(tmp_path.glob(".reasoning-*"))


def test_wrong_key_and_expected_identity(installation, tmp_path):
    archive, digest, _ = snapshot(installation, tmp_path)
    wrong = tmp_path / "wrong-key"
    create_key(wrong)
    with pytest.raises(BackupError, match="authentication"):
        inspect_backup(archive, wrong, digest)
    with pytest.raises(BackupError, match="expected identity"):
        inspect_backup(archive, wrong, "a" * 64)


@pytest.mark.parametrize("size", [0, 1, module.FRAME - 1, module.FRAME, module.FRAME + 1, 2 * module.FRAME + 17])
def test_encryption_frame_boundary_round_trip(tmp_path, size):
    body, key, stream = os.urandom(size), os.urandom(32), io.BytesIO()
    writer = module._Encryptor(stream, key)
    for offset in range(0, len(body), 37_111):
        writer.write(body[offset : offset + 37_111])
    writer.finish()
    encoded = stream.getvalue()
    reader = module._Decryptor(io.BytesIO(encoded), key)
    actual = bytearray()
    while part := reader.read(61_133):
        actual.extend(part)
    assert bytes(actual) == body and reader.finished
    assert len(encoded) <= size + (size // module.FRAME + 2) * 20 + 28


def test_frame_reordering_and_duplicate_frames_fail_authentication():
    key, stream = os.urandom(32), io.BytesIO()
    writer = module._Encryptor(stream, key)
    writer.write(b"a" * module.FRAME + b"b" * module.FRAME)
    writer.finish()
    original, length = stream.getvalue(), module.FRAME + 20
    for swapped in (
        original[:28] + original[28 + length : 28 + 2 * length] + original[28 : 28 + length] + original[28 + 2 * length :],
        original[:28] + original[28 : 28 + length] * 2 + original[28 + 2 * length :],
    ):
        with pytest.raises(BackupError, match="authentication"):
            module._Decryptor(io.BytesIO(swapped), key).read(3 * module.FRAME)


@pytest.mark.parametrize("kind", ["public", "symlink", "hardlink", "fifo"])
def test_private_inputs_refuse_exposed_or_special_files(tmp_path, kind):
    path = tmp_path / "key"
    if kind == "fifo":
        os.mkfifo(path, 0o600)
    else:
        private_file(path, b"k" * 32)
        if kind == "public":
            path.chmod(0o644)
        elif kind == "hardlink":
            os.link(path, tmp_path / "other")
        else:
            other = tmp_path / "real"
            path.rename(other)
            path.symlink_to(other)
    with pytest.raises((BackupError, module.InstallError)):
        module._key(path)


def test_missing_state_does_not_initialize_and_existing_backup_key_is_not_overwritten(tmp_path):
    key = tmp_path / "key"
    create_key(key)
    before = key.read_bytes()
    with pytest.raises(BackupError):
        create_key(key)
    assert key.read_bytes() == before and key.stat().st_mode & 0o777 == 0o600
    with pytest.raises(BackupError):
        backup(tmp_path / "missing-state", tmp_path / "backup", key)
    assert not (tmp_path / "missing-state").exists() and not (tmp_path / "backup").exists()


def test_existing_archive_is_preserved(installation, tmp_path):
    state, _, _, _, _, _, key = installation
    path = tmp_path / "archive"
    private_file(path, b"keep original")
    with pytest.raises(BackupError):
        backup(state.data_dir, path, key)
    assert path.read_bytes() == b"keep original"


@pytest.mark.parametrize("damage", ["missing", "content", "exposed", "directory-link", "whole-digest"])
def test_corrupt_referenced_artifacts_never_publish_a_backup(installation, tmp_path, damage):
    state, _, _, _, _, _, key = installation
    _, record, _ = artifact(installation)
    path = state.data_dir / "artifacts" / record["id"] / "0"
    if damage == "missing":
        path.unlink()
    elif damage == "content":
        path.write_bytes(b"changed")
    elif damage == "exposed":
        path.chmod(0o644)
    elif damage == "directory-link":
        real = path.parent.with_name("real")
        path.parent.rename(real)
        path.parent.symlink_to(real, target_is_directory=True)
    else:
        with state.transaction(write=True) as db:
            db.execute("UPDATE compute_artifacts SET digest=? WHERE id=?", ("sha256:" + "f" * 64, record["id"]))
    with pytest.raises(BackupError):
        backup(state.data_dir, tmp_path / "archive", key)
    assert not (tmp_path / "archive").exists() and not list(tmp_path.glob(".reasoning-*"))


@pytest.mark.parametrize("damage", ["key", "foreign-key", "schema", "future"])
def test_invalid_database_and_wrong_installation_key_never_publish(installation, tmp_path, damage):
    state, _, _, _, _, _, key = installation
    if damage == "key":
        (state.data_dir / "encryption.key").write_bytes(os.urandom(32))
    else:
        with sqlite3.connect(state.database) as db:
            if damage == "foreign-key":
                db.execute("UPDATE memberships SET user_id='missing'")
            elif damage == "schema":
                db.execute("CREATE TABLE extra(value TEXT)")
            else:
                db.execute("PRAGMA user_version=999")
    with pytest.raises(BackupError):
        backup(state.data_dir, tmp_path / "archive", key)
    assert not (tmp_path / "archive").exists()


@pytest.mark.parametrize("value", [True, 0, -1, 1024, module.MAX_BYTES + 1])
def test_byte_allowance_is_explicit_and_bounded(tmp_path, value):
    with pytest.raises(BackupError, match="byte allowance"):
        inspect_backup(tmp_path / "missing", tmp_path / "missing-key", "a" * 64, maximum=value)


def test_initialization_lock_is_respected(installation, tmp_path):
    state, _, _, _, _, _, key = installation
    with (state.data_dir / "initialization.lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with pytest.raises(BackupError):
            backup(state.data_dir, tmp_path / "archive", key)
    assert not (tmp_path / "archive").exists()


def test_snapshot_byte_bound_is_enforced_before_publication(installation, tmp_path):
    state, owner, topic, _, _, _, key = installation
    with state.transaction(write=True) as db:
        db.executemany(
            "INSERT INTO messages(id,organization_id,topic_id,author_id,content,client_id,created_at) VALUES(?,?,?,?,?,?,?)",
            [
                (f"large-{index}", owner["organization_id"], topic, owner["user_id"], "x" * 100_000, f"large-{index}", 1)
                for index in range(15)
            ],
        )
    with pytest.raises(BackupError):
        backup(state.data_dir, tmp_path / "archive", key, maximum=module.FRAME)
    assert not (tmp_path / "archive").exists()


@pytest.mark.parametrize(
    "change", ["traversal", "absolute", "duplicate", "missing-key", "index", "boolean-size", "oversized", "unknown", "schema", "extra"]
)
def test_manifest_rejects_untrusted_paths_and_metadata(change):
    files = [
        {"name": "encryption.key", "size": 32, "sha256": "a" * 64},
        {"name": "server.sqlite3", "size": 100, "sha256": "b" * 64},
        {"name": "setup-token", "size": 43, "sha256": "c" * 64},
    ]
    value = {"format": 1, "schema": len(module.MIGRATIONS), "created_at": 1, "files": files}
    if change == "duplicate":
        files.insert(1, files[0])
    elif change == "missing-key":
        files[0]["name"] = "artifacts/" + "a" * 32 + "/0"
    elif change == "boolean-size":
        files[1]["size"] = True
    elif change == "oversized":
        files[1]["size"] = module.MAX_BYTES + 1
    elif change == "schema":
        value["schema"] = 999
    elif change == "extra":
        value["extra"] = True
    else:
        files[1]["name"] = {
            "traversal": "../outside",
            "absolute": "/outside",
            "index": "artifacts/" + "a" * 32 + "/32768",
            "unknown": "worker-key",
        }[change]
    with pytest.raises(BackupError, match="manifest"):
        module._manifest(canonical(value), module.DEFAULT_BYTES)


def test_noncanonical_and_duplicate_json_keys_are_refused():
    with pytest.raises(BackupError):
        module._manifest(b'{"format":1,"format":1}', module.DEFAULT_BYTES)


@pytest.mark.parametrize("damage", ["unlisted", "hash", "key", "setup", "manifest", "traversal", "extra-plaintext"])
def test_authenticated_but_invalid_payload_never_publishes_state(installation, tmp_path, damage):
    state, _, _, _, _, _, key = installation
    stage = tmp_path / "stage"
    stage.mkdir(mode=0o700)
    module._snapshot(state.data_dir, stage, module.DEFAULT_BYTES)
    contents = {name: (stage / name).read_bytes() for name in module.BASE_FILES}
    if damage == "unlisted":
        contents["artifacts/" + "a" * 32 + "/0"] = b"unreferenced"
    elif damage == "key":
        contents["encryption.key"] = b"x" * 32
    elif damage == "setup":
        contents["setup-token"] = b"!" * 43
    elif damage == "traversal":
        contents["../outside"] = b"outside"
    files = [{"name": name, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()} for name, body in sorted(contents.items())]
    manifest = {"format": 1, "schema": len(module.MIGRATIONS), "created_at": 1, "files": files}
    if damage == "hash":
        files[0]["sha256"] = "f" * 64
    encoded = canonical(manifest)
    if damage == "manifest":
        encoded = b" " + encoded
    archive = tmp_path / "forged"
    with archive.open("wb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        encrypted = module._Encryptor(stream, key.read_bytes())
        encrypted.write(struct.pack(">I", len(encoded)))
        encrypted.write(encoded)
        for _, body in sorted(contents.items()):
            encrypted.write(body)
        if damage == "extra-plaintext":
            encrypted.write(b"not in manifest")
        encrypted.finish()
    identity = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(BackupError):
        restore(archive, key, identity, tmp_path / "destination", source_stopped=True)
    assert not (tmp_path / "destination").exists() and not (tmp_path / "outside").exists()
    assert not list(tmp_path.glob(".reasoning-restore-*"))


def test_empty_installation_restore_rotates_setup_and_remains_bootstrappable(tmp_path):
    state = Store(tmp_path / "source")
    key = tmp_path / "key"
    create_key(key)
    result = backup(state.data_dir, tmp_path / "archive", key)
    destination = tmp_path / "restored"
    restore(tmp_path / "archive", key, result["sha256"], destination, source_stopped=True)
    restored = Store(destination)
    assert not restored.initialized() and restored._setup_token != state._setup_token
    owner = restored.bootstrap(restored._setup_token, "owner@example.org", "Owner", "fixture password long", "Org")
    assert owner["organization_id"] and restored.initialized()


def test_multiple_organization_keys_are_not_cross_decryptable_after_restore(installation, tmp_path):
    state, owner, _, first_key, _, _, _ = installation
    org = state.create_organization(owner["user_id"], "Second")
    second_key = state.add_key(owner["user_id"], org["id"], "openrouter", "Second", "second-organization-secret")
    archive, digest, encryption = snapshot(installation, tmp_path)
    destination = tmp_path / "restored"
    restore(archive, encryption, digest, destination, source_stopped=True)
    restored = Store(destination)
    with restored.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM organizations").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE action='installation.restore'").fetchone()[0] == 2
        row = db.execute("SELECT * FROM provider_keys WHERE id=?", (second_key["id"],)).fetchone()
        assert (
            restored._box.open(row["ciphertext"], organization_id=org["id"], provider="openrouter", key_id=second_key["id"])
            == "second-organization-secret"
        )
        with pytest.raises(module.InvalidTag):
            restored._box.open(row["ciphertext"], organization_id=owner["organization_id"], provider="openrouter", key_id=first_key["id"])


def test_restore_cli_operates_before_store_creation_and_never_echoes_secrets(tmp_path, capsys):
    from reasoning_ledger.server.cli import main

    assert main(["--data-dir", str(tmp_path / "unused"), "backup-key", "--output", str(tmp_path / "key")]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["key_file"] == str(tmp_path / "key") and not (tmp_path / "unused").exists()
    with pytest.raises(SystemExit) as error:
        main(
            [
                "--data-dir",
                str(tmp_path / "destination"),
                "restore",
                "--backup",
                str(tmp_path / "missing"),
                "--key-file",
                str(tmp_path / "key"),
                "--expected-sha256",
                "secret-invalid-digest",
            ]
        )
    assert error.value.code == 2 and "secret-invalid-digest" not in capsys.readouterr().err
    assert not (tmp_path / "destination").exists()


def test_backup_inspect_restore_cli_round_trip(installation, tmp_path, capsys):
    from reasoning_ledger.server.cli import main

    state, _, _, _, _, _, key = installation
    archive = tmp_path / "cli-archive"
    assert main(["--data-dir", str(state.data_dir), "backup", "--output", str(archive), "--key-file", str(key)]) == 0
    saved = json.loads(capsys.readouterr().out)
    common = ["--backup", str(archive), "--key-file", str(key), "--expected-sha256", saved["sha256"]]
    assert main(["--data-dir", str(tmp_path / "unused"), "backup-inspect", *common]) == 0
    assert json.loads(capsys.readouterr().out)["verified"]
    assert not (tmp_path / "unused").exists()
    assert main(["--data-dir", str(tmp_path / "destination"), "restore", *common, "--confirm-source-stopped"]) == 0
    recovered = json.loads(capsys.readouterr().out)
    assert recovered["verified"] and not recovered["server_started"] and (tmp_path / "destination" / "server.sqlite3").exists()


def test_restore_racing_destination_is_preserved_and_private_staging_is_removed(installation, tmp_path, monkeypatch):
    archive, digest, key = snapshot(installation, tmp_path)
    original = module._rename_noreplace

    def race(source, target):
        target.mkdir(mode=0o700)
        private_file(target / "keep", b"existing installation")
        original(source, target)

    monkeypatch.setattr(module, "_rename_noreplace", race)
    target = tmp_path / "destination"
    with pytest.raises(BackupError, match="already exists"):
        restore(archive, key, digest, target, source_stopped=True)
    assert (target / "keep").read_bytes() == b"existing installation"
    assert not (target / "server.sqlite3").exists() and not list(tmp_path.glob(".reasoning-restore-*"))


def test_held_compute_can_be_reconciled_and_workers_must_reenroll(installation, tmp_path):
    state, owner, topic, _, compute, worker, _ = installation
    original = compute.enqueue(
        owner["user_id"], owner["organization_id"], topic, "queued", compute.parameters("fixture", "python", "pass", DEFAULT_RESOURCES)
    )
    archive, digest, key = snapshot(installation, tmp_path)
    target = tmp_path / "destination"
    restore(archive, key, digest, target, source_stopped=True)
    recovered = ComputeStore(Store(target), {"fixture": ComputeImage.parse(IMAGE)})
    with recovered.store.transaction() as db:
        assert recovered._balance(db, owner["organization_id"])["held_cpu_seconds"] > 0
    result = recovered.reconcile(owner["user_id"], owner["organization_id"], original["id"], 0, True)
    assert result["status"] == "cancelled" and result["used_cpu_seconds"] == 0
    with recovered.store.transaction() as db:
        assert recovered._balance(db, owner["organization_id"])["held_cpu_seconds"] == 0
    replacement = recovered.register_worker(owner["user_id"], owner["organization_id"], "Replacement", ["fixture"], DEFAULT_RESOURCES)
    assert replacement["token"] != worker["token"] and recovered.claim(replacement["token"]) is None
    with pytest.raises(StoreError) as error:
        recovered.claim(worker["token"])
    assert error.value.status == 401
