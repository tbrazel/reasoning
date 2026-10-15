"""Human-authenticated project work contracts and progressively loaded tasks."""

import json
from typing import Annotated, Literal

from fastapi import Depends, Query

from .store import _row
from .work_contracts import KnowledgeRelation, TaskCreate, WorkCreate, WorkRevision
from .work_prompts import catalog
from .work_store import WorkStore


def install_routes(app, store, authenticated):
    from .work_overview import install_routes as overview_routes

    overview_routes(app, store, authenticated)
    work = WorkStore(store)
    app.state.work = work
    User = Annotated[str, Depends(authenticated)]
    PageSize = Annotated[int, Query(ge=1, le=100)]
    Revision = Annotated[int | None, Query(ge=1)]
    prefix = "/api/organizations/{organization_id}/projects/{project_id}/work"

    @app.get("/api/organizations/{organization_id}/work-prompts")
    def prompts(organization_id: str, user: User):
        with store.transaction() as db:
            store._require(db, user, organization_id)
        return catalog()

    @app.get(prefix + "/{program_id}/tasks/{task_id}/prompt")
    def prompt(organization_id: str, project_id: str, program_id: str, task_id: str, user: User):
        with store.transaction() as db:
            work._program(db, user, organization_id, project_id, program_id)
            row = _row(
                db.execute(
                    "SELECT body_json,sha256 FROM work_task_prompts WHERE organization_id=? AND program_id=? AND task_id=?",
                    (organization_id, program_id, task_id),
                ).fetchone()
            )
            return {"prompt": json.loads(row["body_json"]), "sha256": row["sha256"]}

    @app.post(prefix, status_code=201)
    def create(organization_id: str, project_id: str, body: WorkCreate, user: User):
        return work.create(user, organization_id, project_id, body)

    @app.get(prefix)
    def listing(organization_id: str, project_id: str, user: User, after: str = "", limit: PageSize = 50):
        return work.list(user, organization_id, project_id, after=after, limit=limit)

    @app.get(prefix + "/{program_id}")
    def read(organization_id: str, project_id: str, program_id: str, user: User, revision: Revision = None):
        return work.read(user, organization_id, project_id, program_id, revision)

    @app.patch(prefix + "/{program_id}")
    def revise(organization_id: str, project_id: str, program_id: str, body: WorkRevision, user: User):
        return work.revise(user, organization_id, project_id, program_id, body)

    @app.post(prefix + "/{program_id}/tasks", status_code=201)
    def create_task(organization_id: str, project_id: str, program_id: str, body: TaskCreate, user: User):
        return work.task_create(user, organization_id, project_id, program_id, body)

    @app.get(prefix + "/{program_id}/tasks")
    def tasks(
        organization_id: str,
        project_id: str,
        program_id: str,
        user: User,
        after: str = "",
        limit: PageSize = 50,
        status: Literal["pending", "ready", "running", "review", "complete", "blocked", "cancelled", "outcome_unknown"] | None = None,
    ):
        return work.tasks(user, organization_id, project_id, program_id, after=after, limit=limit, status=status)

    @app.get(prefix + "/{program_id}/tasks/{task_id}")
    def task(organization_id: str, project_id: str, program_id: str, task_id: str, user: User):
        return work.task_read(user, organization_id, project_id, program_id, task_id)

    @app.post(prefix + "/{program_id}/relations", status_code=201)
    def relate(organization_id: str, project_id: str, program_id: str, body: KnowledgeRelation, user: User):
        return work.relate(user, organization_id, project_id, program_id, body)

    @app.get(prefix + "/{program_id}/relations")
    def relations(
        organization_id: str,
        project_id: str,
        program_id: str,
        user: User,
        kind: Literal[
            "work_spec", "work_task", "work_result", "knowledge", "research_artifact", "source_snapshot", "compute_result", "mathematical"
        ],
        identity: str,
        revision: str,
        direction: Literal["in", "out"] = "out",
        after: str = "",
        limit: PageSize = 50,
    ):
        return work.relations(
            user,
            organization_id,
            project_id,
            program_id,
            kind=kind,
            identity=identity,
            revision=revision,
            direction=direction,
            after=after,
            limit=limit,
        )
