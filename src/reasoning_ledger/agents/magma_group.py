"""Bounded profiles of literal Magma integer matrix groups via GAP."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..canonical import canonical_json
from .scientific import ScientificToolError

_MATRIX_GROUP_MAX_CHARACTERS = 2_000_000
_MATRIX_GROUP_MAX_DIMENSION = 32
_MATRIX_GROUP_MAX_GENERATORS = 16
_MATRIX_GROUP_MAX_INTEGER_DIGITS = 40
_GAP_MAX_GROUP_ORDER = 10_000_000
_GAP_MAX_SYLOW_ORDER = 100_000
_GAP_MAX_OUTPUT_BYTES = 64 * 1024
_GAP_TIMEOUT_SECONDS = 60
_GAP_PROTOCOL_VERSION = 1
_INTEGER = re.compile(r"^[+-]?[0-9]+$")
_MATRIX_GROUP_HEADER = re.compile(r"\s*MatrixGroup\s*<\s*([0-9]+)\s*,\s*IntegerRing\(\)\s*\|")
_MATRIX_HEADER = re.compile(r"\s*Matrix\s*\(\s*([0-9]+)\s*,\s*([0-9]+)\s*,\s*\\?\[\s*")


@dataclass(frozen=True)
class IntegerMatrixGroup:
    """One validated literal MatrixGroup over the integers."""

    dimension: int
    generators: tuple[tuple[tuple[int, ...], ...], ...]


def _prime(value: int) -> bool:
    if value < 2:
        return False
    divisor = 2
    while divisor * divisor <= value:
        if value % divisor == 0:
            return False
        divisor += 1
    return True


def parse_magma_integer_matrix_group(expression: str) -> IntegerMatrixGroup:
    """Parse the narrow literal MatrixGroup grammar emitted by the source archive."""

    if len(expression) > _MATRIX_GROUP_MAX_CHARACTERS:
        raise ScientificToolError("Magma MatrixGroup expression exceeds the safe bound")
    header = _MATRIX_GROUP_HEADER.match(expression)
    if header is None:
        raise ScientificToolError("selected Magma component is not a literal integer MatrixGroup")
    dimension = int(header.group(1))
    if not 1 <= dimension <= _MATRIX_GROUP_MAX_DIMENSION:
        raise ScientificToolError("Magma MatrixGroup dimension is outside the safe bound")

    position = header.end()
    generators: list[tuple[tuple[int, ...], ...]] = []
    while True:
        whitespace = re.match(r"\s*", expression[position:])
        assert whitespace is not None
        position += whitespace.end()
        if position < len(expression) and expression[position] == ">":
            position += 1
            break
        if generators:
            if position >= len(expression) or expression[position] != ",":
                raise ScientificToolError("Magma MatrixGroup generators are not comma-separated literals")
            position += 1
        matrix_header = _MATRIX_HEADER.match(expression, position)
        if matrix_header is None:
            raise ScientificToolError("Magma MatrixGroup contains a nonliteral generator")
        rows, columns = (int(value) for value in matrix_header.groups())
        if rows != dimension or columns != dimension:
            raise ScientificToolError("Magma MatrixGroup generator dimension does not match its parent")
        list_end = expression.find("]", matrix_header.end())
        if list_end < 0:
            raise ScientificToolError("Magma MatrixGroup generator has an unterminated integer list")
        raw_values = expression[matrix_header.end() : list_end]
        tokens = [token.strip() for token in raw_values.split(",")]
        if any(
            not token or not _INTEGER.fullmatch(token) or len(token.lstrip("+-")) > _MATRIX_GROUP_MAX_INTEGER_DIGITS for token in tokens
        ):
            raise ScientificToolError("Magma MatrixGroup generator contains a nonliteral or oversized integer")
        values = [int(token) for token in tokens]
        if len(values) != dimension * dimension:
            raise ScientificToolError("Magma MatrixGroup generator has an inconsistent number of entries")
        close = re.match(r"\s*\)", expression[list_end + 1 :])
        if close is None:
            raise ScientificToolError("Magma MatrixGroup generator has an invalid terminator")
        position = list_end + 1 + close.end()
        generators.append(tuple(tuple(values[index : index + dimension]) for index in range(0, len(values), dimension)))
        if len(generators) > _MATRIX_GROUP_MAX_GENERATORS:
            raise ScientificToolError("Magma MatrixGroup has too many generators for the safe bound")

    if expression[position:].strip():
        raise ScientificToolError("Magma MatrixGroup expression contains unexpected trailing text")
    if not generators:
        raise ScientificToolError("Magma MatrixGroup has no literal generators")
    return IntegerMatrixGroup(dimension, tuple(generators))


def _gap_program(group: IntegerMatrixGroup, prime: int) -> str:
    generators = canonical_json({"generators": group.generators}).strip()
    generators = generators[len('{"generators":') : -1]
    dimension = group.dimension
    return f"""gens := {generators};;
