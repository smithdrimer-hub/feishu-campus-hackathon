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

    # Decisions：按 decision_strength 区分"已确认"和"待定"
    all_decisions = by_type.get("decision", [])
    recent_decisions: list[dict[str, Any]] = []
    open_decisions: list[dict[str, Any]] = []
    for item in all_decisions:
        val = item.current_value
        # 跳过 needs_review 和冲突中的决策——审核前不显示在面板
        if getattr(item, "review_status", "") == "needs_review":
            continue
        item_meta = getattr(item, "metadata", None) or {}
        if item_meta.get("conflict_status") == "conflicting":
            continue
        decision_entry = {
            "id": item.memory_id,
            "title": val[:120],
            "status": "confirmed",
            "decided_at": item.updated_at,
            "source_refs": [
                {"message_id": ref.message_id, "excerpt": ref.excerpt[:60],
                 "sender": ref.sender_name, "url": ref.source_url}
                for ref in item.source_refs
            ],
        }
        ds = getattr(item, "decision_strength", "")
        if ds == "confirmed":
            decision_entry["status"] = "confirmed"
            recent_decisions.append(decision_entry)
        elif ds == "discussion":
            continue  # 讨论级别不显示
        elif ds in ("tentative", "preference"):
            decision_entry["status"] = "open"
            open_decisions.append(decision_entry)
        else:
            # 旧数据兼容：按关键词判断
            if any(kw in val for kw in ("待定", "考虑", "是否", "讨论")):
                decision_entry["status"] = "open"
                open_decisions.append(decision_entry)
            else:
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
            "source_refs": [
                {"message_id": ref.message_id, "excerpt": ref.excerpt[:60],
                 "sender": ref.sender_name, "url": ref.source_url}
                for ref in item.source_refs
            ],
        })

    # Risks / blockers (V1.15: split by blocker_status)
    risks: list[dict[str, Any]] = []
    resolved_blockers: list[dict[str, Any]] = []
    for item in by_type.get("blocker", []):
        meta = getattr(item, "metadata", None) or {}
        bs = meta.get("blocker_status", "open")
        entry = {
            "id": item.memory_id,
            "description": item.current_value[:200],
            "severity": "high" if "严重" in item.current_value else "medium",
            "blocker_status": bs,
            "source_refs": [
                {"message_id": ref.message_id, "excerpt": ref.excerpt[:60],
                 "sender": ref.sender_name, "url": ref.source_url}
                for ref in item.source_refs
            ],
        }
        if bs in ("resolved", "obsolete"):
            resolved_blockers.append(entry)
        else:
            risks.append(entry)

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

    # V1.18: 协作模式记忆
    from memory.pattern_memory import generate_all_patterns
    pattern_list = generate_all_patterns(items_list, project_id)
    pattern_dicts = [p.to_dict() for p in pattern_list]

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
        "resolved_blockers": resolved_blockers,
        "next_actions": next_actions,
        "patterns": pattern_dicts,
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
            bs = r.get("blocker_status", "open")
            status_label = {"acknowledged": "[已确认]", "waiting_external": "[等待外部]"}.get(bs, "")
            icon = "[HIGH]" if severity == "high" else "[MED]"
            lines.append(f"- {icon} {r.get('description', '')} {status_label}")
        lines.append("")

    # Recently resolved blockers
    resolved = state.get("resolved_blockers", [])
    if resolved:
        lines.append("✅ 最近已解决")
        for r in resolved[:5]:
            lines.append(f"- {r.get('description', '')}")
        lines.append("")

    # V1.18: 协作模式提示
    patterns = state.get("patterns", [])
    lines.append("🔄 协作模式")
    if patterns:
        for p in patterns[:3]:
            lines.append(f"- [{p.get('pattern_type','')}] {p.get('summary','')}")
    else:
        lines.append("- 暂无协作模式提示（积累更多协作数据后自动生成）")
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


# ── V1.12 FIX-9: 跨项目用户视图 ────────────────────────────────

