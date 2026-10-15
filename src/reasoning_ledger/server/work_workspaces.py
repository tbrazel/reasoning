"""Persistent branch-isolated text workspaces backed by shared artifact revisions."""

from types import SimpleNamespace

from .artifact_chunks import full, read
from .research_artifacts import ResearchArtifacts, safe_path
from .store import StoreError, _row
from .work_contracts import VersionReference
from .work_inputs import bind


class WorkWorkspaces:
    def __init__(self, tools):
        self.tools, self.work = tools, tools.work
        self.artifacts = ResearchArtifacts(tools.store)

    @staticmethod
    def scope(db, task):
        controller = db.execute(
            "SELECT c.target_task FROM work_controller_steps s JOIN work_controllers c ON c.id=s.controller_id WHERE s.task_id=?",
            (task["id"],),
        ).fetchone()
        return controller[0] if controller else task["id"]

    def _file(self, db, task, root, path):
        if not safe_path(path):
            raise StoreError(400, "workspace requires a safe relative file path")
        return _row(
            db.execute(
                "SELECT a.* FROM work_workspace_files w JOIN research_artifacts a ON a.id=w.artifact_id "
                "WHERE w.organization_id=? AND w.project_id=? AND w.program_id=? AND w.scope_task=? AND w.path=?",
                (task["organization_id"], root["project_id"], root["id"], self.scope(db, task), path),
            ).fetchone()
        )

    def _reference(self, db, task, root, row, revision, checksum, tool):
        reference = VersionReference(kind="research_artifact", id=row["id"], revision=revision, sha256=checksum)
        bind(db, self.work, task, root, reference, tool)
        return reference.model_dump()

    def page(self, db, job, task, root, body):
        rows = db.execute(
            "SELECT w.path,a.id,a.head_revision,v.sha256,COALESCE(o.total_characters,LENGTH(v.text)) AS total_characters "
            "FROM work_workspace_files w JOIN research_artifacts a ON a.id=w.artifact_id "
            "JOIN research_artifact_versions v ON v.artifact_id=a.id AND v.revision=a.head_revision "
            "LEFT JOIN research_artifact_blobs b ON b.artifact_id=a.id AND b.revision=v.revision "
            "LEFT JOIN research_blob_objects o ON o.organization_id=b.organization_id AND o.project_id=b.project_id AND o.digest=b.digest "
            "WHERE w.organization_id=? AND w.project_id=? AND w.program_id=? AND w.scope_task=? AND w.path>? ORDER BY w.path LIMIT ?",
            (task["organization_id"], root["project_id"], root["id"], self.scope(db, task), body.after, body.limit + 1),
        ).fetchall()
        return {
            "items": [
                {
                    "path": row["path"],
                    "total_characters": row["total_characters"],
                    "reference": self._reference(db, task, root, row, row["head_revision"], row["sha256"], "workspace_list"),
                }
                for row in rows[: body.limit]
            ],
            "next": rows[body.limit - 1]["path"] if len(rows) > body.limit else None,
        }

    def read(self, db, job, task, root, body):
        row = self._file(db, task, root, body.path)
        revision = body.revision or row["head_revision"]
        checksum = _row(
            db.execute("SELECT sha256 FROM research_artifact_versions WHERE artifact_id=? AND revision=?", (row["id"], revision)).fetchone()
        )["sha256"]
        return {
            "path": body.path,
            "reference": self._reference(db, task, root, row, revision, checksum, "workspace_read"),
            **read(db, row["id"], revision, body.offset, body.length),
        }

    def write(self, db, job, task, root, body):
        if not safe_path(body.path):
            raise StoreError(400, "workspace requires a safe relative file path")
        scope = self.scope(db, task)
        path = f"work/{root['id']}/{scope}/{body.path}"
        saved = self.artifacts._write(
            db, task["requested_by"], task["organization_id"], root["project_id"], path, body.text, body.expected_sha256, strict=True
        )
        db.execute(
            "INSERT OR IGNORE INTO work_workspace_files VALUES(?,?,?,?,?,?)",
            (task["organization_id"], root["project_id"], root["id"], scope, body.path, saved["id"]),
        )
        self.work._provenance(
            db, task["organization_id"], root["project_id"], root["id"], "workspace_file", saved["id"], saved["revision"], []
        )
        return {
            "path": body.path,
            "reference": self._reference(db, task, root, saved, saved["revision"], saved["sha256"], "workspace_write"),
        }

    def patch(self, db, job, task, root, body):
        row = self._file(db, task, root, body.path)
        checksum = db.execute(
            "SELECT sha256 FROM research_artifact_versions WHERE artifact_id=? AND revision=?", (row["id"], row["head_revision"])
        ).fetchone()[0]
        if checksum != body.expected_sha256:
            raise StoreError(409, "workspace changed; read the current version before patching")
        text, cursor, parts = full(db, row["id"], row["head_revision"]), 0, []
        for change in body.changes:
            if change.end > len(text):
                raise StoreError(400, "patch range exceeds the source file")
            parts.extend((text[cursor : change.start], change.text))
            cursor = change.end
        parts.append(text[cursor:])
        result = self.write(db, job, task, root, SimpleNamespace(path=body.path, text="".join(parts), expected_sha256=checksum))
        if result["reference"]["revision"] != row["head_revision"]:
            self.work._provenance(
                db,
                task["organization_id"],
                root["project_id"],
                root["id"],
                "workspace_patch",
                row["id"],
                result["reference"]["revision"],
                [VersionReference(kind="research_artifact", id=row["id"], revision=row["head_revision"], sha256=checksum)],
            )
        return result
