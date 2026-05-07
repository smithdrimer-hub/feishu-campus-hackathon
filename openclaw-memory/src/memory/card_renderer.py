"""Feishu interactive card renderer for Memory Engine outputs.

V1.18: Reusable card builders for handoff summary, review desk, risk alerts,
standup summary, and morning briefing. Each function accepts MemoryItem lists
and returns Feishu interactive card JSON dicts.

Usage:
    from memory.card_renderer import render_handoff_card
    card = render_handoff_card(items, project_id)
    adapter.send_message(chat_id, json.dumps(card), msg_type="interactive")
"""

from __future__ import annotations

import time
from typing import Any


# ── Helpers ────────────────────────────────────────────────────

def _evidence_note(item) -> dict | None:
    ref = item.source_refs[0] if item.source_refs else None
    if not ref:
        return None
    sender = ref.sender_name or "?"
    excerpt = (ref.excerpt or "").strip()
    if len(excerpt) > 80:
        excerpt = excerpt[:80] + "..."
    return {
        "tag": "note",
        "elements": [{"tag": "lark_md", "content": f"[src] {sender}：{excerpt}"}],
    }


def _divider() -> dict:
    return {"tag": "hr"}


def _header(title: str, subtitle: str = "", template: str = "blue") -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "subtitle": {"tag": "plain_text", "content": subtitle},
            "template": template,
        },
        "elements": [],
    }


def _section(icon: str, title: str, lines: list[str]) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md",
            "content": f"{icon} **{title}**\n" + "\n".join(f"- {l}" for l in lines)}}


def _status_badge(item) -> str:
    """Generate inline status badges for V1.15-V1.18 features."""
    badges = []
    ds = getattr(item, "decision_strength", "")
    if ds and ds != "confirmed":
        badges.append({"confirmed": "[OK]", "tentative": "[PEND]", "preference": "[PREF]",
                        "discussion": "[DISC]"}.get(ds, ""))

    rs = getattr(item, "review_status", "")
    if rs == "needs_review":
        badges.append("[WARN]待审核")

    meta = getattr(item, "metadata", None) or {}
    if meta.get("conflict_status") == "conflicting":
        badges.append("[CONFLICT]冲突")

    bs = meta.get("blocker_status", "")
    if bs in ("acknowledged", "waiting_external", "resolved"):
        badges.append({"acknowledged": "[ACK]已接", "waiting_external": "[WAIT]等外部",
                        "resolved": "[OK]已解决"}.get(bs, ""))

    return " ".join(badges) if badges else ""


# ── Card builders ──────────────────────────────────────────────

def render_handoff_card(
    items: list,
    project_id: str = "",
    project_title: str = "项目交接摘要",
) -> dict:
    """交接摘要卡片：8 种状态类型 + 证据追踪。"""
    by_type: dict[str, list] = {}
    for item in items:
        by_type.setdefault(item.state_type, []).append(item)

    card = _header(
        title=f"项目交接摘要 · {project_title or project_id}",
        subtitle="无需翻聊天记录，0 秒了解项目现状",
    )

    # Goals
    goals = by_type.get("project_goal", [])
    if goals:
        card["elements"].append({"tag": "div", "text": {"tag": "lark_md",
            "content": f"[GOAL] **项目目标**\n{goals[0].current_value[:200]}"}})
        note = _evidence_note(goals[0])
        if note: card["elements"].append(note)
        card["elements"].append(_divider())

    # Owners
    owners = by_type.get("owner", [])
    if owners:
        seen = set()
        lines = []
        for o in owners:
            v = o.current_value[:60]
            if v in seen or len(v) < 2: continue
            seen.add(v)
            lines.append(v)
        card["elements"].append(_section("[TEAM]", "负责人", lines[:5]))
        card["elements"].append(_divider())

    # Decisions with strength badges
    decisions = by_type.get("decision", [])
    if decisions:
        for d in decisions[:5]:
            badge = _status_badge(d)
            v = d.current_value[:100]
            card["elements"].append({"tag": "div", "text": {"tag": "lark_md",
                "content": f"{badge} {v}" if badge else f"- {v}"}})
            note = _evidence_note(d)
            if note: card["elements"].append(note)
        card["elements"].append(_divider())

    # Blockers with lifecycle
    blockers = by_type.get("blocker", [])
    if blockers:
        for b in blockers[:5]:
            badge = _status_badge(b)
            v = b.current_value[:100]
            card["elements"].append({"tag": "div", "text": {"tag": "lark_md",
                "content": f"[!!] {badge} {v}" if badge else f"[!!] {v}"}})
            note = _evidence_note(b)
            if note: card["elements"].append(note)
        card["elements"].append(_divider())

    # Deadline
    deadlines = by_type.get("deadline", [])
    if deadlines:
        card["elements"].append({"tag": "div", "text": {"tag": "lark_md",
            "content": f"[TIME] **DDL**：{deadlines[0].current_value}"}})
        card["elements"].append(_divider())

    # Deferred
    deferred = by_type.get("deferred", [])
    if deferred:
        lines = [d.current_value[:80] for d in deferred[:3]]
        card["elements"].append(_section("[HOLD]️", "暂缓事项", lines))
        card["elements"].append(_divider())

    # Member status
    members = by_type.get("member_status", [])
    if members:
        lines = []
        for m in members[:3]:
            sender = m.source_refs[0].sender_name if m.source_refs else "?"
            lines.append(f"{sender}：{m.current_value[:60]}")
        card["elements"].append(_section("[USER]", "成员状态", lines))
        card["elements"].append(_divider())

    # Next steps
    nexts = by_type.get("next_step", [])
    if nexts:
        seen = set()
        lines = []
        for n in nexts[:5]:
            v = n.current_value[:80]
            if v in seen: continue
            seen.add(v)
            owner_hint = f" ({n.owner})" if n.owner else ""
            badge = _status_badge(n)
            lines.append(f"{v}{owner_hint} {badge}".strip())
        card["elements"].append(_section("[NEXT]️", "建议下一步", lines))

    # Patterns (V1.18)
    from memory.pattern_memory import generate_all_patterns
    patterns = generate_all_patterns(items, project_id)
    if patterns:
        lines = [p.summary[:120] for p in patterns[:3]]
        card["elements"].append(_divider())
        card["elements"].append(_section("[LOOP]", "协作模式", lines))

    # Footer
    card["elements"].append({"tag": "note", "elements": [{"tag": "lark_md",
        "content": f"由 OpenClaw Memory Engine 从 {len(items)} 条结构化记忆生成 · 每条可追溯原始消息"}]})

    return card