G := Group(gens);;
if not IsFinite(G) then
  Print("REASONING_ERROR=group_not_finite\\n");
  QUIT_GAP(2);
fi;
p := {prime};;
sizeG := Size(G);;
if sizeG > {_GAP_MAX_GROUP_ORDER} then
  Print("REASONING_ERROR=group_order_bound\\n");
  QUIT_GAP(3);
fi;
P := SylowSubgroup(G,p);;
sizeP := Size(P);;
if sizeP > {_GAP_MAX_SYLOW_ORDER} then
  Print("REASONING_ERROR=sylow_order_bound\\n");
  QUIT_GAP(4);
fi;
cent := Center(P);;
pelts := Filtered(Elements(P), g -> Order(g)=p);;
z := First(Elements(cent), g -> Order(g)=p);;
hgens := fail;;
if z <> fail then
  zgroup := Group(z);;
  for x in pelts do
    if not x in zgroup and Comm(z,x)=One(P) and Size(Group(z,x))=p^2 then
      hgens := [z,x];
      break;
    fi;
  od;
fi;
if hgens = fail then
  for i in [1..Length(pelts)] do
    if i < Length(pelts) then
      for j in [i+1..Length(pelts)] do
        if Comm(pelts[i],pelts[j])=One(P) and Size(Group(pelts[i],pelts[j]))=p^2 then
          hgens := [pelts[i],pelts[j]];
          break;
        fi;
      od;
    fi;
    if hgens <> fail then
      break;
    fi;
  od;
fi;
smallgrp := LoadPackage("smallgrp", false);;
Print("REASONING_GAP_PROFILE_V{_GAP_PROTOCOL_VERSION}\\n");
Print("gap_version=", GAPInfo.Version, "\\n");
if smallgrp = fail then
  Print("small_groups_version=unavailable\\n");
else
  Print("small_groups_version=", PackageInfo("smallgrp")[1].Version, "\\n");
fi;
Print("group_size=", sizeG, "\\n");
if smallgrp = fail then
  Print("group_structure=unavailable\\n");
else
  Print("group_structure=", StructureDescription(G), "\\n");
fi;
Print("sylow_size=", sizeP, "\\n");
if smallgrp = fail then
  Print("sylow_structure=unavailable\\n");
else
  Print("sylow_structure=", StructureDescription(P), "\\n");
fi;
Print("center_size=", Size(cent), "\\n");
if hgens = fail then
  Print("subgroup_found=false\\n");
  Print("REASONING_END\\n");
  QUIT_GAP(0);
fi;
H := Group(hgens);;
id := IdentityMat({dimension});;
frank := g -> {dimension} - RankMat(g-id);;
ranks := List(Filtered(Elements(H), g -> g<>One(H)), frank);;
remaining := Filtered(Elements(H), g -> g<>One(H));;
lineranks := [];;
while Length(remaining)>0 do
  x := remaining[1];;
  line := Set(Elements(Group(x)));;
  Add(lineranks,frank(x));
  remaining := Difference(Set(remaining),line);
od;
stacked := Concatenation(List(hgens, g -> g-id));;
Print("subgroup_found=true\\n");
Print("subgroup_size=", Size(H), "\\n");
if smallgrp = fail then
  Print("subgroup_structure=unavailable\\n");
else
  Print("subgroup_structure=", StructureDescription(H), "\\n");
