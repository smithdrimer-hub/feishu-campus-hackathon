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
    """Run verified lark-cli commands behind the V1 safety policy.

    V1.19 P0-F: 可选群成员验证 + 凭证检查。
    """

    def __init__(self, executable: str = "lark-cli.cmd",
                 policy: SafetyPolicy | None = None,
                 verify_membership_before_write: bool = False,
                 bot_open_id: str = "") -> None:
        """Create an adapter for a lark-cli executable and safety policy.

        Args:
            executable: lark-cli 可执行文件路径或名称。
            policy: 安全策略实例。
            verify_membership_before_write: 如果为 True，发消息/置顶前验证 bot 在群内。
            bot_open_id: bot 的 open_id，用于群成员验证。
        """
        self.executable = executable
        self.policy = policy or SafetyPolicy()
        self._verify_membership = verify_membership_before_write
        self._bot_open_id = bot_open_id

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
            timeout=120,
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
        page_token: str | None = None,
    ) -> CliResult:
        """List messages from a chat by chat_id and return raw CLI data.

        V1.11: 支持 page_token 分页，用于增量同步。
        """
        args = [
            "im", "+chat-messages-list",
            "--as", identity,
            "--chat-id", chat_id,
            "--page-size", str(page_size),
            "--sort", sort,
        ]
        if page_token:
            args.extend(["--page-token", page_token])
        args.extend(["--format", "json"])
        return self.run(args)

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
        """Fetch a Lark document by URL or token and return content metadata.

        V1.12: 支持完整飞书文档 URL，自动提取 doc token。
        """
        doc_token = _extract_doc_token(doc)
        args = ["docs", "+fetch", "--doc", doc_token, "--format", "json"]
        if limit is not None:
            args.extend(["--limit", str(limit)])
        if offset is not None:
            args.extend(["--offset", str(offset)])
        return self.run(args)

    def search_tasks(self, query: str, page_limit: int = 20,
                     page_token: str | None = None) -> CliResult:
        """Search tasks by query and return matching task summaries.

        V1.17 P1: 支持 page_token 分页。
        """
        args = ["task", "+search", "--query", query,
                "--page-limit", str(page_limit), "--format", "json"]
        if page_token:
            args.extend(["--page-token", page_token])
        return self.run(args)

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

    # ── V1.11 写入操作 ────────────────────────────────────────────

    def send_message(
        self,
        chat_id: str,
        content: str,
        msg_type: str = "text",
        identity: str = "bot",
    ) -> CliResult:
        """Send a message to a chat.

        Args:
            chat_id: Target chat ID (oc_xxx).
            content: Message body. Interpreted as plain text when msg_type="text",
                     as markdown when msg_type="markdown".
            msg_type: "text" or "markdown".
            identity: "bot" (default) or "user".
        """
        if self._verify_membership:
            self._check_membership(chat_id)
        if msg_type == "markdown":
            return self.run(
                ["im", "+messages-send", "--as", identity, "--chat-id", chat_id,
                 "--markdown", content],
                allow_write=True,
            )
        # V1.18: 交互式卡片——内联 JSON（Windows 命令行列参限制 ~500 字符）
        if msg_type == "interactive":
            return self.run(
                ["im", "+messages-send", "--as", identity, "--chat-id", chat_id,
                 "--msg-type", "interactive", "--content", content],
                allow_write=True,
            )
        return self.run(
            ["im", "+messages-send", "--as", identity, "--chat-id", chat_id,
             "--text", content],
            allow_write=True,
        )

    def reply_message(
        self,
        message_id: str,
        content: str,
        msg_type: str = "text",
        identity: str = "bot",
        in_thread: bool = False,
    ) -> CliResult:
        """Reply to a specific message.

        Args:
            message_id: The message to reply to (om_xxx).
            content: Reply body.
            msg_type: "text" or "markdown".
            identity: "bot" (default) or "user".
            in_thread: If True, reply in thread instead of main chat.
        """
        args = ["im", "+messages-reply", "--as", identity, "--message-id", message_id]
        if msg_type == "markdown":
            args.extend(["--markdown", content])
        else:
            args.extend(["--text", content])
        if in_thread:
            args.append("--reply-in-thread")
        return self.run(args, allow_write=True)

    def pin_message(self, message_id: str, chat_id: str = "") -> CliResult:
        """Pin a message in its chat.

        Uses the native pins.create API. The chat is inferred from message_id.

        Args:
            message_id: 要置顶的消息 ID。
            chat_id: 可选，用于群成员验证（如果启用了 verify_membership_before_write）。
        """
        if self._verify_membership and chat_id:
            self._check_membership(chat_id)
        import json
        return self.run(
            ["im", "pins", "create", "--data",
             json.dumps({"message_id": message_id}, ensure_ascii=False)],
            allow_write=True,
        )

    def unpin_message(self, message_id: str) -> CliResult:
        """Remove a pinned message."""
        import json
        return self.run(
            ["im", "pins", "delete", "--data",
             json.dumps({"message_id": message_id}, ensure_ascii=False)],
            allow_write=True,
        )

    # ── V1.14 写入操作扩展 ──────────────────────────────────────

    def create_task(
        self,
        summary: str,
        description: str = "",
        due_at: str = "",
        identity: str = "user",
    ) -> CliResult:
        """Create a Feishu task.

        Args:
            summary: Task title (required).
            description: Optional longer body text.
            due_at: ISO-8601 deadline, e.g. "2026-05-10T00:00:00Z".
            identity: "bot" (default) or "user".
        """
        args = ["task", "+create", "--summary", summary, "--as", identity, "--format", "json"]
        if description:
            args.extend(["--description", description])
        if due_at:
            args.extend(["--due", due_at])
        return self.run(args, allow_write=True)

    def create_doc(
        self,
        title: str,
        content: str = "",
        identity: str = "user",
    ) -> CliResult:
        """Create a Feishu document.

        WARNING: Every call creates a real document. --dry-run is BLOCKED
        by SafetyPolicy (it previously created real documents).

        Args:
            title: Document title.
            content: Optional markdown body text.
            identity: "bot" (default) or "user".
        """
        args = ["docs", "+create", "--title", title, "--as", identity]
        if content:
            args.extend(["--markdown", content])
        return self.run(args, allow_write=True)

    def assign_task(
        self,
        task_guid: str,
        assignee_ids: list[str],
        identity: str = "bot",
    ) -> CliResult:
        """Assign an existing task to one or more Feishu users.

        Args:
            task_guid: The task's unique GUID from create_task or search_tasks.
            assignee_ids: List of Feishu open_ids (ou_xxx).
            identity: "bot" (default) or "user".
        """
        args = [
            "task", "+assign", "--task-guid", task_guid,
            "--assignee-ids", ",".join(assignee_ids),
            "--as", identity, "--format", "json",
        ]
        return self.run(args, allow_write=True)

    def search_contact(self, query: str) -> CliResult:
        """V1.12: 按姓名搜索飞书用户，返回 open_id/name。

        用于 owner 姓名 → open_id 解析。
        """
        return self.run(
            ["contact", "+search", "--query", query, "--page-size", "3",
             "--format", "json"],
        )

    def list_chat_members(self, chat_id: str, page_size: int = 100) -> CliResult:
        """V1.12: 获取群聊成员列表 (AUTH-7)。

        用于验证用户是否有权访问该群的记忆。
        """
        import json
        params = json.dumps({"page_size": page_size})
        return self.run(
            ["im", "+chat-members-list", "--chat-id", chat_id,
             "--params", params, "--as", "user"],
        )

    # ── 安全验证 ──────────────────────────────────────────────────

    def _check_membership(self, chat_id: str) -> None:
        """写入前检查 bot 是否为群成员，不是则抛出 SafetyError。"""
        from safety.policy import SafetyError
        if not self._bot_open_id:
            return  # 没有 bot_open_id 时静默跳过
        if not self.verify_chat_membership(chat_id, self._bot_open_id):
            raise SafetyError(
                f"Bot ({self._bot_open_id}) 不是群 {chat_id} 的成员，拒绝写入操作"
            )

    def verify_chat_membership(self, chat_id: str, open_id: str) -> bool:
        """V1.12: 验证用户是否为群成员 (AUTH-7)。"""
        result = self.list_chat_members(chat_id)
        if result.returncode != 0:
            return False
        data = result.data or {}
        members = data.get("data", {}).get("items", []) or data.get("items", []) or []
        return any(m.get("member_id", m.get("open_id", "")) == open_id
                   for m in members)

    def verify_doc_accessible(self, doc_url: str) -> bool:
        """V1.19 P0-F: 验证 bot 能否访问指定文档。

        通过尝试 fetch 文档来判断（有权限则成功，无权限则 API 报错）。
        注意：此方法仅检查 bot 自身权限，不检查其他用户权限。
        飞书 CLI 目前不支持按用户粒度查询文档权限。
        """
        doc_id = _extract_doc_token(doc_url)
        result = self.fetch_doc(doc_id)
        return result.returncode == 0

    def fetch_doc_comments(self, doc_id: str, page_size: int = 50) -> CliResult:
        """V1.12: 获取文档评论列表。

        使用 drive file.comments.list API。
        返回评论含 user_id, create_time, reply_list。
        """
        import json
        params = json.dumps({
            "file_token": doc_id,
            "file_type": "docx",
            "page_size": page_size,
            "user_id_type": "open_id",
        }, ensure_ascii=False)
        return self.run(
            ["drive", "file.comments", "list", "--as", "user",
             "--params", params],
        )

    # ── V1.13 日历/会议/审批 ───────────────────────────────────

    def list_calendar_events(self, start: str, end: str) -> CliResult:
        return self.run(
            ["calendar", "+agenda", "--start", start, "--end", end,
             "--format", "json"],
        )

    def search_minutes(self, start: str, end: str, page_size: int = 10) -> CliResult:
        return self.run(
            ["minutes", "+search", "--start", start, "--end", end,
             "--page-size", str(page_size), "--format", "json"],
        )

    def get_minute_detail(self, minute_token: str) -> CliResult:
        return self.run(
            ["minutes", "minutes", "get", "--token", minute_token,
             "--format", "json"],
        )

    def list_approval_instances(self, status: str = "pending",
                                 page_size: int = 10) -> CliResult:
        import json
        params = json.dumps({"status": status, "page_size": page_size})
        return self.run(
            ["approval", "instances", "initiated", "--as", "user",
             "--params", params],
        )

    # ── V1.17 P1: 日历参会人 ─────────────────────────────────

    def list_event_attendees(self, calendar_id: str = "primary",
                              event_id: str = "") -> CliResult:
        """获取日程参与人列表 (P1 C4)。"""
        import json
        params = json.dumps({
            "calendar_id": calendar_id,
            "event_id": event_id,
        })
        return self.run(
            ["calendar", "event.attendees", "list",
             "--params", params, "--as", "user"],
        )

    # ── V1.19: 资源下载 ────────────────────────────────────────────

    def download_resource(self, message_id: str, file_key: str,
                          output_path: str, file_type: str = "file") -> CliResult:
        """V1.19: 下载消息中的文件/图片资源。

        Args:
            message_id: 消息 ID (om_xxx)。
            file_key: 文件 key（从消息 content JSON 中提取）。
            output_path: 输出文件路径。
            file_type: "file" 或 "image"。
        """
        return self.run(
            ["im", "+resource", "--message-id", message_id,
             "--file-key", file_key, "--type", file_type,
             "--output", output_path],
        )

    def _parse_json(self, stdout: str) -> Any | None:
        """Parse stdout as JSON and return None when parsing fails."""
        import logging
        text = stdout.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logging.getLogger(__name__).warning(
                "lark-cli JSON parse failed (first 100 chars): %s... — %s",
                text[:100], e)
            return None