def build_cross_project_context(
    owner_name: str,
    project_items: dict[str, list[MemoryItem]],
    requester_name: str = "",
    requester_open_id: str = "",
    supervisor_ids: frozenset | None = None,
) -> dict[str, Any]:
    """聚合用户在所有项目中的任务、阻塞、截止日期。

    V1.13: 增加权限校验。仅用户本人或上级可查看。

    Args:
        owner_name: 目标用户姓名（如 "张三"）。
        project_items: {project_id: [MemoryItem, ...]}。
        requester_name: 请求者姓名，空字符串表示跳过校验（仅 Demo）。
        requester_open_id: 请求者 open_id，优先于 name 匹配。
        supervisor_ids: 上级 open_id 白名单（None = 无上级）。

    Returns:
        {"user": ..., "projects": {...}} 或 {"user": ..., "projects": {}, "denied": True}
    """
    result: dict[str, Any] = {"user": owner_name, "projects": {}}

    # V1.13: 权限校验 — 仅本人或上级可查看
    if requester_name and requester_name != owner_name:
        is_supervisor = (
            supervisor_ids and requester_open_id and
            requester_open_id in supervisor_ids
        )
        if not is_supervisor:
            result["denied"] = True
            result["reason"] = f"仅用户本人或上级可查看 {owner_name} 的跨项目状态"
            return result

    for pid, items in project_items.items():
        user_tasks = []
        user_blockers = []
        user_deadlines = []

        for item in items:
            item_owner = item.owner or ""
            if owner_name not in item_owner:
                continue

            entry = {
                "memory_id": item.memory_id,
                "title": item.current_value[:120],
                "confidence": item.confidence,
                "updated_at": item.updated_at,
            }

            if item.state_type == "next_step":
                user_tasks.append(entry)
            elif item.state_type == "blocker":
                user_blockers.append(entry)
            elif item.state_type == "deadline":
                user_deadlines.append(entry)
            elif item.state_type == "owner":
                user_tasks.append({**entry, "title": f"负责: {item.current_value[:100]}"})

        if user_tasks or user_blockers or user_deadlines:
            result["projects"][pid] = {
                "tasks": user_tasks,
                "blockers": user_blockers,
                "deadlines": user_deadlines,
            }

    return result


def render_cross_project_text(ctx: dict[str, Any]) -> str:
    """将 build_cross_project_context 的结果渲染为 Markdown。"""
    user = ctx.get("user", "")
    projects = ctx.get("projects", {})

    lines = [f"【{user} 的跨项目视图】", ""]
    total_tasks = 0
    total_blockers = 0

    for pid, data in sorted(projects.items()):
        tasks = data.get("tasks", [])
        blockers = data.get("blockers", [])
        deadlines = data.get("deadlines", [])

        if not tasks and not blockers and not deadlines:
            continue

        lines.append(f"## {pid}")
        total_tasks += len(tasks)
        total_blockers += len(blockers)

        if tasks:
            lines.append("📌 任务:")
            for t in tasks:
                lines.append(f"- {t['title']}")
        if deadlines:
            lines.append("⏰ 截止:")
            for d in deadlines:
                lines.append(f"- {d['title']}")
        if blockers:
            lines.append("⚠️ 阻塞:")
            for b in blockers:
                lines.append(f"- {b['title']}")
        lines.append("")

    if not projects:
        lines.append("当前没有分配给你的任务。")
    else:
        lines.insert(1, f"共 {len(projects)} 个项目，{total_tasks} 个任务，{total_blockers} 个阻塞")
        lines.insert(2, "")

    return "\n".join(lines).strip() + "\n"


# ── V1.15: 站会摘要 ────────────────────────────────────────────

def render_standup_summary(
    items: list[MemoryItem],
    project_id: str = "",
    project_title: str = "",
) -> str:
    """Generate a standup-format summary: yesterday / today / blockers."""
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    lines = [f"## 今日站会 — {project_title or project_id}", ""]

    yesterday: list[str] = []
    for item in items:
        if item.updated_at and item.updated_at > cutoff:
            yesterday.append(f"- {item.current_value[:80]} [{item.state_type}]")
        meta = getattr(item, "metadata", None) or {}
        if meta.get("blocker_status") == "resolved":
            resolved_at = meta.get("resolved_at", "")
            if resolved_at and resolved_at > cutoff:
                resolved_by = meta.get("resolved_by", "")
                yesterday.append(f"- ✅ 已解决: {item.current_value[:80]} ({resolved_by})")

    lines.append("### 昨日进展")
    if yesterday:
        lines.extend(yesterday[:10])
    else:
        lines.append("- (无记录)")
    lines.append("")

    today = [i for i in items if i.state_type == "next_step" and i.status == "active"]
    today.sort(key=lambda i: (0 if i.owner else 1, -(i.confidence)))
    lines.append("### 今日计划")
    if today:
        for t in today[:10]:
            owner_hint = f"（{t.owner}）" if t.owner else ""
            lines.append(f"- {t.current_value[:100]}{owner_hint}")
    else:
        lines.append("- (无计划)")
    lines.append("")

    blockers = [i for i in items if i.state_type == "blocker" and i.status == "active"]
    unresolved = []
    for b in blockers:
        meta = getattr(b, "metadata", None) or {}
        if meta.get("blocker_status", "open") in ("open", "acknowledged", "waiting_external"):
            unresolved.append(b)
    lines.append("### 阻塞与风险")
    if unresolved:
        for b in unresolved[:10]:
            meta = getattr(b, "metadata", None) or {}
            bs = meta.get("blocker_status", "open")
            status_hint = {"acknowledged": "[已接]", "waiting_external": "[等外部]"}.get(bs, "")
            lines.append(f"- {b.current_value[:100]} {status_hint}")
    else:
        lines.append("- (无阻塞)")
    lines.append("")

    return "\n".join(lines).strip() + "\n"


