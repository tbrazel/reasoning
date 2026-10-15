"""Human administrator management of outgoing bot subscriptions and delivery metadata."""

from typing import Annotated

from fastapi import Depends, FastAPI, Query, Response
from pydantic import Field

from .request_input import RequestInput


class Creation(RequestInput):
    bot_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
    url: Annotated[str, Field(min_length=1, max_length=2048)]
    prefix: Annotated[str, Field(max_length=64)] = ""
    client_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{16,128}$")]


def install_routes(app: FastAPI, state, authenticated, worker):
    User = Annotated[str, Depends(authenticated)]
    root = "/api/organizations/{organization_id}/bot-webhooks"

    @app.get(root + "/status")
    def status(organization_id: str, user: User):
        with state.store.transaction() as db:
            state.store._require(db, user, organization_id, admin=True)
        return {"worker_running": worker.running}

    @app.get(root)
    def listing(organization_id: str, user: User):
        return state.list(user, organization_id)

    @app.post(root, status_code=201)
    def create(organization_id: str, body: Creation, user: User):
        return state.create(user, organization_id, body.bot_id, body.url, body.prefix, body.client_id)

    @app.delete(root + "/{webhook_id}", status_code=204)
    def revoke(organization_id: str, webhook_id: str, user: User):
        state.revoke(user, organization_id, webhook_id)
        return Response(status_code=204)

    @app.get(root + "/{webhook_id}/deliveries")
    def deliveries(
        organization_id: str,
        webhook_id: str,
        user: User,
        before: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ):
        return state.deliveries(user, organization_id, webhook_id, before, limit)
