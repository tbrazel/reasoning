"""Domain-neutral, versionable work specifications and exact evidence references."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .request_input import RequestInput
from .research_context import encoded

Identity = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:/-]+$")]
Text = Annotated[str, Field(min_length=1, max_length=8000, pattern=r"\S")]
Revision = Annotated[int, Field(ge=1, strict=True)]
Amount = Annotated[int, Field(ge=0, le=9_000_000_000_000_000, strict=True)]
Positive = Annotated[int, Field(ge=1, strict=True)]
Role = Literal["coordinator", "worker", "librarian", "adviser", "critic", "executor", "specification_reviewer"]
TaskKind = Literal["explore", "collect", "build", "review", "verify"]
Controller = Literal["explore_review_revise", "parallel_integrate", "build_check_repair"]
CheckKind = Literal["human_review", "source_check", "tests_passed", "llm_review", "proof_checked"]
Strategy = Literal["breadth", "depth", "adversarial", "retrieval_first"]


class VersionReference(RequestInput):
    """A locator alone is not evidence: both a version and digest are required."""

    kind: Literal[
        "work_spec", "work_task", "work_result", "knowledge", "research_artifact", "source_snapshot", "compute_result", "mathematical"
    ]
    id: Annotated[str, Field(min_length=1, max_length=512, pattern=r"^[A-Za-z0-9_.:/-]+$")]
    revision: Revision | Identity
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]

    @model_validator(mode="after")
    def revision_type(self):
        if self.kind == "mathematical":
            if not isinstance(self.revision, str):
                raise ValueError("mathematical inputs require an immutable revision ID")
        elif not isinstance(self.revision, int):
            raise ValueError("this input kind requires a numeric revision")
        return self


class AcceptanceCriterion(RequestInput):
    id: Identity
    description: Text
    check: CheckKind


class AuthorizedResources(RequestInput):
    """Absent numeric ceilings are unlimited; empty capability lists grant nothing.

    This is an authorization envelope, not an allocation to copy to each child.
    Admission/accounting must charge children against the shared root envelope.
    """

    budget_microusd: Amount | None = None
    cpu_seconds: Amount | None = None
    max_turns: Positive | None = None
    max_tool_calls: Positive | None = None
    deadline_at: Positive | None = None
    max_concurrency: Positive = 2
    allowed_models: list[Identity] = Field(default_factory=list)
    allowed_tools: list[Identity] = Field(default_factory=list)
    allowed_images: list[Identity] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_capabilities(self):
        for name in ("allowed_models", "allowed_tools", "allowed_images"):
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")
        return self

    def contains(self, child: AuthorizedResources) -> bool:
        for name in ("budget_microusd", "cpu_seconds", "max_turns", "max_tool_calls", "deadline_at"):
            ceiling, request = getattr(self, name), getattr(child, name)
            if ceiling is not None and (request is None or request > ceiling):
                return False
        return child.max_concurrency <= self.max_concurrency and all(
            set(getattr(child, name)) <= set(getattr(self, name)) for name in ("allowed_models", "allowed_tools", "allowed_images")
        )


class WorkSpecification(RequestInput):
    schema_version: Literal[1] = 1
    objective: Text
    kind: TaskKind
    role: Role
    controller: Controller
    strategies: list[Strategy] = Field(default_factory=list)
    acceptance: Annotated[list[AcceptanceCriterion], Field(min_length=1)]
    resources: AuthorizedResources = Field(default_factory=AuthorizedResources)
    inputs: list[VersionReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_ids(self):
        if len(set(self.strategies)) != len(self.strategies):
            raise ValueError("strategy modifiers must be unique")
        if len({item.id for item in self.acceptance}) != len(self.acceptance):
            raise ValueError("acceptance criterion IDs must be unique")
        if len({(item.kind, item.id, item.revision) for item in self.inputs}) != len(self.inputs):
            raise ValueError("input versions must be unique")
        return self

    def canonical(self) -> str:
        payload = self.model_dump()
        if not self.strategies:
            payload.pop("strategies")
        return encoded(payload)

    def digest(self) -> str:
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()


class WorkCreate(RequestInput):
    client_id: Identity
    specification: WorkSpecification


class WorkRevision(RequestInput):
    expected_revision: Revision
    reason: Text
    specification: WorkSpecification


class TaskCreate(WorkCreate):
    expected_root_revision: Revision
    parent_id: Identity | None = None
    depends_on: list[Identity] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_dependencies(self):
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("dependencies must be unique")
        return self


class KnowledgeRelation(RequestInput):
    client_id: Identity
    source: VersionReference
    target: VersionReference
    relation: Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")]
    explanation: Text
