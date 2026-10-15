"""Curated LMFDB arithmetic-geometry tools with bounded typed queries."""

from __future__ import annotations

import re
from typing import Any

from .scientific import ScientificGateway, ScientificToolError
from .tools import ToolRegistry

_LICENSE = "CC BY-SA; see https://www.lmfdb.org/license"
_LABEL = re.compile(r"^[A-Za-z0-9.-]{1,120}$")
_ELLIPTIC_LABEL = re.compile(r"^(?P<conductor>[0-9]+)\.(?P<class>[a-z]+)(?P<number>[0-9]+)$")
_ELLIPTIC_FIELDS = (
    "lmfdb_label,ainvs,conductor,rank,analytic_rank,torsion,torsion_structure,"
    "cm,jinv,bad_primes,isogeny_degrees,sha,semistable,nonmax_primes,"
    "min_quad_twist_ainvs,min_quad_twist_disc"
)
_NUMBER_FIELD_FIELDS = (
    "label,coeffs,degree,r2,disc_abs,disc_sign,galois_label,class_number,"
    "class_group,narrow_class_number,narrow_class_group,ramps,cm,is_galois,"
    "gal_is_abelian,gal_is_cyclic,gal_is_solvable,subfields,used_grh"
)


def _label(value: str) -> str:
    result = value.strip()
    if not _LABEL.fullmatch(result):
        raise ValueError("LMFDB labels may contain only letters, numbers, dots, and hyphens")
    return result


def _integer(value: int | None) -> str | None:
    return f"i{value}" if value is not None else None


def _string(value: str | None) -> str | None:
    return f"s{_label(value)}" if value else None


def _elliptic_url(label: str) -> str:
    match = _ELLIPTIC_LABEL.fullmatch(label)
    if not match:
        return f"https://www.lmfdb.org/EllipticCurve/Q/data/{label}"
    return (
        "https://www.lmfdb.org/EllipticCurve/Q/"
        f"{match.group('conductor')}/{match.group('class')}/{match.group('number')}"
    )


def _query(
    gateway: ScientificGateway,
    *,
    table: str,
    fields: str,
    filters: dict[str, str | None],
    offset: int,
    max_results: int,
    object_url,
) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        "_format": "json",
        "_fields": fields,
        "_offset": offset,
    }
    parameters.update({key: value for key, value in filters.items() if value is not None})
    envelope = gateway.get_json(
        provider="LMFDB",
        url=f"https://www.lmfdb.org/api/{table}/",
        query=parameters,
        license=_LICENSE,
        ttl_seconds=7 * 24 * 60 * 60,
        minimum_interval=0.5,
    )
    payload = envelope.get("data")
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ScientificToolError("LMFDB JSON response has no data list")
    records = []
    for value in payload["data"][:max_results]:
        if not isinstance(value, dict):
            continue
        record = dict(value)
        label = record.get("lmfdb_label") or record.get("label")
        if isinstance(label, str):
            record["object_url"] = object_url(label)
        records.append(record)
    envelope["data"] = {
        "table": payload.get("table", table),
        "start": payload.get("start", offset),
        "provider_next_offset": payload.get("offset"),
        "returned": len(records),
        "records": records,
        "provider_next": payload.get("next"),
    }
    envelope["source"]["attribution"] = "The L-functions and Modular Forms Database (LMFDB)"
    envelope["source"]["transformation"] = "Selected fields and bounded results by Reasoning"
    return envelope


def register_lmfdb_tools(registry: ToolRegistry, gateway: ScientificGateway) -> None:
    @registry.tool(
        name="lmfdb_elliptic_curves_q",
        description=(
            "Search the LMFDB's elliptic curves over Q by exact label, conductor, "
            "rank, analytic rank, or torsion order. Returns bounded records with "
            "source provenance; database values still require mathematical interpretation."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "label": {"type": "string", "minLength": 1, "maxLength": 120},
                "conductor": {"type": "integer", "minimum": 1},
                "rank": {"type": "integer", "minimum": 0},
                "analytic_rank": {"type": "integer", "minimum": 0},
                "torsion": {"type": "integer", "minimum": 1},
                "offset": {"type": "integer", "minimum": 0, "maximum": 9900},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
        risk="external",
        capabilities=("science:query",),
    )
    def lmfdb_elliptic_curves_q(
        label: str | None = None,
        conductor: int | None = None,
        rank: int | None = None,
        analytic_rank: int | None = None,
        torsion: int | None = None,
        offset: int = 0,
        max_results: int = 10,
    ) -> dict[str, Any]:
        return _query(
            gateway,
            table="ec_curvedata",
            fields=_ELLIPTIC_FIELDS,
            filters={
                "lmfdb_label": _string(label),
                "conductor": _integer(conductor),
                "rank": _integer(rank),
                "analytic_rank": _integer(analytic_rank),
                "torsion": _integer(torsion),
            },
            offset=offset,
            max_results=max_results,
            object_url=_elliptic_url,
        )

    @registry.tool(
        name="lmfdb_number_fields",
        description=(
            "Search the LMFDB's number fields by exact label, degree, complex places, "
            "absolute discriminant, class number, or transitive-group label. Returns "
            "bounded cited records and retains flags such as used_grh."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "label": {"type": "string", "minLength": 1, "maxLength": 120},
                "degree": {"type": "integer", "minimum": 1},
                "complex_places": {"type": "integer", "minimum": 0},
                "discriminant_abs": {"type": "integer", "minimum": 1},
                "class_number": {"type": "integer", "minimum": 1},
                "galois_label": {"type": "string", "minLength": 1, "maxLength": 120},
                "offset": {"type": "integer", "minimum": 0, "maximum": 9900},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
        risk="external",
        capabilities=("science:query",),
    )
    def lmfdb_number_fields(
        label: str | None = None,
        degree: int | None = None,
        complex_places: int | None = None,
        discriminant_abs: int | None = None,
        class_number: int | None = None,
        galois_label: str | None = None,
        offset: int = 0,
        max_results: int = 10,
    ) -> dict[str, Any]:
        return _query(
            gateway,
            table="nf_fields",
            fields=_NUMBER_FIELD_FIELDS,
            filters={
                "label": _string(label),
                "degree": _integer(degree),
                "r2": _integer(complex_places),
                "disc_abs": _integer(discriminant_abs),
                "class_number": _integer(class_number),
                "galois_label": _string(galois_label),
            },
            offset=offset,
            max_results=max_results,
            object_url=lambda label: f"https://www.lmfdb.org/NumberField/{label}",
        )
