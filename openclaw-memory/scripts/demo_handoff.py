"""Demo script: process raw events and print a handoff summary."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.engine import MemoryEngine  # noqa: E402
from memory.handoff import generate_handoff  # noqa: E402
from memory.store import MemoryStore  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for handoff generation."""
    parser = argparse.ArgumentParser(description="Generate an interruption handoff summary.")
    parser.add_argument("--project-id", default="openclaw-memory-demo", help="Project id to summarize.")
    parser.add_argument("--data-dir", default=str(ROOT / "data"), help="Directory containing raw_events.jsonl.")
    return parser.parse_args()


def main() -> None:
    """Process new raw events and print the handoff summary."""
    args = parse_args()
    store = MemoryStore(args.data_dir)
    engine = MemoryEngine(store)
    items = engine.process_new_events(args.project_id)
    print(generate_handoff(args.project_id, items))


if __name__ == "__main__":
    main()
