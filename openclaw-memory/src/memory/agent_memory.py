"""Agent Memory Document generator.

Produces a comprehensive Markdown document — a "memory pack" that
a new AI agent (or human) can read to instantly understand project
state without scanning chat history.

Usage:
    from memory.agent_memory import build_agent_memory_doc
    doc_md = build_agent_memory_doc("aurora-sprint", store, engine=engine)
    result = adapter.create_doc("Agent Memory Pack — aurora-sprint", doc_md)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from memory.schema import MemoryItem


def build_agent_memory_doc(
    project_id: str,
    store: Any,
    engine: Any = None,
) -> str:
    """Generate a full Agent Memory Pack as Markdown.

    Aggregates data from 5 existing sources into 11 sections.
    Returns a Markdown string ready for Feishu doc creation.
    """
    items = store.list_items(project_id)
    history = store.list_history(project_id)
    vector_store = getattr(engine, "vector_store", None) if engine else None

    sections: list[str] = []

    # 1. 项目概览
    sections.append(_section_overview(project_id, items))

    # 2. 团队
    sections.append(_section_team(items))

    # 3. 目标
    sections.append(_section_goal(items))

    # 4. 决策记录
    sections.append(_section_decisions(items))

    # 5. 任务清单
    sections.append(_section_tasks(items))

    # 6. 阻塞看板 + 依赖图
    sections.append(_section_blockers(project_id, items))

    # 7. 截止日期
    sections.append(_section_deadlines(items))

    # 8. 协作模式
    sections.append(_section_patterns(items, project_id))

    # 9. 待确认项
    sections.append(_section_needs_review(items))

    # 10. 近期变更
    sections.append(_section_recent_changes(history, project_id))

    # 11. 语义索引
    sections.append(_section_semantic_index(items, project_id, vector_store))

    # 页脚
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sections.append(
        f"---\n*由 OpenClaw Memory Engine 生成 · {now} · "
        f"{len(items)} 条活跃记忆 · 每条可追溯原始飞书消息*"
    )

    return "\n\n".join(s for s in sections if s)


# ── Section renderers ──────────────────────────────────────────────

def _section_overview(project_id: str, items: list) -> str:
    goals = [i for i in items if i.state_type == "project_goal"]
    title = goals[0].current_value[:120] if goals else project_id
    desc = goals[0].rationale[:200] if goals and getattr(goals[0], "rationale", "") else ""
    type_counts = {}
    for i in items:
        type_counts[i.state_type] = type_counts.get(i.state_type, 0) + 1
    counts_str = " · ".join(f"{t}:{c}" for t, c in sorted(type_counts.items()))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return (
        f"# Agent Memory Pack — {title}\n\n"
        f"**项目**: {project_id} | **生成时间**: {now} | **活跃记忆**: {len(items)}\n\n"
        f"**概述**: {desc}\n\n"
        f"**记忆分布**: {counts_str}"
    )


def _section_team(items: list) -> str:
    owners = [i for i in items if i.state_type == "owner"]
    members = [i for i in items if i.state_type == "member_status"]
    if not owners and not members:
        return ""
    lines = ["## 团队"]
    if owners:
        lines.append("\n**负责人**:")
        seen = set()
        for o in owners:
            name = o.owner or (o.source_refs[0].sender_name if o.source_refs else "?")
            if name not in seen and len(name) >= 2:
                seen.add(name)
                lines.append(f"- {name}: {o.current_value[:80]}")
    if members:
        lines.append("\n**成员状态**:")
        for m in members:
            sender = m.source_refs[0].sender_name if m.source_refs else "?"
            val = m.current_value[:80]
            lines.append(f"- {sender}: {val}")
    return "\n".join(lines)


def _section_goal(items: list) -> str:
    goals = [i for i in items if i.state_type == "project_goal"]
    if not goals:
        return ""
    return f"## 项目目标\n\n{goals[0].current_value}"


def _section_decisions(items: list) -> str:
    decisions = [i for i in items if i.state_type == "decision"]
    if not decisions:
        return ""
    lines = ["## 决策记录\n"]
    lines.append("| 决策 | 强度 | 置信度 | 来源 |")
    lines.append("|------|------|--------|------|")
    for d in decisions[:20]:
        val = d.current_value[:100].replace("|", "/")
        ds = getattr(d, "decision_strength", "") or "-"
        conf = f"{d.confidence:.0%}"
        sender = d.source_refs[0].sender_name if d.source_refs else "?"
        lines.append(f"| {val} | {ds} | {conf} | {sender} |")
    return "\n".join(lines)


def _section_tasks(items: list) -> str:
    tasks = [i for i in items if i.state_type == "next_step"]
    if not tasks:
        return ""
    lines = ["## 任务清单\n"]
    lines.append("| 任务 | 负责人 | 状态 |")
    lines.append("|------|--------|------|")
    for t in tasks[:20]:
        val = t.current_value[:100].replace("|", "/")
        owner = t.owner or "-"
        meta = getattr(t, "metadata", {}) or {}
        ts = meta.get("task_status", t.status)
        lines.append(f"| {val} | {owner} | {ts} |")
    return "\n".join(lines)


def _section_blockers(project_id: str, items: list) -> str:
    blockers = [i for i in items if i.state_type == "blocker"]
    if not blockers:
        return ""
    lines = ["## 阻塞看板\n"]
    # List
    for b in blockers[:10]:
        meta = getattr(b, "metadata", {}) or {}
        bs = meta.get("blocker_status", "open")
        age = _blocker_age_days(b)
        dep = meta.get("dependency_owner", "")
        extra = f" [{bs}]" if bs != "open" else ""
        extra += f" {age}d" if age > 0 else ""
        extra += f" →依赖{dep}" if dep else ""
        lines.append(f"- {b.current_value[:120]}{extra}")
    # Dependency graph summary
    try:
        from memory.orchestrator import build_dependency_graph, orchestrate
        graph = build_dependency_graph(items)
        edges = graph.get("edges", [])
        # F4: 只展示有明确 resolver 的依赖边
        resolved_edges = [e for e in edges if e.get("potential_resolver")]
        if resolved_edges:
            lines.append(f"\n**依赖链** ({len(resolved_edges)} 条):")
            for e in resolved_edges[:10]:
                lines.append(
                    f"- {e['blocked_person']} 被阻塞 "
                    f"→ 需 {e['potential_resolver']} 处理 "
                    f"(影响 {e['downstream_count']} 个下游)"
                )
        plan = orchestrate(project_id, items)
        bs = plan.blocker_summary
        if bs:
            lines.append(f"\n**统计**: 活跃 {bs.get('total_active_blockers',0)} · "
                         f"超3天 {bs.get('stale_blockers_gt_3d',0)} · "
                         f"DDL紧迫 {bs.get('urgent_ddl_within_3d',0)}")
    except Exception:
        pass
    return "\n".join(lines)


def _blocker_age_days(item) -> int:
    raw = getattr(item, "recorded_at", "") or ""
    if not raw:
        return 0
    try:
        recorded = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if recorded.tzinfo is None:
            recorded = recorded.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - recorded).days)
    except (ValueError, TypeError):
        return 0


def _section_deadlines(items: list) -> str:
    deadlines = [i for i in items if i.state_type == "deadline"]
    if not deadlines:
        return ""
    lines = ["## 截止日期\n"]
    for d in deadlines[:10]:
        lines.append(f"- {d.current_value[:200]}")
    return "\n".join(lines)


def _section_patterns(items: list, project_id: str) -> str:
    try:
        from memory.pattern_memory import generate_all_patterns
        patterns = generate_all_patterns(items, project_id)
    except Exception:
        return ""
    if not patterns:
        return ""
    lines = ["## 协作模式\n"]
    for p in patterns[:10]:
        lines.append(f"- [{p.pattern_type}] {p.summary[:200]}")
    return "\n".join(lines)


def _section_needs_review(items: list) -> str:
    nr = [i for i in items if getattr(i, "review_status", "") == "needs_review"]
    if not nr:
        return ""
    lines = ["## 待确认项\n"]
    for item in nr[:10]:
        reason = getattr(item, "status_reason", "") or ""
        conf = f" (置信度: {item.confidence:.0%})"
        lines.append(f"- [{item.state_type}] {item.current_value[:150]}{conf}")
        if reason:
            lines.append(f"  *原因: {reason[:120]}*")
    return "\n".join(lines)


def _section_recent_changes(history: list, project_id: str) -> str:
    if not history:
        return ""
    try:
        from memory.handoff import _collect_recent_invalidated
        invalidated = _collect_recent_invalidated(history, project_id)
    except Exception:
        return ""
    if not invalidated:
        return ""
    labels = {"corrected": "已纠正", "expired": "已过期",
              "forgotten": "已遗忘", "superseded": "已替代"}
    lines = ["## 近期变更（7天内）\n"]
    for item in invalidated[:10]:
        label = labels.get(item.status, item.status)
        who = getattr(item, "status_changed_by", "") or ""
        who_str = f" ({who})" if who else ""
        lines.append(f"- [{label}]{who_str} {item.current_value[:150]}")
    return "\n".join(lines)


def _section_semantic_index(items: list, project_id: str,
                             vector_store: Any = None) -> str:
    if not vector_store or not getattr(vector_store, "available", False):
        return ""
    lines = ["## 语义索引\n"]
    lines.append(f"向量存储: 可用 ({getattr(vector_store, 'collection_name', 'memories')})")
    # Generate sample search results for key terms
    key_terms = ["目标", "阻塞", "决策", "负责人", "截止", "DDL"]
    for term in key_terms:
        try:
            results = vector_store.search(term, project_id=project_id, top_k=2)
            if results:
                ids = [f"{mid[:12]}...({score:.2f})" for mid, score in results]
                lines.append(f"- 搜索 `{term}`: {', '.join(ids)}")
        except Exception:
            pass
    return "\n".join(lines)
