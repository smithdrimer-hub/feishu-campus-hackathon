"""End-to-end tests for sync_* paths using a FakeLarkCliAdapter.

Phase C: real-channel coverage. We don't depend on lark-cli being installed;
instead each FakeLarkCliAdapter method returns a pre-recorded JSON payload
under tests/fixtures/lark_payloads/, exercising the real engine.sync_*
normalisation and extraction pipeline.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adapters.lark_cli_adapter import CliResult  # noqa: E402
from memory.engine import MemoryEngine  # noqa: E402
from memory.extractor import RuleBasedExtractor  # noqa: E402
from memory.store import MemoryStore  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "lark_payloads"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeLarkCliAdapter:
    """Drop-in replacement for LarkCliAdapter used by MemoryEngine.sync_*.

    Each method returns a CliResult carrying recorded JSON payloads, which
    keeps the real normalisation paths (markdown chunking, task/deadline
    splitting, calendar attendees, minute action items, approval status to
    blocker/decision) under test without a live lark-cli.
    """

    def __init__(self, *,
                 doc_payload: dict | None = None,
                 doc_comment_payload: dict | None = None,
                 tasks_payload: dict | None = None,
                 calendar_payload: dict | None = None,
                 attendees_payload: dict | None = None,
                 minutes_search_payload: dict | None = None,
                 minute_detail_payload: dict | None = None,
                 approvals_payload: dict | None = None) -> None:
        self.doc_payload = doc_payload
        self.doc_comment_payload = doc_comment_payload
        self.tasks_payload = tasks_payload
        self.calendar_payload = calendar_payload
        self.attendees_payload = attendees_payload
        self.minutes_search_payload = minutes_search_payload
        self.minute_detail_payload = minute_detail_payload
        self.approvals_payload = approvals_payload

    @staticmethod
    def _ok(data) -> CliResult:
        return CliResult(args=[], returncode=0, stdout=json.dumps(data),
                         stderr="", data=data)

    def fetch_doc(self, doc, limit=None, offset=None):
        return self._ok(self.doc_payload or {})

    def fetch_doc_comments(self, doc_id, page_size=50):
        return self._ok(self.doc_comment_payload or {})

    def search_tasks(self, query, page_token=None, page_limit=20):
        return self._ok(self.tasks_payload or {})

    def list_calendar_events(self, start, end):
        return self._ok(self.calendar_payload or {})

    def list_event_attendees(self, calendar_id, event_id):
        return self._ok(self.attendees_payload or {})

    def search_minutes(self, start, end, page_size=10):
        return self._ok(self.minutes_search_payload or {})

    def get_minute_detail(self, token):
        return self._ok(self.minute_detail_payload or {})

    def list_approval_instances(self, status="pending", page_size=10):
        return self._ok(self.approvals_payload or {})


def _engine_with(adapter: FakeLarkCliAdapter, tmp: Path) -> MemoryEngine:
    store = MemoryStore(tmp)
    return MemoryEngine(store, RuleBasedExtractor(), adapter=adapter)


class TestSyncDoc(unittest.TestCase):
    def test_chunks_and_extracts(self) -> None:
        adapter = FakeLarkCliAdapter(doc_payload=_load("doc_techplan.json"))
        with TemporaryDirectory() as tmp:
            engine = _engine_with(adapter, Path(tmp))
            engine.sync_doc("doc_aurora", project_id="aurora")
            items = engine.store.list_items("aurora")
        types = {item.state_type for item in items}
        self.assertIn("project_goal", types)
        self.assertIn("owner", types)
        self.assertIn("decision", types)
        self.assertIn("blocker", types)
        owners = {item.owner for item in items if item.state_type == "owner"}
        self.assertIn("周明", owners)
        self.assertIn("林夏", owners)


class TestSyncDocComments(unittest.TestCase):
    def test_decision_in_comment_is_extracted(self) -> None:
        adapter = FakeLarkCliAdapter(doc_comment_payload=_load("doc_comments.json"))
        with TemporaryDirectory() as tmp:
            engine = _engine_with(adapter, Path(tmp))
            engine.sync_doc_comments("doc_aurora", project_id="aurora")
            items = engine.store.list_items("aurora")
        decisions = [item for item in items if item.state_type == "decision"]
        self.assertTrue(decisions, "doc comment decision should be extracted")
        self.assertTrue(any("Envoy" in item.current_value for item in decisions))


class TestSyncTasks(unittest.TestCase):
    def test_task_with_assignee_field_keeps_owner(self) -> None:
        adapter = FakeLarkCliAdapter(tasks_payload=_load("tasks_search.json"))
        with TemporaryDirectory() as tmp:
            engine = _engine_with(adapter, Path(tmp))
            engine.sync_tasks("Envoy", project_id="aurora")
            items = engine.store.list_items("aurora")
        next_steps = [item for item in items if item.state_type == "next_step"]
        self.assertTrue(any(item.owner == "周明" for item in next_steps))
        # Task without assignee must not invent an owner.
        no_owner = [item for item in next_steps if item.owner is None]
        self.assertTrue(no_owner)
        deadlines = [item for item in items if item.state_type == "deadline"]
        self.assertTrue(deadlines, "task with due_at should generate deadline")


class TestSyncCalendar(unittest.TestCase):
    def test_calendar_attendees_appear(self) -> None:
        adapter = FakeLarkCliAdapter(
            calendar_payload=_load("calendar_events.json"),
            attendees_payload=_load("calendar_attendees.json"),
        )
        with TemporaryDirectory() as tmp:
            engine = _engine_with(adapter, Path(tmp))
            engine.sync_calendar("2026-06-08", "2026-06-09", project_id="aurora")
            items = engine.store.list_items("aurora")
        next_steps = [item for item in items if item.state_type == "next_step"]
        self.assertTrue(next_steps, "calendar events should produce next_step")
        text = "\n".join(ref.excerpt for item in next_steps for ref in item.source_refs)
        for attendee in ("周明", "林夏", "陶然"):
            self.assertIn(attendee, text)


class TestSyncMinutes(unittest.TestCase):
    def test_action_items_become_distinct_next_steps(self) -> None:
        adapter = FakeLarkCliAdapter(
            minutes_search_payload=_load("minutes_search.json"),
            minute_detail_payload=_load("minute_detail.json"),
        )
        with TemporaryDirectory() as tmp:
            engine = _engine_with(adapter, Path(tmp))
            engine.sync_minutes("2026-06-08", "2026-06-09", project_id="aurora")
            items = engine.store.list_items("aurora")
        action_steps = [
            item for item in items
            if item.state_type == "next_step"
            and (item.metadata or {}).get("source_kind") == "meeting_action_item"
        ]
        self.assertEqual(len(action_steps), 3)
        owners = {item.owner for item in action_steps}
        self.assertSetEqual(owners, {"周明", "林夏", "陶然"})


class TestSyncApprovals(unittest.TestCase):
    def test_pending_creates_blocker_and_approved_resolves_it(self) -> None:
        with TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp))
            engine = MemoryEngine(store, RuleBasedExtractor())

            # 1) Pending approval -> blocker(waiting_external)
            engine.adapter = FakeLarkCliAdapter(approvals_payload=_load("approvals_pending.json"))
            engine.sync_approvals(status="pending", project_id="aurora")
            blockers = [
                item for item in store.list_items("aurora")
                if item.state_type == "blocker"
            ]
            self.assertTrue(blockers)
            self.assertEqual((blockers[0].metadata or {}).get("blocker_status"),
                             "waiting_external")

            # 2) Approved -> blocker resolved + decision approved
            engine.adapter = FakeLarkCliAdapter(approvals_payload=_load("approvals_approved.json"))
            engine.sync_approvals(status="approved", project_id="aurora")

            statuses = {
                (item.metadata or {}).get("blocker_status")
                for item in store.list_items("aurora")
                if item.state_type == "blocker"
            }
            self.assertIn("resolved", statuses)

            decisions = [
                item for item in store.list_items("aurora")
                if item.state_type == "decision"
            ]
            self.assertTrue(any(
                (item.metadata or {}).get("approval_status") == "approved"
                for item in decisions
            ))


if __name__ == "__main__":  # pragma: no cover - manual run
    unittest.main()
