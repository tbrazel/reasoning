"""Immutable ledger record construction and validation.

Every constructor returns fully validated, content-addressed record dicts.
Records are pure data; nothing here touches disk.
"""

from __future__ import annotations

import json
from functools import cache
from importlib import resources

import jsonschema

from .canonical import content_id

SCHEMA_NAMES = (
    "claim-head",
    "claim-revision",
    "proof-head",
    "proof-revision",
    "verification",
    "reference-head",
    "reference-revision",
)

REFERENCE_FIELDS = (
    "author",
    "title",
    "year",
    "arxiv",
    "url",
    "doi",
    "isbn",
    "notes",
    # a cached copy of the source, as a path relative to the project's
    # sources directory (uploaded by the user or fetched by an agent)
    "file",
)

# Weakest link along an establishment chain determines the reported
# authority. Machine-checked authorities rank equal and highest; model
# review is weaker; a bare operator assertion is weakest.
AUTHORITY_RANKS = {
    "corpus": 3,
    "computation": 3,
    "lean": 3,
    "review": 2,
    "operator": 1,
}

PROOF_VERDICTS = ("verified", "flawed", "inconclusive")
CLAIM_VERDICTS = ("refuted", "established")


class LedgerRecordError(ValueError):
    pass


@cache
def _schema(name: str) -> dict:
    if name not in SCHEMA_NAMES:
        raise LedgerRecordError(f"unknown ledger schema: {name}")
    text = (
        resources.files("reasoning_ledger") / "schemas" / f"{name}.schema.json"
    ).read_text(encoding="utf-8")
    return json.loads(text)


# jsonschema.validate() builds a fresh validator per call (~4ms); a
# compiled validator is ~40x faster, and record validation runs on
# every write.
_VALIDATORS: dict = {}


def validate_record(record: dict, schema_name: str) -> None:
    validator = _VALIDATORS.get(schema_name)
    if validator is None:
        schema = _schema(schema_name)
        validator = jsonschema.validators.validator_for(schema)(schema)
        _VALIDATORS[schema_name] = validator
    try:
        validator.validate(record)
    except jsonschema.ValidationError as exc:
        raise LedgerRecordError(
            f"invalid {schema_name} record: {exc.message}"
        ) from exc


def _author(author: dict | None) -> dict:
    author = author or {}
    return {
        "operator": author.get("operator"),
        "agent": author.get("agent"),
        "session": author.get("session"),
    }


def new_claim(
    statement_text: str,
    *,
    kind: str = "claim",
    reference_id: str | None = None,
    reference_revision_id: str | None = None,
    locator: str | None = None,
    notes: str | None = None,
    author: dict | None = None,
) -> tuple[dict, dict]:
    """Create a claim or definition: returns (head, initial revision)."""
    revision = {
        "schema_version": 1,
        "claim_id": "",
        "revision_id": "",
        "parent_revision_id": None,
        "kind": kind,
        "statement_text": statement_text,
        "statement_format": "tex-prose-v1",
        "reference_id": reference_id,
        "locator": locator,
        "notes": notes,
        "author": _author(author),
    }
    if reference_revision_id is not None:
        if reference_id is None:
            raise LedgerRecordError("a source revision requires a reference")
        revision["reference_revision_id"] = reference_revision_id
    claim_id = content_id("clm", revision, blank_fields=("claim_id", "revision_id"))
    revision["claim_id"] = claim_id
    revision["revision_id"] = content_id("clmrev", revision, blank_fields=("revision_id",))
    head = {
        "schema_version": 1,
        "claim_id": claim_id,
        "current_revision_id": revision["revision_id"],
        "withdrawn": False,
    }
    validate_record(revision, "claim-revision")
    validate_record(head, "claim-head")
    return head, revision


_UNSET = object()


