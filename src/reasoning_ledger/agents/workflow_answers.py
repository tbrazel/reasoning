"""Private answer keys and deterministic typed workflow scoring."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..canonical import canonical_json

ANSWER_KINDS = ("choice", "integer", "rational", "number")
_INTEGER = re.compile(r"^[+-]?\d+$")
_PRIVATE_ANSWER_FIELDS = {"answer_key", "correct_answer", "expected", "gold_answer", "reference_answer"}


class ValidationError(ValueError):
    """An internal validation definition or report is invalid."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _safe_identifier(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or not value[0].isalnum()
        or any(not (character.isalnum() or character in "-_") for character in value)
    ):
        raise ValidationError(f"{label} must use only letters, numbers, '-' and '_'")
    return value


def _id_for_name(name: str, fallback: str) -> str:
    base = "".join(character if character.isalnum() else "-" for character in name.lower())
    return "-".join(filter(None, base.split("-"))) or fallback


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def _atomic_json(path: Path, value: Any, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700 if private else 0o755)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    if private:
        temporary.chmod(0o600)
    os.replace(temporary, path)
    if private:
        path.chmod(0o600)


def _dataset_ref(value: Any, label: str = "dataset_ref") -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be an object")
    return {
        "dataset_id": _safe_identifier(value.get("dataset_id"), f"{label} dataset_id"),
        "revision_id": _safe_identifier(value.get("revision_id"), f"{label} revision_id"),
    }


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValidationError(f"{label} must be a finite decimal")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValidationError(f"{label} must be a finite decimal") from error
    if not result.is_finite():
        raise ValidationError(f"{label} must be a finite decimal")
    return result


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _normalize_target(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be an object")
    kind = value.get("kind")
    if kind not in ANSWER_KINDS:
        raise ValidationError(f"{label} kind must be one of: {', '.join(ANSWER_KINDS)}")
    if kind == "choice":
        choices = value.get("choices")
        answer = value.get("value")
        if (
            not isinstance(choices, list)
            or len(choices) < 2
            or any(not isinstance(item, str) or not item for item in choices)
            or len(set(choices)) != len(choices)
        ):
            raise ValidationError(f"{label} choices must contain at least two unique non-empty strings")
        if not isinstance(answer, str) or answer not in choices:
            raise ValidationError(f"{label} value must be one of its choices")
        return {"kind": kind, "value": answer, "choices": list(choices)}
    if kind == "integer":
        answer = value.get("value")
        text = str(answer) if not isinstance(answer, bool) else ""
        if not _INTEGER.fullmatch(text):
            raise ValidationError(f"{label} value must be an integer")
        return {"kind": kind, "value": str(int(text))}
    if kind == "rational":
        answer = value.get("value")
        if isinstance(answer, bool) or not isinstance(answer, (str, int)):
            raise ValidationError(f"{label} value must be an integer or rational string")
        try:
            fraction = Fraction(answer)
        except (ValueError, ZeroDivisionError) as error:
            raise ValidationError(f"{label} value must be an integer or rational string") from error
        return {"kind": kind, "value": f"{fraction.numerator}/{fraction.denominator}"}
    answer = _decimal(value.get("value"), f"{label} value")
    absolute = _decimal(value.get("absolute_tolerance", "0"), f"{label} absolute_tolerance")
    relative = _decimal(value.get("relative_tolerance", "0"), f"{label} relative_tolerance")
    if absolute < 0 or relative < 0:
        raise ValidationError(f"{label} tolerances must be non-negative")
    unit = value.get("unit")
    if unit is not None and (not isinstance(unit, str) or not unit.strip()):
        raise ValidationError(f"{label} unit must be a non-empty string")
    return {
        "kind": kind,
        "value": _decimal_text(answer),
        "absolute_tolerance": _decimal_text(absolute),
        "relative_tolerance": _decimal_text(relative),
        **({"unit": unit.strip()} if isinstance(unit, str) else {}),
    }


def validate_public_validation_dataset(dataset: dict[str, Any]) -> None:
    """Reject answer-bearing fields anywhere in an internally graded dataset."""

    def inspect(value: Any, location: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).casefold() in _PRIVATE_ANSWER_FIELDS:
                    raise ValidationError(f"typed validation dataset exposes reserved answer field {key!r} at {location}")
                inspect(item, f"{location}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                inspect(item, f"{location}[{index}]")

    inspect(dataset.get("cases") or [], "cases")


def validate_answer_key(
    answer_key: Any,
    *,
    dataset_resolver: Callable[[str, str | None], dict[str, Any]],
) -> dict[str, Any]:
    """Validate a private key against one exact public dataset revision."""
    if not isinstance(answer_key, dict):
        raise ValidationError("answer key must be an object")
    name = answer_key.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValidationError("answer key needs a name")
    reference = _dataset_ref(answer_key.get("dataset_ref"))
    try:
        dataset = dataset_resolver(reference["dataset_id"], reference["revision_id"])
    except KeyError as error:
        raise ValidationError(str(error)) from error
    cases = dataset.get("cases") or []
    validate_public_validation_dataset(dataset)
    raw_targets = answer_key.get("targets")
    if not isinstance(raw_targets, list):
        raise ValidationError("answer key targets must be a list")
    targets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_targets, start=1):
        if not isinstance(raw, dict):
            raise ValidationError(f"answer key target {index} must be an object")
        case_id = _safe_identifier(raw.get("case_id"), f"answer key target {index} case_id")
        if case_id in seen:
            raise ValidationError(f"duplicate answer key case id: {case_id}")
        seen.add(case_id)
        targets.append({"case_id": case_id, "answer": _normalize_target(raw.get("answer"), f"target {case_id} answer")})
    case_ids = {str(case.get("case_id")) for case in cases}
    if seen != case_ids:
        missing = sorted(case_ids - seen)
        extra = sorted(seen - case_ids)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unknown " + ", ".join(extra))
        raise ValidationError("answer key cases must exactly match the dataset: " + "; ".join(details))
    return {
        "kind": "typed_answer_key_v1",
        "schema_version": 1,
        "name": name.strip(),
        "description": str(answer_key.get("description") or ""),
        "dataset_ref": reference,
        "targets": targets,
    }


class AnswerKeyStore:
    """Private content-addressed answer keys; public methods redact targets."""

    def __init__(self, root: Path | str, dataset_resolver: Callable[[str, str | None], dict[str, Any]]):
        self.root = Path(root)
        _private_directory(self.root)
        self.dataset_resolver = dataset_resolver

    def create(
        self,
        name: str,
        *,
        dataset_ref: dict[str, Any],
        targets: list[dict[str, Any]],
        description: str = "",
    ) -> dict[str, Any]:
        base = _id_for_name(name, "answer-key")
        answer_key_id = base
        counter = 2
        while (self.root / answer_key_id).exists():
            answer_key_id = f"{base}-{counter}"
            counter += 1
        return self.save(
            {
                "answer_key_id": answer_key_id,
                "name": name,
                "description": description,
                "dataset_ref": dataset_ref,
                "targets": targets,
            }
        )

    def save(self, answer_key: dict[str, Any]) -> dict[str, Any]:
        normalized = validate_answer_key(answer_key, dataset_resolver=self.dataset_resolver)
        answer_key_id = _safe_identifier(answer_key.get("answer_key_id"), "answer_key_id")
        directory = self.root / answer_key_id
        _private_directory(directory)
        head_path = directory / "head.json"
        parent_revision_id = None
        created_at = _now()
        if head_path.is_file():
            head = json.loads(head_path.read_text(encoding="utf-8"))
            parent_revision_id = head["revision_id"]
            created_at = head["created_at"]
        content = {"answer_key_id": answer_key_id, **normalized}
        identity = {"parent_revision_id": parent_revision_id, **content}
        revision_id = "akrev_" + hashlib.sha256(canonical_json(identity).encode()).hexdigest()[:24]
        revision = {**content, "revision_id": revision_id, "parent_revision_id": parent_revision_id, "created_at": _now()}
        revision_path = directory / "revisions" / f"{revision_id}.json"
        if not revision_path.exists():
            _atomic_json(revision_path, revision, private=True)
        head = {
            "answer_key_id": answer_key_id,
            "revision_id": revision_id,
            "name": normalized["name"],
            "description": normalized["description"],
            "dataset_ref": normalized["dataset_ref"],
            "target_count": len(normalized["targets"]),
            "created_at": created_at,
            "updated_at": _now(),
        }
        _atomic_json(head_path, head, private=True)
        return _copy(head)

    def get_private(self, answer_key_id: str, revision_id: str | None = None) -> dict[str, Any]:
        answer_key_id = _safe_identifier(answer_key_id, "answer_key_id")
        directory = self.root / answer_key_id
        head_path = directory / "head.json"
        if not head_path.is_file():
            raise KeyError(f"unknown answer key: {answer_key_id}")
        if revision_id is None:
            revision_id = json.loads(head_path.read_text(encoding="utf-8"))["revision_id"]
        revision_id = _safe_identifier(revision_id, "answer key revision_id")
        path = directory / "revisions" / f"{revision_id}.json"
        if not path.is_file():
            raise KeyError(f"unknown answer key revision: {answer_key_id}/{revision_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def describe(self, answer_key_id: str, revision_id: str | None = None) -> dict[str, Any]:
        private = self.get_private(answer_key_id, revision_id)
        return {
            key: _copy(private[key])
            for key in (
                "answer_key_id",
                "revision_id",
                "parent_revision_id",
                "name",
                "description",
                "dataset_ref",
                "created_at",
            )
        } | {"target_count": len(private["targets"])}

    def list(self) -> list[dict[str, Any]]:
        heads = []
        for path in self.root.glob("*/head.json"):
            try:
                heads.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(heads, key=lambda item: (str(item.get("updated_at") or ""), str(item.get("answer_key_id"))), reverse=True)


def _parse_payload(output: dict[str, Any]) -> dict[str, Any] | None:
    structured = output.get("structured_output")
    if isinstance(structured, dict):
        return structured
    try:
        value = json.loads(str(output.get("text") or ""))
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _actual_verdict(target: dict[str, Any], actual: Any, unit: Any) -> str:
    kind = target["kind"]
    if kind == "choice":
        if not isinstance(actual, str) or actual not in target["choices"]:
            return "malformed"
        return "correct" if actual == target["value"] else "incorrect"
    if kind == "integer":
        if isinstance(actual, bool) or not isinstance(actual, (str, int)) or not _INTEGER.fullmatch(str(actual)):
            return "malformed"
        return "correct" if int(actual) == int(target["value"]) else "incorrect"
    if kind == "rational":
        if isinstance(actual, bool) or not isinstance(actual, (str, int)):
            return "malformed"
        try:
            matches = Fraction(actual) == Fraction(target["value"])
        except (ValueError, ZeroDivisionError):
            return "malformed"
        return "correct" if matches else "incorrect"
    if target.get("unit") is not None:
        if not isinstance(unit, str) or not unit:
            return "malformed"
        if unit != target["unit"]:
            return "incorrect"
    try:
        received = _decimal(actual, "numeric answer")
    except ValidationError:
        return "malformed"
    expected = Decimal(target["value"])
    tolerance = max(Decimal(target["absolute_tolerance"]), Decimal(target["relative_tolerance"]) * abs(expected))
    return "correct" if abs(received - expected) <= tolerance else "incorrect"


def _metric_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = ("correct", "incorrect", "abstained", "malformed", "missing", "failed")
    counts = {status: sum(row["status"] == status for row in rows) for status in statuses}
    total = len(rows)
    valid = counts["correct"] + counts["incorrect"]
    calibration = [row for row in rows if row["status"] in {"correct", "incorrect"} and row.get("confidence") is not None]
    brier = None
    if calibration:
        brier = sum((float(row["confidence"]) - float(row["status"] == "correct")) ** 2 for row in calibration) / len(calibration)
    return {
        "case_outputs": total,
        "accuracy": round(counts["correct"] / total, 6) if total else None,
        "coverage": round(valid / total, 6) if total else None,
        "selective_accuracy": round(counts["correct"] / valid, 6) if valid else None,
        "brier_score": round(brier, 6) if brier is not None else None,
        "calibration_outputs": len(calibration),
        "counts": counts,
    }


def score_typed_answers(
    *,
    dataset: dict[str, Any],
    variants: list[dict[str, Any]],
    node_id: str,
    outputs: list[dict[str, Any]],
    answer_key: dict[str, Any],
) -> dict[str, Any]:
    """Score one explicit terminal node without returning expected values."""
    targets = {item["case_id"]: item["answer"] for item in answer_key["targets"]}
    dataset_case_ids = [str(case["case_id"]) for case in dataset.get("cases") or []]
    if set(targets) != set(dataset_case_ids):
        raise ValidationError("answer key cases no longer match the resolved dataset")
    indexed: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for output in outputs:
        if str(output.get("node_id")) != node_id:
            continue
        key = (str(output.get("case_id")), str(output.get("variant_id") or "default"))
        indexed.setdefault(key, []).append(output)
    rows: list[dict[str, Any]] = []
    for case_id in dataset_case_ids:
        target = targets[case_id]
        for variant in variants:
            variant_id = str(variant["variant_id"])
            matches = indexed.get((case_id, variant_id), [])
            status = "missing"
            confidence = None
            if len(matches) > 1:
                status = "malformed"
            elif matches:
                output = matches[0]
                if output.get("status") != "completed":
                    status = "failed"
                else:
                    payload = _parse_payload(output)
                    if payload is not None:
                        raw_confidence = payload.get("confidence")
                        valid_confidence = (
                            isinstance(raw_confidence, (int, float))
                            and not isinstance(raw_confidence, bool)
                            and math.isfinite(float(raw_confidence))
                            and 0 <= float(raw_confidence) <= 1
                        )
                        if valid_confidence:
                            confidence = float(raw_confidence)
                        well_formed = payload.get("answer_type") == target["kind"] and (raw_confidence is None or confidence is not None)
                        if well_formed and payload.get("abstain") is True:
                            status = "abstained"
                        elif well_formed and (payload.get("abstain") is None or payload.get("abstain") is False) and "answer" in payload:
                            status = _actual_verdict(target, payload["answer"], payload.get("unit"))
                        else:
                            status = "malformed"
                    else:
                        status = "malformed"
            rows.append(
                {
                    "case_id": case_id,
                    "variant_id": variant_id,
                    "node_id": node_id,
                    "status": status,
                    **({"confidence": confidence} if confidence is not None else {}),
                }
            )
    by_variant = {
        str(variant["variant_id"]): _metric_summary([row for row in rows if row["variant_id"] == variant["variant_id"]])
        for variant in variants
    }
    return {"kind": "typed_answer_v1", **_metric_summary(rows), "by_variant": by_variant, "results": rows}
