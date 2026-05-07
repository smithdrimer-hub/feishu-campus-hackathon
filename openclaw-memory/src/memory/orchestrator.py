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


def _normalize_name(name: str) -> str:
    """统一人名格式：'前端-吴凡' / '吴凡' / '吴凡(产品)' → '吴凡'。

    去掉常见职能前缀（前端-/后端-/测试-/产品-/设计-），
    再去掉括号备注 (产品)/（产品）。
    """
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


def _normalize_name(name: str) -> str:
    """统一人名：'前端-吴凡' / '吴凡' / '吴凡(产品)' → '吴凡'。"""
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

    # Who can unblock — only use the canonical owner field, not the value blob
    # (avoids garbage like "测试-张蕾在写测试用例")
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
    # Priority: metadata.dependency_owner (explicit) > name in blocker text (heuristic)
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

    # Expand unavailable set with normalized variants so resolver lookups
    # can catch "前端-小杨" when only "小杨" is in unavailable.
    unavailable_norms = {_normalize_name(n) for n in graph["unavailable"]}
    expanded_unavailable = set(graph["unavailable"])
    for n in graph["all_names"]:
        if _normalize_name(n) in unavailable_norms:
            expanded_unavailable.add(n)
    graph["unavailable"] = expanded_unavailable

    actions: list[UnblockAction] = []

    # Sort edges by (has_resolver desc, downstream_count desc) — items with a
    # clear resolver are more actionable, so they bubble to the top.
    sorted_edges = sorted(
        graph["edges"],
        key=lambda e: (1 if e.get("potential_resolver") else 0,
                       e.get("downstream_count", 0)),
        reverse=True,
    )

    for idx, edge in enumerate(sorted_edges):
        priority = idx + 1
        resolver = edge["potential_resolver"]
        blocked = edge["blocked_person"]

        if resolver and resolver in graph["unavailable"]:
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
                reason=f"解决后可解锁 {blocked} 的 {edge['downstream_count']} 个下游任务",
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

    # Team status summary — dedup by normalized name
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
    # Pick the LONGEST variant for each normalized key (so "前端-吴凡" beats "吴凡")
    by_canonical: dict[str, tuple[str, str]] = {}
    rank = {"被阻塞": 0, "不可用": 1, "可正常工作": 2}  # severe states win
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
