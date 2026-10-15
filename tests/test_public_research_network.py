"""Research source reads cannot reach private networks or execute source markup."""

import asyncio

import pytest

from reasoning_ledger.server import research_network


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org",
        "https://127.0.0.1",
        "https://example.org:444/path",
        "https://user:password@example.org",
        "https://example.org/#fragment",
        "https://example.org/\r\nsecret",
    ],
)
def test_nonpublic_or_ambiguous_source_url_never_opens_socket(monkeypatch, url):
    async def socket(*args, **kwargs):
        raise AssertionError("must refuse the endpoint before opening a socket")

    monkeypatch.setattr(asyncio, "open_connection", socket)
    with pytest.raises(ValueError):
        asyncio.run(research_network.public_get(url))


def test_mixed_public_private_dns_answers_never_open_socket(monkeypatch):
    async def resolve(_host):
        return ["1.1.1.1", "10.0.0.1"]

    async def socket(*args, **kwargs):
        raise AssertionError("must check every DNS answer")

    monkeypatch.setattr(research_network, "resolve", resolve)
    monkeypatch.setattr(asyncio, "open_connection", socket)
    with pytest.raises(ValueError, match="exclusively to public"):
        asyncio.run(research_network.public_get("https://example.org/source?q=test"))


def test_source_html_ignores_scripts_and_rejects_binary_media():
    text = research_network.source_text(
        b"<h1>A source</h1><script>steal_credentials()</script><p>A theorem</p>", {"content-type": "text/html; charset=utf-8"}
    )
    assert "A theorem" in text and "A source" in text and "steal" not in text
    with pytest.raises(ValueError, match="not supported text"):
        research_network.source_text(b"executable bytes", {"content-type": "application/octet-stream"})
