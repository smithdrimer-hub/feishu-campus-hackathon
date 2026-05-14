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

def _clean_text(text: str) -> str:
    """Strip HTML-like tags and truncate for safe card rendering.

    BUG-6 fix: append "…" when text is actually truncated so the card
    visually indicates there is more content, rather than silently
    cutting off mid-sentence.
    """
    import re
    text = re.sub(r"<[^>]+>", "", text)  # remove <card>, <at>, etc.
    text = text.replace("**", "").replace("###", "").replace("🟢", "")
    text = text.strip()
    if len(text) > 200:
        return text[:197] + "..."
    return text


def _evidence_note(item) -> dict | None:
    """B6: 证据不再内嵌卡片。改用 send_evidence_replies() 发送飞书原生回复。"""
    return None


def _should_reply_to_source(item) -> bool:
    """B6a: 相似度检查——证据和正文内容相同时不回复。"""
    ref = item.source_refs[0] if item.source_refs else None
    if not ref:
        return False
    sender = ref.sender_name or ""
    if sender in ("doc_sync", "task_sync", "minute_sync", "approval_sync", "calendar_sync", ""):
        return False
    excerpt = (ref.excerpt or "").strip()
    if not excerpt or excerpt.startswith("@bot") or "【文档】" in excerpt or "【任务】" in excerpt:
        return False
    excerpt_clean = _strip_sender_prefix(excerpt, sender)
    body = getattr(item, "current_value", "") or ""
    body_clean = _strip_sender_prefix(body, sender).strip()
    # 前 40 字符相同，或互为子串 → 跳过
    if excerpt_clean[:40] == body_clean[:40]:
        return False
    if excerpt_clean in body_clean or body_clean in excerpt_clean:
        return False
    return True


def send_evidence_replies(adapter, chat_id: str, items: list, card_msg_id: str = "") -> int:
    """B6b: 对每条记忆的原始消息发送飞书原生回复，实现可点击溯源。

    遍历 items，对每条 source_refs[0].message_id 发送回复，
    飞书自动展示"引用原文"关系，点击可跳转到原始消息。

    对同一 message_id 去重。返回发送的回复数。
    """
    replied: set[str] = set()
    sent = 0
    for item in items:
        ref = item.source_refs[0] if item.source_refs else None
        if not ref:
            continue
        msg_id = ref.message_id
        if not msg_id or msg_id in replied:
            continue
        if not _should_reply_to_source(item):
            continue
        replied.add(msg_id)
        sender = ref.sender_name or "?"
        excerpt = (ref.excerpt or "").strip()
        excerpt = _strip_sender_prefix(excerpt, sender)[:120]
        state_type = item.state_type
        value = getattr(item, "current_value", "")[:80]
        reply_text = (
            f"已提取为 [{state_type}] {value}\n"
            f"原文：{excerpt}"
        )
        result = adapter.reply_message(msg_id, reply_text)
        if result.returncode == 0:
            sent += 1
    return sent


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
    clean_lines = [_clean_text(l) for l in lines]
    return {"tag": "div", "text": {"tag": "lark_md",
            "content": f"{icon} **{title}**\n" + "\n".join(f"- {l}" for l in clean_lines)}}


def _status_badge(item) -> str:
    """Generate minimal status indicators.

    B2: 用单字符 Unicode 替代长 ASCII 标记。
    """
    badges = []
    ds = getattr(item, "decision_strength", "")
    if ds:
        badges.append({"confirmed": "", "tentative": "?", "preference": "~",
                        "discussion": "..."}.get(ds, ""))

    rs = getattr(item, "review_status", "")
    if rs == "needs_review":
        badges.append("!")

    meta = getattr(item, "metadata", None) or {}
    if meta.get("conflict_status") == "conflicting":
        badges.append("!!")

    bs = meta.get("blocker_status", "")
    if bs:
        badges.append({"acknowledged": "ok", "waiting_external": "...",
                        "resolved": "done"}.get(bs, ""))

    return " ".join(b for b in badges if b) if badges else ""


