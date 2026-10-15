"""Human-only, exact-revision annotations that never promote derived status."""

from __future__ import annotations

import hashlib
import json
import time

from ..canonical import canonical_json
from .mathematical_store import MathematicalStore
from .store import StoreError, _id


class HumanChecks:
    def __init__(self, store):
        self.store = store

    def _subject(self, db, user, org, object_id, *, write=False):
        ledger = MathematicalStore(self.store, db, user, org)
        row = db.execute(
            "SELECT * FROM mathematical_records WHERE organization_id=? AND object_id=? "
            "AND record_type IN ('claim-head','proof-head','reference-head')",
            (org, object_id),
        ).fetchone()
        if row is None or not ledger._visible(row):
            raise StoreError(404, "mathematical record unavailable")
        if write:
            self.store._require(db, user, org, write=True)
            if row["topic_id"]:
                self.store._topic_access(db, user, org, row["topic_id"], write=True)
            elif row["project_id"]:
                self.store._project_access(db, user, org, row["project_id"], write=True)
        return row, json.loads(row["body_json"])["current_revision_id"]

    def list(self, user, org, object_id):
        with self.store.transaction() as db:
            _, current = self._subject(db, user, org, object_id)
            return self._list(db, org, object_id, current)

    @staticmethod
    def _list(db, org, object_id, current):
        rows = db.execute(
            "SELECT c.*,u.name AS actor_name FROM ledger_human_checks c JOIN users u ON u.id=c.actor_id "
            "WHERE c.organization_id=? AND c.object_id=? ORDER BY c.created_at,c.id",
            (org, object_id),
        )
        return [{**dict(row), "current": row["revision_id"] == current, "authority": "human-judgment"} for row in rows]

    @staticmethod
    def for_revision(db, org, object_id, revision_id):
        """Bounded exact evidence for an already-authorized mathematical read."""
        rows = db.execute(
            "SELECT c.id,c.revision_id,c.record_sha256,c.actor_id,c.created_at,c.note,u.name AS actor_name "
            "FROM ledger_human_checks c JOIN users u ON u.id=c.actor_id "
            "WHERE c.organization_id=? AND c.object_id=? AND c.revision_id=? "
            "ORDER BY c.created_at DESC,c.id LIMIT 101",
            (org, object_id, revision_id),
        ).fetchall()
        return {"checks": [{**dict(row), "authority": "human-judgment"} for row in rows[:100]], "truncated": len(rows) > 100}

    def check(self, user, org, object_id, revision_id, *, note=""):
        if not isinstance(note, str) or len(note) > 2000:
            raise StoreError(400, "human check note must be at most 2000 characters")
        with self.store.transaction(write=True) as db:
            row, current = self._subject(db, user, org, object_id, write=True)
            # An identical retry of a retained annotation is safe even if the head
            # moved; a new check must attest the revision the person just inspected.
            prior = db.execute(
                "SELECT * FROM ledger_human_checks WHERE organization_id=? AND object_id=? AND revision_id=? AND actor_id=?",
                (org, object_id, revision_id, user),
            ).fetchone()
            if prior:
                if prior["note"] != note:
                    raise StoreError(409, "this revision already has your immutable human check")
                return next(item for item in self._list(db, org, object_id, current) if item["id"] == prior["id"])
            if revision_id != current:
                raise StoreError(409, "statement changed; reopen its current revision before checking it")
            revision = db.execute(
                "SELECT * FROM mathematical_records WHERE organization_id=? AND path=? AND record_type=?",
                (
                    org,
                    row["path"].removesuffix("head.json") + "revisions/" + revision_id + ".json",
                    row["record_type"].replace("-head", "-revision"),
                ),
            ).fetchone()
            if revision is None:
                raise StoreError(409, "exact mathematical revision unavailable")
            digest = hashlib.sha256(canonical_json(json.loads(revision["body_json"])).encode()).hexdigest()
            identity = "human_" + _id()
            db.execute(
                "INSERT INTO ledger_human_checks(id,organization_id,object_id,revision_id,record_path,record_sha256,actor_id,"
                "created_at,note) VALUES(?,?,?,?,?,?,?,?,?)",
                (identity, org, object_id, revision_id, revision["path"], digest, user, int(time.time()), note),
            )
            self.store._audit(db, org, user, "ledger.human-checked", object_id)
            return next(item for item in self._list(db, org, object_id, current) if item["id"] == identity)
