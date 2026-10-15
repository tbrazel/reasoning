"""DNS API/command fixtures, not live registrar or public delegation acceptance."""

import json
import subprocess
from types import SimpleNamespace

import httpx
import pytest

from reasoning_ledger.deployment import cli, dns
from reasoning_ledger.deployment.digitalocean import DigitalOcean, ProviderRejected
from reasoning_ledger.deployment.host import InstallError
from reasoning_ledger.deployment.private import read_document


@pytest.fixture
def provider():
    state = {"exists": False, "failure": None, "calls": [], "account": "12345678-1234-1234-1234-123456789012"}

    def handle(request):
        state["calls"].append((request.method, request.url.path))
        assert request.headers["authorization"] == "Bearer " + "x" * 40
        if request.url.path == "/v2/account":
            return httpx.Response(200, json={"account": {"status": "active", "email_verified": True, "uuid": state["account"]}})
        if request.method == "GET" and request.url.path == "/v2/domains/example.org":
            return httpx.Response(200, json={"domain": {"name": "example.org"}}) if state["exists"] else httpx.Response(404)
        if request.method == "POST" and request.url.path == "/v2/domains":
            assert json.loads(request.content) == {"name": "example.org"}
            if state["failure"] == "rejected":
                return httpx.Response(422, json={"message": "untrusted provider reply with secret"})
            state["exists"] = state["failure"] != "lost_before"
            if state["failure"] in {"lost_before", "lost_after"}:
                raise httpx.ReadError("untrusted transport text with secret")
            return httpx.Response(201, json={"domain": {"name": "example.org"}})
        raise AssertionError("unexpected request")

    client = DigitalOcean("x" * 40, transport=httpx.MockTransport(handle))
    try:
        yield client, state
    finally:
        client.close()


def test_setup_creates_only_zone_and_reuses_it_without_changing_records(provider, tmp_path):
    client, state = provider
    directory = tmp_path / "dns-state"
    receipt = dns.setup_zone(client, "example.org", directory, "example.org")
    assert receipt["zone_ready"] is True
    assert receipt["delegation_verified"] is False
    assert receipt["registrar_modified"] is receipt["existing_records_modified"] is False
    assert dns.setup_zone(client, "example.org", directory, "example.org") == receipt
    assert [row for row in state["calls"] if row[0] == "POST"] == [("POST", "/v2/domains")]
    assert read_document(directory / "digitalocean-dns.json")["phase"] == "ready"


def test_existing_zone_is_preserved_without_any_post(provider, tmp_path):
    client, state = provider
    state["exists"] = True
    assert dns.setup_zone(client, "example.org", tmp_path / "state", "example.org")["zone_ready"]
    assert not any(method == "POST" for method, _ in state["calls"])


@pytest.mark.parametrize("failure", ["lost_before", "lost_after"])
def test_uncertain_request_never_blindly_repeats_creation(provider, tmp_path, failure):
    client, state = provider
    directory = tmp_path / "state"
    state["failure"] = failure
    with pytest.raises(InstallError, match="uncertain"):
        dns.setup_zone(client, "example.org", directory, "example.org")
    assert read_document(directory / "digitalocean-dns.json")["phase"] == "creating"
    state["failure"] = None
    if failure == "lost_after":
        assert dns.setup_zone(client, "example.org", directory, "example.org")["zone_ready"]
    else:
        with pytest.raises(InstallError, match="reconcile"):
            dns.setup_zone(client, "example.org", directory, "example.org")
    assert sum(method == "POST" for method, _ in state["calls"]) == 1


def test_definitively_rejected_request_can_retry_without_rebinding(provider, tmp_path):
    client, state = provider
    directory = tmp_path / "state"
    state["failure"] = "rejected"
    with pytest.raises(ProviderRejected):
        dns.setup_zone(client, "example.org", directory, "example.org")
    assert read_document(directory / "digitalocean-dns.json")["phase"] == "rejected"
    state["failure"] = None
    assert dns.setup_zone(client, "example.org", directory, "example.org")["zone_ready"]


def test_confirmation_precedes_provider_access(provider, tmp_path):
    client, state = provider
    with pytest.raises(InstallError, match="confirm"):
        dns.setup_zone(client, "example.org", tmp_path / "state", "different.org")
    assert state["calls"] == []
    assert not (tmp_path / "state").exists()


