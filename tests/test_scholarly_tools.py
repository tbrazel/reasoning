import hashlib
import io
import json
import tarfile

import pytest

from reasoning_ledger import LedgerStore
from reasoning_ledger.agents import HttpResponse, ScientificGateway, ScientificToolError
from reasoning_ledger.agents.project_tools import build_project_tool_registry


def _fixed_lattice_table_row(number, rank, order, group, genus="$1$"):
    fields = [
        str(number),
        str(rank),
        str(order),
        group,
        genus,
        "0",
        "1",
        "1",
        "1",
        "1",
        "-",
        "S*",
    ]
    return "\n".join([*(f"{field} &" for field in fields[:-1]), fields[-1], r"\\"])


def test_arxiv_source_magma_lattice_indexes_literal_data_and_derives_gram(tmp_path):
    magma = b"""
lattices := [ PowerSequence(PowerStructure(List)) |
/* rank one */
[ PowerStructure(List) | [*
LatticeWithBasis(2, [ RationalField() | 1, 0 ],
    MatrixRing(RationalField(), 2) ! [ RationalField() | 1, 0, 0, 1 ]
    :CheckIndependent:=false,CheckPositive:=false),
LatticeWithBasis(2, [ RationalField() | 1, 0, 0, 1 ],
    MatrixRing(RationalField(), 2) ! [ RationalField() | 4, 1, 1, 4 ]
    :CheckIndependent:=false,CheckPositive:=false),
MatrixGroup<2, IntegerRing() | >
*] ]
];
"""
    source = _source_archive(("data/lattices.m", magma))
    registry, transport = _registry(tmp_path, _binary_response(source))

    result = registry.invoke(
        "arxiv_source_magma_lattice",
        {
            "arxiv_id": "1505.06420v3",
            "path": "data/lattices.m",
            "variable": "lattices",
            "indices": [1, 1],
            "component": "fixed",
            "modulus": 5,
        },
    )

    assert len(transport.calls) == 1
    assert result["data"]["indices"] == [1, 1]
    assert result["data"]["component"] == "fixed"
    assert result["data"]["ambient_dimension"] == 2
    assert result["data"]["lattice_rank"] == 2
    assert result["data"]["gram_matrix"] == [[4, 1], [1, 4]]
    assert result["data"]["determinant"] == 15
    assert result["data"]["rank_mod_modulus"] == 1
    assert len(result["data"]["expression_sha256"]) == 64
    assert result["source"]["content_sha256"] == hashlib.sha256(source).hexdigest()
    assert result["source"]["transformation"].endswith("without execution")

    with pytest.raises(ValueError, match="modulus must be prime"):
        registry.invoke(
            "arxiv_source_magma_lattice",
            {
                "arxiv_id": "1505.06420v3",
                "path": "data/lattices.m",
                "variable": "lattices",
                "indices": [1, 1],
                "component": "fixed",
                "modulus": 4,
            },
        )


