"""Demo script: run V1.1 Fake LLM extraction on bundled example data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.action_planner import generate_action_plan, render_action_plan  # noqa: E402
from memory.engine import MemoryEngine  # noqa: E402
from memory.extractor import LLMExtractor, RuleBasedExtractor  # noqa: E402
from memory.handoff import generate_handoff  # noqa: E402
from memory.llm_provider import FakeLLMProvider  # noqa: E402
from memory.store import MemoryStore  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the V1.1 example runner."""
    parser = argparse.ArgumentParser(description="Run V1.1 example through LLM extraction, handoff, and plan.")
    parser.add_argument("--project-id", default="openclaw-memory-demo", help="Project id to process.")
    parser.add_argument(
        "--example",
        default=str(ROOT / "examples" / "handoff_scenario_01.jsonl"),
        help="Path to example raw events JSONL.",
    )
    parser.add_argument(
        "--data-dir",
        default=str(ROOT / "data" / "example_run"),
        help="Output directory for raw_events.jsonl and memory_state.json.",
    )
    return parser.parse_args()


def read_jsonl(path: str | Path) -> list[dict]:
    """Read a JSONL file and return decoded event dicts."""
    events = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def main() -> None:
    """Run example extraction and print handoff plus action plan."""
    args = parse_args()
    events = read_jsonl(args.example)
    store = MemoryStore(args.data_dir)
    extractor = LLMExtractor(FakeLLMProvider(), fallback=RuleBasedExtractor())
    engine = MemoryEngine(store, extractor=extractor)
    items = engine.ingest_events(events)
    handoff = generate_handoff(args.project_id, items)
    actions = generate_action_plan(args.project_id, items)
    print(handoff)
    print(render_action_plan(args.project_id, actions))
    print(f"Data written to: {Path(args.data_dir).resolve()}")


if __name__ == "__main__":
    main()