def _is_displayable(item) -> bool:
    """1.3: 仅做卡片级展示过滤。数据清洗已在 engine._sanitize_items() 完成。"""
    val = getattr(item, "current_value", "") or ""
    if not val.strip():
        return False
    # 测试标记前缀（卡片展示不需要看到这些）
    if val.strip().startswith("[阻塞]") or val.strip().startswith("[决策]"):
        return False
    # 裸名 owner 不展示（应该已被 engine 降级为 needs_review）
    if item.state_type == "owner" and val.strip() == (getattr(item, "owner", "") or "").strip():
        return False
    return True


def _sender_name(item) -> str:
    """Extract the sender name from an item's first source_ref."""
    ref = item.source_refs[0] if item.source_refs else None
    return (ref.sender_name or "") if ref else ""


def _clean_item_text(item, max_len: int = 200) -> str:
    """Clean item text for card display: strip tags, sender prefix, truncate."""
    val = getattr(item, "current_value", "") or ""
    sender = _sender_name(item)
    val = _strip_sender_prefix(val, sender)
    return _clean_text(val)[:max_len]


def _strip_sender_prefix(text: str, sender_name: str = "") -> str:
    """去掉文本开头的 sender 前缀。

    - '李四：李四：阻塞...' → '阻塞...'（重复前缀，完全剥离）
    - '李四：阻塞...' → '阻塞...'（匹配到已知 sender，剥离）
    - 无匹配时返回原文
    """
    import re
    # 1. 完全重复：'李四：李四：xxx' → 'xxx'
    match = re.match(r'^(.{1,10})[：:]\1[：:]\s*', text)
    if match:
        return text[match.end():]
    # 2. 单次前缀匹配已知 sender：'李四：xxx' → 'xxx'
    if sender_name and len(sender_name) >= 2:
        prefix = sender_name + '：'
        if text.startswith(prefix):
            return text[len(prefix):]
        prefix = sender_name + ':'
        if text.startswith(prefix):
            return text[len(prefix):]
    # 3. 通用中文名前缀：'张三：xxx' → 'xxx'
    match = re.match(r'^([一-鿿]{2,4})[：:]\s*', text)
    if match:
        name = match.group(1)
        rest = text[match.end():]
        # 如果剩下的文本又以此名开头，继续剥离
        if rest.startswith(name + '：') or rest.startswith(name + ':'):
            return rest[len(name)+1:]
        return rest
    return text


# ── Card builders ──────────────────────────────────────────────

