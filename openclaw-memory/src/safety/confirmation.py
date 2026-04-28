"""Helpers for rendering blocked write commands as human review items."""

from typing import Sequence

from adapters.command_registry import command_to_string


def build_confirmation_note(args: Sequence[str], reason: str) -> str:
    """Return a short confirmation note for a blocked command."""
    return f"Manual confirmation required: {command_to_string(args)}\nReason: {reason}"


def requires_human_confirmation(args: Sequence[str]) -> bool:
    """Return true for all V1 write-like command plans."""
    return bool(args)
