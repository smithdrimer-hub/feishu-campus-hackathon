"""Tests for review_status marking and update_item_review."""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.schema import MemoryItem, source_ref_from_event
from memory.store import MemoryStore
from memory.engine import MemoryEngine
from memory.extractor import RuleBasedExtractor


def _make_event(text, msg_id="m1", created_at="2026-05-04T10:00:00"):
    return {
        "project_id": "test", "chat_id": "c", "message_id": msg_id,
        "text": text, "created_at": created_at,
    }


class TestReviewStatusMarking(unittest.TestCase):

    def test_auto_approved_for_normal_items(self):
        """Normal items with good confidence and evidence get auto_approved."""
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            engine = MemoryEngine(store, RuleBasedExtractor())
            engine.ingest_events([_make_event("目标：完成项目")], debounce=False)
            items = store.list_items("test")
            self.assertGreater(len(items), 0)
            for item in items:
                self.assertEqual(item.review_status, "auto_approved",
                                 f"Item {item.key} should be auto_approved")

    def test_low_confidence_needs_review(self):
        """Low confidence items get needs_review."""
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            engine = MemoryEngine(store, RuleBasedExtractor())
            # next_step has confidence 0.62 (below threshold by default... no, 0.62 > 0.60)
            # Let's use member_status which has 0.65 confidence and modify
            # Actually the threshold is < 0.60. Let me use a message that won't trigger
            # any extraction but will have very low default confidence.
            # Easiest: create a MemoryItem directly with low confidence
            low_conf = MemoryItem(
                project_id="test", state_type="next_step", key="low_conf_test",
                current_value="test", rationale="r", owner=None, status="active",
                confidence=0.45,
                source_refs=[source_ref_from_event(_make_event("test"))],
            )
            items, diff = store.upsert_items([low_conf])
            # Check that it was marked needs_review
            created = diff.get("created", [])
            self.assertEqual(len(created), 1)
            self.assertEqual(created[0].review_status, "needs_review")

    def test_no_evidence_needs_review(self):
        """Items without source_refs get needs_review."""
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            no_evidence = MemoryItem(
                project_id="test", state_type="blocker", key="no_evidence_test",
                current_value="test", rationale="r", owner=None, status="active",
                confidence=0.7, source_refs=[],  # empty!
            )
            items, diff = store.upsert_items([no_evidence])
            created = diff.get("created", [])
            self.assertEqual(len(created), 1)
            self.assertEqual(created[0].review_status, "needs_review")

    def test_decision_override_needs_review(self):
        """Decisions that override via Layer 4 should be needs_review."""
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            # Insert first decision
            d1 = MemoryItem(
                project_id="test", state_type="decision", key="dec_a",
                current_value="用React做前端", rationale="r", owner=None,
                status="active", confidence=0.8,
                source_refs=[source_ref_from_event(_make_event("用React做前端"))],
            )
            store.upsert_items([d1])
            # Insert second decision that overrides via Layer 4 (same topic)
            d2 = MemoryItem(
                project_id="test", state_type="decision", key="dec_b",
                current_value="改为用Vue做前端", rationale="r", owner=None,
                status="active", confidence=0.8,
                source_refs=[source_ref_from_event(_make_event("改为用Vue做前端", "m2"))],
            )
            items, diff = store.upsert_items([d2])
            # The new item should be in updated with needs_review
            updated = diff.get("updated", [])
            self.assertEqual(len(updated), 1)
            self.assertEqual(updated[0].review_status, "needs_review")


class TestUpdateItemReview(unittest.TestCase):

    def test_approve_memory(self):
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            item = MemoryItem(
                project_id="test", state_type="decision", key="k",
                current_value="v", rationale="r", owner=None, status="active",
                confidence=0.8,
                source_refs=[source_ref_from_event(_make_event("test"))],
                review_status="needs_review",
            )
            store.upsert_items([item])
            items = store.list_items("test")
            self.assertEqual(len(items), 1)
            mid = items[0].memory_id

            result = store.update_item_review(mid, "approved")
            self.assertIsNotNone(result)
            self.assertEqual(result.review_status, "approved")

            items_after = store.list_items("test")
            self.assertEqual(len(items_after), 1)
            self.assertEqual(items_after[0].review_status, "approved")

    def test_reject_moves_to_history(self):
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            item = MemoryItem(
                project_id="test", state_type="decision", key="k",
                current_value="v", rationale="r", owner=None, status="active",
                confidence=0.8,
                source_refs=[source_ref_from_event(_make_event("test"))],
                review_status="needs_review",
            )
            store.upsert_items([item])
            items = store.list_items("test")
            mid = items[0].memory_id

            result = store.update_item_review(mid, "rejected")
            self.assertIsNotNone(result)
            self.assertEqual(result.review_status, "rejected")

            # Should be removed from active items
            items_after = store.list_items("test")
            self.assertEqual(len(items_after), 0)

            # Should be in history
            history = store.list_history()
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0].review_status, "rejected")

    def test_modify_value(self):
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            item = MemoryItem(
                project_id="test", state_type="decision", key="k",
                current_value="旧值", rationale="r", owner=None, status="active",
                confidence=0.8,
                source_refs=[source_ref_from_event(_make_event("test"))],
                review_status="needs_review",
            )
            store.upsert_items([item])
            mid = store.list_items("test")[0].memory_id

            result = store.update_item_review(mid, "approved", "修改后的值")
            self.assertEqual(result.current_value, "修改后的值")
            self.assertEqual(result.review_status, "approved")

    def test_nonexistent_memory_id(self):
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            result = store.update_item_review("mem_nonexistent", "approved")
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
