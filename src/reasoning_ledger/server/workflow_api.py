"""Human-only workflow definition authoring and immutable version inspection."""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query
from pydantic import Field

from .request_input import RequestInput
from .workflow_store import WorkflowStore


class DefinitionInput(RequestInput):
    client_id: Annotated[str, Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
    definition: dict[str, Any]


class RevisionInput(DefinitionInput):
    expected_version: Annotated[int, Field(ge=1, le=128, strict=True)]


def install_routes(app: FastAPI, state: WorkflowStore, authenticated: Callable):
    User = Annotated[str, Depends(authenticated)]

    @app.post("/api/organizations/{organization_id}/topics/{topic_id}/workflows", status_code=201)
    def create(organization_id: str, topic_id: str, body: DefinitionInput, user: User):
        return state.create(user, organization_id, topic_id, body.client_id, body.definition)

    @app.get("/api/organizations/{organization_id}/topics/{topic_id}/workflows")
    def workflows(
        organization_id: str,
        topic_id: str,
        user: User,
        after: Annotated[str, Query(max_length=64)] = "",
        limit: Annotated[int, Query(ge=1, le=50)] = 50,
    ):
        return state.list(user, organization_id, topic_id, after, limit)

    @app.get("/api/organizations/{organization_id}/workflows/{workflow_id}")
    def current(organization_id: str, workflow_id: str, user: User):
        return state.get(user, organization_id, workflow_id)

    @app.post("/api/organizations/{organization_id}/workflows/{workflow_id}/versions", status_code=201)
    def revise(organization_id: str, workflow_id: str, body: RevisionInput, user: User):
        return state.revise(user, organization_id, workflow_id, body.client_id, body.expected_version, body.definition)

    @app.get("/api/organizations/{organization_id}/workflows/{workflow_id}/versions")
    def versions(
        organization_id: str,
        workflow_id: str,
        user: User,
        before: Annotated[int, Query(ge=1, le=129)] = 129,
        limit: Annotated[int, Query(ge=1, le=50)] = 50,
    ):
        return state.versions(user, organization_id, workflow_id, before, limit)

    @app.get("/api/organizations/{organization_id}/workflows/{workflow_id}/versions/{version}")
    def version(organization_id: str, workflow_id: str, version: Annotated[int, Field(ge=1, le=128)], user: User):
        return state.get(user, organization_id, workflow_id, version)