def test_saved_account_and_ready_zone_cannot_be_replaced(provider, tmp_path):
    client, state = provider
    directory = tmp_path / "state"
    dns.setup_zone(client, "example.org", directory, "example.org")
    original = state["account"]
    state["account"] = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    with pytest.raises(InstallError, match="account and zone"):
        dns.setup_zone(client, "example.org", directory, "example.org")
    state["account"], state["exists"] = original, False
    with pytest.raises(InstallError, match="reconcile"):
        dns.setup_zone(client, "example.org", directory, "example.org")
    assert sum(method == "POST" for method, _ in state["calls"]) == 1


def answer(nameservers):
    return "".join(f"example.org. 300 IN NS {name}.\n" for name in nameservers)


@pytest.mark.parametrize("nameservers,verified", [(dns.NAMESERVERS, True), ([], False), (["ns1.other.org"], False)])
def test_delegation_requires_exact_public_nameservers(monkeypatch, nameservers, verified):
    monkeypatch.setattr(dns.shutil, "which", lambda name: "/usr/bin/dig")

    def query(args, **kwargs):
        assert args == ["/usr/bin/dig", "+time=3", "+tries=1", "+noall", "+answer", "+noshort", "NS", "example.org."]
        assert kwargs["timeout"] == 10 and kwargs["check"] is False
        return SimpleNamespace(returncode=0, stdout=answer(nameservers))

    assert dns.check_delegation("example.org", query=query)["delegation_verified"] is verified


@pytest.mark.parametrize("output", ["other.org. 300 IN NS ns1.digitalocean.com.\n", "example.org. 300 IN CNAME other.org.\n", "x" * 16385])
def test_unexpected_dns_answers_are_not_verified(monkeypatch, output):
    monkeypatch.setattr(dns.shutil, "which", lambda name: "/usr/bin/dig")
    with pytest.raises(InstallError, match="unverified"):
        dns.check_delegation("example.org", query=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=output))


def test_missing_dig_and_timed_out_query_are_actionable(monkeypatch):
    monkeypatch.setattr(dns.shutil, "which", lambda name: None)
    with pytest.raises(InstallError, match="dnsutils"):
        dns.check_delegation("example.org")
    monkeypatch.setattr(dns.shutil, "which", lambda name: "/usr/bin/dig")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("dig", 10)

    with pytest.raises(InstallError, match="unverified"):
        dns.check_delegation("example.org", query=timeout)


@pytest.mark.parametrize("zone", ["Example.org", "https://example.org", "example.org/path", "127.0.0.1", "--help.org", "example.org."])
def test_invalid_zone_cannot_reach_provider_or_dns(zone, provider, tmp_path):
    client, state = provider
    with pytest.raises(InstallError):
        dns.setup_zone(client, zone, tmp_path / "state", zone)
    assert state["calls"] == []


def test_cli_confirmation_before_token_and_public_check_exit_status(monkeypatch, capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(["do-dns-setup", "--zone", "example.org", "--confirm-zone", "other.org", "--state-dir", "/missing/state",
                  "--token-file", "/missing/token"])
    assert error.value.code == 2
    monkeypatch.setattr(dns, "check_delegation", lambda zone: {"zone": zone, "delegation_verified": False})
    assert cli.main(["do-dns-check", "--zone", "example.org"]) == 1
    assert json.loads(capsys.readouterr().out)["delegation_verified"] is False


def test_cli_setup_uses_protected_token_and_returns_no_secret(provider, tmp_path, monkeypatch, capsys):
    client, _ = provider
    token = tmp_path / "token"
    token.write_text("x" * 40, encoding="ascii")
    token.chmod(0o600)
    monkeypatch.setattr(cli, "DigitalOcean", lambda value: client)
    assert cli.main(["do-dns-setup", "--zone", "example.org", "--confirm-zone", "example.org", "--state-dir", str(tmp_path / "state"),
                     "--token-file", str(token)]) == 0
    output = capsys.readouterr().out
    assert "x" * 40 not in output and json.loads(output)["zone_ready"] is True
