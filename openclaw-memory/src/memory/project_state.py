"""Project state aggregation and rendering for the Group Project State Panel.

V1.9 新增（Dev Spec 形态 A）：
聚合当前 Memory 中的结构化状态，生成一个可读的项目状态面板。
面向场景：在飞书群中发送"项目状态"命令后，Bot 聚合记忆返回状态概览。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from memory.schema import MemoryItem


def _resolve_owner(
    owner_name: str | None,
    owner_map: dict[str, str] | None,
) -> str:
    """将 owner 姓名解析为 user_id。

    如果 owner_map 存在且包含该姓名，返回对应的 open_id；
    否则返回原姓名。
    """
    if owner_name and owner_map and owner_name in owner_map:
        return owner_map[owner_name]
    return owner_name or ""


def build_group_project_state(
    project_id: str,
    items: Iterable[MemoryItem],
    owner_map: dict[str, str] | None = None,
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
        resolved = _resolve_owner(item.owner, owner_map)
        if resolved and resolved not in seen_owners:
            seen_owners.add(resolved)
            owners_list.append({
                "user_id": resolved,
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
        resolved = _resolve_owner(item.owner, owner_map)
        active_tasks.append({
            "id": item.memory_id,
            "title": item.current_value[:120],
            "assignees": [resolved] if resolved else [],
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
        resolved = _resolve_owner(item.owner, owner_map)
        if resolved:
            next_actions.append({
                "title": item.current_value[:120],
                "owner": resolved,
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


def build_agent_context_pack(
    project_id: str,
    items: Iterable[MemoryItem],
    user_id: str | None = None,
    max_items_per_section: int = 20,
    owner_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """构造给其他 Agent 使用的上下文包（Dev Spec 形态 C）。

    输出为结构化 JSON，不做自然语言渲染，便于其他进程/Agent 消费。

    聚合逻辑：
    - project：项目元信息
    - decisions：唯一的最新有效决策（已自动处理 supersedes）
    - tasks：当前活跃任务
    - risks：当前阻塞与风险
    - recent_discussion_snippets：关联消息摘要
    - user_perspective：如果给了 user_id，附加该用户视角

    Args:
        project_id: 项目 ID
        items: 当前 active memory items
        user_id: 可选，附加指定用户的个人上下文
        max_items_per_section: 每类最多返回多少条

    Returns:
        Dev Spec 5.2 格式的结构化字典
    """
    items_list = list(items)
    by_type: dict[str, list[MemoryItem]] = defaultdict(list)
    for item in items_list:
        if item.project_id == project_id:
            by_type[item.state_type].append(item)

    # Project metadata
    goals = by_type.get("project_goal", [])
    project_title = goals[0].current_value[:80] if goals else f"项目 {project_id}"
    project_description = goals[0].rationale if goals else "暂无描述"

    # Decisions：唯一且最新的有效决策（处理 supersedes）
    # 相同 key 的决策只保留最新版本
    decisions: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for item in sorted(by_type.get("decision", []), key=lambda x: x.version, reverse=True):
        if item.key in seen_keys:
            continue
        if len(decisions) >= max_items_per_section:
            break
        seen_keys.add(item.key)
        decisions.append({
            "id": item.memory_id,
            "title": item.current_value[:120],
            "status": "confirmed",
            "decided_at": item.updated_at,
            "supersedes": item.supersedes,
            "raw_snippets": [
                {
                    "chat_id": ref.chat_id,
                    "message_id": ref.message_id,
                    "text": ref.excerpt,
                }
                for ref in item.source_refs
            ],
        })

    # Tasks
    tasks: list[dict[str, Any]] = []
    for item in by_type.get("next_step", [])[:max_items_per_section]:
        resolved = _resolve_owner(item.owner, owner_map)
        tasks.append({
            "id": item.memory_id,
            "title": item.current_value[:120],
            "status": "in_progress" if item.status == "active" else item.status,
            "assignees": [resolved] if resolved else [],
        })

    # Risks
    risks: list[dict[str, Any]] = []
    for item in by_type.get("blocker", [])[:max_items_per_section]:
        risks.append({
            "id": item.memory_id,
            "description": item.current_value[:200],
            "severity": "high" if "严重" in item.current_value else "medium",
        })

    # Recent discussion snippets：从有 source_refs 的记忆中提取
    snippets: list[dict[str, Any]] = []
    for item in items_list[:max_items_per_section]:
        for ref in item.source_refs:
            snippets.append({
                "chat_id": ref.chat_id,
                "message_id": ref.message_id,
                "text": ref.excerpt,
                "sent_at": ref.created_at,
            })

    result: dict[str, Any] = {
        "project": {
            "project_id": project_id,
            "title": project_title,
            "description": project_description,
        },
        "decisions": decisions,
        "tasks": tasks,
        "risks": risks,
        "recent_discussion_snippets": snippets,
    }

    # User perspective（可选）
    if user_id:
        user_tasks = [t for t in tasks if t["assignees"] and user_id in t["assignees"]]
        result["user_perspective"] = {
            "user_id": user_id,
            "open_tasks": user_tasks[:max_items_per_section],
        }

    return result


def _enrich_snippets(
    snippets: list[dict[str, Any]],
    raw_events_path: str | None,
) -> list[dict[str, Any]]:
    """从 raw_events.jsonl 按 message_id 回读完整原文，替换 excerpt。

    纯只读操作。文件不存在或不传参数时返回原样。

    Args:
        snippets: raw_snippets 列表，每个包含 message_id
        raw_events_path: raw_events.jsonl 的文件路径，可选

    Returns:
        更新后的 snippets 列表（text 字段变为完整原文）
    """
    if not raw_events_path:
        return snippets

    import json
    from pathlib import Path

    path = Path(raw_events_path)
    if not path.exists():
        return snippets

    # 构建 message_id → 原文 的映射
    text_map: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            mid = event.get("message_id", "")
            raw_text = event.get("text") or event.get("content") or ""
            if mid and raw_text:
                # 只保留第一次出现的原文（最早的消息）
                if mid not in text_map:
                    text_map[mid] = raw_text
        except json.JSONDecodeError:
            continue

    # 用完整原文替换 excerpt
    enriched = []
    for s in snippets:
        mid = s.get("message_id", "")
        if mid in text_map:
            s["text"] = text_map[mid][:2000]  # 限制 2000 字
        enriched.append(s)

    return enriched


def build_personal_work_context(
    user_id: str,
    project_id: str,
    items: Iterable[MemoryItem],
    owner_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """聚合某个用户在项目中的个人工作上下文（Dev Spec 形态 B）。

    按 user_id（可以是姓名或 open_id）过滤出该用户相关的记忆：
    - my_open_tasks：owner 为该用户的 next_step
    - my_recent_decisions_involved：owner 为该用户的 decision
    - my_related_risks：owner 为该用户的 blocker
    - suggested_next_actions：owner 为该用户的 next_step

    Args:
        user_id: 用户标识（姓名或 open_id，取决于 owner_map）
        project_id: 项目 ID
        items: 当前 active memory items
        owner_map: 可选，{姓名: open_id} 映射，用于解析 owner

    Returns:
        Dev Spec 4.2 格式的结构化字典
    """
    items_list = list(items)
    by_type: dict[str, list[MemoryItem]] = defaultdict(list)
    for item in items_list:
        if item.project_id == project_id:
            by_type[item.state_type].append(item)

    def _match_owner(item: MemoryItem) -> bool:
        resolved = _resolve_owner(item.owner, owner_map)
        return resolved == user_id

    # Open tasks
    open_tasks: list[dict[str, Any]] = []
    for item in by_type.get("next_step", []):
        if _match_owner(item):
            open_tasks.append({
                "id": item.memory_id,
                "title": item.current_value[:120],
                "status": "in_progress" if item.status == "active" else item.status,
            })

    # Decisions involved
    decisions_involved: list[dict[str, Any]] = []
    for item in by_type.get("decision", []):
        if _match_owner(item):
            decisions_involved.append({
                "id": item.memory_id,
                "title": item.current_value[:120],
                "role": "participant",
                "decided_at": item.updated_at,
            })

    # Related risks
    related_risks: list[dict[str, Any]] = []
    for item in by_type.get("blocker", []):
        if _match_owner(item):
            related_risks.append({
                "id": item.memory_id,
                "description": item.current_value[:200],
            })

    # Suggested next actions
    suggested_next: list[dict[str, Any]] = []
    for item in by_type.get("next_step", []):
        if _match_owner(item):
            suggested_next.append({
                "title": item.current_value[:120],
            })

    return {
        "user_id": user_id,
        "project_id": project_id,
        "my_open_tasks": open_tasks,
        "my_recent_decisions_involved": decisions_involved,
        "my_related_risks": related_risks,
        "suggested_next_actions": suggested_next,
    }


def render_personal_context_text(ctx: dict[str, Any]) -> str:
    """把 build_personal_work_context 的结果渲染为可发给用户的 Markdown 文本。

    无数据时显示友好的降级文案。
    """
    lines: list[str] = []
    user_id = ctx.get("user_id", "")
    project_id = ctx.get("project_id", "")

    lines.append(f"【你的当前状态 @ {project_id}】")
    lines.append(f"用户：{user_id}")
    lines.append("")

    tasks = ctx.get("my_open_tasks", [])
    if tasks:
        lines.append("📌 你当前的任务")
        for t in tasks:
            lines.append(f"- {t.get('title', '')}（{t.get('status', '')}）")
        lines.append("")
    else:
        lines.append("📌 你当前没有分配给你的任务。")
        lines.append("")

    decisions = ctx.get("my_recent_decisions_involved", [])
    if decisions:
        lines.append("🧠 你参与的关键决策")
        for d in decisions:
            lines.append(f"- {d.get('title', '')}")
        lines.append("")

    risks = ctx.get("my_related_risks", [])
    if risks:
        lines.append("⚠️ 与你相关的风险")
        for r in risks:
            lines.append(f"- {r.get('description', '')}")
        lines.append("")

    next_acts = ctx.get("suggested_next_actions", [])
    if next_acts:
        lines.append("▶️ 推荐下一步")
        for n in next_acts:
            lines.append(f"- {n.get('title', '')}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"