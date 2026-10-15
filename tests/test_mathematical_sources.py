"""Mathematical source adapters use bounded public requests, not paid inference."""

from urllib.parse import parse_qs, urlsplit

import pytest

from reasoning_ledger.server import mathematical_sources as sources


def test_lmfdb_discovers_all_live_collections_and_preserves_typed_query(monkeypatch):
    urls = []

    async def public_get(url):
        urls.append(url)
        if url.endswith("/api/"):
            return b'<a href="/api/gps_groups/">Groups</a><a href="/api/hmf_forms/">Forms</a>', {}
        return b'{"data":[{"label":"example"}]}', {"content-type": "application/json"}

    monkeypatch.setattr(sources, "public_get", public_get)
    assert sources.lmfdb_catalog()["collections"] == ["gps_groups", "hmf_forms"]
    value = sources.lmfdb_query("hmf_forms", {"level_norm": "i23"}, ["label"], 10)
    assert value["content"]["data"][0]["label"] == "example"
    assert parse_qs(urlsplit(urls[-1]).query)["level_norm"] == ["i23"]
    with pytest.raises(ValueError, match="Python"):
        sources.lmfdb_query("hmf_forms", {"level_norm": "py23"})
    with pytest.raises(ValueError):
        sources.lmfdb_query("../private", {})


@pytest.mark.parametrize("source", ["mathoverflow", "wikipedia", "nlab", "lmfdb", "stacks", "encyclopedia"])
def test_source_read_is_bounded_and_rejects_site_escape(monkeypatch, source):
    monkeypatch.setattr(sources, "fetch", lambda url: {"url": url, "sha256": "a" * 64, "content": "Mathematical text."})
    value = sources.source_read(source, "/example", 0, 4)
    assert value["text"] == "Math" and value["truncated"]
    assert value["url"].startswith(sources.SOURCES[source])
    with pytest.raises(ValueError):
        sources.source_read(source, "//127.0.0.1/secret")
