"""Server-brokered inference inputs; callers never supply provider credentials."""

from typing import Literal

from pydantic import Field, model_validator

from .request_input import RequestInput
from .work_contracts import Identity, Positive
from .work_job_contracts import JobLease


class CompletionPayload(RequestInput):
    model: Identity
    messages: list[dict] = Field(min_length=1)
    tools: list[dict] = Field(default_factory=list)
    max_tokens: Positive | None = None
    tool_choice: Literal["auto", "none", "required"] | dict | None = None
    reasoning: dict | None = None
    response_format: dict | None = None

    @model_validator(mode="after")
    def message_protocol(self):
        allowed = {"role", "content", "name", "tool_calls", "tool_call_id", "reasoning", "reasoning_details"}
        for message in self.messages:
            if (
                set(message) - allowed
                or not isinstance(message.get("role"), str)
                or message.get("role") not in {"system", "user", "assistant", "tool"}
                or message.get("content") is not None
                and not isinstance(message["content"], str)
            ):
                raise ValueError("completion messages require the text/tool protocol")
        if not self.tools and self.tool_choice is not None and self.tool_choice != "none":
            raise ValueError("tool choice requires a granted tool schema")
        return self


class WorkCompletion(JobLease):
    client_id: Identity
    purpose: Literal["work", "compaction"] = "work"
    payload: CompletionPayload


class WorkModelInfo(JobLease):
    model: Identity


class WorkProgress(JobLease):
    client_id: Identity
    phase: Identity
    summary: str = Field(min_length=1, max_length=400)
