"""Portable semantic checkpoints and conservative failover decisions.

Private harness session handles are intentionally absent. A portable
checkpoint contains only normalized user/assistant messages, credential-free
source metadata, optional content-addressed artifacts, and an auditable safety
assessment. It can seed a fresh session on another harness without claiming
that provider-private state, hidden reasoning, or tool execution state moved
with it.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

from .contracts import HarnessEvent, HarnessEventKind

_SCHEMA_VERSION = "1"
_CHECKPOINT_ID = re.compile(r"^ptchk_[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_MESSAGES = 5_000
_MAX_MESSAGE_CHARACTERS = 2_000_000
_SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "credentials",
    "password",
    "private_key",
    "secret",
    "token",
}


class PortableCheckpointError(ValueError):
    """A checkpoint is malformed, unsafe to deserialize, or was modified."""


class CheckpointEffectState(StrEnum):
    NONE_OBSERVED = "none_observed"
    POTENTIAL_EFFECTS = "potential_effects"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PortableMessage:
    """Semantic conversation context safe to replay at its original role."""

    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in {"user", "assistant"}:
            raise PortableCheckpointError(
                "portable messages must be user or assistant messages"
            )
        if not self.content.strip():
            raise PortableCheckpointError("portable message content cannot be empty")
        if len(self.content) > _MAX_MESSAGE_CHARACTERS:
            raise PortableCheckpointError(
                "portable message content exceeds the size limit"
            )


@dataclass(frozen=True)
class PortableArtifactReference:
    """Content-addressed artifact reference; never an arbitrary host path."""

    artifact_id: str
    path: str
    sha256: str

    def __post_init__(self) -> None:
        if not self.artifact_id.strip():
            raise PortableCheckpointError("artifact_id is required")
        path = PurePosixPath(self.path)
        if path.is_absolute() or ".." in path.parts or not self.path.strip():
            raise PortableCheckpointError(
                "artifact paths must be relative and cannot traverse parents"
            )
        if not _SHA256.fullmatch(self.sha256):
            raise PortableCheckpointError(
                "artifact sha256 must be a lowercase hexadecimal digest"
            )


@dataclass(frozen=True)
class PortableCheckpointSource:
    """Credential-free provenance for the source operation."""

    harness_id: str
    target_id: str
    model: str
    operation_id: str
    inference_route_id: str | None = None
    model_provider: str | None = None
    project_id: str | None = None
    chat_id: str | None = None

    def __post_init__(self) -> None:
        for label, value in (
            ("harness_id", self.harness_id),
            ("target_id", self.target_id),
            ("model", self.model),
            ("operation_id", self.operation_id),
        ):
            if not value.strip():
                raise PortableCheckpointError(f"{label} is required")


@dataclass(frozen=True)
class CheckpointSafety:
    """Observed effect boundary for one completed source operation."""

    effect_state: CheckpointEffectState
    event_stream_complete: bool
    last_event_sequence: int | None = None
    last_effect_sequence: int | None = None
    advertised_checkpoint_sequences: tuple[int, ...] = ()
    effect_kinds: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.event_stream_complete, bool):
            raise PortableCheckpointError("event_stream_complete must be a boolean")
        for label, value in (
            ("last_event_sequence", self.last_event_sequence),
            ("last_effect_sequence", self.last_effect_sequence),
        ):
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 1
            ):
                raise PortableCheckpointError(f"{label} must be a positive integer")
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 1
            for value in self.advertised_checkpoint_sequences
        ):
            raise PortableCheckpointError(
                "checkpoint event sequences must be positive"
            )
        if (
            tuple(sorted(set(self.advertised_checkpoint_sequences)))
            != self.advertised_checkpoint_sequences
        ):
            raise PortableCheckpointError(
                "checkpoint event sequences must be unique and sorted"
            )
        if len(set(self.effect_kinds)) != len(self.effect_kinds):
            raise PortableCheckpointError("effect_kinds must be unique")
        if any(not isinstance(kind, str) or not kind.strip() for kind in self.effect_kinds):
            raise PortableCheckpointError("effect_kinds must contain non-empty strings")
        if (
            self.effect_state is CheckpointEffectState.NONE_OBSERVED
            and self.last_effect_sequence is not None
        ):
            raise PortableCheckpointError(
                "an effect-free checkpoint cannot have a last effect sequence"
            )
        if (
            self.effect_state is CheckpointEffectState.POTENTIAL_EFFECTS
            and self.last_effect_sequence is None
        ):
            raise PortableCheckpointError(
                "a checkpoint with effects requires a last effect sequence"
            )


@dataclass(frozen=True)
class PortableCheckpointPromotion:
    """Explicit control-plane attestation permitting post-effect handoff."""

    actor: str
    reason: str
    promoted_at: str
    safe_after_sequence: int | None

    def __post_init__(self) -> None:
        if not self.actor.strip() or not self.reason.strip() or not self.promoted_at.strip():
            raise PortableCheckpointError(
                "checkpoint promotion requires actor, reason, and promoted_at"
            )
        if self.safe_after_sequence is not None and self.safe_after_sequence < 1:
            raise PortableCheckpointError("safe_after_sequence must be positive")


@dataclass(frozen=True)
class PortableCheckpoint:
    """Validated, content-addressed semantic handoff envelope."""

    checkpoint_id: str
    sha256: str
    created_at: str
    source: PortableCheckpointSource
    messages: tuple[PortableMessage, ...]
    safety: CheckpointSafety
    artifacts: tuple[PortableArtifactReference, ...] = ()
    promotion: PortableCheckpointPromotion | None = None
    schema_version: str = _SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA_VERSION:
            raise PortableCheckpointError(
                "unsupported portable checkpoint schema_version"
            )
        if not _CHECKPOINT_ID.fullmatch(self.checkpoint_id):
            raise PortableCheckpointError("invalid portable checkpoint_id")
        if not _SHA256.fullmatch(self.sha256):
            raise PortableCheckpointError("invalid portable checkpoint sha256")
        if not self.created_at.strip():
            raise PortableCheckpointError("created_at is required")
        if not self.messages:
            raise PortableCheckpointError(
                "a portable checkpoint requires conversation messages"
            )
        if len(self.messages) > _MAX_MESSAGES:
            raise PortableCheckpointError(
                "portable checkpoint exceeds the message count limit"
            )
        if len({artifact.artifact_id for artifact in self.artifacts}) != len(
            self.artifacts
        ):
            raise PortableCheckpointError("portable artifact ids must be unique")

    @property
    def safe_for_automatic_failover(self) -> bool:
        return assess_automatic_failover(self).automatic

    def content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "source": asdict(self.source),
            "messages": [asdict(message) for message in self.messages],
            "safety": _safety_dict(self.safety),
            "artifacts": [asdict(artifact) for artifact in self.artifacts],
            "promotion": (
                asdict(self.promotion) if self.promotion is not None else None
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "sha256": self.sha256,
            **self.content_dict(),
        }


@dataclass(frozen=True)
class AutomaticFailoverAssessment:
    automatic: bool
    reason: str
    checkpoint_id: str | None = None


class TurnSafetyTracker:
    """Conservatively summarize canonical events for failover policy."""

    _EFFECT_EVENTS = {
        HarnessEventKind.TOOL_STARTED,
        HarnessEventKind.TOOL_COMPLETED,
        HarnessEventKind.FILE_CHANGED,
    }

    def __init__(self) -> None:
        self._last_sequence: int | None = None
        self._last_effect_sequence: int | None = None
        self._effects: set[str] = set()
        self._advertised_checkpoints: set[int] = set()
        self._stream_complete = False

    def observe(self, event: HarnessEvent) -> None:
        if self._last_sequence is not None and event.sequence <= self._last_sequence:
            raise PortableCheckpointError(
                "harness event sequences must increase monotonically"
            )
        self._last_sequence = event.sequence
        if event.kind is HarnessEventKind.CHECKPOINT_CREATED:
            self._advertised_checkpoints.add(event.sequence)
        if event.kind in self._EFFECT_EVENTS or (
            event.kind is HarnessEventKind.APPROVAL_RESOLVED
            and _approval_accepted(event.data)
        ) or (
            event.kind is HarnessEventKind.PROGRESS
            and event.data.get("category") == "artifact"
        ):
            self._record_effect(event)

    def mark_stream_complete(self) -> None:
        self._stream_complete = True

    def snapshot(self) -> CheckpointSafety:
        if not self._stream_complete:
            effect_state = CheckpointEffectState.UNKNOWN
        elif self._last_effect_sequence is None:
            effect_state = CheckpointEffectState.NONE_OBSERVED
        else:
            effect_state = CheckpointEffectState.POTENTIAL_EFFECTS
        return CheckpointSafety(
            effect_state=effect_state,
            event_stream_complete=self._stream_complete,
            last_event_sequence=self._last_sequence,
            last_effect_sequence=self._last_effect_sequence,
            advertised_checkpoint_sequences=tuple(
                sorted(self._advertised_checkpoints)
            ),
            effect_kinds=tuple(sorted(self._effects)),
        )

    def _record_effect(self, event: HarnessEvent) -> None:
        self._last_effect_sequence = event.sequence
        self._effects.add(event.kind.value)


def portable_messages(
    messages: Iterable[Mapping[str, Any]],
) -> tuple[PortableMessage, ...]:
    """Keep visible semantic context while excluding reasoning and activity."""

    normalized: list[PortableMessage] = []
    for message in messages:
        role = message.get("role")
        text = message.get("content", message.get("text"))
        if (
            role not in {"user", "assistant"}
            or not isinstance(text, str)
            or not text.strip()
        ):
            continue
        normalized.append(PortableMessage(role=role, content=text))
    if not normalized:
        raise PortableCheckpointError(
            "no portable user or assistant messages were found"
        )
    return tuple(normalized)


def create_portable_checkpoint(
    *,
    source: PortableCheckpointSource,
    messages: Iterable[Mapping[str, Any] | PortableMessage],
    safety: CheckpointSafety,
    artifacts: Iterable[PortableArtifactReference] = (),
    created_at: str | None = None,
) -> PortableCheckpoint:
    normalized_messages = tuple(
        message
        if isinstance(message, PortableMessage)
        else PortableMessage(
            role=str(message.get("role", "")),
            content=str(message.get("content", "")),
        )
        for message in messages
    )
    provisional = PortableCheckpoint(
        checkpoint_id="ptchk_" + "0" * 24,
        sha256="0" * 64,
        created_at=created_at or _now(),
        source=source,
        messages=normalized_messages,
        safety=safety,
        artifacts=tuple(artifacts),
    )
    return _seal(provisional)


def promote_portable_checkpoint(
    checkpoint: PortableCheckpoint,
    *,
    actor: str,
    reason: str,
    promoted_at: str | None = None,
) -> PortableCheckpoint:
    """Explicitly attest that the semantic state is safe after its effects."""

    validated = validate_portable_checkpoint(checkpoint.to_dict())
    promotion = PortableCheckpointPromotion(
        actor=actor,
        reason=reason,
        promoted_at=promoted_at or _now(),
        safe_after_sequence=validated.safety.last_event_sequence,
    )
    return _seal(replace(validated, promotion=promotion))


def validate_portable_checkpoint(value: Mapping[str, Any]) -> PortableCheckpoint:
    """Parse an untrusted checkpoint and verify its exact content digest."""

    _validate_no_secrets(value)
    try:
        source = PortableCheckpointSource(**_mapping(value["source"], "source"))
        messages = tuple(
            PortableMessage(**_mapping(message, "message"))
            for message in _list(value["messages"], "messages")
        )
        safety_value = _mapping(value["safety"], "safety")
        safety = CheckpointSafety(
            effect_state=CheckpointEffectState(safety_value["effect_state"]),
            event_stream_complete=safety_value["event_stream_complete"],
            last_event_sequence=safety_value.get("last_event_sequence"),
            last_effect_sequence=safety_value.get("last_effect_sequence"),
            advertised_checkpoint_sequences=tuple(
                safety_value.get("advertised_checkpoint_sequences", ())
            ),
            effect_kinds=tuple(safety_value.get("effect_kinds", ())),
        )
        artifacts = tuple(
            PortableArtifactReference(**_mapping(artifact, "artifact"))
            for artifact in _list(value.get("artifacts", []), "artifacts")
        )
        promotion_value = value.get("promotion")
        promotion = (
            PortableCheckpointPromotion(
                **_mapping(promotion_value, "promotion")
            )
            if promotion_value is not None
            else None
        )
        checkpoint = PortableCheckpoint(
            checkpoint_id=str(value["checkpoint_id"]),
            sha256=str(value["sha256"]),
            created_at=str(value["created_at"]),
            source=source,
            messages=messages,
            safety=safety,
            artifacts=artifacts,
            promotion=promotion,
            schema_version=str(value["schema_version"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, PortableCheckpointError):
            raise
        raise PortableCheckpointError(
            f"malformed portable checkpoint: {error}"
        ) from error
    expected = _seal(
        replace(
            checkpoint,
            checkpoint_id="ptchk_" + "0" * 24,
            sha256="0" * 64,
        )
    )
    if (
        checkpoint.checkpoint_id != expected.checkpoint_id
        or checkpoint.sha256 != expected.sha256
    ):
        raise PortableCheckpointError(
            "portable checkpoint digest does not match its content"
        )
    return checkpoint


def assess_automatic_failover(
    checkpoint: PortableCheckpoint,
) -> AutomaticFailoverAssessment:
    """Allow automatic continuation only before effects or after promotion."""

    safety = checkpoint.safety
    if (
        not safety.event_stream_complete
        or safety.effect_state is CheckpointEffectState.UNKNOWN
    ):
        return AutomaticFailoverAssessment(
            False,
            "The source event stream is incomplete.",
            checkpoint.checkpoint_id,
        )
    if safety.effect_state is CheckpointEffectState.NONE_OBSERVED:
        return AutomaticFailoverAssessment(
            True,
            "No potential side effect was observed in the complete source event stream.",
            checkpoint.checkpoint_id,
        )
    promotion = checkpoint.promotion
    if promotion is None:
        return AutomaticFailoverAssessment(
            False,
            "Potential side effects require an explicitly promoted semantic checkpoint.",
            checkpoint.checkpoint_id,
        )
    if safety.last_effect_sequence is None or promotion.safe_after_sequence is None:
        return AutomaticFailoverAssessment(
            False,
            "The promoted boundary does not cover source effects.",
            checkpoint.checkpoint_id,
        )
    if promotion.safe_after_sequence < safety.last_effect_sequence:
        return AutomaticFailoverAssessment(
            False,
            "The promoted boundary predates the last source effect.",
            checkpoint.checkpoint_id,
        )
    return AutomaticFailoverAssessment(
        True,
        f"Checkpoint promoted by {promotion.actor} after the last observed effect.",
        checkpoint.checkpoint_id,
    )


def checkpoint_messages(
    checkpoint: PortableCheckpoint,
) -> tuple[dict[str, str], ...]:
    """Return role-preserving context for a fresh destination session."""

    validated = validate_portable_checkpoint(checkpoint.to_dict())
    return tuple(
        {"role": message.role, "content": message.content}
        for message in validated.messages
    )


def _seal(checkpoint: PortableCheckpoint) -> PortableCheckpoint:
    payload = json.dumps(
        checkpoint.content_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return replace(
        checkpoint,
        checkpoint_id=f"ptchk_{digest[:24]}",
        sha256=digest,
    )


def _safety_dict(safety: CheckpointSafety) -> dict[str, Any]:
    value = asdict(safety)
    value["effect_state"] = safety.effect_state.value
    return value


def _approval_accepted(data: Mapping[str, Any]) -> bool:
    value = data.get("decision")
    return value in {"accept", "acceptForSession", "accepted", True}


def _validate_no_secrets(value: Any, path: str = "checkpoint") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in _SECRET_KEYS:
                raise PortableCheckpointError(
                    f"credential-like field is forbidden at {path}.{key}"
                )
            _validate_no_secrets(child, f"{path}.{key}")
    elif isinstance(value, list | tuple):
        for index, child in enumerate(value):
            _validate_no_secrets(child, f"{path}[{index}]")


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PortableCheckpointError(f"{label} must be an object")
    return {str(key): child for key, child in value.items()}


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list | tuple):
        raise PortableCheckpointError(f"{label} must be a list")
    return list(value)


def _now() -> str:
    return datetime.now(UTC).isoformat()
