import json

import pytest

from reasoning_ledger import LedgerStore
from reasoning_ledger.agents import HttpResponse, ScientificGateway, ScientificToolError
from reasoning_ledger.agents.project_tools import build_project_tool_registry


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        return self.responses.pop(0)


def _registry(tmp_path, *payloads):
    transport = FakeTransport(
        *(HttpResponse(200, json.dumps(payload).encode(), {}) for payload in payloads)
    )
    registry = build_project_tool_registry(
        LedgerStore(tmp_path / "ledger"),
        tmp_path / "artifacts",
        scientific_gateway=ScientificGateway(
            tmp_path / "cache", transport=transport, sleeper=lambda _: None
        ),
    )
    return registry, transport


def test_lmfdb_elliptic_curve_search_uses_typed_filters_and_direct_links(tmp_path):
    payload = {
        "table": "ec_curvedata",
        "data": [
            {
                "lmfdb_label": "11.a1",
                "ainvs": [0, -1, 1, -7820, -263580],
                "conductor": 11,
                "rank": 0,
                "analytic_rank": 0,
                "torsion": 1,
                "bad_primes": [11],
            },
            {"lmfdb_label": "11.a2", "rank": 0},
        ],
        "start": 0,
        "offset": 100,
        "next": "/api/ec_curvedata/?_offset=100",
    }
    registry, transport = _registry(tmp_path, payload)

    result = registry.invoke(
        "lmfdb_elliptic_curves_q",
        {"label": "11.a1", "rank": 0, "max_results": 1},
    )

    url = transport.calls[0][0]
    assert url.startswith("https://www.lmfdb.org/api/ec_curvedata/?")
    assert "lmfdb_label=s11.a1" in url
    assert "rank=i0" in url
    assert "_fields=" in url
    assert result["data"] == {
        "table": "ec_curvedata",
        "start": 0,
        "provider_next_offset": 100,
        "returned": 1,
        "records": [
            {
                "lmfdb_label": "11.a1",
                "ainvs": [0, -1, 1, -7820, -263580],
                "conductor": 11,
                "rank": 0,
                "analytic_rank": 0,
                "torsion": 1,
                "bad_primes": [11],
                "object_url": "https://www.lmfdb.org/EllipticCurve/Q/11/a/1",
            }
        ],
        "provider_next": "/api/ec_curvedata/?_offset=100",
    }
    assert result["source"]["provider"] == "LMFDB"
    assert result["source"]["license"].startswith("CC BY-SA")
    assert result["source"]["content_sha256"]


def test_lmfdb_number_field_search_preserves_conditional_flags(tmp_path):
    payload = {
        "table": "nf_fields",
        "data": [
            {
                "label": "2.2.5.1",
                "coeffs": [-1, -1, 1],
                "degree": 2,
                "r2": 0,
                "disc_abs": 5,
                "class_number": 1,
                "galois_label": "2T1",
                "used_grh": False,
            }
        ],
        "start": 400,
        "offset": 500,
        "next": None,
    }
    registry, transport = _registry(tmp_path, payload)

    result = registry.invoke(
        "lmfdb_number_fields",
        {
            "degree": 2,
            "complex_places": 0,
            "class_number": 1,
            "galois_label": "2T1",
            "offset": 400,
        },
    )

    url = transport.calls[0][0]
    assert "degree=i2" in url
    assert "r2=i0" in url
    assert "class_number=i1" in url
    assert "galois_label=s2T1" in url
    assert "_offset=400" in url
    record = result["data"]["records"][0]
    assert record["used_grh"] is False
    assert record["object_url"] == "https://www.lmfdb.org/NumberField/2.2.5.1"


def test_lmfdb_tools_reject_unsafe_labels_and_malformed_responses(tmp_path):
    registry, _ = _registry(tmp_path, {"unexpected": []})

    with pytest.raises(ValueError, match="LMFDB labels"):
        registry.invoke("lmfdb_number_fields", {"label": "2.2.5.1&degree=i9"})
    with pytest.raises(ScientificToolError, match="no data list"):
        registry.invoke("lmfdb_elliptic_curves_q", {"conductor": 11})
