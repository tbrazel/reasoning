"""Registrar instructions and retry binding without network or browser side effects."""

import shlex
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from reasoning_ledger.deployment import registrar_guides as guides
from reasoning_ledger.deployment.dns import NAMESERVERS
from reasoning_ledger.deployment.host import InstallError
from reasoning_ledger.deployment.private import read_document, write_document

EXPECTED_NAMES = {
    "Bluehost", "Domain.com", "DreamHost", "Dynadot", "Enom", "Gandi", "GoDaddy",
    "HostGator", "Hover", "IONOS", "Name.com", "Namecheap", "Porkbun", "Squarespace",
}


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    state = tmp_path / "operator state"
    state.mkdir(mode=0o700)
    config = {"release_id": "a" * 64, "zone": "example.org"}
    opened = []
    monkeypatch.setattr(guides, "open_page", opened.append)
    return state, config, opened


def answers(monkeypatch, values):
    iterator = iter(values)
    monkeypatch.setattr("builtins.input", lambda prompt: next(iterator))


def test_catalog_covers_the_fourteen_do_registrars_and_fixed_https_links():
    assert {guide.name for guide in guides.GUIDES} == EXPECTED_NAMES
    assert len(guides.GUIDES) == len({guide.id for guide in guides.GUIDES}) == 14
    for guide in guides.GUIDES:
        assert len(guide.steps) >= 2
        for url in (guide.dashboard, guide.help):
            parsed = urlsplit(url)
            assert parsed.scheme == "https" and parsed.hostname
            assert parsed.username is parsed.password is None and not parsed.query and not parsed.fragment
        assert guide.help in Path("docs/deployment.md").read_text(encoding="utf-8")


def test_cloudflare_nameservers_offer_confirmed_provider_specific_handoff(handoff, monkeypatch, capsys):
    state, config, opened = handoff
    nameservers = ["sue.ns.cloudflare.com", "terin.ns.cloudflare.com"]
    answers(monkeypatch, ["yes", "no"])
    assert guides.guide_delegation(state, config, nameservers) == "cloudflare"
    output = capsys.readouterr().out
    assert "Cloudflare DNS" in output and "Cloudflare Registrar" in output
    assert guides.CLOUDFLARE_HELP in output and opened == []


def test_declined_cloudflare_detection_preserves_manual_registrar_choice(handoff, monkeypatch):
    state, config, _opened = handoff
    answers(monkeypatch, ["no", "porkbun", "no"])
    assert guides.guide_delegation(
        state, config, ["sue.ns.cloudflare.com", "terin.ns.cloudflare.com"]
    ) == "porkbun"


def test_saved_manual_guide_can_upgrade_to_confirmed_cloudflare_detection(handoff, monkeypatch):
    state, config, _opened = handoff
    write_document(state / "registrar-guide.json", {"format": 1, **config, "registrar": "manual"}, new=True)
    answers(monkeypatch, ["yes", "no"])
    assert guides.guide_delegation(
        state, config, ["sue.ns.cloudflare.com", "terin.ns.cloudflare.com"]
    ) == "cloudflare"
    assert read_document(state / "registrar-guide.json")["registrar"] == "cloudflare"


def test_cloudflare_child_guide_preserves_parent_selection(handoff, monkeypatch, capsys):
    state, config, opened = handoff
    write_document(
        state / "registrar-guide.json",
        {"format": 1, **config, "registrar": "cloudflare"},
        new=True,
    )
    child = {**config, "zone": "chat.example.org"}
    answers(monkeypatch, ["no"])

    assert guides.guide_delegation(
        state,
        child,
        ["sue.ns.cloudflare.com", "terin.ns.cloudflare.com"],
        parent_zone="example.org",
        confirmed_dns_provider="cloudflare",
    ) == "cloudflare"

    assert read_document(state / "registrar-guide.json")["zone"] == "example.org"
    child_guides = list(state.glob("registrar-guide-*.json"))
    assert len(child_guides) == 1
    assert read_document(child_guides[0])["zone"] == "chat.example.org"
    output = capsys.readouterr().out
    assert "example.org stays on Cloudflare" in output
    assert "chat" in output and "NS" in output
    assert "MX, TXT, SPF, DKIM, and DMARC" not in output and opened == []


