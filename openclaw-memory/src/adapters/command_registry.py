"""Registry of verified lark-cli commands and their safety categories."""

from dataclasses import dataclass
from enum import Enum
from typing import Sequence


class CommandKind(str, Enum):
    """Classify a lark-cli command by execution risk."""

    READ_ONLY = "read_only"
    WRITE = "write"
    BLOCKED_DRY_RUN = "blocked_dry_run"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CommandSpec:
    """Describe a lark-cli command prefix and its safety category."""

    prefix: tuple[str, ...]
    kind: CommandKind
    description: str


READ_ONLY_COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec(("doctor",), CommandKind.READ_ONLY, "CLI health check"),
    CommandSpec(("im", "+chat-search"), CommandKind.READ_ONLY, "Search visible chats"),
    CommandSpec(("im", "+chat-messages-list"), CommandKind.READ_ONLY, "List chat messages"),
    CommandSpec(("im", "+messages-mget"), CommandKind.READ_ONLY, "Batch get messages"),
    CommandSpec(("docs", "+fetch"), CommandKind.READ_ONLY, "Fetch document content"),
    CommandSpec(("drive", "file.comments", "list"), CommandKind.READ_ONLY, "List doc comments"),
    CommandSpec(("contact", "+search"), CommandKind.READ_ONLY, "Search users by name"),
    CommandSpec(("im", "+chat-members-list"), CommandKind.READ_ONLY, "List chat members"),
    CommandSpec(("calendar", "+agenda"), CommandKind.READ_ONLY, "List calendar events"),
    CommandSpec(("minutes", "+search"), CommandKind.READ_ONLY, "Search meeting minutes"),
    CommandSpec(("minutes", "minutes", "get"), CommandKind.READ_ONLY, "Get minute detail"),
    CommandSpec(("approval", "instances"), CommandKind.READ_ONLY, "List approval instances"),
    CommandSpec(("task", "+search"), CommandKind.READ_ONLY, "Search tasks"),
    CommandSpec(("task", "+tasklist-search"), CommandKind.READ_ONLY, "Search tasklists"),
    CommandSpec(("task", "tasklists", "tasks"), CommandKind.READ_ONLY, "List tasklist tasks"),
)

WRITE_COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec(("im", "+messages-send"), CommandKind.WRITE, "Send message"),
    CommandSpec(("im", "+messages-reply"), CommandKind.WRITE, "Reply to message"),
    CommandSpec(("im", "pins"), CommandKind.WRITE, "Pin/unpin/list messages"),
    CommandSpec(("docs", "+create"), CommandKind.WRITE, "Create document"),
    CommandSpec(("docs", "+update"), CommandKind.WRITE, "Update document"),
    CommandSpec(("task", "+create"), CommandKind.WRITE, "Create task"),
    CommandSpec(("task", "+update"), CommandKind.WRITE, "Update task"),
    CommandSpec(("task", "+complete"), CommandKind.WRITE, "Complete task"),
    CommandSpec(("task", "+comment"), CommandKind.WRITE, "Comment on task"),
    CommandSpec(("task", "+assign"), CommandKind.WRITE, "Assign task"),
    CommandSpec(("task", "+followers"), CommandKind.WRITE, "Manage task followers"),
    CommandSpec(("task", "+tasklist-create"), CommandKind.WRITE, "Create tasklist"),
    CommandSpec(("task", "+tasklist-task-add"), CommandKind.WRITE, "Add task to tasklist"),
)


def _matches_prefix(args: Sequence[str], prefix: Sequence[str]) -> bool:
    """Return whether args starts with a command prefix."""
    return len(args) >= len(prefix) and tuple(args[: len(prefix)]) == tuple(prefix)


def command_to_string(args: Sequence[str]) -> str:
    """Render command args for logs and safety messages."""
    return " ".join(args)


class CommandRegistry:
    """Classify lark-cli commands using the verified V1 allow/block lists."""

    def __init__(self) -> None:
        """Create a registry with the built-in V1 command specs."""
        self.read_only = READ_ONLY_COMMANDS
        self.write = WRITE_COMMANDS

    def classify(self, args: Sequence[str]) -> CommandKind:
        """Classify args as read-only, write, blocked dry-run, or unknown."""
        if _matches_prefix(args, ("docs", "+create")) and "--dry-run" in args:
            return CommandKind.BLOCKED_DRY_RUN
        for spec in self.write:
            if _matches_prefix(args, spec.prefix):
                return spec.kind
        for spec in self.read_only:
            if _matches_prefix(args, spec.prefix):
                return spec.kind
        return CommandKind.UNKNOWN

    def is_auto_allowed(self, args: Sequence[str]) -> bool:
        """Return true when args belongs to the V1 automatic read-only set."""
        return self.classify(args) == CommandKind.READ_ONLY

    def is_write(self, args: Sequence[str]) -> bool:
        """Return true when args belongs to a known write command."""
        return self.classify(args) == CommandKind.WRITE
