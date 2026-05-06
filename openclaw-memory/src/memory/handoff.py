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
    "pattern": "协作模式与交接风险",
}


def generate_handoff(project_id: str, items: Iterable[MemoryItem]) -> str:
    """Render a Markdown handoff summary for a project."""
    grouped: dict[str, list[MemoryItem]] = defaultdict(list)
    items_list = list(items)
    for item in items_list:
        if item.project_id == project_id:
            grouped[item.state_type].append(item)
    lines = [f"# 中断续办交接摘要：{project_id}", ""]
    for state_type, title in SECTION_TITLES.items():
        lines.append(f"## {title}")
        if state_type == "pattern":
            from memory.pattern_memory import generate_all_patterns
            patterns = generate_all_patterns(items_list, project_id)
            if patterns:
                for p in patterns:
                    lines.append(f"- **[{p.pattern_type}]** {p.summary}")
                    lines.append(f"  - 置信度：{p.confidence:.2f}")
                    srcs = [mid[:20] for mid in p.source_memory_ids[:3]]
                    lines.append(f"  - 来源记忆：{', '.join(srcs)}...")
            else:
                lines.append("- 暂无明确的协作模式。")
            lines.append("")
            continue
        state_items = grouped.get(state_type, [])
        if not state_items:
            lines.extend(["- 暂无明确状态。", ""])
            continue
        for item in state_items:
            ds = getattr(item, "decision_strength", "")
            rs = getattr(item, "review_status", "")
            strength_label = f" [{ds}]" if ds else ""
            review_label = " ⚠️待审核" if rs == "needs_review" else ""
            lines.append(f"- **{item.current_value}**{strength_label}{review_label}")
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
        sender = f"{ref.sender_name}: " if ref.sender_name else ""
        url_hint = f" ({ref.source_url})" if ref.source_url else ""
        chunks.append(f"{sender}“{ref.excerpt}”{url_hint}")
    return "；".join(chunks)
