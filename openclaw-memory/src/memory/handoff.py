"""Generate a handoff summary from current Memory state."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from memory.schema import MemoryItem, SourceRef


SECTION_TITLES = {
    "project_goal": "当前项目目标",
    "owner": "当前负责人",
    "decision": "决策时间线",
    "deadline": "截止时间与期限",
    "deferred": "重要暂缓事项及原因",
    "blocker": "当前阻塞与风险",
    "next_step": "建议下一步",
    "member_status": "成员状态与可用性",
    "pattern": "协作模式与交接风险",
}


def generate_handoff(project_id: str, items: Iterable[MemoryItem],
                     history_items: Iterable[MemoryItem] | None = None,
                     store=None) -> str:
    """Render a Markdown handoff summary for a project.

    V1.19 P0-C: 可选传入 history_items 以展示近期失效记忆。
    V1.19: 可选传入 store，在生成摘要前执行一次 maintenance。
    """
    if store is not None:
        try:
            store.maintenance()
        except Exception:
            pass

    grouped: dict[str, list[MemoryItem]] = defaultdict(list)
    items_list = list(items)
    for item in items_list:
        if item.project_id == project_id:
            grouped[item.state_type].append(item)
    lines = [f"# 中断续办交接摘要：{project_id}", ""]
    for state_type, title in SECTION_TITLES.items():
        lines.append(f"## {title}")
        if state_type == "decision":
            from memory.project_state import render_decision_timeline
            history = items_list  # decisions in active already
            lines.append(render_decision_timeline(
                [i for i in items_list if i.state_type == "decision"],
                None, project_id))
            lines.append("")
            continue
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

    # V1.19 P0-C: 待确认记忆（需要接手人关注的）
    needs_review_items = [i for i in items_list
                          if i.project_id == project_id
                          and i.review_status == "needs_review"]
    if needs_review_items:
        lines.append("## ⚠️ 待确认记忆（接手人请重点关注）")
        lines.append("")
        lines.append("以下记忆的置信度较低或已超过复查周期，需要你来确认是否仍然有效：")
        lines.append("")
        for item in needs_review_items:
            strength = f"[{item.decision_strength}] " if item.decision_strength else ""
            lines.append(f"- **{item.current_value}** {strength}— {item.state_type}")
            if item.status_reason:
                lines.append(f"  - 标记原因：{item.status_reason}")
            lines.append(f"  - 置信度：{item.confidence:.2f}")
            lines.append(f"  - 证据：{_render_refs(item.source_refs)}")
        lines.append("")

    # V1.19 P0-C: 近期失效记忆
    if history_items:
        invalidated = _collect_recent_invalidated(history_items, project_id)
        if invalidated:
            lines.append("## 近期纠正/过期/遗忘的记忆")
            lines.append("")
            for item in invalidated:
                status_label = {
                    "corrected": "🔧 已纠正", "expired": "⏰ 已过期",
                    "forgotten": "🗑️ 已遗忘", "superseded": "🔄 已替代",
                }.get(item.status, item.status)
                lines.append(f"- {status_label}: **{item.current_value}**")
                if item.status_reason:
                    lines.append(f"  - 原因：{item.status_reason}")
                if item.status_changed_by and item.status_changed_by != "system":
                    lines.append(f"  - 操作者：{item.status_changed_by}")
                lines.append(f"  - 证据：{_render_refs(item.source_refs)}")
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def _collect_recent_invalidated(
    history: Iterable[MemoryItem], project_id: str,
    days: int = 7,
) -> list[MemoryItem]:
    """从 history 中收集最近 N 天内失效的记忆。"""
    from datetime import datetime, timedelta, timezone as tz
    cutoff = (datetime.now(tz.utc) - timedelta(days=days)).isoformat()
    results = []
    for item in history:
        if item.project_id != project_id:
            continue
        if item.status in ("corrected", "expired", "forgotten"):
            changed_at = item.status_changed_at or item.valid_to or ""
            if changed_at >= cutoff:
                results.append(item)
    results.sort(key=lambda i: i.status_changed_at or i.valid_to or "", reverse=True)
    return results


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
