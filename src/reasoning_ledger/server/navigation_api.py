"""Authorized hierarchy navigation and confirmed spending summaries."""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, FastAPI
from pydantic import Field

from .request_input import RequestInput
from .store import Store
from .usage_store import UsageStore


class Rename(RequestInput):
    name: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]


def install_routes(app: FastAPI, store: Store, authenticated: Callable):
    User = Annotated[str, Depends(authenticated)]

    @app.patch("/api/organizations/{organization_id}/topics/{topic_id}")
    def rename(organization_id: str, topic_id: str, body: Rename, user: User):
        return store.rename_entity(user, organization_id, "topics", topic_id, body.name)

    @app.get("/api/organizations/{organization_id}/navigation-usage")
    def usage(organization_id: str, user: User):
        return UsageStore(store).navigation_totals(user, organization_id)

    @app.get("/api/organizations/{organization_id}/retained-work")
    def retained_work(organization_id: str, user: User):
        """Billing/cleanup evidence survives deletion; no transcript or program is returned."""
        with store.transaction() as db:
            store._require(db, user, organization_id, admin=True)
            inference = [dict(row) for row in db.execute(
                "SELECT r.id,r.model,r.status,r.accounting_state,r.reserved_microusd,r.provider_generation_id "
                "FROM llm_runs r JOIN llm_requests q ON q.id=r.request_id JOIN topics t ON t.id=q.topic_id "
                "WHERE r.organization_id=? AND t.deleted_at IS NOT NULL AND r.accounting_state IN ('uncertain','reserved') "
                "ORDER BY q.created_at DESC,r.id LIMIT 101", (organization_id,))]
            executions = {}
            for kind, table in (("compute", "compute_jobs"), ("bots", "bot_runs")):
                executions[kind] = [dict(row) for row in db.execute(
                    f"SELECT r.id,r.status FROM {table} r JOIN topics t ON t.id=r.topic_id "
                    "WHERE r.organization_id=? AND t.deleted_at IS NOT NULL AND r.status IN ('running','outcome_unknown') "
                    "ORDER BY r.created_at DESC,r.id LIMIT 101", (organization_id,))]
            return {"inference": inference[:100], **{key: rows[:100] for key, rows in executions.items()},
                    "truncated": len(inference) > 100 or any(len(rows) > 100 for rows in executions.values())}