def render_handoff_card(
    items: list,
    project_id: str = "",
    project_title: str = "项目交接摘要",
    history_items: list | None = None,
) -> dict:
    """交接摘要卡片：8 种状态类型 + 证据追踪。

    V1.19 P1 FEAT-3: 新增 history_items 参数，支持"待确认记忆"
    和"近期失效记忆"两个生命周期维度。
    """
    by_type: dict[str, list] = {}
    for item in items:
        by_type.setdefault(item.state_type, []).append(item)

    card = _header(
        title=f"项目交接摘要 · {project_title or project_id}",
        subtitle="无需翻聊天记录，0 秒了解项目现状",
    )

    # Goals
    goals = [i for i in by_type.get("project_goal", []) if _is_displayable(i)]
    if goals:
        v = _clean_item_text(goals[0])
        card["elements"].append({"tag": "div", "text": {"tag": "lark_md",
            "content": f"**项目目标**\n{v}"}})
        note = _evidence_note(goals[0])
        if note: card["elements"].append(note)
        card["elements"].append(_divider())

    # Owners
    owners = [i for i in by_type.get("owner", []) if _is_displayable(i)]
    if owners:
        seen = set()
        lines = []
        for o in owners:
            v = _clean_item_text(o, 60)
            if v in seen or len(v) < 2: continue
            seen.add(v)
            lines.append(v)
        card["elements"].append(_section("", "负责人", lines[:5]))
        card["elements"].append(_divider())

    # Decisions
    decisions = [i for i in by_type.get("decision", []) if _is_displayable(i)]
    if decisions:
        badge_map = {"confirmed": "ok", "tentative": "?", "preference": "~", "discussion": "..."}
        for d in decisions[:5]:
            ds = getattr(d, "decision_strength", "") or ""
            badge = badge_map.get(ds, "")
            v = _clean_item_text(d, 100)
            line = f"[{badge}] {v}" if badge else f"- {v}"
            card["elements"].append({"tag": "div", "text": {"tag": "lark_md",
                "content": line}})
            note = _evidence_note(d)
            if note: card["elements"].append(note)
        card["elements"].append(_divider())

    # Blockers
    blockers = [i for i in by_type.get("blocker", []) if _is_displayable(i)]
    if blockers:
        for b in blockers[:5]:
            badge = _status_badge(b)
            v = _clean_item_text(b, 100)
            line = f"- {v}"
            if badge:
                line += f"  [{badge}]"
            card["elements"].append({"tag": "div", "text": {"tag": "lark_md",
                "content": line}})
            note = _evidence_note(b)
            if note: card["elements"].append(note)
        card["elements"].append(_divider())

    # Deadline
    deadlines = [i for i in by_type.get("deadline", []) if _is_displayable(i)]
    if deadlines:
        v = _clean_item_text(deadlines[0])
        card["elements"].append({"tag": "div", "text": {"tag": "lark_md",
            "content": f"**DDL**：{v}"}})
        card["elements"].append(_divider())

    # Deferred
    deferred = [i for i in by_type.get("deferred", []) if _is_displayable(i)]
    if deferred:
        lines = [_clean_item_text(d, 80) for d in deferred[:3]]
        card["elements"].append(_section("", "暂缓事项", lines))
        card["elements"].append(_divider())

    # Member status
    members = [i for i in by_type.get("member_status", []) if _is_displayable(i)]
    if members:
        lines = []
        for m in members[:3]:
            sender = _sender_name(m) or "?"
            v = _clean_item_text(m, 60)
            lines.append(f"{sender}：{v}")
        card["elements"].append(_section("", "成员状态", lines))
        card["elements"].append(_divider())

    # Next steps
    nexts = [i for i in by_type.get("next_step", []) if _is_displayable(i)]
    if nexts:
        seen = set()
        lines = []
        for n in nexts[:5]:
            v = _clean_item_text(n, 80)
            if v in seen: continue
            seen.add(v)
            owner_hint = f" ({n.owner})" if n.owner else ""
            badge = _status_badge(n)
            if badge:
                owner_hint += f" [{badge}]"
            lines.append(f"{v}{owner_hint}".strip())
        card["elements"].append(_section("", "建议下一步", lines))

    # Patterns (V1.18)
    from memory.pattern_memory import generate_all_patterns
    patterns = generate_all_patterns(items, project_id)
    if patterns:
        lines = [p.summary[:120] for p in patterns[:3]]
        card["elements"].append(_divider())
        card["elements"].append(_section("", "协作模式", lines))

    # V1.19 P1 FEAT-3: 待确认记忆
    needs_review = [i for i in items
                    if getattr(i, "review_status", "") == "needs_review"
                    and _is_displayable(i)]
    if needs_review:
        card["elements"].append(_divider())
        lines = []
        for item in needs_review[:3]:
            v = _clean_item_text(item, 80)
            reason = _clean_text(getattr(item, "status_reason", "") or "")
            line = f"! {v}"
            if reason:
                line += f"\n  _{reason[:60]}_"
            lines.append(line)
        if len(needs_review) > 3:
            lines.append(f"...等 {len(needs_review)} 条待确认")
        card["elements"].append(_section("", "待确认记忆", lines))

    # V1.19 P1 FEAT-3: 近期纠正/过期/遗忘的记忆
    if history_items:
        from memory.handoff import _collect_recent_invalidated
        invalidated = _collect_recent_invalidated(history_items, project_id)
        if invalidated:
            card["elements"].append(_divider())
            status_labels = {
                "corrected": "已纠正", "expired": "已过期",
                "forgotten": "已遗忘", "superseded": "已替代",
            }
            lines = []
            for item in invalidated[:3]:
                label = status_labels.get(item.status, item.status)
                v = _clean_item_text(item, 80)
                lines.append(f"({label}) {v}")
            card["elements"].append(_section("", "近期失效记忆", lines))

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
        v = _clean_item_text(item, 80)
        line = f"{i}. {v}"
        if badge:
            line += f"  [{badge}]"
        card["elements"].append({"tag": "div", "text": {"tag": "lark_md",
            "content": line}})
        note = _evidence_note(item)
        if note: card["elements"].append(note)
    if len(pending_items) > 5:
        card["elements"].append({"tag": "div", "text": {"tag": "lark_md",
            "content": f"...等 {len(pending_items)} 条"}})
    return card


