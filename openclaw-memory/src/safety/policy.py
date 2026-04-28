"""Safety policy that prevents accidental lark-cli writes in V1."""

from dataclasses import dataclass
from typing import Sequence

from adapters.command_registry import CommandKind, CommandRegistry, command_to_string


class SafetyError(RuntimeError):
    """Raised when a command is blocked by the V1 safety policy."""


@dataclass(frozen=True)
class SafetyDecision:
    """Describe whether a command can run and why."""

    allowed: bool
    kind: CommandKind
    reason: str
    requires_confirmation: bool


class SafetyPolicy:
    """Decide whether lark-cli commands may run automatically."""

    def __init__(self, registry: CommandRegistry | None = None) -> None:
        """Create a policy using a command registry."""
        self.registry = registry or CommandRegistry()

    def evaluate(self, args: Sequence[str], allow_write: bool = False) -> SafetyDecision:
        """Return the safety decision for args and an optional write override."""
        kind = self.registry.classify(args)
        command = command_to_string(args)
        if kind == CommandKind.READ_ONLY:
            return SafetyDecision(True, kind, f"Read-only command allowed: {command}", False)
        if kind == CommandKind.BLOCKED_DRY_RUN:
            return SafetyDecision(
                False,
                kind,
                "docs +create --dry-run is blocked because it previously created a real document.",
                True,
            )
        if kind == CommandKind.WRITE:
            return SafetyDecision(
                allow_write,
                kind,
                "Write command requires explicit confirmation in V1." if not allow_write else "Write override accepted.",
                True,
            )
        return SafetyDecision(False, kind, f"Unknown command is not auto-allowed: {command}", True)

    def assert_allowed(self, args: Sequence[str], allow_write: bool = False) -> SafetyDecision:
        """Raise SafetyError unless args is allowed by the policy."""
        decision = self.evaluate(args, allow_write=allow_write)
        if not decision.allowed:
            raise SafetyError(decision.reason)
        return decision
