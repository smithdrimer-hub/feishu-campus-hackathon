"""Project Steward Review Desk — audit high-risk memories before activation.

V1.15: CLI for the project steward (human) to review memories flagged as
needs_review. Approved items become active; rejected items move to history.

Write operations (approve/reject/modify/merge) require steward identity
verification via steward.json config or explicit --steward-id override.

Usage:
  python scripts/demo_review_desk.py --data-dir data/ --project-id xxx   [list]
  python scripts/demo_review_desk.py --data-dir data/ --approve mem_xxx  [approve]
  python scripts/demo_review_desk.py --data-dir data/ --reject mem_xxx   [reject]
  python scripts/demo_review_desk.py --data-dir data/ --modify mem_xxx --value "new" [modify]
  python scripts/demo_review_desk.py --data-dir data/ --merge mem_aaa --from mem_bbb [merge]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.store import MemoryStore


# ── Steward identity ───────────────────────────────────────────

def _load_steward_config(data_dir: Path) -> dict:
    """Load steward.json or return empty config."""
    path = data_dir / "steward.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _get_current_open_id() -> str:
    """Get current user's open_id from lark-cli doctor."""
    try:
        result = subprocess.run(
            ["lark-cli.cmd", "doctor"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False,
        )
        data = json.loads(result.stdout)
        checks = data.get("checks", [])
        for c in checks:
            if c.get("name") == "token_exists":
                msg = c.get("message", "")
                if "(" in msg:
                    return msg.split("(")[-1].rstrip(")")
    except Exception:
        pass
    return ""


def _verify_steward(data_dir: Path, steward_id: str = "",
                    chat_id: str = "") -> bool:
    """Verify that the current operator is an authorized steward.

    Two checks (both must pass when steward.json exists):
    1. Feishu chat membership — user must be in the target group
    2. steward.json authorization — user must be in the authorized list
    If steward.json doesn't exist, only chat membership is checked.
    """
    current_id = steward_id or _get_current_open_id()
    if not current_id:
        print("  Warning: Cannot verify identity (lark-cli doctor failed).")
        print("  Use --steward-id to explicitly provide your open_id.")
        return False

    # Check 1: Feishu chat membership (user must be in the group)
    if chat_id:
        in_chat = _is_chat_member(chat_id, current_id)
        if not in_chat:
            print(f"  Denied: {current_id} is not a member of chat {chat_id}")
            return False
        print(f"  Chat membership verified: {current_id}")
    else:
        print("  Warning: No --chat-id provided. Cannot verify chat membership.")

    # Check 2: steward.json authorization (if exists)
    config = _load_steward_config(data_dir)
    allowed = config.get("steward_open_ids", [])
    if allowed:
        if current_id not in allowed:
            print(f"  Denied: {current_id} not in steward.json authorized list.")
            print(f"  Authorized stewards: {allowed}")
            return False
        print(f"  Steward authorization verified: {current_id}")
        return True

    # No steward.json → anyone in the chat can review
    if chat_id:
        print(f"  No steward.json; all chat members can review. Verified: {current_id}")
        return True

    print(f"  Warning: No steward.json and no --chat-id for membership check.")
    print(f"  Create steward.json: {{\"steward_open_ids\": [\"{current_id}\"]}}")
    return False


def _is_chat_member(chat_id: str, open_id: str) -> bool:
    """Check if a user is a member of a Feishu chat via lark-cli."""
    try:
        import json as _json
        params = _json.dumps({"chat_id": chat_id, "page_size": 100})
        result = subprocess.run(
            ["lark-cli.cmd", "im", "chat.members", "get",
             "--params", params, "--as", "user"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False,
        )
        if result.returncode != 0:
            return False
        data = _json.loads(result.stdout)
        members = data.get("data", {}).get("items", []) or []
        for m in members:
            if m.get("member_id", "") == open_id:
                return True
    except Exception:
        pass
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Project Steward Review Desk — audit high-risk memories",
    )
    parser.add_argument("--data-dir", required=True, help="Data directory path")
    parser.add_argument("--project-id", default=None, help="Filter by project")
    parser.add_argument("--approve", default=None, help="Approve a memory (mem_xxx)")
    parser.add_argument("--reject", default=None, help="Reject a memory (mem_xxx)")
    parser.add_argument("--modify", default=None,
                        help="Modify a memory's value (mem_xxx)")
    parser.add_argument("--value", default=None,
                        help="New value for --modify")
    parser.add_argument("--merge", default=None,
                        help="Merge target memory_id (merge source into this)")
    parser.add_argument("--from", default=None, dest="from_id",
                        help="Source memory_id for --merge")
    parser.add_argument("--blocker-status", default=None,
                        choices=["acknowledged", "waiting_external", "resolved", "obsolete"],
                        help="Update blocker lifecycle status (use with --approve)")
    parser.add_argument("--blocker-extra", default=None,
                        help="JSON extra: {\"dependency_owner\":\"X\",\"blocked_item\":\"Y\"}")
    parser.add_argument("--steward-id", default="",
                        help="Explicit steward open_id (overrides auto-detect)")
    parser.add_argument("--chat-id", default="",
                        help="Chat ID for Feishu group role verification")
    parser.add_argument("--all", action="store_true",
                        help="Show all memories, not just needs_review")
    return parser.parse_args()


def list_review(store: MemoryStore, project_id: str | None, show_all: bool) -> int:
    """List pending review items with enhanced evidence display. Returns count."""
    items = store.list_items(project_id)
    pending = [
        i for i in items
        if getattr(i, "review_status", "") == "needs_review"
    ]
    if show_all:
        pending = items
        print(f"\n  All items ({len(items)} total):\n")
    elif not pending:
        print("\n  No items pending review.\n")
        return 0
    else:
        print(f"\n  Pending review: {len(pending)} item(s)\n")

    # Load raw events for full-text evidence
    raw_events = {}
    try:
        for ev in store.read_raw_events(project_id):
            mid = ev.get("message_id", "")
            if mid:
                raw_events[mid] = ev
    except Exception:
        pass

    for i, item in enumerate(pending, 1):
        ds = getattr(item, "decision_strength", "")
        rs = getattr(item, "review_status", "")
        ds_label = f" [{ds}]" if ds else ""
        rs_label = f" ({rs})" if rs else ""

        conflict_label = ""
        item_meta = getattr(item, "metadata", None) or {}
        if item_meta.get("conflict_status") == "conflicting":
            conflict_label = " [CONFLICT]"

        print(f"  {i}. [{item.state_type}]{ds_label}{rs_label}{conflict_label}")
        print(f"     memory_id: {item.memory_id}")
        print(f"     value:     {item.current_value[:150]}")
        print(f"     owner:     {item.owner or '(none)'}")
        print(f"     confidence: {item.confidence:.2f}  version: v{item.version}")

        # Enhanced evidence with full original text
        if item.source_refs:
            for j, ref in enumerate(item.source_refs[:3]):
                sender = ref.sender_name or "(unknown)"
                created = ref.created_at[:16] if ref.created_at else ""
                print(f"     evidence[{j+1}]: {sender} ({created})")
                # Show full original text from raw_events if available
                if ref.message_id and ref.message_id in raw_events:
                    full_text = str(
                        raw_events[ref.message_id].get("text", "")
                        or raw_events[ref.message_id].get("content", "")
                    )[:300]
                    print(f"              原文: {full_text}")
                else:
                    print(f"              摘要: {ref.excerpt[:120]}")
                if ref.source_url:
                    print(f"              链接: {ref.source_url}")
        else:
            print(f"     evidence:  (none — no source refs)")
            print(f"     reason:    auto-marked needs_review (no evidence)")

        # Show override reason if applicable
        if item.supersedes:
            print(f"     supersedes: {len(item.supersedes)} older version(s)")

        # V1.15 OPT-4: suggest similar items for potential merge
        similar = _find_similar(items, item)
        if similar:
            print(f"     similar (consider --merge):")
            for sim_id, sim_val, sim_score in similar[:2]:
                print(f"       [{sim_score:.0%}] {sim_id[:20]}... — {sim_val[:60]}")

        print()

    return len(pending)


def _find_similar(
    items: list, target, threshold: float = 0.35,
) -> list[tuple[str, str, float]]:
    """Find items with similar content to target for merge suggestion."""
    results = []
    for item in items:
        if item.memory_id == target.memory_id:
            continue
        if item.state_type != target.state_type:
            continue
        # Simple bigram similarity
        def _bigrams(t):
            t = t.replace(" ", "").lower()
            return {t[i:i+2] for i in range(len(t)-1)}
        b1 = _bigrams(target.current_value)
        b2 = _bigrams(item.current_value)
        if not b1 or not b2:
            continue
        sim = len(b1 & b2) / len(b1 | b2)
        if sim > threshold:
            results.append((item.memory_id, item.current_value, sim))
    results.sort(key=lambda x: x[2], reverse=True)
    return results


def do_review(store: MemoryStore, memory_id: str, action: str,
              new_value: str | None = None) -> None:
    """Approve, reject, or modify a single memory."""
    if action == "approve":
        result = store.update_item_review(memory_id, "approved")
        if result:
            print(f"  Approved: {result.memory_id} — {result.current_value[:80]}")
        else:
            print(f"  Not found: {memory_id}")
    elif action == "reject":
        result = store.update_item_review(memory_id, "rejected")
        if result:
            print(f"  Rejected: {result.memory_id} (moved to history)")
        else:
            print(f"  Not found: {memory_id}")
    elif action == "modify":
        if not new_value:
            print("  Error: --value is required for --modify")
            return
        result = store.update_item_review(memory_id, "approved", new_value)
        if result:
            print(f"  Modified and approved: {result.memory_id}")
            print(f"    new value: {result.current_value[:120]}")
        else:
            print(f"  Not found: {memory_id}")
    elif action == "merge":
        result = store.merge_items(memory_id)
        if result:
            print(f"  Merged: source refs combined into {result.memory_id}")
        else:
            print(f"  Merge failed: check memory_id")


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir)
    store = MemoryStore(data_dir)
    store.ensure_files()

    is_write = any([args.approve, args.reject, args.modify, args.merge,
                    args.blocker_status])
    if is_write:
        if not _verify_steward(data_dir, args.steward_id, args.chat_id):
            print("  Write operation denied. Only authorized stewards can modify memories.")
            return

    if args.blocker_status:
        mid = args.approve  # --blocker-status uses --approve as memory_id
        if not mid:
            print("  Error: --blocker-status requires --approve <memory_id>")
            return
        extra = {}
        if args.blocker_extra:
            try:
                extra = json.loads(args.blocker_extra)
            except json.JSONDecodeError:
                print("  Warning: --blocker-extra is not valid JSON, ignoring.")
        operator_id = args.steward_id or _get_current_open_id()
        bs = args.blocker_status
        if bs == "acknowledged" and operator_id:
            extra["acknowledged_by"] = operator_id
        if bs == "resolved" and operator_id:
            extra["resolved_by"] = operator_id
        result = store.update_blocker_status(mid, bs, extra)
        if result:
            print(f"  Blocker status: {mid[:20]}... -> {bs}")
        else:
            print(f"  Not found or not a blocker: {mid}")
    elif args.approve:
        do_review(store, args.approve, "approve")
    elif args.reject:
        do_review(store, args.reject, "reject")
    elif args.modify:
        do_review(store, args.modify, "modify", args.value)
    elif args.merge:
        do_review(store, args.merge, "merge")
    else:
        print("=" * 60)
        print("  Project Steward Review Desk")
        print("=" * 60)
        count = list_review(store, args.project_id, args.all)
        if count > 0:
            print(f"  Use --approve / --reject / --modify / --merge with memory_id.")
        print()


if __name__ == "__main__":
    main()
