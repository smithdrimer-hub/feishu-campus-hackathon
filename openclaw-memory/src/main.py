"""Small command entrypoint for the V1 Memory Engine demos."""

from pathlib import Path

from memory.action_planner import generate_action_plan, render_action_plan
from memory.engine import MemoryEngine
from memory.handoff import generate_handoff
from memory.store import MemoryStore


def run_local_demo(project_id: str = "openclaw-memory-demo") -> str:
    """Process local raw events and return handoff plus action plan text."""
    root = Path(__file__).resolve().parents[1]
    store = MemoryStore(root / "data")
    engine = MemoryEngine(store)
    items = engine.process_new_events(project_id)
    handoff = generate_handoff(project_id, items)
    plan = render_action_plan(project_id, generate_action_plan(project_id, items))
    return f"{handoff}\n{plan}"


def main() -> None:
    """Print the local demo output for the default project id."""
    print(run_local_demo())


if __name__ == "__main__":
    main()
