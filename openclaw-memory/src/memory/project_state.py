"""Project state aggregation and rendering for the Group Project State Panel.

V1.9 新增（Dev Spec 形态 A）：
聚合当前 Memory 中的结构化状态，生成一个可读的项目状态面板。
面向场景：在飞书群中发送"项目状态"命令后，Bot 聚合记忆返回状态概览。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from memory.schema import MemoryItem


def build_group_project_state(
    project_id: str,
    items: Iterable[MemoryItem],
) -> dict[str, Any]:
    """从 Memory 中聚合项目状态，返回结构化字典。

    聚合逻辑：
    - owners：从 state_type="owner" 的记忆中提取负责人。
    - recent_decisions：state_type="decision" 且不含"待定/考虑"的活跃决策。
    - open_decisions：state_type="decision" 且含"待定/考虑"的决策。
    - active_tasks：state_type="next_step" 或 owner 非空的记忆。
    - risks：state_type="blocker" 的记忆。
    - next_actions：state_type="next_step" 且有 owner 的记忆。
    - 空数据时优雅降级（返回空列表而非报错）。

    Args:
        project_id: 项目 ID
        items: 当前 active memory items（已过滤好 project_id）

    Returns:
        Dev Spec 3.3 格式的结构化字典
    """
    items_list = list(items)
    # 按 state_type 分组
    by_type: dict[str, list[MemoryItem]] = defaultdict(list)
    for item in items_list:
        if item.project_id == project_id:
            by_type[item.state_type].append(item)

    # 提取项目标题和阶段
    goals = by_type.get("project_goal", [])
    project_title = goals[0].current_value[:80] if goals else f"项目 {project_id}"
    project_description = goals[0].rationale if goals else "暂无描述"

    # Owners：从 owner 类型提取唯一负责人列表
    seen_owners: set[str] = set()
    owners_list: list[dict[str, str]] = []
    for item in by_type.get("owner", []):
        if item.owner and item.owner not in seen_owners:
            seen_owners.add(item.owner)
            owners_list.append({
                "user_id": item.owner,
                "role": item.state_type,
            })

    # Decisions：区分"已确认"和"待定"
    all_decisions = by_type.get("decision", [])
    recent_decisions: list[dict[str, Any]] = []
    open_decisions: list[dict[str, Any]] = []
    for item in all_decisions:
        val = item.current_value
        decision_entry = {
            "id": item.memory_id,
            "title": val[:120],
            "status": "confirmed" if item.status == "active" else item.status,
            "decided_at": item.updated_at,
        }
        # 简单判定：含"待定/考虑/是否" 视为未定案
        if any(kw in val for kw in ("待定", "考虑", "是否", "讨论")):
            decision_entry["status"] = "open"
            open_decisions.append(decision_entry)
        else:
            decision_entry["status"] = "confirmed"
            recent_decisions.append(decision_entry)

    # Active tasks：next_step 或有 owner 的记忆
    active_tasks: list[dict[str, Any]] = []
    for item in by_type.get("next_step", []):
        active_tasks.append({
            "id": item.memory_id,
            "title": item.current_value[:120],
            "assignees": [item.owner] if item.owner else [],
            "status": "in_progress" if item.status == "active" else item.status,
        })

    # Risks / blockers
    risks: list[dict[str, Any]] = []
    for item in by_type.get("blocker", []):
        risks.append({
            "id": item.memory_id,
            "description": item.current_value[:200],
            "severity": "high" if "严重" in item.current_value else "medium",
        })

    # Next actions with owner
    next_actions: list[dict[str, Any]] = []
    for item in by_type.get("next_step", []):
        if item.owner:
            next_actions.append({
                "title": item.current_value[:120],
                "owner": item.owner,
            })

    # 找到最近更新时间
    last_update = ""
    if items_list:
        times = [item.updated_at for item in items_list if item.updated_at]
        if times:
            times.sort(reverse=True)
            last_update = times[0]

    return {
        "project_id": project_id,
        "project_title": project_title,
        "project_description": project_description,
        "current_phase": "项目开发中",
        "last_major_update_at": last_update,
        "owners": owners_list,
        "recent_decisions": recent_decisions,
        "open_decisions": open_decisions,
        "active_tasks": active_tasks,
        "risks": risks,
        "next_actions": next_actions,
    }


def render_group_state_panel_text(state: dict[str, Any]) -> str:
    """把 build_group_project_state 的结果渲染为可发到群里的 Markdown 文本。

    每个区块仅在有关键数据时显示，空区块自动隐藏。
    无任何数据时返回一条简单状态说明。
    """
    lines: list[str] = []
    title = state.get("project_title", "未知项目")
    phase = state.get("current_phase", "")
    last_update = state.get("last_major_update_at", "")

    lines.append(f"【项目状态】{title}")
    if phase:
        lines.append(f"阶段：{phase}")
    if last_update:
        lines.append(f"最近更新：{last_update[:16]}")
    lines.append("")

    # Owners
    owners = state.get("owners", [])
    if owners:
        lines.append("👥 负责人")
        for o in owners:
            lines.append(f"- {o['user_id']}")
        lines.append("")

    # Recent decisions
    recent = state.get("recent_decisions", [])
    if recent:
        lines.append("✅ 最近决策")
        for d in recent:
            title_text = d.get("title", "")
            decided_at = d.get("decided_at", "")[:10]
            suffix = f"（{decided_at}）" if decided_at else ""
            lines.append(f"- [已定] {title_text}{suffix}")
        lines.append("")

    # Open decisions
    open_d = state.get("open_decisions", [])
    if open_d:
        lines.append("❓ 待定决策")
        for d in open_d:
            lines.append(f"- [待定] {d.get('title', '')}")
        lines.append("")

    # Active tasks
    tasks = state.get("active_tasks", [])
    if tasks:
        lines.append("📌 进行中任务")
        for t in tasks:
            assignees = t.get("assignees", [])
            owner_str = f"（Owner: {', '.join(assignees)}）" if assignees else ""
            lines.append(f"- {t.get('title', '')}{owner_str}")
        lines.append("")

    # Risks
    risks = state.get("risks", [])
    if risks:
        lines.append("⚠️ 风险与阻塞")
        for r in risks:
            severity = r.get("severity", "medium")
            icon = "🔴" if severity == "high" else "🟡"
            lines.append(f"- {icon} {r.get('description', '')}")
        lines.append("")

    # Next actions
    next_acts = state.get("next_actions", [])
    if next_acts:
        lines.append("▶️ 下一步")
        for n in next_acts:
            owner_str = f"（Owner: {n.get('owner', '')}）" if n.get("owner") else ""
            lines.append(f"- {n.get('title', '')}{owner_str}")
        lines.append("")

    # 无任何数据时的降级
    if not any([owners, recent, open_d, tasks, risks, next_acts]):
        lines.append("当前项目暂无提取到的结构化记忆。请先同步飞书消息或文档后再试。")
        lines.append("")

    return "\n".join(lines).strip() + "\n"