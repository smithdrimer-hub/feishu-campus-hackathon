"""Generate non-executing action plans from Memory state."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from memory.schema import MemoryItem


@dataclass
class PlannedAction:
    """A proposed future action that must not execute automatically in V1."""

    action_type: str
    title: str
    reason: str
    command_hint: str
    requires_confirmation: bool = True

    def to_dict(self) -> dict:
        """Serialize a planned action to a JSON-compatible dict."""
        return asdict(self)


def generate_action_plan(project_id: str, items: Iterable[MemoryItem]) -> list[PlannedAction]:
    """Generate proposed next actions from current memory items."""
    relevant = [item for item in items if item.project_id == project_id]
    actions: list[PlannedAction] = []
    for item in relevant:
        if item.state_type == "next_step":
            actions.append(
                PlannedAction(
                    "create_task",
                    f"拟创建任务：{item.current_value[:80]}",
                    "Memory 中存在下一步任务，适合转成飞书任务。",
                    "lark-cli.cmd task +create ...",
                )
            )
        elif item.state_type == "blocker":
            actions.append(
                PlannedAction(
                    "send_message",
                    f"拟发送阻塞同步：{item.current_value[:80]}",
                    "Memory 中存在阻塞，需要在群内同步或请求协助。",
                    "lark-cli.cmd im +messages-send ...",
                )
            )
        elif item.state_type == "project_goal":
            actions.append(
                PlannedAction(
                    "create_doc",
                    "拟创建交接说明文档",
                    "当前项目目标已明确，适合生成可分享的交接文档。",
                    "lark-cli.cmd docs +create ...",
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