# ── V1.15: 确认清单 ────────────────────────────────────────────

def render_confirmation_checklist(
    items: list[MemoryItem],
    title: str = "会议纪要",
) -> str:
    """Generate a markdown confirmation checklist from extracted memory items."""
    lines = [f"## 确认清单 — {title}", ""]

    decisions = [i for i in items if i.state_type == "decision"]
    next_steps = [i for i in items if i.state_type == "next_step"]
    blockers = [i for i in items if i.state_type == "blocker"]

    if decisions:
        lines.append("### 识别到以下决策，请确认：")
        for j, d in enumerate(decisions[:5], 1):
            ref = d.source_refs[0] if d.source_refs else None
            evidence = f" [{ref.sender_name} {ref.created_at[:16]}]" if ref else ""
            ds = getattr(d, "decision_strength", "")
            ds_label = f" ({ds})" if ds else ""
            lines.append(f"{j}. {d.current_value[:120]}{ds_label}{evidence}")
        lines.append("")

    if next_steps:
        lines.append("### 识别到以下待办：")
        for j, ns in enumerate(next_steps[:10], 1):
            owner = ns.owner or "(待分配)"
            ref = ns.source_refs[0] if ns.source_refs else None
            evidence = f" [{ref.sender_name}]" if ref else ""
            lines.append(f"{j}. {owner}：{ns.current_value[:100]}{evidence}")
        lines.append("")

    if blockers:
        lines.append("### 识别到以下风险：")
        for j, b in enumerate(blockers[:5], 1):
            ref = b.source_refs[0] if b.source_refs else None
            evidence = f" [{ref.sender_name}]" if ref else ""
            lines.append(f"{j}. {b.current_value[:120]}{evidence}")
        lines.append("")

    lines.append("---")
    lines.append("请回复确认或修改：确认全部回复\"确认\"，修改某项回复\"修改 N. 改为XXX\"")

    return "\n".join(lines).strip() + "\n"


# ── Personal Morning Briefing ─────────────────────────────────

