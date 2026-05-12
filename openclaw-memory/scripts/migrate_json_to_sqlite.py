"""V1.19 P3: JSON → SQLite 存储迁移工具。

用法:
  python scripts/migrate_json_to_sqlite.py                     # 迁移
  python scripts/migrate_json_to_sqlite.py --dry-run           # 仅验证不写入
  python scripts/migrate_json_to_sqlite.py --rollback          # SQLite → JSON 导出
  python scripts/migrate_json_to_sqlite.py --data-dir data/auto  # 指定数据目录
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def load_json_data(data_dir: Path) -> dict:
    """从 JSON 文件加载完整状态和原始事件。"""
    state_path = data_dir / "memory_state.json"
    events_path = data_dir / "raw_events.jsonl"

    state = {"items": [], "history": [], "processed_event_ids": []}
    events = []

    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [WARN] 无法读取 memory_state.json: {e}")

    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    return {"state": state, "events": events}


def migrate(data_dir: Path, dry_run: bool = False) -> dict:
    """执行迁移，返回报告 dict。"""
    from memory.store_sqlite import SQLiteStorageBackend

    print(f"数据源: {data_dir}")
    data = load_json_data(data_dir)

    state = data["state"]
    events = data["events"]
    items = state.get("items", [])
    history = state.get("history", [])
    processed = state.get("processed_event_ids", [])

    print(f"  active items: {len(items)}")
    print(f"  history items: {len(history)}")
    print(f"  processed events: {len(processed)}")
    print(f"  raw events: {len(events)}")

    if dry_run:
        print("\n  [DRY RUN] 仅验证，不实际写入")
        report = _validate(items, history, processed, events)
        print(f"  验证 {'通过' if report['valid'] else '未通过'}")
        return report

    # 执行迁移
    backend = SQLiteStorageBackend(data_dir)
    backend.ensure_files()

    try:
        backend.save_state(items, history, processed)
        if events:
            backend.append_raw_events(events)
        backend.mark_processed(processed)
    finally:
        backend.close()

    print("\n  迁移完成。正在验证...")
    report = _validate(items, history, processed, events, backend, data_dir)
    if report["valid"]:
        print(f"  [OK] 迁移验证通过")
    else:
        print(f"  [FAIL] 迁移验证失败: {report['errors']}")
    return report


def rollback(data_dir: Path) -> None:
    """从 SQLite 导出回 JSON 文件。"""
    from memory.store_sqlite import SQLiteStorageBackend

    backend = SQLiteStorageBackend(data_dir)
    try:
        state = backend.load_state()
        events = backend.read_raw_events()
    finally:
        backend.close()

    # 写回 JSON
    state_path = data_dir / "memory_state.json"
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    events_path = data_dir / "raw_events.jsonl"
    with events_path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    print(f"  已导出: {len(state.get('items',[]))} active + "
          f"{len(state.get('history',[]))} history items, "
          f"{len(events)} raw events → {data_dir}")


# ── 验证 ─────────────────────────────────────────────────────────

def _validate(items: list, history: list, processed: list, events: list,
              backend=None, data_dir: Path | None = None) -> dict:
    """字段级验证。"""
    report: dict = {"valid": True, "errors": [], "sample_check": {}}

    # 条数验证
    if backend is not None:
        state = backend.load_state()
        if len(state.get("items", [])) != len(items):
            report["valid"] = False
            report["errors"].append(
                f"active items 数量不匹配: {len(state.get('items',[]))} vs {len(items)}")
        if len(state.get("history", [])) != len(history):
            report["valid"] = False
            report["errors"].append(
                f"history items 数量不匹配: {len(state.get('history',[]))} vs {len(history)}")
        if set(state.get("processed_event_ids", [])) != set(processed):
            report["valid"] = False
            report["errors"].append("processed_event_ids 不匹配")
        if len(backend.read_raw_events()) != len(events):
            report["valid"] = False
            report["errors"].append("raw_events 数量不匹配")

        # 字段级抽查（10% 条目的关键字段）
        sample_size = max(5, len(items) // 10)
        if items and backend is not None:
            loaded_state = backend.load_state()
            loaded_items = {i.get("memory_id", ""): i for i in loaded_state.get("items", [])}
            loaded_history = {i.get("memory_id", ""): i for i in loaded_state.get("history", [])}
            all_loaded = {**loaded_items, **loaded_history}

            samples = random.sample(items, min(sample_size, len(items)))
            mismatches = 0
            for orig in samples:
                mid = orig.get("memory_id", "")
                loaded = all_loaded.get(mid)
                if loaded is None:
                    mismatches += 1
                    continue
                checks = {
                    "current_value": orig.get("current_value") == loaded.get("current_value"),
                    "state_type": orig.get("state_type") == loaded.get("state_type"),
                    "owner": orig.get("owner") == loaded.get("owner"),
                    "confidence": abs(float(orig.get("confidence", 0)) - float(loaded.get("confidence", 0))) < 0.01,
                }
                failed = [k for k, v in checks.items() if not v]
                if failed:
                    mismatches += 1
            report["sample_check"] = {
                "sampled": len(samples), "mismatches": mismatches,
                "pass_rate": f"{(len(samples) - mismatches) / len(samples) * 100:.1f}%",
            }
            if mismatches > sample_size // 10:
                report["valid"] = False
                report["errors"].append(
                    f"字段抽查失败: {mismatches}/{len(samples)} 不匹配")

    return report


# ── CLI ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="JSON → SQLite 迁移工具")
    parser.add_argument("--data-dir", default="data/auto",
                        help="数据目录（默认 data/auto）")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅验证不写入")
    parser.add_argument("--rollback", action="store_true",
                        help="从 SQLite 导出回 JSON")
    args = parser.parse_args()

    data_dir = ROOT / args.data_dir
    if not data_dir.exists():
        print(f"错误: 数据目录不存在: {data_dir}")
        sys.exit(1)

    if args.rollback:
        print("SQLite → JSON 回滚")
        rollback(data_dir)
    else:
        print("JSON → SQLite 迁移")
        if args.dry_run:
            print("(仅验证模式)\n")
        report = migrate(data_dir, dry_run=args.dry_run)
        if not report["valid"]:
            sys.exit(1)


if __name__ == "__main__":
    main()
