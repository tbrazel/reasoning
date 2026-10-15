"""Independent deployment-operator enrollment and secret-free handoff."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import pytest
import test_public_deployment as deployment_fixtures
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_public_provisioning import ProviderFixture

from reasoning_ledger.deployment import operator_handoff
from reasoning_ledger.deployment.host import InstallError, inspect_bundle
from reasoning_ledger.deployment.private import read_document, write_document, write_private
from reasoning_ledger.deployment.provision import save_plan
from reasoning_ledger.deployment.release_state import record_current, release_binding, retain_release
from reasoning_ledger.deployment.ssh_host import host_identity, server_trust


def key_pair(tmp_path: Path, name: str) -> tuple[Path, Path, str]:
    key = Ed25519PrivateKey.generate()
    private = tmp_path / name
    public = tmp_path / (name + ".pub")
    private_body = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    )
    public_body = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
    write_private(private, private_body)
    public.write_bytes(public_body + b" collaborator\n")
    public.chmod(0o644)
    return private, public, public_body.decode("ascii")


def deployment_state(tmp_path: Path, *, release_payload: dict[str, bytes] | None = None):
    state = tmp_path / "original"
    state.mkdir(mode=0o700)
    ownership = state / "ownership"
    ownership.mkdir(mode=0o700)
    if release_payload is None:
        release_payload = deployment_fixtures.payload.__wrapped__()
    source = deployment_fixtures.pack(tmp_path / "source.tar.gz", release_payload)
    manifest, release_id, checksum = inspect_bundle(source)
    provider = ProviderFixture()
    try:
        plan = provider.client.plan(
            name="reasoning-test",
            region="fra1",
            size="s-2vcpu-4gb",
            origin="https://chat.example.org",
            dns_zone="example.org",
            ssh_key_id=123,
            admin_cidr="203.0.113.20/32",
            maximum_hourly_usd="0.10",
            pin_ssh_host_key=True,
        )
    finally:
        provider.client.close()
    saved = save_plan(state / "plan.json", plan)
    document, plan_id = read_document(state / "plan.json"), saved["plan_id"]
    binding = release_binding(manifest, release_id, checksum, "https://chat.example.org")
    server_binding = {name: binding[name] for name in ("release_id", "bundle_sha256", "source_revision", "public_url")}
    request = {
        "format": 1,
        "plan_id": plan_id,
        "deployment_id": document["deployment_id"],
        "droplet_id": 456,
    }
    write_document(
        state / "setup-completion.json",
        {
            "format": 1,
            "release_id": release_id,
            "plan_id": plan_id,
            "public_url": binding["public_url"],
            "organization_id": "d" * 32,
            "compute_backend": "none",
            "compute_setup": None,
        },
        new=True,
    )
    write_document(
        ownership / "digitalocean-installation.json",
        {
            "intent": {"binding": server_binding, "request": request, "ssh_host": "139.59.1.2"},
        },
        new=True,
    )
    write_document(
        ownership / "digitalocean.json",
        {
            "plan_id": plan_id,
            "resources": {"droplet": {"status": "created", "id": 456}},
        },
        new=True,
    )
    host_identity(ownership, plan_id, document["deployment_id"], allow_new=True)
    token = tmp_path / "original-token"
    original_identity = tmp_path / "original-identity"
    write_private(token, b"original-private-token-" + b"x" * 32)
    write_private(original_identity, b"original private SSH key fixture")
    write_document(state / "setup.json", {"token_file": str(token), "identity": str(original_identity)}, new=True)
    retain_release(source, state, binding)
    record_current(state, binding)
    return state, document, request, binding, token, original_identity


def test_operator_handoff_uses_own_credentials_and_public_host_trust(tmp_path, monkeypatch, capsys):
    state, document, request, binding, original_token, original_identity = deployment_state(tmp_path)
    collaborator_identity, collaborator_public_file, collaborator_public = key_pair(tmp_path, "collaborator")
    known = tmp_path / "known"
    write_private(known, b"139.59.1.2 ssh-ed25519 fixture\n")
    calls = []

    @contextmanager
    def trust(*_args, **_kwargs):
        yield known

    monkeypatch.setattr(operator_handoff, "server_trust", trust)
    monkeypatch.setattr(operator_handoff, "approve", lambda *_args: None)

    def authorize(address, identity, trusted, public):
        calls.append((address, identity, trusted, public))
        return {"phase": "authorized"}

    def command(arguments):
        assert arguments[0] == "do-ssh-access"
        print(json.dumps({"phase": "closed" if "--close" in arguments else "open"}))
        return 0

    handoff = tmp_path / "operator-handoff.tar.gz"
    result = operator_handoff.grant_operator(
        state,
        collaborator_public_file,
        handoff,
        command,
        detect_public_ipv4=lambda: "203.0.113.20/32",
        authorize_remote=authorize,
    )
    assert result["phase"] == "operator_handoff_ready"
    assert calls == [("139.59.1.2", original_identity, known, collaborator_public)]
    archive = handoff.read_bytes()
    assert original_token.read_bytes() not in archive
    assert original_identity.read_bytes() not in archive

    collaborator_token = tmp_path / "collaborator-token"
    write_private(collaborator_token, b"collaborator-private-token-" + b"x" * 32)

    class SameTeam:
        def __init__(self, token):
            assert token == collaborator_token.read_text()

        def identity(self):
            return document["plan"]["account_identity"]

        def close(self):
            pass

    monkeypatch.setattr(operator_handoff, "DigitalOcean", SameTeam)
    joined = tmp_path / "joined"
    joined_result = operator_handoff.join_operator(handoff, joined, collaborator_token, collaborator_identity, result["handoff_sha256"])
    assert joined_result["phase"] == "operator_ready" and joined_result["release_id"] == binding["release_id"]
    assert read_document(joined / "setup.json") == {
        "identity": str(collaborator_identity),
        "token_file": str(collaborator_token),
    }
    assert not (joined / "ownership" / "digitalocean-ssh.json").exists()
    with server_trust(document, joined / "ownership", "139.59.1.2", request) as delegated_known:
        assert delegated_known.read_text().startswith("139.59.1.2 ssh-ed25519 ")
    _third_identity, third_public_file, third_public = key_pair(tmp_path, "third")
    second_handoff = tmp_path / "second-handoff.tar.gz"
    second_result = operator_handoff.grant_operator(
        joined,
        third_public_file,
        second_handoff,
        command,
        detect_public_ipv4=lambda: "203.0.113.21/32",
        authorize_remote=authorize,
    )
    assert second_result["phase"] == "operator_handoff_ready"
    assert calls[-1] == ("139.59.1.2", collaborator_identity, known, third_public)
    assert "original-private-token" not in capsys.readouterr().out


def test_operator_join_rejects_a_different_private_key_before_provider_access(tmp_path, monkeypatch):
    state, *_ = deployment_state(tmp_path)
    _identity, public_file, _public = key_pair(tmp_path, "authorized")
    wrong_identity, _wrong_public_file, _wrong_public = key_pair(tmp_path, "wrong")
    known = tmp_path / "known"
    write_private(known, b"fixture")

    @contextmanager
    def trust(*_args, **_kwargs):
        yield known

    monkeypatch.setattr(operator_handoff, "server_trust", trust)
    monkeypatch.setattr(operator_handoff, "approve", lambda *_args: None)
    handoff = tmp_path / "operator-handoff.tar.gz"

    def command(arguments):
        print(json.dumps({"phase": "closed" if "--close" in arguments else "open"}))
        return 0

    operator_handoff.grant_operator(
        state,
        public_file,
        handoff,
        command,
        detect_public_ipv4=lambda: "203.0.113.20/32",
        authorize_remote=lambda *_args: {"phase": "authorized"},
    )
    token = tmp_path / "token"
    write_private(token, b"x" * 40)
    monkeypatch.setattr(operator_handoff, "DigitalOcean", lambda *_args: pytest.fail("provider must not be contacted"))
    with pytest.raises(InstallError, match="does not match"):
        operator_handoff.join_operator(
            handoff,
            tmp_path / "joined",
            token,
            wrong_identity,
            hashlib.sha256(handoff.read_bytes()).hexdigest(),
        )