def refine_claim(
    parent_revision: dict,
    statement_text: str,
    *,
    kind: str | None = None,
    reference_id: str | None | object = _UNSET,
    locator: str | None | object = _UNSET,
    notes: str | None = None,
    author: dict | None = None,
) -> dict:
    """Create the next revision of a claim; the caller advances the head.

    reference_id / locator inherit from the parent when left unset; pass
    an explicit value (including None) to change them."""
    revision = {
        "schema_version": 1,
        "claim_id": parent_revision["claim_id"],
        "revision_id": "",
        "parent_revision_id": parent_revision["revision_id"],
        "kind": kind if kind is not None else parent_revision["kind"],
        "statement_text": statement_text,
        "statement_format": "tex-prose-v1",
        "reference_id": (
            parent_revision["reference_id"] if reference_id is _UNSET else reference_id
        ),
        "locator": parent_revision["locator"] if locator is _UNSET else locator,
        "notes": notes,
        "author": _author(author),
    }
    if revision["reference_id"] == parent_revision["reference_id"] and parent_revision.get("reference_revision_id"):
        revision["reference_revision_id"] = parent_revision["reference_revision_id"]
    revision["revision_id"] = content_id("clmrev", revision, blank_fields=("revision_id",))
    validate_record(revision, "claim-revision")
    return revision


def new_reference(
    *,
    author: str,
    title: str,
    year: int | None = None,
    arxiv: str | None = None,
    url: str | None = None,
    doi: str | None = None,
    isbn: str | None = None,
    notes: str | None = None,
    file: str | None = None,
) -> tuple[dict, dict]:
    """Create a bibliographic reference: returns (head, initial revision).

    No record-author: the git commit records who added it, and omitting
    it makes the content id purely bibliographic, so two agents recording
    the same source dedupe to one reference. The cached-copy `file` is
    storage metadata, not identity: it is blanked from the reference id
    for the same reason."""
    revision = {
        "schema_version": 1,
        "reference_id": "",
        "revision_id": "",
        "parent_revision_id": None,
        "author": author,
        "title": title,
        "year": year,
        "arxiv": arxiv,
        "url": url,
        "doi": doi,
        "isbn": isbn,
        "notes": notes,
        "file": file,
    }
    reference_id = content_id(
        "ref", revision, blank_fields=("reference_id", "revision_id", "file")
    )
    revision["reference_id"] = reference_id
    revision["revision_id"] = content_id("refrev", revision, blank_fields=("revision_id",))
    head = {
        "schema_version": 1,
        "reference_id": reference_id,
        "current_revision_id": revision["revision_id"],
    }
    validate_record(revision, "reference-revision")
    validate_record(head, "reference-head")
    return head, revision


def refine_reference(parent_revision: dict, changes: dict) -> dict:
    """Create the next revision of a reference from field changes; the
    caller advances the head. Unlisted fields inherit from the parent."""
    revision = {
        "schema_version": 1,
        "reference_id": parent_revision["reference_id"],
        "revision_id": "",
        "parent_revision_id": parent_revision["revision_id"],
    }
    for field in REFERENCE_FIELDS:
        # .get: revisions recorded before a field existed simply lack it
        revision[field] = (
            changes[field] if field in changes else parent_revision.get(field)
        )
    revision["revision_id"] = content_id("refrev", revision, blank_fields=("revision_id",))
    validate_record(revision, "reference-revision")
    return revision


def new_proof(
    target_revision: dict,
    body: dict,
    *,
    premises: list[dict] | None = None,
    artifacts: list[dict] | None = None,
    notes: str | None = None,
    author: dict | None = None,
) -> tuple[dict, dict]:
    """Create a proof of the given claim revision: returns (head, revision).

    Formal artifacts (lean/computation) attach to argument proofs: the
    artifact machine-checks a formal statement, while the argument text is
    responsible for explaining why that formal result yields the prose
    claim — which is what verification of the proof attests.
    """
    revision = {
        "schema_version": 1,
        "claim_id": target_revision["claim_id"],
        "proof_id": "",
        "revision_id": "",
        "parent_revision_id": None,
        "target_revision_id": target_revision["revision_id"],
        "premises": _premises(premises),
        "body": body,
        "artifacts": artifacts or [],
        "notes": notes,
        "author": _author(author),
    }
    proof_id = content_id("prf", revision, blank_fields=("proof_id", "revision_id"))
    revision["proof_id"] = proof_id
    revision["revision_id"] = content_id("prfrev", revision, blank_fields=("revision_id",))
    head = {
        "schema_version": 1,
        "claim_id": revision["claim_id"],
        "proof_id": proof_id,
        "current_revision_id": revision["revision_id"],
        "abandoned": False,
    }
    validate_record(revision, "proof-revision")
    validate_record(head, "proof-head")
    return head, revision


