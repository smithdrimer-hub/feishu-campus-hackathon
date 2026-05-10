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


# ── V1.18: 可审计周报 ──────────────────────────────────────────

def render_weekly_report(
    items: list[MemoryItem],
    project_id: str = "",
    days: int = 7,
) -> str:
    """Generate an evidence-backed weekly report draft.

    Unlike the daily morning report (current snapshot), this shows what
    CHANGED in the past N days. Every conclusion links to source evidence.
    No evidence → not included in the report.

    Args:
        items: Active memory items.
        project_id: Project identifier.
        days: Look-back window (default 7).

    Returns:
        Markdown weekly report draft.
    """
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    now = datetime.now(timezone.utc)
    start_date = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")

    # Filter items updated within the time window
    recent = [i for i in items if i.updated_at and i.updated_at > cutoff]
    by_type: dict[str, list[MemoryItem]] = defaultdict(list)
    for item in items:
        by_type[item.state_type].append(item)

    lines = [
        f"# 本周汇报草稿 — {project_id or ''}",
        f"*{start_date} ~ {end_date} | 生成时间: {now.strftime('%Y-%m-%d %H:%M')}*",
        "",
    ]

    # ── 本周进展 ──
    progress_items = [i for i in recent
                      if i.state_type in ("next_step", "project_goal")
                      and i.status == "active"]
    if progress_items:
        lines.append("### 本周进展")
        for item in progress_items[:8]:
            ref = item.source_refs[0] if item.source_refs else None
            sender = ref.sender_name if ref and ref.sender_name else "?"
            time_str = (ref.created_at[:10] if ref and ref.created_at else "?")
            url = ref.source_url if ref and ref.source_url else ""
            evidence = f"[{sender} {time_str}]({url})" if url else f"{sender} {time_str}"
            owner_hint = f"（{item.owner}）" if item.owner else ""
            lines.append(f"- {item.current_value[:100]}{owner_hint} [{evidence}]")
        lines.append("")

    # ── 关键决策 ──
    from memory.project_state import render_decision_timeline
    decisions = by_type.get("decision", [])
    if decisions:
        lines.append("### 关键决策")
        lines.append(render_decision_timeline(items, None, project_id)
                     .split("\n", 4)[-1] if "\n" in render_decision_timeline(items, None, project_id) else "")
        lines.append("")

    # ── 当前阻塞 ──
    blockers = by_type.get("blocker", [])
    unresolved = []
    for b in blockers:
        meta = getattr(b, "metadata", None) or {}
        if meta.get("blocker_status", "open") not in ("resolved", "obsolete"):
            unresolved.append(b)
    if unresolved:
        lines.append(f"### 当前阻塞（{len(unresolved)} 个未解决）")
        for b in unresolved[:5]:
            ref = b.source_refs[0] if b.source_refs else None
            sender = ref.sender_name if ref and ref.sender_name else "?"
            dep = (getattr(b, "metadata", {}) or {}).get("dependency_owner", "")
            evidence = f"[{sender}]" if sender else ""
            dep_hint = f"（依赖：{dep}）" if dep else ""
            lines.append(f"- {b.current_value[:100]}{dep_hint} {evidence}")
        lines.append("")

    # ── 下周计划（活跃任务） ──
    tasks = by_type.get("next_step", [])
    if tasks:
        lines.append(f"### 下周计划（{len(tasks)} 个进行中任务）")
        for t in tasks[:8]:
            owner = t.owner or "未分配"
            meta = getattr(t, "metadata", None) or {}
            ts = meta.get("task_status", "")
            done = " ✅" if ts == "completed" else ""
            lines.append(f"- {owner}：{t.current_value[:80]}{done}")
        lines.append("")

    # ── 待确认事项 ──
    pending = [i for i in items
               if getattr(i, "review_status", "") == "needs_review"]
    if pending:
        lines.append(f"### 待确认事项（{len(pending)} 条）")
        for p in pending[:5]:
            ds = getattr(p, "decision_strength", "")
            ds_label = f" [{ds}]" if ds else ""
            ref = p.source_refs[0] if p.source_refs else None
            sender = ref.sender_name if ref and ref.sender_name else "?"
            lines.append(f"- {p.current_value[:80]}{ds_label}（{sender}）")
        lines.append("")

    # ── 证据索引 ──
    lines.append("---")
    lines.append("*所有结论均来自 confirmed/high-confidence MemoryItem，每条可点击跳转原始飞书消息。无证据来源的内容不进入正式汇报。*")

    return "\n".join(lines)
