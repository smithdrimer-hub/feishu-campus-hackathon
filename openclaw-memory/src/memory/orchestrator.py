"""Team Work Orchestrator: 基于 Memory 状态自动编排全组最优行动序列。

核心理念：
  Memory Engine 知道每个人的状态（在做什么、被什么卡住、卡住了谁）。
  Orchestrator 分析依赖链，找到"拉开堵塞口"的最优顺序，
  让环环相扣的阻塞像多米诺骨牌一样依次解决。

算法：
  1. 构建依赖图：谁在等谁、谁能解除谁的阻塞
  2. 拓扑排序：找出"先做哪件事能解锁最多下游"
  3. 生成编排方案：按人分配，附带优先级和理由
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from memory.schema import MemoryItem


@dataclass
class UnblockAction:
    """一个解除阻塞的行动建议。"""
    priority: int           # 1=最高
    assignee: str           # 谁来做
    action: str             # 做什么
    unblocks: list[str]     # 解决后能解锁谁/什么
    reason: str             # 为什么优先
    evidence_msg: str = ""  # 来源消息摘要


@dataclass
class OrchestratedPlan:
    """全组编排方案。"""
    project_id: str
    generated_reason: str
    actions: list[UnblockAction] = field(default_factory=list)
    dependency_chains: list[dict[str, Any]] = field(default_factory=list)
    team_status_summary: dict[str, str] = field(default_factory=dict)


def build_dependency_graph(items: list[MemoryItem]) -> dict[str, Any]:
    """从 Memory Items 中构建依赖关系图。

    逻辑：
    - blocker 的 owner = 被阻塞的人
    - blocker 的 current_value 里可能提到"等XX"/"找XX" = 能解除阻塞的人
    - next_step 的 owner = 该做这件事的人
    - 如果某人的 next_step 依赖另一个 blocker 解除，形成链
    """
    blockers = [i for i in items if i.state_type == "blocker" and i.status == "active"]
    next_steps = [i for i in items if i.state_type == "next_step" and i.status == "active"]
    members = [i for i in items if i.state_type == "member_status"]
    owners = [i for i in items if i.state_type == "owner"]

    # Who is blocked — use sender as blocked person (they reported the blocker)
    blocked_people: dict[str, list[MemoryItem]] = {}
    for b in blockers:
        who = b.source_refs[0].sender_name if b.source_refs else b.owner
        if who:
            blocked_people.setdefault(who, []).append(b)

    # Who can unblock (heuristic: look for names in blocker text)
    all_names = set()
    for o in owners:
        if o.owner:
            all_names.add(o.owner)
        if o.current_value:
            all_names.add(o.current_value.split("负责")[0].strip() if "负责" in o.current_value else o.current_value)
    for ns in next_steps:
        if ns.owner:
            all_names.add(ns.owner)

    # Build edges: blocker -> who might resolve it
    edges: list[dict[str, Any]] = []
    for b in blockers:
        blocked_by = b.owner or "?"
        resolver = None
        text = b.current_value.lower()
        for name in all_names:
            if name and name in b.current_value and name != blocked_by:
                resolver = name
                break
        edges.append({
            "blocker_id": b.memory_id,
            "blocker_text": b.current_value[:80],
            "blocked_person": blocked_by,
            "potential_resolver": resolver,
            "downstream_count": sum(1 for ns in next_steps if ns.owner == blocked_by),
        })

    # Available people
    unavailable = set()
    for m in members:
        val = m.current_value.lower()
        if any(k in val for k in ("请假", "出差", "不在", "休息")):
            who = m.owner or (m.source_refs[0].sender_name if m.source_refs else None)
            if who:
                unavailable.add(who)

    return {
        "blockers": blockers,
        "next_steps": next_steps,
        "blocked_people": blocked_people,
        "edges": edges,
        "all_names": all_names,
        "unavailable": unavailable,
    }


def orchestrate(project_id: str, items: Iterable[MemoryItem]) -> OrchestratedPlan:
    """核心编排算法：找到最优解阻塞顺序。

    优先级规则：
    1. 解决后能解锁最多下游任务的阻塞 → 最高优先
    2. 已有明确 resolver 的阻塞 → 优先于无人认领的
    3. 不可用人员的任务 → 降级或转交
    """
    items_list = [i for i in items if i.project_id == project_id]
    graph = build_dependency_graph(items_list)

    actions: list[UnblockAction] = []
    priority = 1

    # Sort edges by downstream impact (descending)
    sorted_edges = sorted(graph["edges"], key=lambda e: e["downstream_count"], reverse=True)

    for edge in sorted_edges:
        resolver = edge["potential_resolver"]
        blocked = edge["blocked_person"]

        if resolver and resolver in graph["unavailable"]:
            # Resolver is unavailable, suggest reassignment
            available_people = graph["all_names"] - graph["unavailable"] - {blocked}
            alt = next(iter(available_people), None)
            actions.append(UnblockAction(
                priority=priority,
                assignee=alt or "待分配",
                action=f"代替{resolver}解决：{edge['blocker_text'][:50]}",
                unblocks=[blocked] + [f"+{edge['downstream_count']}个下游任务"],
                reason=f"{resolver}不可用（请假/出差），需要其他人接手",
                evidence_msg=edge["blocker_text"],
            ))
        elif resolver:
            actions.append(UnblockAction(
                priority=priority,
                assignee=resolver,
                action=f"解除阻塞：{edge['blocker_text'][:50]}",
                unblocks=[blocked] + [f"+{edge['downstream_count']}个下游任务"],
                reason=f"解决后可解锁{blocked}的{edge['downstream_count']}个下游任务",
                evidence_msg=edge["blocker_text"],
            ))
        else:
            actions.append(UnblockAction(
                priority=priority + 1,
                assignee=blocked,
                action=f"自行推动解决：{edge['blocker_text'][:50]}",
                unblocks=[f"{edge['downstream_count']}个下游任务"],
                reason="无明确resolver，需自行推动或寻求帮助",
                evidence_msg=edge["blocker_text"],
            ))
        priority += 1

    # Add non-blocked next_steps as lower priority
    blocked_names = set(graph["blocked_people"].keys())
    for ns in graph["next_steps"]:
        if ns.owner and ns.owner not in blocked_names and ns.owner not in graph["unavailable"]:
            actions.append(UnblockAction(
                priority=priority,
                assignee=ns.owner,
                action=f"继续推进：{ns.current_value[:50]}",
                unblocks=[],
                reason="无阻塞，正常推进",
                evidence_msg=ns.current_value,
            ))
            priority += 1

    # Team status summary
    team_summary = {}
    for name in graph["all_names"]:
        if not name:
            continue
        if name in graph["unavailable"]:
            team_summary[name] = "不可用（请假/出差）"
        elif name in blocked_names:
            team_summary[name] = f"被阻塞（{len(graph['blocked_people'][name])}个）"
        else:
            team_summary[name] = "可正常工作"

    # Dependency chains for visualization
    chains = []
    for edge in sorted_edges:
        if edge["downstream_count"] > 0:
            chains.append({
                "blocker": edge["blocker_text"][:40],
                "blocks": edge["blocked_person"],
                "downstream": edge["downstream_count"],
                "resolver": edge["potential_resolver"],
            })

    return OrchestratedPlan(
        project_id=project_id,
        generated_reason="基于当前阻塞依赖链分析，按解锁下游任务数量排序",
        actions=actions,
        dependency_chains=chains,
        team_status_summary=team_summary,
    )


def render_orchestrated_plan_text(plan: OrchestratedPlan) -> str:
    """渲染编排方案为 Markdown。"""
    lines: list[str] = []
    lines.append(f"🎯 团队任务编排方案 [{plan.project_id}]")
    lines.append(f"策略：{plan.generated_reason}")
    lines.append("")

    # Team status
    if plan.team_status_summary:
        lines.append("👥 团队当前状态")
        for name, status in sorted(plan.team_status_summary.items()):
            icon = "🔴" if "阻塞" in status else ("⚪" if "不可用" in status else "🟢")
            lines.append(f"  {icon} {name}：{status}")
        lines.append("")

    # Dependency chains
    if plan.dependency_chains:
        lines.append("🔗 阻塞依赖链")
        for chain in plan.dependency_chains[:5]:
            resolver_hint = f" → 需要{chain['resolver']}处理" if chain["resolver"] else ""
            lines.append(f"  [{chain['blocker']}] → 卡住{chain['blocks']}（影响{chain['downstream']}个下游）{resolver_hint}")
        lines.append("")

    # Actions
    if plan.actions:
        lines.append("📋 建议行动序列（按优先级）")
        by_person: dict[str, list[UnblockAction]] = {}
        for a in plan.actions:
            by_person.setdefault(a.assignee, []).append(a)

        for person, person_actions in sorted(by_person.items()):
            lines.append(f"")
            lines.append(f"  【{person}】")
            for a in sorted(person_actions, key=lambda x: x.priority):
                unblock_text = f" → 解锁: {', '.join(a.unblocks)}" if a.unblocks else ""
                lines.append(f"    P{a.priority}. {a.action}{unblock_text}")
                lines.append(f"        理由: {a.reason}")
        lines.append("")

    if not plan.actions:
        lines.append("✅ 当前无阻塞，团队运转顺畅！")

    return "\n".join(lines).strip() + "\n"
