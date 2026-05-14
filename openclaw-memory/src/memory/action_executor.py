"""Bridge between PlannedAction and actual LarkCliAdapter calls.

V1.14: Maps planned actions to executable adapter methods while
respecting the requires_confirmation safety flag. Does NOT implement
any trigger/scheduling logic — invoked explicitly by callers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from adapters.lark_cli_adapter import CliResult, LarkCliAdapter, compose_at_mention
from memory.action_planner import PlannedAction


@dataclass
class ExecutionResult:
    """Outcome of attempting to execute one PlannedAction."""

    action: PlannedAction
    success: bool
    cli_result: CliResult | None = None
    error: str = ""
    output_data: dict[str, Any] = field(default_factory=dict)


class ActionExecutor:
    """Execute PlannedAction instances through LarkCliAdapter.

    Usage:
        executor = ActionExecutor(adapter)
        results = executor.execute_plan(actions, context={
            "chat_id": "oc_xxx",
            "project_id": "my-project",
        })
    """

    def __init__(self, adapter: LarkCliAdapter, auto_confirm: bool = False) -> None:
        self.adapter = adapter
        self.auto_confirm = auto_confirm

    # ── Public API ───────────────────────────────────────────────

    def execute(
        self,
        action: PlannedAction,
        context: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Execute a single PlannedAction.

        Args:
            action: The planned action to execute.
            context: Dict with runtime data:
                - chat_id (str): Target chat for send_message.
                - owner_map (dict[str, str]): {name: open_id} for @mentions.
                - project_id (str): Current project identifier.
                - assignee_ids (list[str]): open_ids for task assignment.
                - task_guid (str): Task GUID for assign_task.
                - task_description (str): Longer body for create_task.
                - task_due_at (str): ISO-8601 deadline for create_task.
                - doc_content (str): Markdown body for create_doc.
                - msg_type (str): "text" or "markdown" for send_message.

        Returns:
            ExecutionResult with success/failure and any output data.
        """
        if action.requires_confirmation and not self.auto_confirm:
            return ExecutionResult(
                action=action,
                success=False,
                error="Requires manual confirmation (auto_confirm=False)",
            )

        ctx = context or {}

        dispatch = {
            "create_task": self._execute_create_task,
            "send_message": self._execute_send_message,
            "create_doc": self._execute_create_doc,
            "assign_task": self._execute_assign_task,
            "send_alert": self._execute_send_alert,
        }
        handler = dispatch.get(action.action_type)
        if handler is None:
            # sync_more_context and other non-executable actions
            return ExecutionResult(action=action, success=True)

        try:
            return handler(action, ctx)
        except Exception as exc:
            return ExecutionResult(action=action, success=False, error=str(exc))

    def execute_plan(
        self,
        actions: list[PlannedAction],
        context: dict[str, Any] | None = None,
    ) -> list[ExecutionResult]:
        """Execute multiple actions sequentially.

        Actions blocked by requires_confirmation are skipped rather
        than failing the whole plan. The caller inspects each result.
        """
        return [self.execute(action, context) for action in actions]

    # ── Internal dispatch implementations ─────────────────────────

    def _execute_create_task(
        self, action: PlannedAction, ctx: dict[str, Any],
    ) -> ExecutionResult:
        summary = action.title
        for prefix in ("拟创建任务：", "拟创建任务:"):
            if summary.startswith(prefix):
                summary = summary[len(prefix):]
                break
        summary = summary[:200]

        description = str(ctx.get("task_description", action.reason))
        due_at = str(ctx.get("task_due_at", ""))

        result = self.adapter.create_task(
            summary=summary, description=description, due_at=due_at,
        )

        task_guid = ""
        task_url = ""
        if result.data:
            inner = result.data.get("data", result.data)
            task_guid = str(inner.get("guid", "") or inner.get("task_guid", ""))
            task_url = str(inner.get("url", "") or inner.get("task_url", ""))

        # V1.15: 持久化 task_guid → summary 映射用于回流
        if task_guid:
            task_map_dir = Path(ctx.get("data_dir", "data"))
            task_map_dir.mkdir(parents=True, exist_ok=True)
            task_map_path = task_map_dir / "task_map.jsonl"
            entry = {
                "task_guid": task_guid,
                "summary": summary,
                "project_id": ctx.get("project_id", ""),
                "created_at": result.data.get("_timestamp", "") if result.data else "",
            }
            with task_map_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        return ExecutionResult(
            action=action,
            success=result.returncode == 0,
            cli_result=result,
            output_data={"task_guid": task_guid, "task_url": task_url, "summary": summary},
        )

    def _execute_send_message(
        self, action: PlannedAction, ctx: dict[str, Any],
    ) -> ExecutionResult:
        chat_id = str(ctx.get("chat_id", ""))
        if not chat_id:
            return ExecutionResult(
                action=action,
                success=False,
                error="'chat_id' is required in context for send_message",
            )

        content = action.title
        for prefix in ("拟发送阻塞同步：", "拟发送阻塞同步:"):
            if content.startswith(prefix):
                content = content[len(prefix):]
                break

        # Replace @name placeholders with Feishu <at> tags
        owner_map = ctx.get("owner_map", {}) or {}
        for name, open_id in owner_map.items():
            at_tag = compose_at_mention(open_id, name)
            content = content.replace(f"@{name}", at_tag)
            # Also try without @ prefix
            content = content.replace(name, at_tag, 1) if at_tag in content else content

        content = content[:2000]

        # FEAT-7: R4确认提问 → 发送交互式卡片替代纯文本
        is_confirm = action.metadata.get("is_confirmation_question", False)
        if is_confirm:
            from memory.card_renderer import render_confirmation_card
            from memory.reply_handler import record_question
            import json as _json
            owner = action.metadata.get("candidate_owner", action.target_owner)
            time_hint = ""
            identity_key = (
                action.metadata.get("candidate_identity_keys", [None])[0]
                or action.idempotency_key
            )
            candidate_count = action.metadata.get("candidate_count", 1)
            card = render_confirmation_card(
                owner=owner,
                item_text=content,
                time_hint=time_hint,
                identity_key=identity_key,
                candidate_count=candidate_count,
            )
            card_json = _json.dumps(card, ensure_ascii=False)
            result = self.adapter.send_message(
                chat_id=chat_id, content=card_json, msg_type="interactive",
            )
            msg_id = ""
            if result.data:
                inner = result.data.get("data", result.data)
                msg_id = str(inner.get("message_id", ""))
            if msg_id:
                candidates = action.metadata.get("candidate_count", 0)
                candidate_keys = action.metadata.get("candidate_identity_keys", [])
                record_question(msg_id, [str(candidates)],
                                ctx.get("project_id", ""),
                                candidate_identity_keys=candidate_keys)
            return ExecutionResult(
                action=action,
                success=result.returncode == 0,
                cli_result=result,
                output_data={"message_id": msg_id},
            )

        msg_type = str(ctx.get("msg_type", "text"))
        result = self.adapter.send_message(
            chat_id=chat_id, content=content, msg_type=msg_type,
        )

        msg_id = ""
        if result.data and not is_confirm:
            inner = result.data.get("data", result.data)
            msg_id = str(inner.get("message_id", ""))

        return ExecutionResult(
            action=action,
            success=result.returncode == 0,
            cli_result=result,
            output_data={"message_id": msg_id},
        )

    def _execute_send_alert(
        self, action: PlannedAction, ctx: dict[str, Any],
    ) -> ExecutionResult:
        """Send an alert/notification to the group chat.

        FEAT-4a: Delegates to _execute_send_message since send_alert is
        semantically identical to a group notification — the distinct
        action_type exists for audit trail and trigger-rule provenance.
        Previously send_alert was missing from the dispatch dict, causing
        R2/R3/R4/R5 proposals to be silently dropped.
        """
        return self._execute_send_message(action, ctx)

    def _execute_create_doc(
        self, action: PlannedAction, ctx: dict[str, Any],
    ) -> ExecutionResult:
        title = action.title
        content = str(ctx.get("doc_content", action.reason))

        result = self.adapter.create_doc(title=title, content=content)

        doc_token = ""
        doc_url = ""
        if result.data:
            inner = result.data.get("data", result.data)
            doc_token = str(inner.get("doc_id", "") or inner.get("document_id", ""))
            doc_url = str(inner.get("doc_url", "") or inner.get("url", ""))

        return ExecutionResult(
            action=action,
            success=result.returncode == 0,
            cli_result=result,
            output_data={"doc_token": doc_token, "url": doc_url},
        )

    def _execute_assign_task(
        self, action: PlannedAction, ctx: dict[str, Any],
    ) -> ExecutionResult:
        task_guid = str(ctx.get("task_guid", ""))
        if not task_guid:
            return ExecutionResult(
                action=action,
                success=False,
                error="'task_guid' is required in context for assign_task",
            )

        assignee_ids = ctx.get("assignee_ids", []) or []
        if not assignee_ids:
            return ExecutionResult(
                action=action,
                success=False,
                error="'assignee_ids' is required in context for assign_task",
            )

        result = self.adapter.assign_task(task_guid, assignee_ids)
        return ExecutionResult(
            action=action,
            success=result.returncode == 0,
            cli_result=result,
        )
