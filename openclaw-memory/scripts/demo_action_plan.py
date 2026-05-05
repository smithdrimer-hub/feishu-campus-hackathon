"""Demo script: generate and optionally execute an action plan from Memory state."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.action_planner import generate_action_plan, render_action_plan  # noqa: E402
from memory.store import MemoryStore  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for action plan generation."""
    parser = argparse.ArgumentParser(description="Generate a safe V1 action plan.")
    parser.add_argument("--project-id", default="openclaw-memory-demo",
                        help="Project id to plan for.")
    parser.add_argument("--data-dir", default=str(ROOT / "data"),
                        help="Directory containing memory_state.json.")
    parser.add_argument("--execute", action="store_true",
                        help="Execute planned actions via ActionExecutor")
    parser.add_argument("--auto-confirm", action="store_true",
                        help="Auto-confirm actions (skip requires_confirmation)")
    parser.add_argument("--chat-id", default="",
                        help="Target chat_id for send_message actions")
    return parser.parse_args()


def main() -> None:
    """Read current memory state and generate/execute an action plan."""
    args = parse_args()
    store = MemoryStore(args.data_dir)
    items = store.list_items(args.project_id)
    actions = generate_action_plan(args.project_id, items)

    if args.execute:
        from adapters.lark_cli_adapter import LarkCliAdapter
        from memory.action_executor import ActionExecutor

        adapter = LarkCliAdapter()
        executor = ActionExecutor(adapter, auto_confirm=args.auto_confirm)
        context = {
            "project_id": args.project_id,
            "chat_id": args.chat_id,
        }
        results = executor.execute_plan(actions, context)

        print(f"执行结果：{len(results)} 个操作")
        for r in results:
            status = "OK" if r.success else "SKIP"
            detail = f" ({r.error[:60]})" if r.error else ""
            print(f"  [{status}] {r.action.action_type}: {r.action.title[:60]}{detail}")
    else:
        print(render_action_plan(args.project_id, actions))


if __name__ == "__main__":
    main()
