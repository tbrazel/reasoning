"""Deterministic controller inputs and deliberately typed review evidence."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .request_input import RequestInput
from .work_contracts import Identity, Revision, Text, VersionReference


class ControllerStart(RequestInput):
    client_id: Identity
    expected_revision: Revision
    target_task: Identity | None = None


class CriterionAssessment(RequestInput):
    criterion_id: Identity
    satisfied: Annotated[bool, Field(strict=True)]
    reason: Text


class ReviewVerdict(RequestInput):
    decision: Literal["approve", "revise", "blocked"]
    summary: Text
    assessments: list[CriterionAssessment] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent_approval(self):
        if len({item.criterion_id for item in self.assessments}) != len(self.assessments):
            raise ValueError("review criteria must be unique")
        if self.decision == "approve" and not all(item.satisfied for item in self.assessments):
            raise ValueError("approval cannot contain an unsatisfied criterion")
        return self


class HumanCheck(RequestInput):
    client_id: Identity
    expected_version: Revision
    subject: VersionReference
    criterion_id: Identity
    check: Literal["human_review", "source_check"]
    passed: Annotated[bool, Field(strict=True)]
    explanation: Text
    evidence: list[VersionReference] = Field(min_length=1)


class ControllerResume(RequestInput):
    expected_version: Revision
    reason: Text
