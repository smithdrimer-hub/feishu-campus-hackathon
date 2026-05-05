"""Action execution log for idempotency, cooldown, and audit trail.

V1.14: JSONL-based action log. Each line records one executed action
proposal with its outcome. Used by ActionTrigger for cooldown checks
and by the demo pipeline for audit display.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def write_action_log(
    log_path: str | Path,
    project_id: str,
    action_type: str,
    proposal_title: str,
    idempotency_key: str,
    success: bool,
    output_data: dict | None = None,
    error: str = "",
) -> None:
    """Append one action execution record to the JSONL log."""
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project_id": project_id,
        "action_type": action_type,
        "proposal_title": proposal_title[:200],
        "idempotency_key": idempotency_key,
        "success": success,
        "output_data": output_data or {},
        "error": error[:200],
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_action_log(log_path: str | Path, limit: int = 200) -> list[dict]:
    """Read recent action log entries (newest last)."""
    path = Path(log_path)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return entries[-limit:]


def has_recent_action(
    log_path: str | Path,
    idempotency_key: str,
    cooldown_seconds: float = 86400,
) -> bool:
    """Check if an action with the same idempotency_key was executed
    within the cooldown window.

    Args:
        log_path: Path to action_log.jsonl.
        idempotency_key: The proposal's dedup key.
        cooldown_seconds: Minimum seconds between repeated actions (default 24h).

    Returns:
        True if a matching action was logged within the cooldown window.
    """
    entries = read_action_log(log_path)
    now = datetime.now(timezone.utc)
    for entry in reversed(entries):
        if entry.get("idempotency_key") != idempotency_key:
            continue
        ts_str = entry.get("timestamp", "")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is not None:
                ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
        except (ValueError, TypeError):
            continue
        elapsed = (now.replace(tzinfo=None) - ts).total_seconds()
        if elapsed < cooldown_seconds:
            return True
    return False