def test_arxiv_source_magma_fixed_lattice_scan_is_exhaustive_and_hash_bound(tmp_path):
    lattice_rank_two_a = """LatticeWithBasis(2,
        [ RationalField() | 1, 0, 0, 1 ],
        MatrixRing(RationalField(), 2) ! [ RationalField() | 4, 1, 1, 4 ]
        :CheckIndependent:=false,CheckPositive:=false)"""
    lattice_rank_two_b = """LatticeWithBasis(2,
        [ RationalField() | 1, 0, 0, 1 ],
        MatrixRing(RationalField(), 2) ! [ RationalField() | 10, 0, 0, 10 ]
        :CheckIndependent:=false,CheckPositive:=false)"""

    def entry(lattice):
        return f"[* {lattice}, {lattice}, MatrixGroup<2, IntegerRing() | > *]"

    magma = f"""
lattices := [ PowerSequence(PowerStructure(List)) |
[ PowerStructure(List) | ],
[ PowerStructure(List) | {entry(lattice_rank_two_a)}, {entry(lattice_rank_two_b)} ],
[ PowerStructure(List) | [* 1, 2, 3 *] ]
];
""".encode()
    table = "\n".join(
        (
            _fixed_lattice_table_row(1, 3, 24, "$G_1$"),
            _fixed_lattice_table_row(2, 2, 25, "$G_2$"),
            _fixed_lattice_table_row(3, 2, 50, "$G_3$"),
            _fixed_lattice_table_row(4, 1, "$|G_4|$", "$G_4$"),
        )
    ).encode()
    source = _source_archive(("table.erg", table), ("anc/lattices.txt", magma))
    registry, transport = _registry(tmp_path, _binary_response(source))

    result = registry.invoke(
        "arxiv_source_magma_fixed_lattice_scan",
        {
            "arxiv_id": "1505.06420v3",
            "table_path": "table.erg",
            "data_path": "anc/lattices.txt",
            "variable": "lattices",
            "prime": 5,
            "minimum_fixed_rank": 2,
            "table_rows_absent_from_magma": [4],
        },
    )

    assert len(transport.calls) == 1
    data = result["data"]
    assert data["table"]["row_count"] == 4
    assert data["table"]["rank_blocks"] == [
        {
            "fixed_rank": 3,
            "table_entries": 1,
            "magma_entries": 1,
            "start_row": 1,
            "end_row": 1,
            "absent_table_rows": [],
        },
        {
            "fixed_rank": 2,
            "table_entries": 2,
            "magma_entries": 2,
            "start_row": 2,
            "end_row": 3,
            "absent_table_rows": [],
        },
        {
            "fixed_rank": 1,
            "table_entries": 1,
            "magma_entries": 0,
            "start_row": 4,
            "end_row": 4,
            "absent_table_rows": [4],
        },
    ]
    assert data["magma_data"]["rank_sequence_count"] == 3
    assert data["necessary_order_divisor"] == 25
    assert data["candidate_count"] == 2
    assert [candidate["table_row"] for candidate in data["candidates"]] == [2, 3]
    assert [candidate["indices"] for candidate in data["candidates"]] == [
        [2, 1],
        [2, 2],
    ]
    assert [candidate["rank_mod_modulus"] for candidate in data["candidates"]] == [
        1,
        0,
    ]
    assert data["maximum_candidate_rank_mod_prime"] == 1
    assert data["all_candidates_rank_mod_prime_at_most_one"] is True
    assert data["table_rows_absent_from_magma"][0]["table_row"] == 4
    assert data["symbolic_orders_below_minimum"][0]["table_row"] == 4
    assert len(data["candidate_set_sha256"]) == 64
    assert len(data["table"]["file_sha256"]) == 64
    assert len(data["magma_data"]["file_sha256"]) == 64
    assert result["source"]["content_sha256"] == hashlib.sha256(source).hexdigest()
    assert result["source"]["transformation"].endswith("without executing source code")


def test_arxiv_source_magma_fixed_lattice_scan_fails_closed(tmp_path):
    symbolic_table = _fixed_lattice_table_row(1, 2, "$|G|$", "$G$").encode()
    aligned_magma = b"""
lattices := [ PowerSequence(PowerStructure(List)) |
[ PowerStructure(List) | ],
[ PowerStructure(List) | [* 1, 2, 3 *] ]
];
"""
    source = _source_archive(("table.erg", symbolic_table), ("anc/lattices.txt", aligned_magma))
    registry, _ = _registry(tmp_path, _binary_response(source))
    with pytest.raises(ScientificToolError, match="symbolic stabilizer order"):
        registry.invoke(
            "arxiv_source_magma_fixed_lattice_scan",
            {
                "arxiv_id": "1505.06420v3",
                "table_path": "table.erg",
                "data_path": "anc/lattices.txt",
                "variable": "lattices",
                "prime": 5,
                "minimum_fixed_rank": 2,
            },
        )
    with pytest.raises(ValueError, match="only rows below minimum_fixed_rank"):
        registry.invoke(
            "arxiv_source_magma_fixed_lattice_scan",
            {
                "arxiv_id": "1505.06420v3",
                "table_path": "table.erg",
                "data_path": "anc/lattices.txt",
                "variable": "lattices",
                "prime": 5,
                "minimum_fixed_rank": 2,
                "table_rows_absent_from_magma": [1],
            },
        )

    mismatched_table = "\n".join(
        (
            _fixed_lattice_table_row(1, 2, 24, "$G_1$"),
            _fixed_lattice_table_row(2, 2, 24, "$G_2$"),
        )
    ).encode()
    mismatched_source = _source_archive(("table.erg", mismatched_table), ("anc/lattices.txt", aligned_magma))
    registry, _ = _registry(tmp_path / "mismatch", _binary_response(mismatched_source))
    with pytest.raises(ScientificToolError, match="entry count does not match"):
        registry.invoke(
            "arxiv_source_magma_fixed_lattice_scan",
            {
                "arxiv_id": "1505.06420v3",
                "table_path": "table.erg",
                "data_path": "anc/lattices.txt",
                "variable": "lattices",
                "prime": 5,
            },
        )


