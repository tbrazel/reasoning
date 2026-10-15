"""Research ledger: claims, proofs, and derived status for working mathematics.

Its conventions are canonical JSON and content-addressed immutable records.
"""

from .canonical import canonical_json, content_id, sha256_hex
from .derive import (
    LedgerState,
    LedgerView,
    argument_state,
    artifact_checks,
    blast_radius,
    derive_state,
    integrity_issues,
)
from .records import (
    AUTHORITY_RANKS,
    LedgerRecordError,
    new_claim,
    new_proof,
    new_reference,
    new_verification,
    refine_claim,
    refine_reference,
    revise_proof,
    validate_record,
)
from .store import LedgerStore, LedgerStoreError

__all__ = [
    "AUTHORITY_RANKS",
    "LedgerRecordError",
    "LedgerState",
    "LedgerStore",
    "LedgerStoreError",
    "LedgerView",
    "argument_state",
    "artifact_checks",
    "blast_radius",
    "canonical_json",
    "content_id",
    "derive_state",
    "integrity_issues",
    "new_claim",
    "new_proof",
    "new_reference",
    "new_verification",
    "refine_claim",
    "refine_reference",
    "revise_proof",
    "sha256_hex",
    "validate_record",
]
