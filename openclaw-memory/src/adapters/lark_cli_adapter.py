"""Adapter that centralizes all lark-cli command execution."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Sequence

from safety.policy import SafetyPolicy


@dataclass
class CliResult:
    """Container for a completed lark-cli invocation."""

    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    data: Any | None


class LarkCliAdapter:
    """Run verified lark-cli commands behind the V1 safety policy."""

    def __init__(self, executable: str = "lark-cli.cmd", policy: SafetyPolicy | None = None) -> None:
        """Create an adapter for a lark-cli executable and safety policy."""
        self.executable = executable
        self.policy = policy or SafetyPolicy()

    def resolve_executable(self) -> str:
        """Return the executable path found on PATH or the configured command name."""
        return shutil.which(self.executable) or self.executable

    def run(
        self,
        args: Sequence[str],
        *,
        stdin_text: str | None = None,
        allow_write: bool = False,
    ) -> CliResult:
        """Run a lark-cli command after applying the safety policy."""
        safe_args = list(args)
        self.policy.assert_allowed(safe_args, allow_write=allow_write)
        completed = subprocess.run(
            [self.resolve_executable(), *safe_args],
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return CliResult(
            args=safe_args,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            data=self._parse_json(completed.stdout),
        )

    def doctor(self) -> CliResult:
        """Run lark-cli doctor and return the CLI health result."""
        return self.run(["doctor"])

    def chat_search(self, query: str, page_size: int = 20, identity: str = "user") -> CliResult:
        """Search visible chats by query and return matching chat metadata."""
        return self.run(
            [
                "im",
                "+chat-search",
                "--as",
                identity,
                "--query",
                query,
                "--page-size",
                str(page_size),
                "--format",
                "json",
            ]
        )

    def list_chat_messages(
        self,
        chat_id: str,
        page_size: int = 50,
        identity: str = "user",
        sort: str = "desc",
    ) -> CliResult:
        """List messages from a chat by chat_id and return raw CLI data."""
        return self.run(
            [
                "im",
                "+chat-messages-list",
                "--as",
                identity,
                "--chat-id",
                chat_id,
                "--page-size",
                str(page_size),
                "--sort",
                sort,
                "--format",
                "json",
            ]
        )

    def messages_mget(self, message_ids: Sequence[str], identity: str = "user") -> CliResult:
        """Batch fetch message details for up to 50 message IDs."""
        return self.run(
            [
                "im",
                "+messages-mget",
                "--as",
                identity,
                "--message-ids",
                ",".join(message_ids),
                "--format",
                "json",
            ]
        )

    def fetch_doc(self, doc: str, limit: int | None = None, offset: int | None = None) -> CliResult:
        """Fetch a Lark document by URL or token and return content metadata."""
        args = ["docs", "+fetch", "--doc", doc, "--format", "json"]
        if limit is not None:
            args.extend(["--limit", str(limit)])
        if offset is not None:
            args.extend(["--offset", str(offset)])
        return self.run(args)

    def search_tasks(self, query: str, page_limit: int = 20) -> CliResult:
        """Search tasks by query and return matching task summaries."""
        return self.run(
            ["task", "+search", "--query", query, "--page-limit", str(page_limit), "--format", "json"]
        )

    def search_tasklists(self, query: str, page_limit: int = 20) -> CliResult:
        """Search tasklists by query and return matching tasklist summaries."""
        return self.run(
            ["task", "+tasklist-search", "--query", query, "--page-limit", str(page_limit), "--format", "json"]
        )

    def list_tasklist_tasks(self, tasklist_guid: str, page_size: int = 50) -> CliResult:
        """List tasks inside a tasklist using stdin JSON for reliable PowerShell quoting."""
        params = {
            "tasklist_guid": tasklist_guid,
            "page_size": page_size,
            "user_id_type": "open_id",
        }
        return self.run(
            ["task", "tasklists", "tasks", "--as", "user", "--params", "-", "--format", "json"],
            stdin_text=json.dumps(params, ensure_ascii=False),
        )

    def _parse_json(self, stdout: str) -> Any | None:
        """Parse stdout as JSON and return None when parsing fails."""
        text = stdout.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