def revise_proof(
    parent_revision: dict,
    *,
    target_revision_id: str | None = None,
    body: dict | None = None,
    premises: list[dict] | None = None,
    artifacts: list[dict] | None = None,
    notes: str | None = None,
    author: dict | None = None,
) -> dict:
    """Create the next revision of a proof (a premise-only change is a rebase)."""
    revision = {
        "schema_version": 1,
        "claim_id": parent_revision["claim_id"],
        "proof_id": parent_revision["proof_id"],
        "revision_id": "",
        "parent_revision_id": parent_revision["revision_id"],
        "target_revision_id": (
            target_revision_id
            if target_revision_id is not None
            else parent_revision["target_revision_id"]
        ),
        "premises": (
            _premises(premises) if premises is not None else parent_revision["premises"]
        ),
        "body": body if body is not None else parent_revision["body"],
        "artifacts": (
            artifacts if artifacts is not None else parent_revision["artifacts"]
        ),
        "notes": notes,
        "author": _author(author),
    }
    revision["revision_id"] = content_id("prfrev", revision, blank_fields=("revision_id",))
    validate_record(revision, "proof-revision")
    return revision


def _premises(premises: list[dict] | None) -> list[dict]:
    normalized = []
    seen = set()
    for premise in premises or []:
        if "unresolved" in premise:
            entry = {"unresolved": premise["unresolved"], "note": premise.get("note")}
            key = ("unresolved", entry["unresolved"])
        else:
            entry = {
                "claim_id": premise["claim_id"],
                "revision_id": premise["revision_id"],
                "note": premise.get("note"),
            }
            key = (entry["claim_id"], entry["revision_id"])
        if key in seen:
            raise LedgerRecordError(f"duplicate premise: {key}")
        seen.add(key)
        normalized.append(entry)
    return normalized


def new_verification(
    subject: dict,
    *,
    verdict: str,
    authority: str,
    evidence: dict,
    author: dict | None = None,
    reviewer: dict | None = None,
) -> dict:
    """Create a verification against a claim revision or proof revision.

    `author` records who caused the verification to be recorded (same
    shape as claim/proof authorship). `reviewer` marks a verdict produced
    by the independent review harness (model, effort, review id) — the
    marker that lets positive review-authority verdicts count toward
    establishment; it is never set by the requesting agent.
    """
    is_proof = "proof_id" in subject
    allowed = PROOF_VERDICTS if is_proof else CLAIM_VERDICTS
    if verdict not in allowed:
        kind = "proof" if is_proof else "claim"
        raise LedgerRecordError(f"verdict {verdict!r} is not valid against a {kind} revision")
    record = {
        "schema_version": 1,
        "verification_id": "",
        "subject": dict(subject),
        "verdict": verdict,
        "authority": authority,
        "evidence": evidence,
        # A display-only, KaTeX-repaired copy of the evidence's prose,
        # filled in later (out of the sandbox, by the repair worker). It
        # is NOT the source of truth — derivation and model-facing reads
        # ignore it — and is excluded from the content id, so populating
        # it never changes the verification's identity and the id still
        # certifies the authoritative evidence was untouched.
        "evidence_repair": None,
    }
    if author is not None:
        record["author"] = dict(author)
    if reviewer is not None:
        record["reviewer"] = dict(reviewer)
    record["verification_id"] = content_id(
        "ver", record, blank_fields=("verification_id", "evidence_repair")
    )
    validate_record(record, "verification")
    return record
