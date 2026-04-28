"""Validated LLM candidate schema for trusted Memory extraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from memory.schema import MemoryItem, SourceRef


class CandidateValidationError(ValueError):
    """Raised when an LLM candidate does not satisfy the trusted schema."""


@dataclass
class MemoryCandidate:
    """Structured candidate emitted by an LLM before conversion to MemoryItem."""

    project_id: str
    state_type: str
    key: str
    current_value: str
    rationale: str
    owner: str | None
    status: str
    confidence: float
    source_refs: list[SourceRef]
    detected_at: str


def validate_candidate_dict(data: dict[str, Any], valid_message_ids: Iterable[str]) -> MemoryCandidate:
    """Validate one candidate dict and return a MemoryCandidate."""
    required = {
        "project_id",
        "state_type",
        "key",
        "current_value",
        "rationale",
        "owner",
        "status",
        "confidence",
        "source_refs",
        "detected_at",
    }
    missing = sorted(required - set(data))
    if missing:
        raise CandidateValidationError(f"candidate missing fields: {', '.join(missing)}")

    confidence = _validate_confidence(data["confidence"])
    source_refs = _validate_source_refs(data["source_refs"], set(valid_message_ids))

    return MemoryCandidate(
        project_id=_required_str(data, "project_id"),
        state_type=_required_str(data, "state_type"),
        key=_required_str(data, "key"),
        current_value=_required_str(data, "current_value"),
        rationale=_required_str(data, "rationale"),
        owner=_optional_str(data["owner"]),
        status=_required_str(data, "status"),
        confidence=confidence,
        source_refs=source_refs,
        detected_at=_required_str(data, "detected_at"),
    )


def candidate_to_memory_item(candidate: MemoryCandidate) -> MemoryItem:
    """Convert a validated MemoryCandidate into a MemoryItem."""
    return MemoryItem(
        project_id=candidate.project_id,
        state_type=candidate.state_type,
        key=candidate.key,
        current_value=candidate.current_value,
        rationale=candidate.rationale,
        owner=candidate.owner,
        status=candidate.status,
        confidence=candidate.confidence,
        source_refs=candidate.source_refs,
        updated_at=candidate.detected_at,
    )


def _required_str(data: dict[str, Any], field: str) -> str:
    """Return a non-empty string field or raise a validation error."""
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CandidateValidationError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_str(value: Any) -> str | None:
    """Return an optional string value or raise a validation error."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise CandidateValidationError("owner must be a string or null")
    return value.strip() or None


def _validate_confidence(value: Any) -> float:
    """Return a confidence float in [0, 1] or raise a validation error."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise CandidateValidationError("confidence must be a number")
    confidence = float(value)
    if confidence < 0 or confidence > 1:
        raise CandidateValidationError("confidence must be between 0 and 1")
    return confidence


def _validate_source_refs(value: Any, valid_message_ids: set[str]) -> list[SourceRef]:
    """Validate source_refs and return SourceRef objects."""
    if not isinstance(value, list) or not value:
        raise CandidateValidationError("source_refs must be a non-empty list")
    refs = []
    for ref in value:
        if not isinstance(ref, dict):
            raise CandidateValidationError("each source_ref must be an object")
        message_id = _required_str(ref, "message_id")
        if message_id not in valid_message_ids:
            raise CandidateValidationError(f"source_ref message_id not found in raw events: {message_id}")
        refs.append(
            SourceRef(
                type=str(ref.get("type", "message")),
                chat_id=_required_str(ref, "chat_id"),
                message_id=message_id,
                excerpt=_required_str(ref, "excerpt"),
                created_at=_required_str(ref, "created_at"),
            )
        )
    return refs