fi;
Print("subgroup_abelian=", IsAbelian(H), "\\n");
Print("subgroup_exponent=", Exponent(H), "\\n");
Print("common_fixed_rank=", {dimension}-RankMat(stacked), "\\n");
Print("element_rank_counts=");
for r in Set(ranks) do
  count := 0;;
  for value in ranks do
    if value=r then count := count+1; fi;
  od;
  Print(r, ":", count, ",");
od;
Print("\\n");
Print("line_rank_counts=");
for r in Set(lineranks) do
  count := 0;;
  for value in lineranks do
    if value=r then count := count+1; fi;
  od;
  Print(r, ":", count, ",");
od;
Print("\\nREASONING_END\\n");
QUIT_GAP(0);
"""


def _integer_field(fields: dict[str, str], name: str) -> int:
    try:
        value = int(fields[name])
    except (KeyError, ValueError) as error:
        raise ScientificToolError(f"GAP profile has an invalid {name} field") from error
    if value < 0:
        raise ScientificToolError(f"GAP profile has an invalid {name} field")
    return value


def _rank_counts(raw: str, dimension: int, name: str) -> list[dict[str, int]]:
    result: dict[int, int] = {}
    for entry in (item for item in raw.split(",") if item):
        try:
            rank_text, count_text = entry.split(":", 1)
            rank, count = int(rank_text), int(count_text)
        except ValueError as error:
            raise ScientificToolError(f"GAP profile has an invalid {name} field") from error
        if not 0 <= rank <= dimension or count <= 0 or rank in result:
            raise ScientificToolError(f"GAP profile has an invalid {name} field")
        result[rank] = count
    if not result:
        raise ScientificToolError(f"GAP profile has an empty {name} field")
    return [{"fixed_rank": rank, "count": result[rank]} for rank in sorted(result)]


def _power_of_prime(value: int, prime: int) -> bool:
    if value < 1:
        return False
    while value % prime == 0:
        value //= prime
    return value == 1


def _parse_gap_profile(transcript: str, *, dimension: int, prime: int) -> dict[str, Any]:
    lines = transcript.splitlines()
    if not lines or lines[0] != f"REASONING_GAP_PROFILE_V{_GAP_PROTOCOL_VERSION}" or lines[-1] != "REASONING_END":
        raise ScientificToolError("GAP matrix-group profile has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("GAP matrix-group profile has an invalid field")
        name, value = line.split("=", 1)
        if not name or name in fields:
            raise ScientificToolError("GAP matrix-group profile has duplicate fields")
        fields[name] = value

    required = {
        "gap_version",
        "small_groups_version",
        "group_size",
        "group_structure",
        "sylow_size",
        "sylow_structure",
        "center_size",
        "subgroup_found",
    }
    if not required.issubset(fields):
        raise ScientificToolError("GAP matrix-group profile is incomplete")
    group_size = _integer_field(fields, "group_size")
    sylow_size = _integer_field(fields, "sylow_size")
    center_size = _integer_field(fields, "center_size")
    if (
        group_size < 1
        or sylow_size < 1
        or group_size % sylow_size
        or not _power_of_prime(sylow_size, prime)
        or center_size < 1
        or sylow_size % center_size
    ):
        raise ScientificToolError("GAP matrix-group profile has inconsistent group orders")
    result: dict[str, Any] = {
        "gap_version": fields["gap_version"],
        "small_groups_version": fields["small_groups_version"],
        "group": {
            "order": group_size,
            "structure_description": fields["group_structure"],
        },
        "prime": prime,
        "sylow_subgroup": {
            "order": sylow_size,
            "structure_description": fields["sylow_structure"],
            "center_order": center_size,
        },
    }
    if fields["subgroup_found"] == "false":
        result["elementary_abelian_subgroup"] = None
        return result
    if fields["subgroup_found"] != "true":
        raise ScientificToolError("GAP matrix-group profile has an invalid subgroup flag")

    subgroup_required = {
        "subgroup_size",
        "subgroup_structure",
        "subgroup_abelian",
        "subgroup_exponent",
        "common_fixed_rank",
        "element_rank_counts",
        "line_rank_counts",
    }
    if not subgroup_required.issubset(fields):
        raise ScientificToolError("GAP elementary-abelian subgroup profile is incomplete")
    subgroup_size = _integer_field(fields, "subgroup_size")
    subgroup_exponent = _integer_field(fields, "subgroup_exponent")
    common_fixed_rank = _integer_field(fields, "common_fixed_rank")
    element_counts = _rank_counts(fields["element_rank_counts"], dimension, "element_rank_counts")
    line_counts = _rank_counts(fields["line_rank_counts"], dimension, "line_rank_counts")
    element_by_rank = {entry["fixed_rank"]: entry["count"] for entry in element_counts}
    line_by_rank = {entry["fixed_rank"]: entry["count"] for entry in line_counts}
    if (
        fields["subgroup_abelian"] != "true"
        or subgroup_size != prime**2
        or subgroup_exponent != prime
        or not 0 <= common_fixed_rank <= dimension
        or sum(element_by_rank.values()) != prime**2 - 1
        or sum(line_by_rank.values()) != prime + 1
        or element_by_rank != {rank: (prime - 1) * count for rank, count in line_by_rank.items()}
        or any(common_fixed_rank > rank for rank in element_by_rank)
    ):
        raise ScientificToolError("GAP elementary-abelian subgroup profile is inconsistent")
    result["elementary_abelian_subgroup"] = {
        "selection": (
            "central order-p element plus the first independent commuting order-p element, with exhaustive commuting-pair fallback"
        ),
        "order": subgroup_size,
        "structure_description": fields["subgroup_structure"],
        "abelian": True,
        "exponent": subgroup_exponent,
        "common_fixed_rank": common_fixed_rank,
        "nonidentity_element_fixed_ranks": element_counts,
        "order_p_line_fixed_ranks": line_counts,
    }
    return result


def magma_integer_matrix_group_profile(expression: str, prime: int) -> dict[str, Any]:
    """Profile one validated literal matrix group with a fixed GAP protocol."""

    if not 2 <= prime <= 97 or not _prime(prime):
        raise ValueError("prime must be a prime number between 2 and 97")
    group = parse_magma_integer_matrix_group(expression)
    executable = shutil.which("gap")
    if executable is None:
        raise ScientificToolError("GAP is unavailable on the Reasoning service PATH")
    program = _gap_program(group, prime)
    environment = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    command = [
        executable,
        "-q",
        "-r",
        "-A",
        "-T",
        "--quitonbreak",
        "-n",
        "-x",
        "1000",
        "-K",
        "512m",
    ]
    try:
        completed = subprocess.run(  # noqa: S603
            command,
            input=program,
            text=True,
            capture_output=True,
            timeout=_GAP_TIMEOUT_SECONDS,
            env=environment,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ScientificToolError(f"GAP matrix-group profile exceeded the {_GAP_TIMEOUT_SECONDS}-second wall timeout") from error
    except OSError as error:
        raise ScientificToolError(f"GAP matrix-group profile could not start: {error}") from error
    stdout = completed.stdout
    stderr = completed.stderr
    if len(stdout.encode()) > _GAP_MAX_OUTPUT_BYTES or len(stderr.encode()) > _GAP_MAX_OUTPUT_BYTES:
        raise ScientificToolError("GAP matrix-group profile output exceeds the safe bound")
    if completed.returncode or stderr.strip():
        detail = (stderr.strip() or stdout.strip() or "unknown GAP failure")[:2000]
        raise ScientificToolError(f"GAP matrix-group profile failed: {detail}")
    profile = _parse_gap_profile(stdout, dimension=group.dimension, prime=prime)
    generator_payload = {"dimension": group.dimension, "generators": group.generators}
    return {
        "ambient_dimension": group.dimension,
        "generator_count": len(group.generators),
        "generators_sha256": hashlib.sha256(canonical_json(generator_payload).encode()).hexdigest(),
        "gap_protocol_version": _GAP_PROTOCOL_VERSION,
        "gap_program_sha256": hashlib.sha256(program.encode()).hexdigest(),
        "gap_transcript_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
        "gap_transcript": stdout.strip(),
        "gap_executable": str(Path(executable).resolve()),
        **profile,
    }
