"""Generate action plans and trigger proposals from Memory state.

V1.14: 新增 ActionProposal 数据结构，用于触发引擎生成可审计的动作提案。
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from memory.schema import MemoryItem


# ── ActionProposal (V1.14 触发引擎) ────────────────────────────

@dataclass
class ActionProposal:
    """A trigger-generated action proposal with evidence and risk level.

    Unlike PlannedAction (which is a simple human hint), ActionProposal
    carries enough structured data for the trigger engine to make
    automated decisions about execution, deduplication, and audit.
    """

    action_type: str          # create_task / send_alert / create_handoff_doc
    title: str                # 人类可读标题
    reason: str               # 为什么触发（触发规则名称）
    confidence: float         # 0-1
    risk_level: str           # low / medium / high
    requires_confirmation: bool = True
    idempotency_key: str = ""  # 防重复键（可通过 make_idempotency_key 计算）
    target_chat_id: str = ""
    target_owner: str = ""
    target_owner_open_id: str = ""
    evidence_refs: list[dict[str, str]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def make_idempotency_key(rule: str, project_id: str, summary: str) -> str:
        digest = hashlib.sha1(
            f"{rule}:{project_id}:{summary}".encode("utf-8")
        ).hexdigest()[:16]
        return f"ap_{digest}"

    def to_dict(self) -> dict:
        return asdict(self)


# ── PlannedAction (V1.14 execution layer) ───────────────────────

@dataclass
class PlannedAction:
    """A proposed future action that must not execute automatically in V1."""

    action_type: str
    title: str
    reason: str
    command_hint: str
    requires_confirmation: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize a planned action to a JSON-compatible dict."""
        return asdict(self)


def generate_action_plan(project_id: str, items: Iterable[MemoryItem]) -> list[PlannedAction]:
    """Generate proposed next actions from current memory items."""
    relevant = [item for item in items if item.project_id == project_id]
    actions: list[PlannedAction] = []
    for item in relevant:
        if item.state_type == "next_step":
            metadata: dict[str, Any] = {}
            if item.owner:
                metadata["potential_assignee"] = item.owner
            actions.append(
                PlannedAction(
                    "create_task",
                    f"拟创建任务：{item.current_value[:80]}",
                    "Memory 中存在下一步任务，适合转成飞书任务。",
                    "lark-cli.cmd task +create ...",
                    metadata=metadata,
                )
            )
        elif item.state_type == "blocker":
            metadata = {"requires_attention": "true"}
            if item.owner:
                metadata["alert_target"] = item.owner
            actions.append(
                PlannedAction(
                    "send_message",
                    f"拟发送阻塞同步：{item.current_value[:80]}",
                    "Memory 中存在阻塞，需要在群内同步或请求协助。",
                    "lark-cli.cmd im +messages-send ...",
                    metadata=metadata,
                )
            )
        elif item.state_type == "project_goal":
            actions.append(
                PlannedAction(
                    "create_doc",
                    "拟创建交接说明文档",
                    "当前项目目标已明确，适合生成可分享的交接文档。",
                    "lark-cli.cmd docs +create ...",
                    metadata={"doc_purpose": "handoff_summary"},
                )
            )
    if not actions:
        actions.append(
            PlannedAction(
                "sync_more_context",
                "拟继续同步更多群消息",
                "当前 Memory 中可行动状态不足。",
                "lark-cli.cmd im +chat-messages-list ...",
                requires_confirmation=False,
            )
        )
    return actions


def render_action_plan(project_id: str, actions: Iterable[PlannedAction]) -> str:
    """Render planned actions as Markdown without executing anything."""
    lines = [f"# 下一步行动计划：{project_id}", ""]
    for index, action in enumerate(actions, start=1):
        lines.append(f"{index}. **{action.title}**")
        lines.append(f"   - 类型：{action.action_type}")
        lines.append(f"   - 原因：{action.reason}")
        lines.append(f"   - 命令提示：`{action.command_hint}`")
        lines.append(f"   - 是否需要确认：{str(action.requires_confirmation).lower()}")
    return "\n".join(lines).strip() + "\n"
