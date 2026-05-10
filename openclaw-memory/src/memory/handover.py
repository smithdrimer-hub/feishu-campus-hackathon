"""Handover brief for new team members — a one-page project snapshot.

V1.18: More concise than the full handoff summary. Designed for rapid
onboarding: one-sentence status, key people, decision timeline, blockers,
this week's priorities, and risk alerts.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from memory.schema import MemoryItem


def render_handover_brief(
    items: list[MemoryItem],
    project_id: str = "",
    project_title: str = "",
) -> str:
    """Generate a one-page handover brief for new team members."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    title = project_title or project_id

    by_type: dict[str, list[MemoryItem]] = defaultdict(list)
    for item in items:
        by_type[item.state_type].append(item)

    lines = [
        f"# 项目交接包 — {title}",
        f"*生成时间: {now}*",
        "",
    ]

    # ── 一句话现状 ──
    goals = by_type.get("project_goal", [])
    if goals:
        lines.append("## 一句话现状")
        lines.append(goals[0].current_value[:200])
        lines.append("")

    # ── 关键人物 ──
    owners = by_type.get("owner", [])
    if owners:
        lines.append("## 关键人物")
        seen = set()
        for o in owners[:8]:
            name = o.current_value[:30]
            if name in seen:
                continue
            seen.add(name)
            tasks = [i for i in items
                     if i.state_type == "next_step" and i.owner == name]
            lines.append(f"- {name}：{len(tasks)} 个活跃任务")
        lines.append("")

    # ── 决策 Timeline ──
    from memory.project_state import render_decision_timeline
    decisions = [i for i in items if i.state_type == "decision"]
    if decisions:
        lines.append("## 最近决策")
        lines.append(render_decision_timeline(
            items, None, project_id,
        ).split("\n", 4)[-1] if "\n" in render_decision_timeline(items, None, project_id) else "")
        lines.append("")

    # ── 当前阻塞 ──
    blockers = by_type.get("blocker", [])
    unresolved = []
    for b in blockers:
        meta = getattr(b, "metadata", None) or {}
        if meta.get("blocker_status", "open") not in ("resolved", "obsolete"):
            unresolved.append(b)

    if unresolved:
        lines.append(f"## 当前阻塞（{len(unresolved)} 个）")
        for b in unresolved[:5]:
            meta = getattr(b, "metadata", None) or {}
            dep = meta.get("dependency_owner", "")
            dep_hint = f"（依赖：{dep}）" if dep else ""
            bs = meta.get("blocker_status", "open")
            tag = {"acknowledged": "[已接]", "waiting_external": "[等外部]"}.get(bs, "")
            lines.append(f"- {b.current_value[:100]}{dep_hint} {tag}")
        lines.append("")

    # ── 本周重点 ──
    nexts = by_type.get("next_step", [])
    if nexts:
        lines.append(f"## 本周重点（{len(nexts)} 个任务）")
        for n in nexts[:8]:
            owner = n.owner or "(未分配)"
            meta = getattr(n, "metadata", None) or {}
            ts = meta.get("task_status", "")
            done = " ✅" if ts == "completed" else ""
            lines.append(f"- {owner}: {n.current_value[:80]}{done}")
        lines.append("")

    # ── 风险提示 ──
    from memory.pattern_memory import generate_blocker_hotspot, generate_all_patterns
    hotspots = generate_blocker_hotspot(items, project_id)
    if hotspots:
        lines.append("## 风险提示")
        for h in hotspots[:2]:
            lines.append(f"- {h.summary[:150]}")
        lines.append("")

    # ── 证据索引 ──
    lines.append("---")
    lines.append("*所有结论均可点击跳转原始飞书消息。运行 `审核台` 查看详细证据。*")

    return "\n".join(lines)