def test_arxiv_source_magma_group_profile_indexes_stabilizer_with_provenance(tmp_path, monkeypatch):
    magma = rb"""
lattices := [ PowerSequence(PowerStructure(List)) |
[ PowerStructure(List) | [*
LatticeWithBasis(2, [ RationalField() | 1, 0 ],
    MatrixRing(RationalField(), 2) ! [ RationalField() | 1, 0, 0, 1 ]),
LatticeWithBasis(2, [ RationalField() | 1, 0 ],
    MatrixRing(RationalField(), 2) ! [ RationalField() | 1, 0, 0, 1 ]),
MatrixGroup<2, IntegerRing() | Matrix(2, 2, \[ 1, 0, 0, 1 ])>
*] ]
];
"""
    source = _source_archive(("data/lattices.m", magma))
    registry, transport = _registry(tmp_path, _binary_response(source))
    seen = {}

    def profile(expression, prime):
        seen.update({"expression": expression, "prime": prime})
        return {
            "ambient_dimension": 2,
            "generator_count": 1,
            "generators_sha256": "a" * 64,
            "gap_protocol_version": 1,
            "gap_program_sha256": "b" * 64,
            "gap_transcript_sha256": "c" * 64,
            "gap_transcript": "REASONING_GAP_PROFILE_V1\nREASONING_END",
            "gap_executable": "/usr/bin/gap",
            "gap_version": "4.12.1",
            "small_groups_version": "1.5.1",
            "group": {"order": 1, "structure_description": "1"},
            "prime": prime,
            "sylow_subgroup": {
                "order": 1,
                "structure_description": "1",
                "center_order": 1,
            },
            "elementary_abelian_subgroup": None,
        }

    monkeypatch.setattr(
        "reasoning_ledger.agents.scholarly.magma_integer_matrix_group_profile",
        profile,
    )
    result = registry.invoke(
        "arxiv_source_magma_group_profile",
        {
            "arxiv_id": "1505.06420v3",
            "path": "data/lattices.m",
            "variable": "lattices",
            "indices": [1, 1],
            "prime": 5,
        },
    )

    assert len(transport.calls) == 1
    assert seen["prime"] == 5
    assert seen["expression"].startswith("MatrixGroup<2")
    assert result["data"]["component"] == "stabilizer"
    assert result["data"]["generator_count"] == 1
    assert len(result["data"]["expression_sha256"]) == 64
    assert result["source"]["content_sha256"] == hashlib.sha256(source).hexdigest()
    assert result["source"]["transformation"].endswith("without executing source code")


