"""Run realistic collaboration scenarios through the Memory Engine.

This is a smoke-test runner for demo readiness. It intentionally validates
coarse outcomes instead of exact item counts, because realistic chat data can
produce multiple valid memories for one scenario.

Usage:
    python scripts/run_realistic_scenarios.py
    python scripts/run_realistic_scenarios.py --print-panels
    python scripts/run_realistic_scenarios.py --keep-data-dir data/realistic_run
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.engine import MemoryEngine  # noqa: E402
from memory.extractor import RuleBasedExtractor  # noqa: E402
from memory.handoff import generate_handoff  # noqa: E402
from memory.project_state import (  # noqa: E402
    build_group_project_state,
    render_group_state_panel_text,
)
from memory.store import MemoryStore  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run realistic Feishu-like scenarios through Memory Engine.",
    )
    parser.add_argument(
        "--scenario-file",
        default=str(ROOT / "examples" / "realistic_scenarios.jsonl"),
        help="Path to realistic scenario JSONL.",
    )
    parser.add_argument(
        "--print-panels",
        action="store_true",
        help="Print rendered project state panel and handoff preview.",
    )
    parser.add_argument(
        "--keep-data-dir",
        default="",
        help="Keep generated store files under this directory for inspection.",
    )
    return parser.parse_args()


def load_scenarios(path: str | Path) -> list[dict]:
    scenarios: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            scenarios.append(json.loads(line))
    return scenarios


def _item_text(item) -> str:
    parts = [
        item.current_value or "",
        item.owner or "",
        item.rationale or "",
    ]
    for ref in item.source_refs:
        parts.append(ref.excerpt or "")
        parts.append(ref.sender_name or "")
    return " ".join(parts)


def _validate_scenario(scenario: dict, items: list) -> list[str]:
    errors: list[str] = []
    expected_types = set(scenario.get("expected_state_types", []))
    extracted_types = {item.state_type for item in items}

    missing_types = sorted(expected_types - extracted_types)
    if missing_types:
        errors.append(f"missing state types: {', '.join(missing_types)}")

    for forbidden in scenario.get("forbidden_state_types", []):
        if forbidden in extracted_types:
            errors.append(f"unexpected state type extracted: {forbidden}")

    by_type: dict[str, list] = defaultdict(list)
    for item in items:
        by_type[item.state_type].append(item)

    for state_type, keywords in scenario.get("expected_keywords", {}).items():
        type_items = by_type.get(state_type, [])
        joined = "\n".join(_item_text(item) for item in type_items)
        missing = [kw for kw in keywords if kw not in joined]
        if missing:
            errors.append(
                f"{state_type} missing keywords {missing}; "
                f"extracted {len(type_items)} item(s)",
            )

    # Evidence chain should exist for all active memories generated from chat.
    no_evidence = [
        item.memory_id
        for item in items
        if not item.source_refs or not item.source_refs[0].message_id
    ]
    if no_evidence:
        errors.append(f"{len(no_evidence)} item(s) missing source evidence")

    return errors


def _run_one(scenario: dict, data_dir: Path, print_panels: bool) -> dict:
    store = MemoryStore(data_dir)
    engine = MemoryEngine(store, RuleBasedExtractor())
    engine.ingest_events(scenario["events"], debounce=False)
    items = store.list_items(scenario["project_id"])
    history = store.list_history(scenario["project_id"])

    errors = _validate_scenario(scenario, items)
    type_counts = Counter(item.state_type for item in items)

    panel_text = ""
    handoff_text = ""
    if print_panels:
        state = build_group_project_state(scenario["project_id"], items)
        panel_text = render_group_state_panel_text(state)
        handoff_text = generate_handoff(scenario["project_id"], items)

    return {
        "scenario_id": scenario["scenario_id"],
        "title": scenario.get("title", scenario["scenario_id"]),
        "project_id": scenario["project_id"],
        "passed": not errors,
        "errors": errors,
        "active_count": len(items),
        "history_count": len(history),
        "type_counts": dict(sorted(type_counts.items())),
        "panel_text": panel_text,
        "handoff_text": handoff_text,
        "data_dir": str(data_dir),
    }


def _print_result(result: dict, print_panels: bool) -> None:
    status = "PASS" if result["passed"] else "FAIL"
    print(f"\n[{status}] {result['scenario_id']} — {result['title']}")
    print(f"  project_id: {result['project_id']}")
    print(f"  active memories: {result['active_count']}, history: {result['history_count']}")
    print(f"  type counts: {result['type_counts']}")
    if result["errors"]:
        print("  errors:")
        for err in result["errors"]:
            print(f"    - {err}")
    print(f"  data dir: {result['data_dir']}")

    if print_panels:
        print("\n  --- Project State Panel ---")
        print(result["panel_text"])
        print("  --- Handoff Preview ---")
        # Keep terminal output readable while still showing the artifact shape.
        lines = result["handoff_text"].splitlines()
        preview = "\n".join(lines[:35])
        if len(lines) > 35:
            preview += "\n  ... (handoff truncated)"
        print(preview)


def main() -> None:
    args = parse_args()
    scenarios = load_scenarios(args.scenario_file)
    if not scenarios:
        raise SystemExit("No scenarios found.")

    results: list[dict] = []
    if args.keep_data_dir:
        base_dir = Path(args.keep_data_dir)
        if base_dir.exists():
            shutil.rmtree(base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        for scenario in scenarios:
            scenario_dir = base_dir / scenario["scenario_id"]
            result = _run_one(scenario, scenario_dir, args.print_panels)
            results.append(result)
            _print_result(result, args.print_panels)
    else:
        with TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            for scenario in scenarios:
                scenario_dir = base_dir / scenario["scenario_id"]
                result = _run_one(scenario, scenario_dir, args.print_panels)
                results.append(result)
                _print_result(result, args.print_panels)

    passed = sum(1 for result in results if result["passed"])
    total = len(results)
    print(f"\nSummary: {passed}/{total} realistic scenarios passed.")

    if passed != total:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
