"""Typed scoped delegation, source retrieval and approach memory operations."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .compute_api import Resources
from .request_input import RequestInput
from .work_contracts import Identity, Revision, Text, VersionReference, WorkSpecification
from .work_job_contracts import Epoch, JobLease

WorkspacePath = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-][A-Za-z0-9._/-]*$")]
Checksum = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class LocalOperation(JobLease):
    client_id: Identity


class Delegate(LocalOperation):
    expected_root_revision: Revision
    specification: WorkSpecification
    depends_on: list[Identity] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_dependencies(self):
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("child dependencies must be unique")
        return self


class ChildPage(LocalOperation):
    after: Identity | Literal[""] = ""
    limit: Annotated[int, Field(ge=1, le=100, strict=True)] = 50


class ChildRead(LocalOperation):
    child_id: Identity


class ChildCancel(ChildRead):
    reason: Text


class KnowledgeSearch(LocalOperation):
    query: Annotated[str, Field(max_length=200)] = ""
    kind: Literal["ledger", "bibliography", "journal"] | None = None
    after: Identity | Literal[""] = ""
    limit: Annotated[int, Field(ge=1, le=100, strict=True)] = 20


class ApproachWrite(LocalOperation):
    title: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
    approach: Text
    status: Literal["attempted", "obstructed", "promising", "superseded"]
    obstruction: Text | None = None
    reopening_condition: Text | None = None
    evidence: list[VersionReference] = Field(default_factory=list)
    entry_id: Identity | None = None
    expected_revision: Revision | None = None

    @model_validator(mode="after")
    def revision_and_obstruction(self):
        if (self.entry_id is None) != (self.expected_revision is None):
            raise ValueError("revising approach memory requires both entry_id and expected_revision")
        if self.status == "obstructed" and (self.obstruction is None or self.reopening_condition is None):
            raise ValueError("an obstructed approach requires its obstruction and reopening condition")
        refs = [(item.kind, item.id, item.revision) for item in self.evidence]
        if len(refs) != len(set(refs)):
            raise ValueError("approach evidence must be unique")
        return self


class BoundInputRead(LocalOperation):
    reference: VersionReference
    offset: Epoch = 0
    length: Annotated[int, Field(ge=1, le=16000, strict=True)] = 12000


class SourceSearch(LocalOperation):
    provider: Literal["crossref", "arxiv"]
    query: Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")]
    limit: Annotated[int, Field(ge=1, le=20, strict=True)] = 10


class SourceFetch(LocalOperation):
    url: Annotated[str, Field(min_length=1, max_length=4096)]
    title: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]


class SourceCheck(LocalOperation):
    subject: VersionReference
    source: VersionReference
    criterion_id: Identity
    statement: Text
    quote: Annotated[str, Field(min_length=1, max_length=4000, pattern=r"\S")]
    offset: Epoch

    @model_validator(mode="after")
    def exact_source_kind(self):
        if self.subject.kind != "work_result" or self.source.kind != "source_snapshot":
            raise ValueError("source checks require a retained candidate result and a server-captured source")
        return self


class ComputeSubmit(LocalOperation):
    image_id: Identity
    language: Literal["sage", "python", "julia", "gap", "pari", "lean"]
    program: Annotated[str, Field(min_length=1, max_length=16000)] | None = None
    source: VersionReference | None = None
    resources: Resources

    @model_validator(mode="after")
    def one_source(self):
        if (self.program is None) == (self.source is None):
            raise ValueError("compute requires exactly one inline program or assigned file version")
        if self.source is not None and self.source.kind != "research_artifact":
            raise ValueError("a computation source must be an exact assigned research file")
        return self


class ComputeRead(LocalOperation):
    compute_job_id: Identity


class ComputeOutputRead(LocalOperation):
    execution: VersionReference
    artifact_id: Identity
    offset: Epoch = 0
    length: Annotated[int, Field(ge=1, le=12288, strict=True)] = 8192

    @model_validator(mode="after")
    def execution_receipt(self):
        if self.execution.kind != "compute_result":
            raise ValueError("output reads require an assigned exact server execution receipt")
        return self


class WorkspacePage(LocalOperation):
    after: Annotated[str, Field(max_length=128)] = ""
    limit: Annotated[int, Field(ge=1, le=100, strict=True)] = 20


class WorkspaceRead(LocalOperation):
    path: WorkspacePath
    revision: Revision | None = None
    offset: Epoch = 0
    length: Annotated[int, Field(ge=1, le=16000, strict=True)] = 12000


class WorkspaceWrite(LocalOperation):
    path: WorkspacePath
    text: Annotated[str, Field(max_length=64000)]
    expected_sha256: Checksum | None = None


class TextEdit(RequestInput):
    start: Epoch
    end: Epoch
    text: Annotated[str, Field(max_length=16000)]

    @model_validator(mode="after")
    def ordered_range(self):
        if self.end < self.start:
            raise ValueError("patch end precedes its start")
        return self


class WorkspacePatch(LocalOperation):
    path: WorkspacePath
    expected_sha256: Checksum
    changes: list[TextEdit] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def ordered_edits(self):
        end = 0
        for change in self.changes:
            if change.start < end:
                raise ValueError("patch changes must be ordered and nonoverlapping")
            end = change.end
        return self
