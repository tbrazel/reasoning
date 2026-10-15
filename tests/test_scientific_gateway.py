from reasoning_ledger.agents import HttpResponse, ScientificGateway, ScientificToolError


class FakeClock:
    def __init__(self):
        self.value = 1_800_000_000.0
        self.sleeps = []

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_scientific_gateway_caches_and_returns_complete_provenance(tmp_path):
    clock = FakeClock()
    transport = FakeTransport(
        HttpResponse(200, b'{"results":[{"id":"A000045"}]}', {"Content-Type": "application/json"})
    )
    gateway = ScientificGateway(
        tmp_path / "cache", transport=transport, clock=clock, sleeper=clock.sleep
    )
    request = {
        "provider": "OEIS",
        "url": "https://oeis.org/search",
        "query": {"q": "1,1,2,3,5", "fmt": "json"},
        "license": "OEIS End-User License Agreement",
        "minimum_interval": 1,
    }

    first = gateway.get_json(**request)
    second = gateway.get_json(**request)

    assert len(transport.calls) == 1
    assert transport.calls[0][0].endswith("?q=1%2C1%2C2%2C3%2C5&fmt=json")
    assert transport.calls[0][1]["User-Agent"].startswith("Reasoning/")
    assert first["source"]["cache"] == "miss"
    assert second["source"]["cache"] == "hit"
    assert first["source"]["content_sha256"] == second["source"]["content_sha256"]
    assert first["source"]["query"] == request["query"]
    assert first["data"]["results"][0]["id"] == "A000045"


def test_scientific_gateway_honors_retry_after_and_provider_rate_limits(tmp_path):
    clock = FakeClock()
    transport = FakeTransport(
        HttpResponse(429, b"busy", {"Retry-After": "2"}),
        HttpResponse(200, b'{"ok":true}', {}),
        HttpResponse(200, b'{"next":true}', {}),
    )
    gateway = ScientificGateway(
        tmp_path / "cache", transport=transport, clock=clock, sleeper=clock.sleep
    )

    result = gateway.get_json(
        provider="LMFDB",
        url="https://www.lmfdb.org/api/ec_curvedata/",
        query={"conductor": 11},
        minimum_interval=1,
    )
    assert result["data"] == {"ok": True}
    assert clock.sleeps == [2.0]

    gateway.get_json(
        provider="LMFDB",
        url="https://www.lmfdb.org/api/ec_curvedata/",
        query={"conductor": 37},
        minimum_interval=1,
    )
    assert clock.sleeps == [2.0, 1.0]


def test_scientific_gateway_marks_stale_fallbacks_and_rejects_bad_payloads(tmp_path):
    clock = FakeClock()
    gateway = ScientificGateway(
        tmp_path / "cache",
        transport=FakeTransport(HttpResponse(200, b"plain text", {})),
        clock=clock,
        sleeper=clock.sleep,
    )
    seeded = gateway.get_text(
        provider="arXiv",
        url="https://export.arxiv.org/api/query",
        query={"id_list": "2401.00001"},
        ttl_seconds=1,
        minimum_interval=0,
    )
    assert seeded["data"] == "plain text"

    clock.value += 2
    gateway.transport = FakeTransport(OSError("offline"))
    stale = gateway.get_text(
        provider="arXiv",
        url="https://export.arxiv.org/api/query",
        query={"id_list": "2401.00001"},
        ttl_seconds=1,
        minimum_interval=0,
        max_attempts=1,
    )
    assert stale["source"]["cache"] == "stale"
    assert "offline" in stale["source"]["warning"]

    broken = ScientificGateway(
        tmp_path / "broken",
        transport=FakeTransport(HttpResponse(200, b"not-json", {})),
        clock=clock,
        sleeper=clock.sleep,
    )
    try:
        broken.get_json(
            provider="Example",
            url="https://example.test/data",
            minimum_interval=0,
            max_attempts=1,
        )
    except ScientificToolError as error:
        assert "invalid JSON" in str(error)
    else:
        raise AssertionError("invalid JSON should fail")
