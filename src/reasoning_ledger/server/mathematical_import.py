"""Explicit, lossless import of a complete scoped mathematical record snapshot."""

import hashlib
import json
import re
from pathlib import Path

from ..canonical import canonical_json
from ..derive import integrity_issues
from ..organization_ledger import import_provenance
from ..records import validate_record
from ..store import LedgerStoreError
from .knowledge_store import KnowledgeStore
from .mathematical_store import MathematicalStore
from .store import StoreError

RECORD_PATH = re.compile(
    r"(?:claims/clm_[a-f0-9]{24}/(?:head\.json|revisions/clmrev_[a-f0-9]{24}\.json|"
    r"proofs/prf_[a-f0-9]{24}/(?:head\.json|revisions/prfrev_[a-f0-9]{24}\.json)|"
    r"verifications/ver_[a-f0-9]{24}\.json)|"
    r"references/ref_[a-f0-9]{24}/(?:head\.json|revisions/refrev_[a-f0-9]{24}\.json))\Z"
)


def import_records(store, user, org, project, records, client_id):
    if not records or len(records) > 10000:
        raise StoreError(400, "import a complete snapshot of 1 to 10000 records")
    identity = hashlib.sha256(canonical_json({"project": project, "records": records}).encode()).hexdigest()
    with store.transaction(write=True) as db:
        store._project_access(db, user, org, project, write=True)
        replay = db.execute(
            "SELECT * FROM mathematical_requests WHERE organization_id=? AND actor_id=? AND client_id=?", (org, user, client_id)
        ).fetchone()
        if replay:
            if replay["arguments_json"] != identity:
                raise StoreError(409, "import identity was reused with a different snapshot")
            return json.loads(replay["result_json"])
        ledger = MathematicalStore(store, db, user, org, project=project)
        ledger.importing = True
        inserted = 0
        try:
            if any(
                not isinstance(item, dict) or not isinstance(item.get("path"), str) or not RECORD_PATH.fullmatch(item["path"])
                for item in records
            ):
                raise ValueError("invalid mathematical record path")
            priority = {
                "claim-head": 0,
                "reference-head": 0,
                "proof-head": 1,
                "claim-revision": 2,
                "reference-revision": 2,
                "proof-revision": 3,
                "verification": 4,
            }
            for item in sorted(records, key=lambda item: (priority[ledger._type(Path(item["path"]))], item["path"])):
                path, record = item["path"], item["record"]
                if not RECORD_PATH.fullmatch(path):
                    raise ValueError("invalid mathematical record path")
                kind = ledger._type(Path(path))
                validate_record(record, kind)
                if kind.endswith("-revision") and Path(path).stem != record["revision_id"]:
                    raise ValueError("revision identity differs from its path")
                if kind == "verification" and (
                    Path(path).stem != record["verification_id"] or path.split("/")[1] != record["subject"]["claim_id"]
                ):
                    raise ValueError("verification identity differs from its path")
                if (
                    record.get("claim_id")
                    and "/" + record["claim_id"] + "/" not in "/" + path
                    or record.get("proof_id")
                    and "/" + record["proof_id"] + "/" not in "/" + path
                    or record.get("reference_id")
                    and "/" + record["reference_id"] + "/" not in "/" + path
                ):
                    raise ValueError("record identity differs from its path")
                old = db.execute("SELECT * FROM mathematical_records WHERE organization_id=? AND path=?", (org, path)).fetchone()
                if old:
                    if not ledger._visible(old):
                        raise ValueError("record unavailable")
                    # Never roll a live head back to an imported historical head.
                    if old["body_json"] != canonical_json(record) and not kind.endswith("-head"):
                        raise ValueError("immutable record differs from canonical history")
                    continue
                if ledger._write_record(ledger.root / path, record):
                    inserted += 1
            issues = integrity_issues(ledger.load())
            if issues:
                raise ValueError("incomplete mathematical snapshot: " + str(issues[:3]))
        except (ValueError, KeyError, TypeError, LedgerStoreError) as exc:
            raise StoreError(409, str(exc)[:1000]) from exc
        result = {"imported_records": inserted, "retained_history": True, "positive_reviews_require_new_attestation": True}
        db.execute("INSERT INTO mathematical_requests VALUES(?,?,?,?,?)", (org, user, client_id, identity, canonical_json(result)))
        store._audit(db, org, user, "ledger.import", project)
        return result


def import_archives(store, user, org, project, client_id, source_label=None):
    records = []
    cursor = None
    while True:
        page = KnowledgeStore(store).list(user, org, limit=500, before=cursor)
        for entry in page:
            if entry["project_id"] != project and not (source_label and entry["scope_kind"] == "organization"):
                continue
            archive = import_provenance(entry)
            if archive and (source_label is None or archive.get("source_label") == source_label):
                try:
                    record = archive["record"]
                    if hashlib.sha256(canonical_json(record).encode()).hexdigest() != archive["record_sha256"]:
                        raise ValueError("archived mathematical record checksum differs")
                    records.append({"path": archive["source_path"], "record": record})
                except (KeyError, TypeError, ValueError) as exc:
                    raise StoreError(409, "archived mathematical record is malformed or its checksum differs") from exc
        if len(page) < 500:
            break
        cursor = str(page[-1]["revised_at"]) + ":" + page[-1]["id"]
    return import_records(store, user, org, project, records, client_id)
