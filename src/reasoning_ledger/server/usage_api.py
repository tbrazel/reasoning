"""Privacy-filtered provider-call receipts, bounded export, and model shortlist settings."""

import json
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, FastAPI, Query
from fastapi.responses import Response
from pydantic import Field

from .request_input import RequestInput
from .usage_store import UsageStore


class RecommendationsInput(RequestInput):
    model_ids: Annotated[list[Annotated[str, Field(min_length=1, max_length=200)]] | None, Field(max_length=20)] = None


class VerifiedCost(RequestInput):
    actual_microusd: Annotated[int, Field(ge=0, le=10**15, strict=True)]


def install_routes(app: FastAPI, state: UsageStore, authenticated: Callable):
    User = Annotated[str, Depends(authenticated)]

    @app.get("/api/organizations/{organization_id}/inference-ledger")
    def calls(
        organization_id: str,
        user: User,
        before: Annotated[int | None, Query(ge=1)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        project_id: str | None = None,
        topic_id: str | None = None,
        model: Annotated[str | None, Query(max_length=200)] = None,
        actor: Annotated[str | None, Query(max_length=64)] = None,
        status: Annotated[str | None, Query(max_length=30)] = None,
        purpose: Annotated[str | None, Query(max_length=40)] = None,
        since: Annotated[int | None, Query(ge=0)] = None,
        until: Annotated[int | None, Query(ge=0)] = None,
    ):
        return state.calls(
            user,
            organization_id,
            before=before,
            limit=limit,
            project_id=project_id,
            topic_id=topic_id,
            model=model,
            actor=actor,
            status=status,
            purpose=purpose,
            since=since,
            until=until,
        )

    @app.get("/api/organizations/{organization_id}/inference-ledger/export")
    def export(
        organization_id: str,
        user: User,
        before: Annotated[int | None, Query(ge=1)] = None,
        project_id: str | None = None,
        topic_id: str | None = None,
        model: Annotated[str | None, Query(max_length=200)] = None,
        actor: Annotated[str | None, Query(max_length=64)] = None,
        status: Annotated[str | None, Query(max_length=30)] = None,
        purpose: Annotated[str | None, Query(max_length=40)] = None,
        since: Annotated[int | None, Query(ge=0)] = None,
        until: Annotated[int | None, Query(ge=0)] = None,
    ):
        records = state.calls(
            user,
            organization_id,
            before=before,
            limit=1000,
            project_id=project_id,
            topic_id=topic_id,
            model=model,
            actor=actor,
            status=status,
            purpose=purpose,
            since=since,
            until=until,
        )
        content = "".join(json.dumps(record, allow_nan=False) + "\n" for record in records["calls"])
        headers = {"Content-Disposition": 'attachment; filename="reasoning-inference-ledger.jsonl"'}
        if records["next_before"] is not None:
            headers["X-Next-Before"] = str(records["next_before"])
        return Response(content, media_type="application/x-ndjson", headers=headers)

    @app.get("/api/organizations/{organization_id}/inference-ledger/{call_id}")
    def call(organization_id: str, call_id: str, user: User):
        return state.call(user, organization_id, call_id)

    @app.put("/api/organizations/{organization_id}/inference-ledger/{call_id}/cost", status_code=204)
    def reconcile(organization_id: str, call_id: str, body: VerifiedCost, user: User):
        state.reconcile_call(user, organization_id, call_id, body.actual_microusd)

    @app.put("/api/organizations/{organization_id}/model-recommendations", status_code=204)
    def recommendations(organization_id: str, body: RecommendationsInput, user: User):
        state.set_recommendations(user, organization_id, body.model_ids)
