"""Public worker/job protocol inputs, with bounded payloads rather than tool quotas."""

from typing import Annotated, Literal

from pydantic import Field, SecretStr, model_validator

from .request_input import RequestInput
from .work_contracts import Amount, Identity, Positive, Revision, Role, Text, VersionReference

Epoch = Annotated[int, Field(ge=0, strict=True)]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class WorkerEnrollment(RequestInput):
    name: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
    project_ids: Annotated[list[Identity], Field(min_length=1)]
    roles: Annotated[list[Role], Field(min_length=1)]
    max_parallel: Positive = 1
    human_slots: Epoch | None = None
    review_slots: Epoch | None = None

    @model_validator(mode="after")
    def unique_grants(self):
        if len(self.project_ids) != len(set(self.project_ids)) or len(self.roles) != len(set(self.roles)):
            raise ValueError("worker project and role grants must be unique")
        self.human_slots = (1 if self.max_parallel >= 2 else 0) if self.human_slots is None else self.human_slots
        self.review_slots = (1 if self.max_parallel >= 3 else 0) if self.review_slots is None else self.review_slots
        if self.human_slots + self.review_slots >= self.max_parallel:
            raise ValueError("reserved slots must leave at least one research slot")
        return self


class SchedulerPolicy(RequestInput):
    max_parallel: Positive = 16
    human_slots: Epoch = 1
    review_slots: Epoch = 1

    @model_validator(mode="after")
    def capacity(self):
        if self.human_slots + self.review_slots >= self.max_parallel:
            raise ValueError("reserved slots must leave at least one research slot")
        return self


class ProgramControl(RequestInput):
    expected_revision: Revision
    status: Literal["active", "paused", "cancelled"]
    reason: Text


class JobEnqueue(RequestInput):
    expected_epoch: Epoch
    lane: Literal["human", "review", "research"] | None = None


class WorkerClaim(RequestInput):
    claim_id: Identity


class JobLease(RequestInput):
    attempt_id: Identity
    fence: Positive
    lease: Annotated[SecretStr, Field(min_length=64, max_length=64)]


class JobResult(JobLease):
    outcome: Literal["result", "failed", "cancelled"]
    summary: Annotated[str, Field(min_length=1, max_length=16000, pattern=r"\S")]
    outputs: list[VersionReference] = Field(default_factory=list)


class EffectIntent(JobLease):
    client_id: Identity
    kind: Literal["inference", "compute", "local"]
    request_sha256: Digest
    held_microusd: Amount = 0
    held_cpu_seconds: Amount = 0
    model: Identity | None = None
    image_id: Identity | None = None
    tool: Identity | None = None

    @model_validator(mode="after")
    def specific_capability(self):
        required = {"inference": "model", "compute": "image_id", "local": "tool"}[self.kind]
        if getattr(self, required) is None:
            raise ValueError(f"{self.kind} effects require {required}")
        for name in ("model", "image_id", "tool"):
            if name != required and getattr(self, name) is not None:
                raise ValueError(f"{self.kind} effects cannot specify {name}")
        return self


class EffectReceipt(JobLease):
    state: Literal["settled", "rejected", "unknown"]
    actual_microusd: Amount | None = None
    actual_cpu_seconds: Amount | None = None
    receipt: Annotated[str, Field(min_length=1, max_length=16000)]

    @model_validator(mode="after")
    def exact_accounting(self):
        if self.state == "settled" and (self.actual_microusd is None or self.actual_cpu_seconds is None):
            raise ValueError("a settled effect requires both actual resource amounts")
        if self.state == "rejected" and (self.actual_microusd not in {None, 0} or self.actual_cpu_seconds not in {None, 0}):
            raise ValueError("a rejected effect cannot report a charge")
        if self.state == "unknown" and (self.actual_microusd is not None or self.actual_cpu_seconds is not None):
            raise ValueError("unknown effects retain their reservation, not invented actual amounts")
        return self


class EffectReconciliation(RequestInput):
    expected_request_sha256: Digest
    state: Literal["settled", "rejected"]
    actual_microusd: Amount
    actual_cpu_seconds: Amount
    receipt: Annotated[str, Field(min_length=1, max_length=16000)]

    @model_validator(mode="after")
    def rejected_amounts(self):
        if self.state == "rejected" and (self.actual_microusd or self.actual_cpu_seconds):
            raise ValueError("a rejected effect cannot report a charge")
        return self


class TaskReopen(RequestInput):
    expected_epoch: Epoch
    reason: Text


class InputRead(JobLease):
    reference: VersionReference
    offset: Epoch = 0
    length: Annotated[int, Field(ge=1, le=16000, strict=True)] = 12000


class CompactionNote(JobLease):
    client_id: Identity
    summary: Annotated[str, Field(min_length=1, max_length=16000, pattern=r"\S")]
    retained_inputs: list[VersionReference] = Field(default_factory=list)
    unresolved: list[Text] = Field(default_factory=list)
    next_actions: list[Text] = Field(default_factory=list)
    obstructions: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_inputs(self):
        refs = [(item.kind, item.id, item.revision) for item in self.retained_inputs]
        if len(refs) != len(set(refs)):
            raise ValueError("retained compaction inputs must be unique")
        return self
