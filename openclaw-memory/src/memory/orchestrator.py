"""Team Work Orchestrator: 基于 Memory 状态自动编排全组最优行动序列。

核心理念：
  Memory Engine 知道每个人的状态（在做什么、被什么卡住、卡住了谁）。
  Orchestrator 分析依赖链，找到"拉开堵塞口"的最优顺序，
  让环环相扣的阻塞像多米诺骨牌一样依次解决。

算法：
  1. 构建依赖图：谁在等谁、谁能解除谁的阻塞
  2. 加权排序：DDL紧迫度 + 阻塞年龄 + 下游影响
  3. 生成编排方案：按人分配独立可追踪的行动项
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from memory.schema import MemoryItem


def _normalize_name(name: str) -> str:
    """统一人名格式：'前端-吴凡' / '吴凡' / '吴凡(产品)' → '吴凡'。"""
    if not name:
        return ""
    n = name.strip()
    for prefix in ("前端-", "后端-", "测试-", "产品-", "设计-",
                   "前端 ", "后端 ", "测试 ", "产品 ", "设计 ",
                   "PM-", "QA-", "UI-", "UX-"):
        if n.startswith(prefix):
            n = n[len(prefix):]
            break
    for sep in ("(", "（"):
        if sep in n:
            n = n.split(sep, 1)[0]
    return n.strip()


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
    blocker_summary: dict[str, Any] = field(default_factory=dict)


# ── deadline urgency helpers ────────────────────────────────────

def _deadline_urgency_for_owner(
    owner: str, items: list[MemoryItem],
) -> int:
    """Return 0-3 urgency for the owner's most imminent deadline.

    0 = no deadline found
    1 = deadline within 7 days
    2 = deadline within 3 days
    3 = deadline within 1 day
    """
    if not owner:
        return 0
    try:
        from memory.date_parser import deadline_is_imminent
    except ImportError:
        return 0

    best = 0
    for item in items:
        if item.state_type != "deadline":
            continue
        if item.owner and _normalize_name(item.owner) != _normalize_name(owner):
            continue
        if deadline_is_imminent(item.current_value, 1):
            return 3  # can't beat this
        if deadline_is_imminent(item.current_value, 3):
            best = max(best, 2)
        elif deadline_is_imminent(item.current_value, 7):
            best = max(best, 1)
    return best


def _blocker_age_days(item: MemoryItem | None) -> int:
    """Days since this blocker was first recorded. 0 if unparseable."""
    if item is None:
        return 0
    raw = getattr(item, "recorded_at", "")
    if not raw:
        return 0
    try:
        recorded = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - recorded
        return max(0, delta.days)
    except (ValueError, TypeError):
        return 0


# ── dependency graph ────────────────────────────────────────────

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

    # Who is blocked
    blocked_people: dict[str, list[MemoryItem]] = {}
    for b in blockers:
        who = b.source_refs[0].sender_name if b.source_refs else b.owner
        if who:
            blocked_people.setdefault(who, []).append(b)

    # Who can unblock — only use the canonical owner field
    all_names: set[str] = set()
    for o in owners:
        if o.owner:
            all_names.add(o.owner)
    for ns in next_steps:
        if ns.owner:
            all_names.add(ns.owner)
    for b in blockers:
        if b.owner:
            all_names.add(b.owner)

    # Build edges: blocker -> who might resolve it
    edges: list[dict[str, Any]] = []
    for b in blockers:
        blocked_by = b.owner or "?"
        resolver = None
        meta = getattr(b, "metadata", None) or {}
        explicit_dep = meta.get("dependency_owner")
        if explicit_dep:
            resolver = str(explicit_dep)
        else:
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
            # V1.19 P1: weighting signals
            "blocker_age_days": _blocker_age_days(b),
            "deadline_urgency": _deadline_urgency_for_owner(blocked_by, items),
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


# ── orchestration ───────────────────────────────────────────────

def orchestrate(project_id: str, items: Iterable[MemoryItem]) -> OrchestratedPlan:
    """核心编排算法：按加权影响排序找到最优解阻塞顺序。

    优先级公式:
      score = deadline_urgency × 3 + blocker_age_weight + downstream_count
      （DDL紧迫 > 卡了多久 > 下游数量）

    特殊情况:
      - 被阻塞人请假且无resolver → 升级为团队决策
      - resolver 请假 → 找团队内其他人替代
    """
    items_list = [i for i in items if i.project_id == project_id]
    graph = build_dependency_graph(items_list)

    # Expand unavailable set with normalized variants
    unavailable_norms = {_normalize_name(n) for n in graph["unavailable"]}
    expanded_unavailable = set(graph["unavailable"])
    for n in graph["all_names"]:
        if _normalize_name(n) in unavailable_norms:
            expanded_unavailable.add(n)
    graph["unavailable"] = expanded_unavailable

    actions: list[UnblockAction] = []

    # ── weighted sort ──
    def _edge_score(edge: dict) -> float:
        urgency = edge.get("deadline_urgency", 0)
        age_days = edge.get("blocker_age_days", 0)
        age_weight = min(age_days / 2.0, 3.0)  # cap at 3
        downstream = edge.get("downstream_count", 0)
        has_resolver = 1 if edge.get("potential_resolver") else 0
        # Primary: weighted score.  Secondary: has_resolver breaks ties.
        return urgency * 3.0 + age_weight + downstream + has_resolver * 0.5

    sorted_edges = sorted(
        graph["edges"],
        key=_edge_score,
        reverse=True,
    )

    # ── generate actions ──
    for idx, edge in enumerate(sorted_edges):
        priority = idx + 1
        resolver = edge["potential_resolver"]
        blocked = edge["blocked_person"]
        age_days = edge.get("blocker_age_days", 0)
        urgency = edge.get("deadline_urgency", 0)

        # Build reason with evidence
        reason_parts = []
        if urgency >= 3:
            reason_parts.append("被阻塞人DDL在1天内")
        elif urgency >= 2:
            reason_parts.append("被阻塞人DDL在3天内")
        elif urgency >= 1:
            reason_parts.append("被阻塞人DDL在7天内")
        if age_days >= 3:
            reason_parts.append(f"已阻塞{age_days}天")
        if reason_parts:
            reason_parts.append(f"解决后可解锁{blocked}的{edge['downstream_count']}个下游任务")
        else:
            reason_parts.append(f"解决后可解锁{blocked}的{edge['downstream_count']}个下游任务")

        if resolver and resolver in graph["unavailable"]:
            available_people = graph["all_names"] - graph["unavailable"] - {blocked}
            alt = next(iter(available_people), None)
            actions.append(UnblockAction(
                priority=priority,
                assignee=alt or "待分配",
                action=f"代替{resolver}解决：{edge['blocker_text'][:50]}",
                unblocks=[blocked] + (
                    [f"+{edge['downstream_count']}个下游任务"]
                    if edge['downstream_count'] > 0 else []),
                reason=f"{resolver}不可用，{', '.join(reason_parts)}",
                evidence_msg=edge["blocker_text"],
            ))
        elif resolver:
            actions.append(UnblockAction(
                priority=priority,
                assignee=resolver,
                action=f"解除阻塞：{edge['blocker_text'][:50]}",
                unblocks=[blocked] + (
                    [f"+{edge['downstream_count']}个下游任务"]
                    if edge['downstream_count'] > 0 else []),
                reason=", ".join(reason_parts),
                evidence_msg=edge["blocker_text"],
            ))
        else:
            if blocked in graph["unavailable"]:
                actions.append(UnblockAction(
                    priority=priority,
                    assignee="团队决策",
                    action=f"⚠️ 无人认领的阻塞：{edge['blocker_text'][:50]}",
                    unblocks=[blocked],
                    reason=(
                        f"被阻塞人{blocked}不可用且无明确依赖方，"
                        f"需团队协调接手"
                    ),
                    evidence_msg=edge["blocker_text"],
                ))
            else:
                actions.append(UnblockAction(
                    priority=priority,
                    assignee=blocked,
                    action=f"自行推动：{edge['blocker_text'][:50]}",
                    unblocks=[f"{edge['downstream_count']}个下游任务"],
                    reason="无明确依赖方，需自行推动或寻求帮助",
                    evidence_msg=edge["blocker_text"],
                ))

    # Add non-blocked next_steps with continuing priorities
    blocked_names = set(graph["blocked_people"].keys())
    next_priority = len(actions) + 1
    for ns in graph["next_steps"]:
        if ns.owner and ns.owner not in blocked_names and ns.owner not in graph["unavailable"]:
            actions.append(UnblockAction(
                priority=next_priority,
                assignee=ns.owner,
                action=f"继续推进：{ns.current_value[:50]}",
                unblocks=[],
                reason="无阻塞，正常推进",
                evidence_msg=ns.current_value,
            ))
            next_priority += 1

    # Team status summary
    raw_summary: dict[str, str] = {}
    for name in graph["all_names"]:
        if not name:
            continue
        if name in graph["unavailable"]:
            raw_summary[name] = "不可用（请假/出差）"
        elif name in blocked_names:
            raw_summary[name] = f"被阻塞（{len(graph['blocked_people'][name])}个）"
        else:
            raw_summary[name] = "可正常工作"
    # Pick the LONGEST variant for each normalized key
    by_canonical: dict[str, tuple[str, str]] = {}
    rank = {"被阻塞": 0, "不可用": 1, "可正常工作": 2}
    for raw, status in raw_summary.items():
        canon = _normalize_name(raw)
        if not canon:
            continue
        prev = by_canonical.get(canon)
        if prev is None:
            by_canonical[canon] = (raw, status)
        else:
            prev_raw, prev_status = prev
            prev_rank = next((v for k, v in rank.items() if k in prev_status), 99)
            cur_rank = next((v for k, v in rank.items() if k in status), 99)
            if cur_rank < prev_rank or (cur_rank == prev_rank and len(raw) > len(prev_raw)):
                by_canonical[canon] = (raw, status)
    team_summary = {raw: status for raw, status in by_canonical.values()}

    # Dependency chains for visualization
    chains = []
    for edge in sorted_edges:
        chains.append({
            "blocker": edge["blocker_text"][:40],
            "blocks": edge["blocked_person"],
            "downstream": edge["downstream_count"],
            "resolver": edge["potential_resolver"],
            "age_days": edge.get("blocker_age_days", 0),
            "deadline_urgency": edge.get("deadline_urgency", 0),
        })

    # ── blocker_summary ──
    blocked_unavailable_count = sum(
        1 for e in sorted_edges
        if e["blocked_person"] in graph["unavailable"]
    )
    blocker_summary = {
        "total_active_blockers": len(sorted_edges),
        "stale_blockers_gt_3d": sum(
            1 for e in sorted_edges if e.get("blocker_age_days", 0) > 3),
        "urgent_ddl_within_3d": sum(
            1 for e in sorted_edges if e.get("deadline_urgency", 0) >= 2),
        "unassigned_blockers": sum(
            1 for e in sorted_edges if e.get("potential_resolver") is None),
        "blocked_unavailable": blocked_unavailable_count,
    }

    # Build reason with evidence
    reason_parts = ["基于当前阻塞依赖链分析"]
    if blocker_summary.get("urgent_ddl_within_3d", 0) > 0:
        reason_parts.append(
            f"DDL紧迫度加权（{blocker_summary['urgent_ddl_within_3d']}项3天内到期）")
    if blocker_summary.get("stale_blockers_gt_3d", 0) > 0:
        reason_parts.append(
            f"老阻塞加权（{blocker_summary['stale_blockers_gt_3d']}项超过3天）")
    reason_parts.append("按解锁下游任务数量排序")

    return OrchestratedPlan(
        project_id=project_id,
        generated_reason="，".join(reason_parts),
        actions=actions,
        dependency_chains=chains,
        team_status_summary=team_summary,
        blocker_summary=blocker_summary,
    )


def render_orchestrated_plan_text(plan: OrchestratedPlan) -> str:
    """渲染编排方案为 Markdown。"""
    lines: list[str] = []
    lines.append(f"🎯 团队任务编排方案 [{plan.project_id}]")
    lines.append(f"策略：{plan.generated_reason}")
    lines.append("")

    # Blocker summary
    if plan.blocker_summary:
        bs = plan.blocker_summary
        lines.append("📊 阻塞概况")
        lines.append(f"  活跃阻塞: {bs.get('total_active_blockers', 0)}")
        if bs.get('stale_blockers_gt_3d', 0):
            lines.append(f"  超过3天: {bs['stale_blockers_gt_3d']}")
        if bs.get('urgent_ddl_within_3d', 0):
            lines.append(f"  DDL在3天内: {bs['urgent_ddl_within_3d']}")
        if bs.get('blocked_unavailable', 0):
            lines.append(f"  ⚠️ 被阻塞且请假: {bs['blocked_unavailable']}")
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
            age_hint = f" [已{chain['age_days']}天]" if chain.get("age_days", 0) >= 2 else ""
            urgency = chain.get("deadline_urgency", 0)
            urgency_hint = " 🔥" if urgency >= 2 else (" ⏰" if urgency >= 1 else "")
            lines.append(
                f"  [{chain['blocker']}]{age_hint}{urgency_hint}"
                f" → 卡住{chain['blocks']}（影响{chain['downstream']}个下游）{resolver_hint}"
            )
        lines.append("")

    # Actions
    if plan.actions:
        lines.append("📋 建议行动序列（按优先级）")
        by_person: dict[str, list[UnblockAction]] = {}
        for a in plan.actions:
            by_person.setdefault(a.assignee, []).append(a)

        for person, person_actions in sorted(by_person.items()):
            lines.append("")
            lines.append(f"  【{person}】")
            for a in sorted(person_actions, key=lambda x: x.priority):
                unblock_text = f" → 解锁: {', '.join(a.unblocks)}" if a.unblocks else ""
                lines.append(f"    P{a.priority}. {a.action}{unblock_text}")
                lines.append(f"        理由: {a.reason}")
        lines.append("")

    if not plan.actions:
        lines.append("✅ 当前无阻塞，团队运转顺畅！")

    return "\n".join(lines).strip() + "\n"
