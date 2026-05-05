"""Tests for V1.15 blocker lifecycle: statuses, panel split, risk filter, sweep."""

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
from memory.project_state import build_group_project_state
from memory.action_trigger import ActionTrigger


def _make_event(text, msg_id="m1", created_at="2026-05-04T10:00:00"):
    return {
        "project_id": "test", "chat_id": "c", "message_id": msg_id,
        "text": text, "created_at": created_at,
    }


def _make_blocker(value="阻塞：等待设计稿", key="blk_1", status="active",
                  metadata=None):
    return MemoryItem(
        project_id="test", state_type="blocker", key=key,
        current_value=value, rationale="r", owner=None, status=status,
        confidence=0.7,
        source_refs=[source_ref_from_event(_make_event(value))],
        metadata=metadata or {},
    )


class TestBlockerExtraction(unittest.TestCase):

    def test_extracted_blocker_has_metadata(self):
        """Newly extracted blockers get blocker_status=open in metadata."""
        extractor = RuleBasedExtractor()
        items = extractor.extract([_make_event("阻塞：等待设计稿，前端动不了")])
        blockers = [i for i in items if i.state_type == "blocker"]
        self.assertEqual(len(blockers), 1)
        meta = blockers[0].metadata
        self.assertIsNotNone(meta)
        self.assertEqual(meta.get("blocker_status"), "open")
        self.assertIn("等待设计稿", meta.get("blocking_reason", ""))
        self.assertEqual(meta.get("blocked_owner"), "")


class TestBlockerStatusUpdate(unittest.TestCase):

    def test_update_open_to_acknowledged(self):
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            item = _make_blocker(metadata={"blocker_status": "open"})
            store.upsert_items([item])
            mid = store.list_items("test")[0].memory_id

            result = store.update_blocker_status(mid, "acknowledged",
                                                  {"acknowledged_by": "ou_001"})
            self.assertIsNotNone(result)
            meta = result.metadata
            self.assertEqual(meta["blocker_status"], "acknowledged")
            self.assertEqual(meta["acknowledged_by"], "ou_001")

    def test_update_to_resolved_sets_timestamp(self):
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            item = _make_blocker(metadata={"blocker_status": "open"})
            store.upsert_items([item])
            mid = store.list_items("test")[0].memory_id

            result = store.update_blocker_status(mid, "resolved",
                                                  {"resolved_by": "ou_002"})
            self.assertEqual(result.metadata["blocker_status"], "resolved")
            self.assertEqual(result.metadata["resolved_by"], "ou_002")
            self.assertIn("resolved_at", result.metadata)
            self.assertNotEqual(result.metadata["resolved_at"], "")

    def test_update_to_obsolete(self):
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            item = _make_blocker()
            store.upsert_items([item])
            mid = store.list_items("test")[0].memory_id

            result = store.update_blocker_status(mid, "obsolete")
            self.assertEqual(result.metadata["blocker_status"], "obsolete")

    def test_update_waiting_external(self):
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            item = _make_blocker()
            store.upsert_items([item])
            mid = store.list_items("test")[0].memory_id

            result = store.update_blocker_status(mid, "waiting_external",
                                                  {"dependency_owner": "李四"})
            self.assertEqual(result.metadata["blocker_status"], "waiting_external")
            self.assertEqual(result.metadata["dependency_owner"], "李四")

    def test_update_nonexistent(self):
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            result = store.update_blocker_status("mem_nonexistent", "resolved")
            self.assertIsNone(result)


class TestStatePanelBlockerSplit(unittest.TestCase):

    def test_unresolved_in_risks(self):
        """open/acknowledged/waiting_external → risks list."""
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            b1 = _make_blocker("阻塞A", "b1", metadata={"blocker_status": "open"})
            b2 = _make_blocker("阻塞B", "b2", metadata={"blocker_status": "acknowledged"})
            b3 = _make_blocker("阻塞C", "b3", metadata={"blocker_status": "waiting_external"})
            store.upsert_items([b1, b2, b3])
            items = store.list_items("test")
            state = build_group_project_state("test", items)
            self.assertEqual(len(state["risks"]), 3)
            self.assertEqual(len(state["resolved_blockers"]), 0)

    def test_resolved_in_resolved_list(self):
        """resolved/obsolete → resolved_blockers list."""
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            b1 = _make_blocker("阻塞A", "b1", metadata={"blocker_status": "open"})
            b2 = _make_blocker("阻塞B", "b2", metadata={"blocker_status": "resolved"})
            b3 = _make_blocker("阻塞C", "b3", metadata={"blocker_status": "obsolete"})
            store.upsert_items([b1, b2, b3])
            items = store.list_items("test")
            state = build_group_project_state("test", items)
            self.assertEqual(len(state["risks"]), 1)
            self.assertEqual(len(state["resolved_blockers"]), 2)

    def test_backward_compat_no_metadata(self):
        """Blockers without metadata default to open (unresolved)."""
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            # Old-style blocker without metadata
            b = _make_blocker("老阻塞", "b_old", metadata=None)
            store.upsert_items([b])
            items = store.list_items("test")
            state = build_group_project_state("test", items)
            self.assertEqual(len(state["risks"]), 1)
            self.assertEqual(len(state["resolved_blockers"]), 0)