def render_risk_card(blockers: list, deadlines: list, project_id: str = "") -> dict:
    """风险预警卡片：阻塞 + 临近 DDL。"""
    blockers = [b for b in blockers if _is_displayable(b)]
    deadlines = [d for d in deadlines if _is_displayable(d)]
    card = _header(
        title="风险预警",
        subtitle=f"项目 {project_id} · {len(blockers)} 个阻塞 · {len(deadlines)} 个临近 DDL",
        template="red",
    )
    if deadlines:
        lines = [_clean_item_text(d, 80) for d in deadlines[:3]]
        card["elements"].append(_section("", "临近截止", lines))
    if blockers:
        card["elements"].append(_divider())
        for b in blockers[:5]:
            badge = _status_badge(b)
            v = _clean_item_text(b, 80)
            line = f"- {v}"
            if badge:
                line += f"  [{badge}]"
            card["elements"].append({"tag": "div", "text": {"tag": "lark_md",
                "content": line}})
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
                  not in ("resolved", "obsolete")
                  and _is_displayable(i)]

    card = _header(title="今日站会", subtitle=datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    if yesterday:
        lines = [_clean_item_text(i, 60) for i in yesterday[:5]]
        card["elements"].append(_section("", "昨日进展", lines))
    else:
        card["elements"].append({"tag": "div", "text": {"tag": "lark_md",
            "content": "**昨日进展**\n(无记录)"}})

    card["elements"].append(_divider())
    if today:
        today.sort(key=lambda i: (0 if i.owner else 1, -i.confidence))
        lines = []
        for t in today[:5]:
            owner = f" ({t.owner})" if t.owner else ""
            lines.append(f"{_clean_item_text(t, 60)}{owner}")
        card["elements"].append(_section("", "今日计划", lines))
    else:
        card["elements"].append({"tag": "div", "text": {"tag": "lark_md",
            "content": "**今日计划**\n(无计划)"}})

    card["elements"].append(_divider())
    if unresolved:
        lines = []
        for b in unresolved[:5]:
            badge = _status_badge(b)
            v = _clean_item_text(b, 60)
            line = v
            if badge:
                line += f"  [{badge}]"
            lines.append(line.strip())
        card["elements"].append(_section("", "阻塞与风险", lines))
    else:
        card["elements"].append({"tag": "div", "text": {"tag": "lark_md",
            "content": "[!!] **阻塞与风险**\n(无阻塞)"}})

    return card


# ── FEAT-7: Task confirmation card ──────────────────────────────

def render_confirmation_card(
    owner: str,
    item_text: str,
    time_hint: str = "",
    identity_key: str = "",
    candidate_count: int = 1,
) -> dict:
    """Render an interactive Feishu card for R4 task confirmation.

    Two buttons:
    - "确认创建任务" (primary) -> creates Feishu task
    - "都不是" (danger) -> adds identity_key to ignore list
    """
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "系统识别到可能与你相关的待办"},
            "template": "blue",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md",
             "content": (
                 f"@{owner} 系统识别到可能与你相关的待办：\n\n"
                 f"**{_clean_text(item_text)}**\n\n"
                 + (f"来源时间：{time_hint}\n\n" if time_hint else "")
                 + f"共 {candidate_count} 个候选"
             )}},
            {"tag": "hr"},
            {"tag": "action", "actions": [
                {"tag": "button",
                 "text": {"tag": "plain_text", "content": "确认创建任务"},
                 "type": "primary",
                 "value": {
                     "action": "confirm_task",
                     "identity_key": identity_key,
                     "owner": owner,
                 }},
                {"tag": "button",
                 "text": {"tag": "plain_text", "content": "都不是"},
                 "type": "danger",
                 "value": {
                     "action": "dismiss_task",
                     "identity_key": identity_key,
                 }},
            ]},
            {"tag": "note", "elements": [{"tag": "lark_md",
             "content": "点击「确认创建任务」自动创建飞书任务 · 点击「都不是」不再提醒"}]},
        ],
    }
