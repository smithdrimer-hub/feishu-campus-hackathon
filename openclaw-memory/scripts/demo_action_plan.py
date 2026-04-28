"""Demo script: generate a non-executing action plan from Memory state."""

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
    parser.add_argument("--project-id", default="openclaw-memory-demo", help="Project id to plan for.")
    parser.add_argument("--data-dir", default=str(ROOT / "data"), help="Directory containing memory_state.json.")
    return parser.parse_args()


def main() -> None:
    """Read current memory state and print a non-executing action plan."""
    args = parse_args()
    store = MemoryStore(args.data_dir)
    items = store.list_items(args.project_id)
    actions = generate_action_plan(args.project_id, items)
    print(render_action_plan(args.project_id, actions))


if __name__ == "__main__":
    main()
