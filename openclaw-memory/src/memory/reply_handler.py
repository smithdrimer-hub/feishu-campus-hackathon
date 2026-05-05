"""Handle confirmation replies and @bot commands from Feishu events.

V1.16: Processes real-time messages received via WebSocket event listener.
Two modes:
  1. Confirmation reply detection — checks if a message is a reply to a
     previously-sent confirmation question, and parses yes/no answers.
  2. @bot command detection — responds to commands like "状态", "风险", etc.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Confirmation reply handling ───────────────────────────────

QUESTION_MAP_PATH = "data/question_map.jsonl"


def record_question(question_msg_id: str, candidates: list[str],
                    project_id: str = "") -> None:
    """Record a sent confirmation question for later reply matching."""
    entry = {
        "question_msg_id": question_msg_id,
        "candidates": candidates,
        "project_id": project_id,
    }
    path = Path(QUESTION_MAP_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def find_question(reply_to_msg_id: str) -> dict | None:
    """Look up a confirmation question by its message_id."""
    path = Path(QUESTION_MAP_PATH)
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            if entry.get("question_msg_id") == reply_to_msg_id:
                return entry
        except json.JSONDecodeError:
            pass
    return None


def parse_confirmation(text: str) -> tuple[bool, list[int]]:
    """Parse a confirmation reply.

    Returns (is_confirmation, confirmed_indices).
    Examples:
        "确认 1,2" → (True, [1, 2])
        "都不是"   → (True, [])  — confirms none
        "好的收到"  → (False, []) — not a confirmation reply
    """
    if any(w in text for w in ("都不是", "都不是", "都不对", "没有", "不对", "否")):
        return True, []
    if any(w in text for w in ("确认", "是", "对", "ok", "可以")):
        import re
        nums = re.findall(r"\d+", text)
        indices = [int(n) for n in nums if 1 <= int(n) <= 10]
        return True, indices
    return False, []


# ── @bot command handling ──────────────────────────────────────

BOT_COMMANDS = {
    "状态": "generate_state_panel",
    "面板": "generate_state_panel",
    "风险": "generate_risk_summary",
    "预警": "generate_risk_summary",
    "待审核": "list_needs_review",
    "审核": "list_needs_review",
    "站会": "generate_standup",
    "交接": "generate_handoff",
    "摘要": "generate_handoff",
}


def detect_bot_command(text: str) -> str | None:
    """Detect if a message contains a @bot command. Returns command action or None."""
    for keyword, action in BOT_COMMANDS.items():
        if keyword in text:
            return action
    return None


def execute_bot_command(
    action: str, chat_id: str, project_id: str,
    store: Any = None, adapter: Any = None,
) -> str:
    """Execute a bot command and return the response text to send."""
    if action == "generate_state_panel":
        return _cmd_state_panel(project_id, store, adapter, chat_id)

    if action == "generate_risk_summary":
        return _cmd_risk_summary(project_id, store)

    if action == "list_needs_review":
        return _cmd_needs_review(project_id, store)

    if action in ("generate_standup", "generate_handoff"):
        return _cmd_standup(project_id, store)

    return "未知指令"


# ── Internal command implementations ───────────────────────────

def _cmd_state_panel(project_id: str, store, adapter, chat_id: str) -> str:
    from memory.project_state import build_group_project_state, \
        render_group_state_panel_text
    items = store.list_items(project_id)
    state = build_group_project_state(project_id, items)
    text = render_group_state_panel_text(state)
    if adapter and chat_id:
        adapter.send_message(chat_id, text, msg_type="markdown")
    return text


def _cmd_risk_summary(project_id: str, store) -> str:
    items = store.list_items(project_id)
    blockers = [i for i in items if i.state_type == "blocker" and i.status == "active"]
    unresolved = []
    for b in blockers:
        meta = getattr(b, "metadata", None) or {}
        if meta.get("blocker_status", "open") not in ("resolved", "obsolete"):
            unresolved.append(b)

    deadlines = [i for i in items if i.state_type == "deadline" and i.status == "active"]
    from memory.date_parser import deadline_is_imminent
    imminent = [d for d in deadlines if deadline_is_imminent(d.current_value, within_days=3)]

    lines = [f"## 风险摘要 — {project_id}", ""]
    if imminent:
        lines.append(f"### 临近截止 ({len(imminent)})")
        for d in imminent:
            lines.append(f"- {d.current_value[:100]}")
    if unresolved:
        lines.append(f"### 未解决阻塞 ({len(unresolved)})")
        for b in unresolved:
            lines.append(f"- {b.current_value[:100]}")
    if not imminent and not unresolved:
        lines.append("当前无高风险项。")
    return "\n".join(lines)


def _cmd_needs_review(project_id: str, store) -> str:
    items = store.list_items(project_id)
    pending = [i for i in items if getattr(i, "review_status", "") == "needs_review"]
    lines = [f"## 待审核项 — {project_id}", ""]
    if pending:
        for i, item in enumerate(pending[:10], 1):
            ds = getattr(item, "decision_strength", "")
            ds_label = f" [{ds}]" if ds else ""
            lines.append(f"{i}. [{item.state_type}]{ds_label} {item.current_value[:80]}")
    else:
        lines.append("无待审核项。")
    return "\n".join(lines)


def _cmd_standup(project_id: str, store) -> str:
    from memory.project_state import render_standup_summary
    items = store.list_items(project_id)
    return render_standup_summary(items, project_id)