def _extract_doc_token(doc: str) -> str:
    """V1.12: 从飞书文档 URL 中提取 doc token。

    支持格式:
    - https://xxx.feishu.cn/docx/Abc123 → Abc123
    - https://xxx.feishu.cn/docs/Abc123 → Abc123
    - doc_xxx → 原样返回
    - Abc123 (纯 token) → 原样返回
    """
    if not doc:
        return doc
    # 从 URL 中提取 token
    for prefix in ("/docx/", "/docs/"):
        if prefix in doc:
            token = doc.split(prefix, 1)[-1]
            # 去掉 query string 和 fragment
            token = token.split("?")[0].split("#")[0]
            return token.strip()
    return doc


    # ── V1.13 日历/会议/审批 ───────────────────────────────────

    def list_calendar_events(self, start: str, end: str) -> CliResult:
        return self.run(
            ["calendar", "+agenda", "--start", start, "--end", end,
             "--format", "json"],
        )

    def search_minutes(self, start: str, end: str, page_size: int = 10) -> CliResult:
        return self.run(
            ["minutes", "+search", "--start", start, "--end", end,
             "--page-size", str(page_size), "--format", "json"],
        )

    def get_minute_detail(self, minute_token: str) -> CliResult:
        return self.run(
            ["minutes", "minutes", "get", "--token", minute_token,
             "--format", "json"],
        )

    def list_approval_instances(self, status: str = "pending",
                                 page_size: int = 10) -> CliResult:
        import json
        params = json.dumps({"status": status, "page_size": page_size})
        return self.run(
            ["approval", "instances", "initiated", "--as", "user",
             "--params", params],
        )


def compose_at_mention(open_id: str, display_name: str = "") -> str:
    """Compose a Feishu @mention XML tag for embedding in message content.

    Feishu IM API renders <at user_id="...">Name</at> as clickable user
    mentions in both text and markdown messages.

    Args:
        open_id: Feishu open_id of the user (ou_xxx).
        display_name: Human-readable name. Falls back to open_id when empty.

    Returns:
        XML snippet like: <at user_id="ou_xxx">张三</at>
    """
    name = display_name or open_id
    return f'<at user_id="{open_id}">{name}</at>'