@pytest.mark.parametrize("guide", guides.GUIDES, ids=lambda guide: guide.id)
def test_name_and_number_selection(monkeypatch, guide):
    for value in (" " + guide.name.upper() + " ", str(guides.GUIDES.index(guide) + 1)):
        answers(monkeypatch, [value])
        assert guides.choose_guide() == guide


@pytest.mark.parametrize("value", ["0", "other", "MANUAL"])
def test_unlisted_manual_fallback(monkeypatch, value):
    answers(monkeypatch, [value])
    assert guides.choose_guide() is guides.MANUAL


def test_search_ambiguity_and_unrecognized_values_do_not_choose_silently(monkeypatch, capsys):
    answers(monkeypatch, ["name", "pork"])
    assert guides.choose_guide().id == "porkbun"
    assert "Several registrars match" in capsys.readouterr().out
    answers(monkeypatch, ["", "https://attacker.example/", "99", "9" * 5000, "manual"])
    assert guides.choose_guide() is guides.MANUAL
    assert "No matching registrar" in capsys.readouterr().out


@pytest.mark.parametrize("guide", guides.GUIDES, ids=lambda guide: guide.id)
def test_every_registrar_handoff_prints_nameservers_and_only_opens_curated_urls(handoff, monkeypatch, capsys, guide):
    state, config, opened = handoff
    answers(monkeypatch, [guide.id, "yes"])
    assert guides.guide_delegation(state, config) == guide.id
    output = capsys.readouterr().out
    assert "\n".join(NAMESERVERS) in output and config["zone"] in output
    assert guide.help in output and guide.dashboard in output
    assert opened == [guide.dashboard, guide.help]
    assert "MX, TXT, SPF, DKIM, and DMARC" in output and "remove the parent DS" in output
    assert "does not copy records or inspect DNSSEC" in output
    assert (state / "registrar-guide.json").stat().st_mode & 0o777 == 0o600


def test_retry_reuses_selection_and_preserves_modal_resume_arguments(handoff, monkeypatch):
    state, config, opened = handoff
    answers(monkeypatch, ["porkbun", "n"])
    bundle = Path("release bundle").absolute()
    guides.guide_delegation(state, config)
    original = (state / "registrar-guide.json").read_bytes()
    answers(monkeypatch, ["no"])
    guides.guide_delegation(state, config)
    assert (state / "registrar-guide.json").read_bytes() == original and opened == []
    expected = ["uv", "run", "--extra", "modal", "reasoning-deploy", "setup", "--bundle", str(bundle),
                "--state-dir", str(state), "--compute-backend", "modal"]
    assert shlex.split(guides.resume_command(state, bundle, "modal")) == expected


def test_manual_headless_handoff_and_explicit_browser_choice(handoff, monkeypatch, capsys):
    state, config, opened = handoff
    answers(monkeypatch, ["other", "typo", "no"])
    guides.guide_delegation(state, config)
    output = capsys.readouterr().out
    assert "does not support external nameservers" in output
    assert "Enter yes or no" in output and guides.CATALOG_URL in output
    assert opened == []
    answers(monkeypatch, [""])
    guides.guide_delegation(state, config)
    assert opened == [guides.CATALOG_URL]


@pytest.mark.parametrize("change", [
    {"format": True}, {"format": 2}, {"release_id": "b" * 64}, {"zone": "other.org"},
    {"registrar": "unknown"}, {"registrar": "https://attacker.example"}, {"registrar": []}, {"extra": "field"},
])
def test_changed_or_unrecognized_journal_is_refused_before_browser(handoff, change):
    state, config, opened = handoff
    write_document(state / "registrar-guide.json", {"format": 1, **config, "registrar": "porkbun", **change}, new=True)
    with pytest.raises(InstallError, match="saved registrar guide"):
        guides.guide_delegation(state, config)
    assert opened == []


def test_symlink_or_public_guide_state_is_refused(handoff):
    state, config, _ = handoff
    path = state / "registrar-guide.json"
    write_document(state / "original.json", {"format": 1, **config, "registrar": "porkbun"}, new=True)
    path.symlink_to(state / "original.json")
    with pytest.raises(InstallError):
        guides.saved_guide(state, config)
    path.unlink()
    write_document(path, read_document(state / "original.json"), new=True)
    path.chmod(0o644)
    with pytest.raises(InstallError):
        guides.saved_guide(state, config)
