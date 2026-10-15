import json

import pytest

from reasoning_ledger import LedgerStore
from reasoning_ledger.agents import (
    HttpResponse,
    ScientificGateway,
    ScientificToolError,
    ToolGrant,
    ToolRegistryError,
    build_project_tool_registry,
)


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        return self.responses.pop(0)


def _registry(tmp_path, *responses):
    transport = FakeTransport(*responses)
    gateway = ScientificGateway(
        tmp_path / "cache",
        transport=transport,
        sleeper=lambda _: None,
    )
    registry = build_project_tool_registry(
        LedgerStore(tmp_path / "ledger"),
        tmp_path / "artifacts",
        scientific_gateway=gateway,
    )
    return registry, transport


def test_oeis_search_normalizes_entries_and_preserves_provenance(tmp_path):
    payload = {
        "count": 2,
        "start": 0,
        "results": [
            {
                "number": 45,
                "data": "0,1,1,2,3,5,8",
                "name": "Fibonacci numbers",
                "offset": "0,4",
                "keyword": "nonn,easy,nice,core",
                "comment": ["A classical recurrence."],
                "formula": "a(n) = a(n-1) + a(n-2).",
                "link": ["<a href=\"/A000045\">entry</a>"],
                "author": "N. J. A. Sloane",
            },
            {"number": 324921, "data": "0,1,1,2,3,5,8", "offset": "0,4"},
        ],
    }
    registry, transport = _registry(
        tmp_path,
        HttpResponse(200, json.dumps(payload).encode(), {}),
    )

    result = registry.invoke(
        "oeis_search",
        {"terms": [0, 1, 1, 2, 3, 5, 8], "max_results": 1},
    )

    assert len(transport.calls) == 1
    assert "q=0%2C1%2C1%2C2%2C3%2C5%2C8" in transport.calls[0][0]
    assert result["data"]["count"] == 2
    assert result["data"]["returned"] == 1
    assert result["data"]["results"][0] == {
        "a_number": "A000045",
        "url": "https://oeis.org/A000045",
        "name": "Fibonacci numbers",
        "terms": [0, 1, 1, 2, 3, 5, 8],
        "offset": [0, 4],
        "keywords": ["nonn", "easy", "nice", "core"],
        "comments": ["A classical recurrence."],
        "formulas": ["a(n) = a(n-1) + a(n-2)."],
        "links": ["<a href=\"/A000045\">entry</a>"],
        "author": "N. J. A. Sloane",
    }
    source = result["source"]
    assert source["provider"] == "OEIS"
    assert source["url"].startswith("https://oeis.org/search?")
    assert source["content_sha256"]
    assert source["license"].startswith("CC BY-SA 4.0")
    assert source["attribution"].endswith("(OEIS)")


def test_oeis_lookup_requires_exact_identifier_and_bfile_keeps_indices(tmp_path):
    lookup = {
        "count": 1,
        "results": [{"number": 45, "data": "0,1,1,2", "offset": "0,4"}],
    }
    bfile = b"# Fibonacci numbers\n0 0\n1 1\n2 1\ninvalid\n3 2\n4 3\n"
    registry, transport = _registry(
        tmp_path,
        HttpResponse(200, json.dumps(lookup).encode(), {}),
        HttpResponse(200, bfile, {}),
    )

    entry = registry.invoke("oeis_lookup", {"a_number": "45"})
    assert entry["data"]["a_number"] == "A000045"
    assert "q=id%3AA000045" in transport.calls[0][0]
    extended = registry.invoke(
        "oeis_bfile", {"a_number": "A45", "max_terms": 4}
    )
    assert transport.calls[1][0] == "https://oeis.org/A000045/b000045.txt"
    assert extended["data"]["terms"] == [
        {"index": 0, "value": 0},
        {"index": 1, "value": 1},
        {"index": 2, "value": 1},
        {"index": 3, "value": 2},
    ]
    with pytest.raises(ValueError, match="OEIS identifier"):
        registry.invoke("oeis_lookup", {"a_number": "not-an-id"})


def test_oeis_tools_require_external_science_grants_and_reject_wrong_lookup(tmp_path):
    registry, _ = _registry(
        tmp_path,
        HttpResponse(
            200,
            b'{"count":1,"results":[{"number":46,"data":"1,1,2"}]}',
            {},
        ),
    )

    with pytest.raises(ToolRegistryError, match="external"):
        registry.select(ToolGrant(("oeis_search",)))
    with pytest.raises(ToolRegistryError, match="science:query"):
        registry.select(
            ToolGrant(("oeis_search",), allowed_risks=("read", "external"))
        )
    selected = registry.select(
        ToolGrant(
            ("oeis_search",),
            allowed_risks=("read", "external"),
            capabilities=("science:query",),
        )
    )
    assert [tool.name for tool in selected] == ["oeis_search"]
    with pytest.raises(ScientificToolError, match="not found"):
        registry.invoke("oeis_lookup", {"a_number": "A000045"})
