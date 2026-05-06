"""V1.18 stability tests: coverage for previously untested modules."""

import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class TestDateParser(unittest.TestCase):
    """Coverage for date_parser.py (previously 0%)."""

    def test_weekday_mapping(self):
        from memory.date_parser import parse_relative_deadline
        ref = date(2026, 5, 4)  # Monday
        self.assertEqual(parse_relative_deadline("周五", ref), date(2026, 5, 8))
        self.assertEqual(parse_relative_deadline("周三", ref), date(2026, 5, 6))
        self.assertEqual(parse_relative_deadline("周一", ref), date(2026, 5, 4))

    def test_next_weekday(self):
        from memory.date_parser import parse_relative_deadline
        ref = date(2026, 5, 4)  # Monday
        self.assertEqual(parse_relative_deadline("下周三", ref), date(2026, 5, 13))
        self.assertEqual(parse_relative_deadline("下周五", ref), date(2026, 5, 15))

    def test_relative_days(self):
        from memory.date_parser import parse_relative_deadline
        ref = date(2026, 5, 4)
        self.assertEqual(parse_relative_deadline("明天", ref), date(2026, 5, 5))
        self.assertEqual(parse_relative_deadline("后天", ref), date(2026, 5, 6))
        self.assertEqual(parse_relative_deadline("今天", ref), date(2026, 5, 4))

    def test_numeric_date(self):
        from memory.date_parser import parse_relative_deadline
        ref = date(2026, 5, 4)
        self.assertEqual(parse_relative_deadline("5月10日", ref), date(2026, 5, 10))

    def test_n_days_later(self):
        from memory.date_parser import parse_relative_deadline
        ref = date(2026, 5, 4)
        self.assertEqual(parse_relative_deadline("3天后", ref), date(2026, 5, 7))

    def test_deadline_is_imminent(self):
        from memory.date_parser import deadline_is_imminent
        ref = date(2026, 5, 4)
        self.assertTrue(deadline_is_imminent("明天", 3, ref))
        self.assertFalse(deadline_is_imminent("周五", 3, ref))  # 4 days

    def test_unknown_pattern_returns_none(self):
        from memory.date_parser import parse_relative_deadline
        self.assertIsNone(parse_relative_deadline("节前交付"))
        self.assertIsNone(parse_relative_deadline(""))

    def test_weekend(self):
        from memory.date_parser import parse_relative_deadline
        ref = date(2026, 5, 4)  # Monday
        result = parse_relative_deadline("周末", ref)
        self.assertIsNotNone(result)
        self.assertEqual(result.weekday(), 5)  # Saturday


class TestActionTriggerStability(unittest.TestCase):
    """Coverage for action_trigger.py edge cases."""

    def test_trigger_with_empty_diff(self):
        from memory.action_trigger import ActionTrigger
        t = ActionTrigger()
        proposals = t.scan({"created": [], "updated": [], "unchanged": [], "conflicts": []},
                           "test", "oc_test")
        self.assertEqual(len(proposals), 0)

    def test_trigger_no_chat_id_skips_alerts(self):
        from memory.action_trigger import ActionTrigger
        t = ActionTrigger()
        proposals = t.scan({"created": [], "updated": [], "unchanged": [], "conflicts": []},
                           "test", "")  # empty chat_id
        self.assertEqual(len(proposals), 0)

    def test_is_unresolved_blocker_with_none_metadata(self):
        from memory.action_trigger import ActionTrigger
        from memory.schema import MemoryItem, source_ref_from_event
        t = ActionTrigger()
        ev = {"project_id": "t", "chat_id": "c", "message_id": "m1",
              "text": "test", "created_at": "2026-05-07"}
        item = MemoryItem("t", "blocker", "k", "v", "r", None, "active", 0.7,
                          [source_ref_from_event(ev)], metadata=None)
        self.assertTrue(t._is_unresolved_blocker(item))

    def test_cooldown_cache_pruning(self):
        from memory.action_trigger import ActionTrigger
        t = ActionTrigger(cooldown_seconds=1)
        # Fill cache with old entries
        old_time = datetime.now() - timedelta(hours=2)
        for i in range(600):
            t._last_alert[f"key_{i}"] = old_time
        # Add one new entry — should trigger pruning
        t._is_cooling_down("new_key")
        self.assertLess(len(t._last_alert), 550,
                        f"Cache should be pruned, got {len(t._last_alert)}")


class TestConfigStability(unittest.TestCase):
    """Coverage for config loading edge cases."""

    def test_load_missing_config_returns_empty(self):
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        from auto_runner import load_config
        result = load_config("/nonexistent/path/config.yaml")
        self.assertIsInstance(result, dict)

    def test_load_invalid_yaml_returns_empty(self):
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        from auto_runner import load_config
        with TemporaryDirectory() as d:
            bad = Path(d) / "bad.yaml"
            bad.write_text("{invalid: [yaml: }}", encoding="utf-8")
            result = load_config(str(bad))
            self.assertIsInstance(result, dict)


class TestMemoryStoreStability(unittest.TestCase):
    """Coverage for store.py edge cases."""

    def test_load_corrupted_state_recovers(self):
        from memory.store import MemoryStore
        with TemporaryDirectory() as d:
            store = MemoryStore(Path(d))
            store.ensure_files()
            # Write corrupted JSON
            store.memory_state_path.write_text("not json{{{", encoding="utf-8")
            # Should not crash — returns empty state
            state = store.load_state()
            self.assertIn("items", state)

    def test_save_and_load_roundtrip(self):
        from memory.store import MemoryStore
        with TemporaryDirectory() as d:
            store = MemoryStore(Path(d))
            store.ensure_files()
            items = []
            hist = []
            processed = ["ev_1", "ev_2"]
            store.save_state(items, hist, processed)
            state = store.load_state()
            self.assertEqual(state["processed_event_ids"], processed)

    def test_audit_log_handles_non_dict_entries(self):
        from memory.store import MemoryStore
        with TemporaryDirectory() as d:
            store = MemoryStore(Path(d))
            store.ensure_files()
            # Write a malformed line
            with store.audit_path.open("a", encoding="utf-8") as f:
                f.write("not json\n")
                f.write('{"valid": "json"}\n')
            entries = store.read_audit_log()
            self.assertEqual(len(entries), 1)  # malformed line skipped


if __name__ == "__main__":
    unittest.main()
