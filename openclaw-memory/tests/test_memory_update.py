"""Tests for extracting and storing current Memory state."""

import sys
import unittest
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.engine import MemoryEngine  # noqa: E402
from memory.store import MemoryStore  # noqa: E402


def make_event(text: str, message_id: str = "om_test_1") -> dict:
    """Build a normalized raw event for tests."""
    return {
        "project_id": "demo",
        "chat_id": "oc_test",
        "message_id": message_id,
        "text": text,
        "created_at": "2026-04-25T10:00:00+08:00",
    }


def fresh_test_dir(name: str) -> Path:
    """Return a clean project-local temp directory for tests."""
    path = ROOT / ".test-tmp" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


class MemoryUpdateTest(unittest.TestCase):
    """Verify raw events can become structured Memory items."""

    def test_extracts_memory_with_source_refs(self) -> None:
        """A next-step event should produce a MemoryItem with message evidence."""
        store = MemoryStore(fresh_test_dir("memory_update"))
        engine = MemoryEngine(store)
        items = engine.ingest_events([make_event("下一步：由 C 负责实现 LarkCliAdapter")])
        self.assertEqual(len(items), 2)
        next_steps = [item for item in items if item.state_type == "next_step"]
        self.assertTrue(next_steps)
        self.assertEqual(next_steps[0].source_refs[0].chat_id, "oc_test")
        self.assertEqual(next_steps[0].source_refs[0].message_id, "om_test_1")


if __name__ == "__main__":
    unittest.main()