def test_arxiv_source_magma_lattice_rejects_bad_indices_and_nonliteral_components(tmp_path):
    magma = b"lattices := [ PowerSequence(List) | [ PowerSequence(List) | [* 1, 2, 3 *] ] ];"
    source = _source_archive(("data.m", magma))
    registry, _ = _registry(tmp_path, _binary_response(source))

    with pytest.raises(ScientificToolError, match="exceeds dimension"):
        registry.invoke(
            "arxiv_source_magma_lattice",
            {
                "arxiv_id": "1505.06420v3",
                "path": "data.m",
                "variable": "lattices",
                "indices": [1, 2],
                "component": "fixed",
            },
        )
    with pytest.raises(ScientificToolError, match="not a literal LatticeWithBasis"):
        registry.invoke(
            "arxiv_source_magma_lattice",
            {
                "arxiv_id": "1505.06420v3",
                "path": "data.m",
                "variable": "lattices",
                "indices": [1, 1],
                "component": "fixed",
            },
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
    registry = build_project_tool_registry(
        LedgerStore(tmp_path / "ledger"),
        tmp_path / "artifacts",
        scientific_gateway=ScientificGateway(tmp_path / "cache", transport=transport, sleeper=lambda _: None),
    )
    return registry, transport


def _response(payload):
    body = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
    return HttpResponse(200, body, {})


def _binary_response(payload):
    return HttpResponse(200, payload, {"Content-Type": "application/gzip"})


def _source_archive(*members):
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode="w:gz") as archive:
        for path, payload in members:
            info = tarfile.TarInfo(path)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return target.getvalue()


def test_crossref_search_normalizes_deposited_metadata_and_omits_abstracts(tmp_path):
    payload = {
        "status": "ok",
        "message": {
            "total-results": 1,
            "items": [
                {
                    "DOI": "10.1000/example",
                    "title": ["A useful theorem"],
                    "author": [
                        {
                            "given": "Ada",
                            "family": "Lovelace",
                            "ORCID": "https://orcid.org/0000-0000-0000-0001",
                            "sequence": "first",
                        }
                    ],
                    "published": {"date-parts": [[2025, 7, 9]]},
                    "container-title": ["Journal of Examples"],
                    "type": "journal-article",
                    "is-referenced-by-count": 12,
                    "references-count": 23,
                    "relation": {"is-preprint-of": [{"id": "10.1000/other"}]},
                    "update-to": [{"DOI": "10.1000/correction", "type": "correction"}],
                    "license": [{"URL": "https://creativecommons.org/licenses/by/4.0/"}],
                    "abstract": "Copyrighted abstract text is not returned.",
                }
            ],
        },
    }
    registry, transport = _registry(tmp_path, _response(payload))

    result = registry.invoke("crossref_search", {"query": "useful theorem", "max_results": 5})

    assert "query.bibliographic=useful+theorem" in transport.calls[0][0]
    assert "rows=5" in transport.calls[0][0]
    assert "select=" in transport.calls[0][0]
    assert "abstract" not in transport.calls[0][0]
    item = result["data"]["items"][0]
    assert item["doi"] == "10.1000/example"
    assert item["published"] == "2025-07-09"
    assert item["authors"][0]["family"] == "Lovelace"
    assert item["updates"][0]["type"] == "correction"
    assert "abstract" not in item
    assert result["source"]["provider"] == "Crossref"
    assert result["source"]["content_sha256"]


def test_crossref_exact_lookup_normalizes_doi_and_rejects_invalid_identifiers(tmp_path):
    payload = {
        "status": "ok",
        "message": {
            "total-results": 1,
            "items": [
                {
                    "DOI": "10.1000/example.part",
                    "title": ["Exact work"],
                    "published-online": {"date-parts": [[2024]]},
                }
            ],
        },
    }
    registry, transport = _registry(tmp_path, _response(payload))

    result = registry.invoke("crossref_lookup", {"doi": "https://doi.org/10.1000/EXAMPLE.PART"})

    assert transport.calls[0][0].startswith("https://api.crossref.org/works?")
    assert "filter=doi%3A10.1000%2Fexample.part" in transport.calls[0][0]
    assert "select=" in transport.calls[0][0]
    assert "abstract" not in transport.calls[0][0]
    assert result["data"]["title"] == "Exact work"
    with pytest.raises(ValueError, match="invalid DOI"):
        registry.invoke("crossref_lookup", {"doi": "not a doi"})


