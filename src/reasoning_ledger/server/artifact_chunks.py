"""Bounded exact artifact slices with tenant-bound immutable chunk hashes."""

import hashlib

from .store import StoreError, _row

CHARS = 16000
MAX_BYTES = 64 * 1024 * 1024


def put(db, org, project, text):
    body = text.encode("utf8")
    if len(body) > MAX_BYTES:
        raise StoreError(413, "artifact exceeds the 64 MiB wire allowance")
    checksum = hashlib.sha256(body).hexdigest()
    old = db.execute(
        "SELECT * FROM research_blob_objects WHERE organization_id=? AND project_id=? AND digest=?", (org, project, checksum)
    ).fetchone()
    if old:
        if old["total_characters"] != len(text) or old["size_bytes"] != len(body):
            raise StoreError(409, "artifact blob identity conflicts with retained content")
        return checksum
    inserted = db.execute("INSERT OR IGNORE INTO research_blob_objects VALUES(?,?,?,?,?)", (org, project, checksum, len(text), len(body)))
    if inserted.rowcount == 0:
        retained = db.execute(
            "SELECT total_characters,size_bytes FROM research_blob_objects WHERE organization_id=? AND project_id=? AND digest=?",
            (org, project, checksum),
        ).fetchone()
        if retained["total_characters"] != len(text) or retained["size_bytes"] != len(body):
            raise StoreError(409, "artifact blob identity conflicts with retained content")
        return checksum
    for index in range(0, len(text), CHARS):
        chunk = text[index : index + CHARS]
        db.execute(
            "INSERT INTO research_blob_chunks VALUES(?,?,?,?,?,?)",
            (org, project, checksum, index // CHARS, chunk, hashlib.sha256(chunk.encode()).hexdigest()),
        )
    return checksum


def read_blob(db, org, project, checksum, offset=0, length=12000):
    if type(offset) is not int or offset < 0 or type(length) is not int or not 1 <= length <= CHARS:
        raise StoreError(400, "artifact slice requires a nonnegative offset and at most 16000 characters")
    total = _row(
        db.execute(
            "SELECT total_characters FROM research_blob_objects WHERE organization_id=? AND project_id=? AND digest=?",
            (org, project, checksum),
        ).fetchone()
    )["total_characters"]
    end, result = min(offset + length, total), ""
    if offset < total:
        first, last = offset // CHARS, (end - 1) // CHARS
        rows = db.execute(
            "SELECT ordinal,text,sha256 FROM research_blob_chunks "
            "WHERE organization_id=? AND project_id=? AND digest=? AND ordinal>=? AND ordinal<=? ORDER BY ordinal",
            (org, project, checksum, first, last),
        ).fetchall()
        if len(rows) != last - first + 1 or any(
            row["ordinal"] != first + index or hashlib.sha256(row["text"].encode()).hexdigest() != row["sha256"]
            for index, row in enumerate(rows)
        ):
            raise StoreError(409, "artifact chunks do not match their retained identities")
        result = "".join(row["text"] for row in rows)[offset - first * CHARS : end - first * CHARS]
    return {"text": result, "offset": offset, "total_characters": total, "truncated": offset + len(result) < total}


def read(db, artifact, revision, offset=0, length=12000):
    if type(offset) is not int or offset < 0 or type(length) is not int or not 1 <= length <= CHARS:
        raise StoreError(400, "artifact slice requires a nonnegative offset and at most 16000 characters")
    meta = _row(
        db.execute(
            "SELECT a.organization_id,a.project_id,v.text,v.sha256,b.digest,o.total_characters FROM research_artifacts a "
            "JOIN research_artifact_versions v ON v.artifact_id=a.id AND v.revision=? "
            "LEFT JOIN research_artifact_blobs b ON b.artifact_id=a.id AND b.revision=v.revision "
            "LEFT JOIN research_blob_objects o ON o.organization_id=b.organization_id AND o.project_id=b.project_id AND o.digest=b.digest "
            "WHERE a.id=?",
            (revision, artifact),
        ).fetchone()
    )
    if meta["digest"] is None:
        text, total = meta["text"], len(meta["text"])
        result = text[offset : offset + length]
    else:
        if meta["digest"] != meta["sha256"] or meta["total_characters"] is None:
            raise StoreError(409, "artifact blob binding does not match its version")
        return read_blob(db, meta["organization_id"], meta["project_id"], meta["digest"], offset, length)
    return {"text": result, "offset": offset, "total_characters": total, "truncated": offset + len(result) < total}


def full(db, artifact, revision):
    meta = _row(
        db.execute(
            "SELECT a.organization_id,a.project_id,v.text,v.sha256,b.digest FROM research_artifacts a "
            "JOIN research_artifact_versions v ON v.artifact_id=a.id AND v.revision=? "
            "LEFT JOIN research_artifact_blobs b ON b.artifact_id=a.id AND b.revision=v.revision WHERE a.id=?",
            (revision, artifact),
        ).fetchone()
    )
    if meta["digest"] is None:
        text = meta["text"]
    else:
        parts = []
        for index, row in enumerate(
            db.execute(
                "SELECT ordinal,text,sha256 FROM research_blob_chunks "
                "WHERE organization_id=? AND project_id=? AND digest=? ORDER BY ordinal",
                (meta["organization_id"], meta["project_id"], meta["digest"]),
            )
        ):
            if row["ordinal"] != index or hashlib.sha256(row["text"].encode()).hexdigest() != row["sha256"]:
                raise StoreError(409, "artifact chunks do not match their retained identities")
            parts.append(row["text"])
        text = "".join(parts)
    if hashlib.sha256(text.encode()).hexdigest() != meta["sha256"] or meta["digest"] not in {None, meta["sha256"]}:
        raise StoreError(409, "artifact content does not match its retained version")
    return text
