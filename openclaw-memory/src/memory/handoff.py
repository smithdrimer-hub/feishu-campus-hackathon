"""Generate a handoff summary from current Memory state."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from memory.schema import MemoryItem, SourceRef


SECTION_TITLES = {
    "project_goal": "当前项目目标",
    "owner": "当前负责人",
    "decision": "当前关键决策",
    "deadline": "截止时间与期限",
    "deferred": "重要暂缓事项及原因",
    "blocker": "当前阻塞与风险",
    "next_step": "建议下一步",
    "member_status": "成员状态与可用性",
}


def generate_handoff(project_id: str, items: Iterable[MemoryItem]) -> str:
    """Render a Markdown handoff summary for a project."""
    grouped: dict[str, list[MemoryItem]] = defaultdict(list)
    for item in items:
        if item.project_id == project_id:
            grouped[item.state_type].append(item)
    lines = [f"# 中断续办交接摘要：{project_id}", ""]
    for state_type, title in SECTION_TITLES.items():
        lines.append(f"## {title}")
        state_items = grouped.get(state_type, [])
        if not state_items:
            lines.extend(["- 暂无明确状态。", ""])
            continue
        for item in state_items:
            lines.append(f"- **{item.current_value}**")
            lines.append(f"  - 依据：{item.rationale}")
            lines.append(f"  - 置信度：{item.confidence:.2f}，版本：v{item.version}")
            lines.append(f"  - 证据：{_render_refs(item.source_refs)}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _render_refs(refs: list[SourceRef]) -> str:
    """Render source references as compact evidence anchors."""
    if not refs:
        return "无"
    chunks = []
    for ref in refs:
        chunks.append(f"{ref.chat_id}/{ref.message_id} @ {ref.created_at}: “{ref.excerpt}”")
    return "；".join(chunks)
