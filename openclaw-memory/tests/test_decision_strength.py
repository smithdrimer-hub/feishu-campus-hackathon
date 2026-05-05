"""Tests for decision_strength inference and filtering."""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.extractor import RuleBasedExtractor
from memory.store import MemoryStore
from memory.engine import MemoryEngine
from memory.project_state import build_group_project_state


class TestDecisionStrengthInference(unittest.TestCase):

    def setUp(self):
        self.extractor = RuleBasedExtractor()

    def test_confirmed_signals(self):
        cases = [
            "确定用 React",
            "就这么定了，按这个方案做",
            "最终方案是用微服务",
            "决定迁移到云端",
            "敲定用 Flask 框架",
        ]
        for text in cases:
            s, c = self.extractor._infer_decision_strength(text)
            self.assertEqual(s, "confirmed", f"Text '{text[:30]}' should be confirmed, got {s}")

    def test_tentative_signals(self):
        cases = [
            "那就先这样吧",
            "暂时按这个方向做",
            "先这样，后面再调",
            "初步定为周三发布",
            "暂定用 PostgreSQL",
        ]
        for text in cases:
            s, c = self.extractor._infer_decision_strength(text)
            self.assertEqual(s, "tentative", f"Text '{text[:30]}' should be tentative, got {s}")

    def test_preference_signals(self):
        cases = [
            "我倾向于用 Go",
            "我觉得还是 React 好",
            "建议用微服务架构",
            "推荐 Figma 做设计",
        ]
        for text in cases:
            s, c = self.extractor._infer_decision_strength(text)
            self.assertEqual(s, "preference", f"Text '{text[:30]}' should be preference, got {s}")

    def test_discussion_signals(self):
        cases = [
            "要不要用 Kubernetes",
            "考虑换成微服务",
            "是否需要用 Redis",
            "打算迁移到云",
            "商量一下技术选型",
        ]
        for text in cases:
            s, c = self.extractor._infer_decision_strength(text)
            self.assertEqual(s, "discussion", f"Text '{text[:30]}' should be discussion, got {s}")

    def test_default_to_tentative(self):
        """Without any signal words, decision defaults to tentative."""
        s, c = self.extractor._infer_decision_strength("采用前后端分离")
        self.assertEqual(s, "tentative")
        self.assertEqual(c, 0.75)

    def test_review_status_set_for_non_confirmed(self):
        """Non-confirmed decisions should get review_status=needs_review."""
        text = "采用微服务架构，先这样吧"  # "采用" triggers decision, no confirmed signal
        items = self.extractor.extract([{
            "project_id": "test", "chat_id": "", "message_id": "m1",
            "text": text, "created_at": "2026-05-04T10:00:00",
        }])
        decisions = [i for i in items if i.state_type == "decision"]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision_strength, "tentative")
        self.assertEqual(decisions[0].review_status, "needs_review")

    def test_confirmed_decision_not_needs_review(self):
        """Confirmed decisions should NOT be auto-marked needs_review."""
        text = "确定用 React 框架"
        items = self.extractor.extract([{
            "project_id": "test", "chat_id": "", "message_id": "m1",
            "text": text, "created_at": "2026-05-04T10:00:00",
        }])
        decisions = [i for i in items if i.state_type == "decision"]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision_strength, "confirmed")
        # review_status not set by extractor for confirmed (upsert_items will set it)
        self.assertEqual(decisions[0].review_status, "")


class TestDecisionStrengthStatePanel(unittest.TestCase):

    def test_confirmed_appears_in_recent(self):
        """Confirmed decisions go to recent_decisions."""
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            engine = MemoryEngine(store, RuleBasedExtractor())
            engine.ingest_events([{
                "project_id": "test", "chat_id": "", "message_id": "m1",
                "text": "决定使用 React", "created_at": "2026-05-04T10:00:00",
            }], debounce=False)
            items = store.list_items("test")
            state = build_group_project_state("test", items)
            self.assertGreater(len(state["recent_decisions"]), 0)

    def test_tentative_is_needs_review_not_in_panel(self):
        """Tentative decisions are needs_review — hidden until steward approves."""
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            engine = MemoryEngine(store, RuleBasedExtractor())
            engine.ingest_events([{
                "project_id": "test", "chat_id": "", "message_id": "m1",
                "text": "采用微服务架构，先这样", "created_at": "2026-05-04T10:00:00",
            }], debounce=False)
            items = store.list_items("test")
            decisions_in_store = [i for i in items if i.state_type == "decision"]
            self.assertGreater(len(decisions_in_store), 0)
            self.assertEqual(decisions_in_store[0].review_status, "needs_review")
            # Not in panel because needs_review
            state = build_group_project_state("test", items)
            self.assertEqual(len(state["recent_decisions"]), 0)
            self.assertEqual(len(state["open_decisions"]), 0,
                             "needs_review decisions are hidden")

    def test_discussion_excluded_from_panel(self):
        """Discussions are excluded from both recent and open decisions."""
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            engine = MemoryEngine(store, RuleBasedExtractor())
            engine.ingest_events([{
                "project_id": "test", "chat_id": "", "message_id": "m1",
                "text": "打算迁移到云，大家讨论一下", "created_at": "2026-05-04T10:00:00",
            }], debounce=False)
            items = store.list_items("test")
            state = build_group_project_state("test", items)
            self.assertEqual(len(state["recent_decisions"]), 0)
            self.assertEqual(len(state["open_decisions"]), 0,
                             "Discussion should not appear in panel")

    def test_needs_review_excluded_from_panel(self):
        """needs_review decisions are excluded from state panel entirely."""
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            engine = MemoryEngine(store, RuleBasedExtractor())
            engine.ingest_events([{
                "project_id": "test", "chat_id": "", "message_id": "m1",
                "text": "那就先这样", "created_at": "2026-05-04T10:00:00",
            }], debounce=False)
            items = store.list_items("test")
            # Manually ensure needs_review
            for item in items:
                if item.state_type == "decision":
                    self.assertEqual(item.review_status, "needs_review")
            state = build_group_project_state("test", items)
            self.assertEqual(len(state["recent_decisions"]), 0)
            self.assertEqual(len(state["open_decisions"]), 0)


if __name__ == "__main__":
    unittest.main()