class TestRiskWarningBlockerFilter(unittest.TestCase):

    def test_is_unresolved_blocker(self):
        """_is_unresolved_blocker correctly identifies unresolved vs resolved."""
        trigger = ActionTrigger()
        b_open = _make_blocker("x", "k1", metadata={"blocker_status": "open"})
        b_ack = _make_blocker("x", "k2", metadata={"blocker_status": "acknowledged"})
        b_wait = _make_blocker("x", "k3", metadata={"blocker_status": "waiting_external"})
        b_resolved = _make_blocker("x", "k4", metadata={"blocker_status": "resolved"})
        b_obsolete = _make_blocker("x", "k5", metadata={"blocker_status": "obsolete"})
        b_nometa = _make_blocker("x", "k6", metadata=None)

        self.assertTrue(trigger._is_unresolved_blocker(b_open))
        self.assertTrue(trigger._is_unresolved_blocker(b_ack))
        self.assertTrue(trigger._is_unresolved_blocker(b_wait))
        self.assertTrue(trigger._is_unresolved_blocker(b_nometa))
        self.assertFalse(trigger._is_unresolved_blocker(b_resolved))
        self.assertFalse(trigger._is_unresolved_blocker(b_obsolete))

    def test_non_blocker_returns_false(self):
        """Non-blocker items always return False from _is_unresolved_blocker."""
        trigger = ActionTrigger()
        decision = MemoryItem(
            project_id="test", state_type="decision", key="d1",
            current_value="v", rationale="r", owner=None, status="active",
            confidence=0.8,
            source_refs=[source_ref_from_event(_make_event("test"))],
        )
        self.assertFalse(trigger._is_unresolved_blocker(decision))


class TestBlockerSweep(unittest.TestCase):

    def test_sweep_resolved_older_than_7_days(self):
        """Resolved blockers older than 7 days move to history."""
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            from datetime import datetime, timedelta, timezone

            old_date = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
            b = _make_blocker("旧阻塞", "b_old", metadata={
                "blocker_status": "resolved",
                "resolved_at": old_date,
            })
            store.upsert_items([b])
            store._sweep_resolved_blockers()
            items = store.list_items("test")
            history = store.list_history()
            self.assertEqual(len(items), 0)
            self.assertEqual(len(history), 1)

    def test_no_sweep_recently_resolved(self):
        """Recently resolved blockers (< 7 days) stay active."""
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            from datetime import datetime, timedelta, timezone

            recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            b = _make_blocker("新解决的阻塞", "b_new", metadata={
                "blocker_status": "resolved",
                "resolved_at": recent,
            })
            store.upsert_items([b])
            store._sweep_resolved_blockers()
            items = store.list_items("test")
            self.assertEqual(len(items), 1)
            self.assertEqual(len(store.list_history()), 0)

    def test_sweep_only_affects_resolved_blockers(self):
        """Sweep does not affect unresolved blockers or non-blocker items."""
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            from datetime import datetime, timedelta, timezone

            old_date = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
            b_open = _make_blocker("活跃阻塞", "b1")
            b_resolved = _make_blocker("旧阻塞", "b2", metadata={
                "blocker_status": "resolved", "resolved_at": old_date,
            })
            store.upsert_items([b_open, b_resolved])
            store._sweep_resolved_blockers()
            items = store.list_items("test")
            blockers = [i for i in items if i.state_type == "blocker"]
            self.assertEqual(len(blockers), 1)
            self.assertEqual(blockers[0].key, "b1")


class TestFullPipelineBlockerLifecycle(unittest.TestCase):

    def test_extract_to_panel_roundtrip(self):
        """Full pipeline: extract blocker → update status → panel reflects."""
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            engine = MemoryEngine(store, RuleBasedExtractor())
            engine.ingest_events([_make_event("阻塞：等待设计稿")], debounce=False)
            items = store.list_items("test")
            blockers = [i for i in items if i.state_type == "blocker"]
            self.assertEqual(len(blockers), 1)
            self.assertEqual(blockers[0].metadata.get("blocker_status"), "open")

            # Panel should show it in risks
            state = build_group_project_state("test", items)
            self.assertEqual(len(state["risks"]), 1)
            self.assertEqual(len(state["resolved_blockers"]), 0)

            # Resolve it
            store.update_blocker_status(blockers[0].memory_id, "resolved")
            items2 = store.list_items("test")
            state2 = build_group_project_state("test", items2)
            self.assertEqual(len(state2["risks"]), 0)
            self.assertEqual(len(state2["resolved_blockers"]), 1)


if __name__ == "__main__":
    unittest.main()