def build_morning_briefing(
    user_id: str,
    project_id: str,
    items: Iterable[MemoryItem],
    last_seen_at: str | None = None,
) -> dict[str, Any]:
    """构建个人每日工作简报：帮助成员快速进入工作状态。

    与 build_personal_work_context 的区别：
    - personal_work_context = "你有什么任务"（静态清单）
    - morning_briefing = "今天你需要关注什么"（动态简报+行动建议）

    信息维度：
    1. 你不在期间发生的变化（新决策/新阻塞/阻塞解除）
    2. 等你处理的事（别人被你阻塞、分配给你的任务）
    3. 时间压力（即将到期的deadline）
    4. 队友状态（谁请假/出差，影响协作）
    5. 下一步行动建议（基于以上信息自动编排优先级）
    """
    from datetime import datetime, timezone

    items_list = list(items)
    now = datetime.now(timezone.utc)

    if last_seen_at:
        try:
            last_seen = datetime.fromisoformat(last_seen_at.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            last_seen = None
    else:
        last_seen = None

    by_type: dict[str, list[MemoryItem]] = defaultdict(list)
    for item in items_list:
        if item.project_id == project_id:
            by_type[item.state_type].append(item)

    def _is_mine(item: MemoryItem) -> bool:
        if not item.owner:
            return False
        return user_id in item.owner

    def _is_recent(item: MemoryItem) -> bool:
        if not last_seen or not item.updated_at:
            return False
        try:
            updated = datetime.fromisoformat(item.updated_at.replace("Z", "+00:00"))
            return updated > last_seen
        except (ValueError, TypeError):
            return False

    # 1. Changes since last seen
    recent_changes = []
    if last_seen:
        for item in items_list:
            if item.project_id == project_id and _is_recent(item):
                recent_changes.append({
                    "type": item.state_type,
                    "value": item.current_value[:100],
                    "sender": item.source_refs[0].sender_name if item.source_refs else None,
                })

    # 2. Waiting on you (others blocked by you / tasks assigned to you)
    waiting_on_me = []
    for item in by_type.get("next_step", []) + by_type.get("blocker", []):
        if _is_mine(item):
            waiting_on_me.append({
                "type": item.state_type,
                "value": item.current_value[:100],
                "urgency": "high" if item.state_type == "blocker" else "normal",
            })

    # 3. Upcoming deadlines
    deadlines = []
    for item in by_type.get("deadline", []):
        deadlines.append({
            "value": item.current_value[:100],
            "source": item.source_refs[0].sender_name if item.source_refs else None,
        })

    # 4. Team availability
    team_status = []
    for item in by_type.get("member_status", []):
        if not _is_mine(item):
            team_status.append({
                "who": item.owner or (item.source_refs[0].sender_name if item.source_refs else "?"),
                "status": item.current_value[:60],
            })

    # 5. Suggested actions (auto-prioritized)
    actions = []
    for item in waiting_on_me:
        if item["urgency"] == "high":
            actions.append({
                "priority": 1,
                "action": f"解除阻塞：{item['value'][:60]}",
                "reason": "有人被你阻塞，优先处理",
            })
    for item in waiting_on_me:
        if item["urgency"] == "normal":
            actions.append({
                "priority": 2,
                "action": f"完成任务：{item['value'][:60]}",
                "reason": "分配给你的待办",
            })
    if deadlines:
        actions.append({
            "priority": 1,
            "action": f"关注截止日：{deadlines[0]['value'][:40]}",
            "reason": "有临近的deadline",
        })
    for change in recent_changes[:3]:
        if change["type"] == "decision":
            actions.append({
                "priority": 3,
                "action": f"了解新决策：{change['value'][:40]}",
                "reason": "你不在期间有新决策产生",
            })

    actions.sort(key=lambda x: x["priority"])

    return {
        "user_id": user_id,
        "project_id": project_id,
        "generated_at": now.isoformat(),
        "recent_changes": recent_changes[:10],
        "waiting_on_me": waiting_on_me,
        "deadlines": deadlines,
        "team_status": team_status,
        "suggested_actions": actions[:8],
    }


def render_morning_briefing_text(briefing: dict[str, Any]) -> str:
    """渲染个人工作简报为飞书可发送的 Markdown 文本。"""
    lines: list[str] = []
    user = briefing.get("user_id", "")
    project = briefing.get("project_id", "")

    lines.append(f"☀️ 早安，{user}！以下是你在 [{project}] 的工作简报：")
    lines.append("")

    # Recent changes
    changes = briefing.get("recent_changes", [])
    if changes:
        lines.append("📥 你不在期间发生的变化")
        for c in changes[:5]:
            sender = f"（{c['sender']}）" if c.get("sender") else ""
            lines.append(f"- [{c['type']}] {c['value']}{sender}")
        lines.append("")

    # Waiting on me
    waiting = briefing.get("waiting_on_me", [])
    if waiting:
        lines.append("🔥 需要你处理的")
        for w in waiting:
            icon = "🚨" if w["urgency"] == "high" else "📋"
            lines.append(f"- {icon} {w['value']}")
        lines.append("")

    # Deadlines
    deadlines = briefing.get("deadlines", [])
    if deadlines:
        lines.append("⏰ 时间提醒")
        for d in deadlines:
            lines.append(f"- {d['value']}")
        lines.append("")

    # Team status
    team = briefing.get("team_status", [])
    if team:
        lines.append("👥 队友状态")
        for t in team:
            lines.append(f"- {t['who']}：{t['status']}")
        lines.append("")

    # Suggested actions
    actions = briefing.get("suggested_actions", [])
    if actions:
        lines.append("▶️ 建议今日行动（按优先级）")
        for i, a in enumerate(actions, 1):
            lines.append(f"  {i}. {a['action']}")
            lines.append(f"     ↳ {a['reason']}")
        lines.append("")

    if not changes and not waiting and not deadlines and not actions:
        lines.append("✅ 当前没有需要你特别关注的事项，保持节奏就好！")
        lines.append("")

    return "\n".join(lines).strip() + "\n"