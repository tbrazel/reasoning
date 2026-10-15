import subprocess

import pytest

from reasoning_ledger.agents import magma_group
from reasoning_ledger.agents.magma_group import (
    _parse_gap_profile,
    magma_integer_matrix_group_profile,
    parse_magma_integer_matrix_group,
)
from reasoning_ledger.agents.scientific import ScientificToolError

EXPRESSION = r"""MatrixGroup<2, IntegerRing() |
Matrix(2, 2, \[ 1, 0, 0, 1 ]),
Matrix(2, 2, [ 0, -1, 1, 0 ])
>"""

TRANSCRIPT = """REASONING_GAP_PROFILE_V1
gap_version=4.12.1
small_groups_version=1.5.1
group_size=500
group_structure=((C5 x C5) : C5) : C4
sylow_size=125
sylow_structure=(C5 x C5) : C5
center_size=5
subgroup_found=true
subgroup_size=25
subgroup_structure=C5 x C5
subgroup_abelian=true
subgroup_exponent=5
common_fixed_rank=1
element_rank_counts=1:4,2:20,
line_rank_counts=1:1,2:5,
REASONING_END
"""


def test_parse_magma_integer_matrix_group_accepts_only_literal_generators():
    parsed = parse_magma_integer_matrix_group(EXPRESSION)

    assert parsed.dimension == 2
    assert len(parsed.generators) == 2
    assert parsed.generators[0] == ((1, 0), (0, 1))
    assert parsed.generators[1] == ((0, -1), (1, 0))

    with pytest.raises(ScientificToolError, match="nonliteral generator"):
        parse_magma_integer_matrix_group('MatrixGroup<2, IntegerRing() | Read("untrusted") >')
    with pytest.raises(ScientificToolError, match="unexpected trailing text"):
        parse_magma_integer_matrix_group(EXPRESSION + '; System("untrusted")')
    with pytest.raises(ScientificToolError, match="inconsistent number of entries"):
        parse_magma_integer_matrix_group("MatrixGroup<2, IntegerRing() | Matrix(2,2,[1,0,0])>")
    with pytest.raises(ScientificToolError, match="does not match"):
        parse_magma_integer_matrix_group("MatrixGroup<2, IntegerRing() | Matrix(3,3,[1,0,0,0,1,0,0,0,1])>")


def test_parse_gap_profile_checks_element_and_line_counts():
    result = _parse_gap_profile(TRANSCRIPT, dimension=2, prime=5)

    subgroup = result["elementary_abelian_subgroup"]
    assert result["group"]["order"] == 500
    assert result["sylow_subgroup"]["center_order"] == 5
    assert subgroup["order"] == 25
    assert subgroup["common_fixed_rank"] == 1
    assert subgroup["nonidentity_element_fixed_ranks"] == [
        {"fixed_rank": 1, "count": 4},
        {"fixed_rank": 2, "count": 20},
    ]
    assert subgroup["order_p_line_fixed_ranks"] == [
        {"fixed_rank": 1, "count": 1},
        {"fixed_rank": 2, "count": 5},
    ]

    with pytest.raises(ScientificToolError, match="inconsistent"):
        _parse_gap_profile(
            TRANSCRIPT.replace("line_rank_counts=1:1,2:5", "line_rank_counts=1:2,2:4"),
            dimension=2,
            prime=5,
        )


def test_matrix_group_profile_runs_only_the_fixed_gap_program(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, TRANSCRIPT, "")

    monkeypatch.setattr(magma_group.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(magma_group.subprocess, "run", fake_run)

    result = magma_integer_matrix_group_profile(EXPRESSION, 5)

    assert captured["command"][0] == "/usr/bin/gap"
    assert captured["timeout"] == 60
    assert set(captured["env"]) == {"PATH", "LANG", "LC_ALL"}
    assert "MatrixGroup" not in captured["input"]
    assert "Read(" not in captured["input"]
    assert "System(" not in captured["input"]
    assert "gens := [[[1,0],[0,1]],[[0,-1],[1,0]]]" in captured["input"]
    assert result["generator_count"] == 2
    assert result["gap_protocol_version"] == 1
    assert len(result["generators_sha256"]) == 64
    assert len(result["gap_program_sha256"]) == 64
    assert len(result["gap_transcript_sha256"]) == 64


def test_matrix_group_profile_requires_a_small_prime_and_gap(monkeypatch):
    with pytest.raises(ValueError, match="prime number"):
        magma_integer_matrix_group_profile(EXPRESSION, 4)

    monkeypatch.setattr(magma_group.shutil, "which", lambda _name: None)
    with pytest.raises(ScientificToolError, match="GAP is unavailable"):
        magma_integer_matrix_group_profile(EXPRESSION, 5)