_ARXIV_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>1</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2401.01234v2</id>
    <updated>2025-03-04T12:00:00Z</updated>
    <published>2024-01-03T10:00:00Z</published>
    <title>  Derived equivalences and examples </title>
    <summary> A precise\nsummary with spacing. </summary>
    <author><name>Ada Lovelace</name></author>
    <author><name>Emmy Noether</name></author>
    <category term="math.AG"/>
    <category term="math.CT"/>
    <arxiv:primary_category term="math.AG"/>
    <arxiv:doi>10.1000/example</arxiv:doi>
    <arxiv:journal_ref>Journal 10 (2025)</arxiv:journal_ref>
    <arxiv:comment>Revised proof</arxiv:comment>
    <arxiv:license href="https://creativecommons.org/licenses/by/4.0/"/>
    <link href="https://arxiv.org/abs/2401.01234v2" rel="alternate" type="text/html"/>
    <link href="https://arxiv.org/pdf/2401.01234v2" rel="related" type="application/pdf" title="pdf"/>
  </entry>
</feed>
"""

_ARXIV_HTML = """<!doctype html>
<html><body>
<nav>This navigation is not paper evidence.</nav>
<article class="ltx_document">
  <h1>A useful paper</h1>
  <section>
    <h2>1. Main results</h2>
    <p>Theorem 1.3. The Hodge-theoretic index divides the generic index.</p>
    <p>Its value is <math alttext="5^6"><mi>ignored</mi></math> in this example.</p>
    <script>malicious_or_irrelevant()</script>
  </section>