def render_review_card(pending_items: list, project_id: str = "") -> dict:
    """审核台卡片：列出待审核记忆数量 + 前 3 条摘要。"""
    card = _header(
        title="待审核记忆",
        subtitle=f"项目 {project_id} · {len(pending_items)} 条待处理",
        template="red",
    )
    for i, item in enumerate(pending_items[:5], 1):
        badge = _status_badge(item)
        v = item.current_value[:80]
        card["elements"].append({"tag": "div", "text": {"tag": "lark_md",
            "content": f"{i}. [{item.state_type}] {badge} {v}"}})
        note = _evidence_note(item)
        if note: card["elements"].append(note)
    if len(pending_items) > 5:
        card["elements"].append({"tag": "div", "text": {"tag": "lark_md",
            "content": f"...等 {len(pending_items)} 条"}})
    return card


def render_risk_card(blockers: list, deadlines: list, project_id: str = "") -> dict:
    """风险预警卡片：阻塞 + 临近 DDL。"""
    card = _header(
        title="风险预警",
        subtitle=f"项目 {project_id} · {len(blockers)} 个阻塞 · {len(deadlines)} 个临近 DDL",
        template="red",
    )
    if deadlines:
        lines = [d.current_value[:80] for d in deadlines[:3]]
        card["elements"].append(_section("[TIME]", "临近截止", lines))
    if blockers:
        card["elements"].append(_divider())
        for b in blockers[:5]:
            badge = _status_badge(b)
            v = b.current_value[:80]
            card["elements"].append({"tag": "div", "text": {"tag": "lark_md",
                "content": f"[!!] {badge} {v}"}})
            note = _evidence_note(b)
            if note: card["elements"].append(note)
    return card


def render_standup_card(items: list, project_id: str = "") -> dict:
    """站会摘要卡片：昨日/今日/阻塞。"""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    yesterday = [i for i in items if i.updated_at and i.updated_at > cutoff]
    today = [i for i in items if i.state_type == "next_step" and i.status == "active"]
    unresolved = [i for i in items if i.state_type == "blocker"
                  and (getattr(i, "metadata", {}) or {}).get("blocker_status", "open")
                  not in ("resolved", "obsolete")]

    card = _header(title="今日站会", subtitle=datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    if yesterday:
        lines = [f"{i.current_value[:60]} [{i.state_type}]" for i in yesterday[:5]]
        card["elements"].append(_section("[LIST]", "昨日进展", lines))
    else:
        card["elements"].append({"tag": "div", "text": {"tag": "lark_md",
            "content": "[LIST] **昨日进展**\n(无记录)"}})

    card["elements"].append(_divider())
    if today:
        today.sort(key=lambda i: (0 if i.owner else 1, -i.confidence))
        lines = []
        for t in today[:5]:
            owner = f" ({t.owner})" if t.owner else ""
            lines.append(f"{t.current_value[:60]}{owner}")
        card["elements"].append(_section("[NEXT]️", "今日计划", lines))
    else:
        card["elements"].append({"tag": "div", "text": {"tag": "lark_md",
            "content": "[NEXT]️ **今日计划**\n(无计划)"}})

    card["elements"].append(_divider())
    if unresolved:
        lines = []
        for b in unresolved[:5]:
            badge = _status_badge(b)
            lines.append(f"{b.current_value[:60]} {badge}".strip())
        card["elements"].append(_section("[!!]", "阻塞与风险", lines))
    else:
        card["elements"].append({"tag": "div", "text": {"tag": "lark_md",
            "content": "[!!] **阻塞与风险**\n(无阻塞)"}})

    return card
