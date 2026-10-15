"""Human project overview with incremental totals and bounded evidence pages."""

import json
from typing import Annotated

from pydantic import Field

from .request_input import RequestInput
from .store import _row
from .work_contracts import VersionReference
from .work_job_contracts import Epoch
from .work_jobs import WorkJobs
from .work_store import WorkStore


class HumanInputSlice(RequestInput):
    reference: VersionReference
    offset: Epoch = 0
    length: Annotated[int, Field(ge=1, le=16000, strict=True)] = 12000


class WorkOverview:
    def __init__(self, store):
        self.store, self.work = store, WorkStore(store)

    @staticmethod
    def _page(rows, limit):
        return {"items": [dict(row) for row in rows[:limit]], "next": rows[limit - 1]["id"] if len(rows) > limit else None}

    def read(self, user, org, project, program):
        with self.store.transaction() as db:
            root = self.work._program(db, user, org, project, program)
            view = self.work._view(db, root)
            counts = {row["status"]: row["total"] for row in db.execute(
                "SELECT status,total FROM work_task_counts WHERE program_id=? AND total>0", (program,),
            )}
            account = db.execute("SELECT tools,turns FROM work_resource_accounts WHERE scope_kind='program' AND scope_id=?",
                                 (program,)).fetchone()
            return {
                "program": view, "tasks": counts, "task_total": sum(counts.values()),
                "resources": WorkJobs._usage(db, "program", program) | {
                    "tools": account["tools"] if account else 0, "turns": account["turns"] if account else 0,
                },
                "event_cursor": db.execute("SELECT COALESCE(MAX(sequence),0) FROM work_events WHERE program_id=?",
                                           (program,)).fetchone()[0],
                "root_controller": next((dict(row) for row in db.execute(
                    "SELECT id,status,phase,version,error FROM work_controllers "
                    "WHERE program_id=? AND root_controller=1 AND status<>'cancelled'", (program,),
                )), None),
            }

    def input_read(self, user, org, project, program, task, body):
        with self.store.transaction() as db:
            root = self.work._program(db, user, org, project, program)
            assignment = _row(db.execute("SELECT * FROM work_tasks WHERE program_id=? AND id=?", (program, task)).fetchone())
            return WorkJobs(self.store)._input_read(db, {"organization_id": org}, assignment, root, body)

    def branches(self, user, org, project, program, *, after="", limit=50, query="", status=None, parent=None):
        with self.store.transaction() as db:
            self.work._program(db, user, org, project, program)
            where, args = "program_id=? AND id>?", [program, after]
            if status:
                where += " AND status=?"
                args.append(status)
            if parent is not None:
                if parent:
                    _row(db.execute("SELECT id FROM work_tasks WHERE program_id=? AND id=?", (program, parent)).fetchone())
                    where += " AND parent_id=?"
                    args.append(parent)
                else:
                    where += " AND parent_id IS NULL"
            if query:
                where += " AND json_extract(body_json,'$.objective') LIKE ? ESCAPE '\\'"
                args.append("%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
            rows = db.execute(
                "SELECT id,parent_id,role,kind,status,epoch,sha256,root_revision,updated_at,"
                "substr(json_extract(body_json,'$.objective'),1,240) AS objective "
                "FROM work_tasks WHERE " + where + " ORDER BY id LIMIT ?", (*args, limit + 1),
            ).fetchall()
            return self._page(rows, limit)

    def controllers(self, user, org, project, program, *, after="", limit=50):
        with self.store.transaction() as db:
            self.work._program(db, user, org, project, program)
            rows = db.execute(
                "SELECT id,target_task,candidate_task,controller,status,phase,version,round,current_job,error,updated_at "
                "FROM work_controllers WHERE program_id=? AND id>? ORDER BY id LIMIT ?", (program, after, limit + 1),
            ).fetchall()
            return self._page(rows, limit)

    def checks(self, user, org, project, program, controller, *, after="", limit=50, identity=None):
        with self.store.transaction() as db:
            self.work._program(db, user, org, project, program)
            _row(db.execute("SELECT id FROM work_controllers WHERE program_id=? AND id=?", (program, controller)).fetchone())
            if identity:
                row = dict(_row(db.execute("SELECT * FROM work_checks WHERE controller_id=? AND id=?",
                                           (controller, identity)).fetchone()))
                row["body"] = json.loads(row.pop("body_json"))
                row["subject"] = json.loads(row.pop("subject_json"))
                return row
            rows = db.execute(
                "SELECT id,criterion_id,check_kind,subject_json,passed,source,sha256,created_at FROM work_checks "
                "WHERE controller_id=? AND id>? ORDER BY id LIMIT ?", (controller, after, limit + 1),
            ).fetchall()
            page = self._page(rows, limit)
            for row in page["items"]:
                row["subject"] = json.loads(row.pop("subject_json"))
            return page


def install_routes(app, store, authenticated):
    from typing import Literal

    from fastapi import Depends, Query

    overview = WorkOverview(store)
    app.state.work_overview = overview
    User = Annotated[str, Depends(authenticated)]
    PageSize = Annotated[int, Query(ge=1, le=100)]
    TextQuery = Annotated[str, Query(max_length=200)]
    prefix = "/api/organizations/{organization_id}/projects/{project_id}/work/{program_id}"

    @app.get(prefix + "/overview")
    def read(organization_id: str, project_id: str, program_id: str, user: User):
        return overview.read(user, organization_id, project_id, program_id)

    @app.post(prefix + "/tasks/{task_id}/inputs/read")
    def input_read(organization_id: str, project_id: str, program_id: str, task_id: str, body: HumanInputSlice, user: User):
        return overview.input_read(user, organization_id, project_id, program_id, task_id, body)

    @app.get(prefix + "/branches")
    def branches(organization_id: str, project_id: str, program_id: str, user: User,
                 after: str = "", limit: PageSize = 50, q: TextQuery = "", parent: str | None = None,
                 status: Literal[
                     "pending", "ready", "running", "review", "complete", "blocked", "cancelled", "outcome_unknown"
                 ] | None = None):
        return overview.branches(user, organization_id, project_id, program_id,
                                 after=after, limit=limit, query=q, status=status, parent=parent)

    @app.get(prefix + "/controllers")
    def controllers(organization_id: str, project_id: str, program_id: str, user: User, after: str = "", limit: PageSize = 50):
        return overview.controllers(user, organization_id, project_id, program_id, after=after, limit=limit)

    @app.get(prefix + "/controllers/{controller_id}/checks")
    def checks(organization_id: str, project_id: str, program_id: str, controller_id: str,
               user: User, after: str = "", limit: PageSize = 50):
        return overview.checks(user, organization_id, project_id, program_id, controller_id, after=after, limit=limit)

    @app.get(prefix + "/controllers/{controller_id}/checks/{check_id}")
    def check(organization_id: str, project_id: str, program_id: str, controller_id: str, check_id: str, user: User):
        return overview.checks(user, organization_id, project_id, program_id, controller_id, identity=check_id)