</article>
<footer>This footer is not paper evidence.</footer>
</body></html>"""


def test_arxiv_search_is_paced_scoped_and_preserves_versions_and_license(tmp_path):
    registry, transport = _registry(tmp_path, _response(_ARXIV_FEED))

    result = registry.invoke(
        "arxiv_search",
        {"query": "derived equivalences", "category": "math.AG", "max_results": 3},
    )

    url = transport.calls[0][0]
    assert "search_query=all%3A%22derived%22+AND+all%3A%22equivalences%22+AND+cat%3Amath.AG" in url
    assert "max_results=3" in url
    item = result["data"]["items"][0]
    assert item["arxiv_id"] == "2401.01234v2"
    assert item["authors"] == ["Ada Lovelace", "Emmy Noether"]
    assert item["summary"] == "A precise summary with spacing."
    assert item["primary_category"] == "math.AG"
    assert item["doi"] == "10.1000/example"
    assert item["license"].endswith("/by/4.0/")
    assert result["source"]["provider"] == "arXiv"


def test_arxiv_search_can_require_an_exact_phrase(tmp_path):
    registry, transport = _registry(tmp_path, _response(_ARXIV_FEED))

    registry.invoke(
        "arxiv_search",
        {"query": "derived equivalences", "query_mode": "exact_phrase"},
    )

    assert "search_query=all%3A%22derived+equivalences%22" in transport.calls[0][0]


def test_arxiv_exact_lookup_and_validation(tmp_path):
    registry, transport = _registry(tmp_path, _response(_ARXIV_FEED), _response("not xml"))

    result = registry.invoke("arxiv_lookup", {"arxiv_id": "https://arxiv.org/abs/2401.01234v2"})
    assert "id_list=2401.01234v2" in transport.calls[0][0]
    assert result["data"]["updated"] == "2025-03-04T12:00:00Z"
    with pytest.raises(ValueError, match="invalid arXiv identifier"):
        registry.invoke("arxiv_lookup", {"arxiv_id": "../../etc/passwd"})
    with pytest.raises(ScientificToolError, match="invalid Atom XML"):
        registry.invoke("arxiv_search", {"query": "broken"})


def test_arxiv_read_returns_bounded_official_html_excerpts(tmp_path):
    registry, transport = _registry(tmp_path, _response(_ARXIV_HTML))

    result = registry.invoke(
        "arxiv_read",
        {
            "arxiv_id": "2401.01234v2",
            "queries": ["Theorem 1.3", "not in the paper"],
            "context_characters": 200,
        },
    )

    assert transport.calls[0][0] == "https://arxiv.org/html/2401.01234v2"
    assert transport.calls[0][1]["User-Agent"].startswith("Reasoning/")
    assert result["data"]["arxiv_id"] == "2401.01234v2"
    assert result["data"]["document_characters"] > 100
    found, missing = result["data"]["matches"]
    assert found["found"] is True
    assert "The Hodge-theoretic index divides the generic index." in found["excerpt"]
    assert "5^6" in found["excerpt"]
    assert "malicious_or_irrelevant" not in found["excerpt"]
    assert "navigation" not in found["excerpt"]
    assert missing == {"query": "not in the paper", "found": False}
    assert result["source"]["content_sha256"]
    assert result["source"]["transformation"].startswith("Official semantic HTML")


def test_arxiv_read_rejects_pages_without_semantic_article(tmp_path):
    registry, _ = _registry(tmp_path, _response("<html><body>error page</body></html>"))

    with pytest.raises(ScientificToolError, match="no readable article"):
        registry.invoke(
            "arxiv_read",
            {"arxiv_id": "2401.01234", "queries": ["Theorem"]},
        )


def test_arxiv_source_archive_lists_and_reads_hashed_members(tmp_path):
    source = _source_archive(
        ("main.tex", b"Theorem 2. All fixed lattices occur.\n"),
        ("tables/fixed.txt", b"row 1: 5A 5B 5C\nrow 2: order 25\n"),
        ("figures/table.pdf", b"%PDF-1.4\x00binary"),
    )
    registry, transport = _registry(tmp_path, _binary_response(source))

    listing = registry.invoke(
        "arxiv_source_list",
        {"arxiv_id": "1505.06420v3", "max_results": 2},
    )

    assert transport.calls[0][0] == "https://export.arxiv.org/src/1505.06420v3"
    assert transport.calls[0][1]["Accept"] == "application/octet-stream, application/gzip"
    assert listing["data"]["total_files"] == 3
    assert listing["data"]["returned"] == 2
    assert listing["data"]["complete"] is False
    assert listing["data"]["next_start"] == 2
    main = next(item for item in listing["data"]["files"] if item["path"] == "main.tex")
    assert main["text_readable"] is True
    assert len(main["sha256"]) == 64
    assert listing["source"]["content_sha256"] == hashlib.sha256(source).hexdigest()

    queried = registry.invoke(
        "arxiv_source_read",
        {
            "arxiv_id": "1505.06420v3",
            "path": "tables/fixed.txt",
            "queries": ["order 25", "not present"],
            "context_characters": 200,
        },
    )
    assert len(transport.calls) == 1
    assert queried["data"]["mode"] == "query"
    assert queried["data"]["matches"][0]["found"] is True
    assert "5A 5B 5C" in queried["data"]["matches"][0]["excerpt"]
    assert queried["data"]["matches"][1] == {"query": "not present", "found": False}
    assert len(queried["data"]["file_sha256"]) == 64

    chunk = registry.invoke(
        "arxiv_source_read",
        {
            "arxiv_id": "1505.06420v3",
            "path": "tables/fixed.txt",
            "start_character": 0,
            "max_characters": 1000,
        },
    )
    assert chunk["data"]["mode"] == "chunk"
    assert chunk["data"]["complete"] is True
    assert chunk["data"]["text"].endswith("order 25\n")


def test_arxiv_source_archive_rejects_unsafe_member_paths(tmp_path):
    source = _source_archive(("../escape.tex", b"unsafe"))
    registry, _ = _registry(tmp_path, _binary_response(source))

    with pytest.raises(ValueError, match="invalid arXiv source path"):
        registry.invoke("arxiv_source_list", {"arxiv_id": "1505.06420v3"})


def test_arxiv_source_archive_rejects_links(tmp_path):
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode="w:gz") as archive:
        link = tarfile.TarInfo("linked-table.txt")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        archive.addfile(link)
    registry, _ = _registry(tmp_path, _binary_response(target.getvalue()))

    with pytest.raises(ScientificToolError, match="unsupported member type"):
        registry.invoke("arxiv_source_list", {"arxiv_id": "1505.06420v3"})


def test_arxiv_rejects_category_query_injection(tmp_path):
    registry, _ = _registry(tmp_path)
    with pytest.raises(ValueError, match="invalid arXiv category"):
        registry.invoke("arxiv_search", {"query": "geometry", "category": "math.AG OR all:*"})
