"""Tests for Memory versioning and supersedes behavior."""

import sys
import unittest
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.schema import MemoryItem, SourceRef  # noqa: E402
from memory.store import MemoryStore  # noqa: E402


def make_ref(message_id: str) -> SourceRef:
    """Build a source reference for conflict-resolution tests."""
    return SourceRef(
        type="message",
        chat_id="oc_test",
        message_id=message_id,
        excerpt="负责人更新",
        created_at="2026-04-25T10:00:00+08:00",
    )


def make_owner(value: str, message_id: str) -> MemoryItem:
    """Build an owner MemoryItem with a stable identity key."""
    return MemoryItem(
        project_id="demo",
        state_type="owner",
        key="current_owner",
        current_value=value,
        rationale="负责人消息",
        owner=value,
        status="active",
        confidence=0.8,
        source_refs=[make_ref(message_id)],
    )


def fresh_test_dir(name: str) -> Path:
    """Return a clean project-local temp directory for tests."""
    path = ROOT / ".test-tmp" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


class ConflictResolutionTest(unittest.TestCase):
    """Verify newer state supersedes older state while preserving history."""

    def test_new_state_supersedes_old_state(self) -> None:
        """Upserting the same identity key should increment version and record supersedes."""
        store = MemoryStore(fresh_test_dir("conflict_resolution"))
        first = make_owner("Alice", "om_1")
        store.upsert_items([first])
        second = make_owner("Bob", "om_2")
        active, _ = store.upsert_items([second])
        owners = [item for item in active if item.state_type == "owner"]
        self.assertEqual(len(owners), 1)
        self.assertEqual(owners[0].current_value, "Bob")
        self.assertEqual(owners[0].version, 2)
        self.assertIn(first.memory_id, owners[0].supersedes)
        self.assertEqual(len(store.list_history()), 1)


if __name__ == "__main__":
    unittest.main()
