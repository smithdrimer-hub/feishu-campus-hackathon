"""V1.18 stability tests: coverage for previously untested modules."""

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
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
        # Fill cache with old entries (use UTC-naive to match BUG-2 fix)
        old_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
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


class TestLifecycleStability(unittest.TestCase):
    """V1.19 P0-C: 记忆生命周期回归测试。"""

    def setUp(self):
        from memory.store import MemoryStore
        from tempfile import TemporaryDirectory
        self.tmp = TemporaryDirectory()
        self.store = MemoryStore(self.tmp.name)
        self.store.ensure_files()

    def tearDown(self):
        self.tmp.cleanup()

    def _make_item(self, state_type, value, owner="张三",
                   decision_strength="", confidence=0.8,
                   review_status="", **kwargs):
        from memory.schema import MemoryItem, source_ref_from_event
        ev = {"project_id": "p", "chat_id": "c", "message_id": f"m_{value[:8]}",
              "text": value, "created_at": "2026-05-01T00:00:00"}
        return MemoryItem("p", state_type, value[:20], value, value,
                          owner, "active", confidence,
                          [source_ref_from_event(ev)],
                          decision_strength=decision_strength,
                          review_status=review_status,
                          **kwargs)

    # ── 锚点记忆保护 ──

    def test_confirmed_decision_not_expired(self):
        """confirmed 决策不因时间自动过期（锚点保护）。"""
        item = self._make_item("decision", "用Python做后端",
                               decision_strength="confirmed", confidence=0.9)
        self.store.upsert_items([item], [])
        swept = self.store.sweep_expired()
        self.assertEqual(swept, 0)
        active = self.store.list_items("p")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].status, "active")

    def test_project_goal_not_expired(self):
        """project_goal 不因时间自动过期（锚点保护）。"""
        item = self._make_item("project_goal", "完成记忆引擎V2",
                               confidence=0.9, decision_strength="confirmed")
        self.store.upsert_items([item], [])
        swept = self.store.sweep_expired()
        self.assertEqual(swept, 0)
        active = self.store.list_items("p")
        self.assertEqual(len(active), 1)

    def test_member_status_not_expired(self):
        """member_status 不因时间自动过期。"""
        item = self._make_item("member_status", "张三请假3天",
                               confidence=0.9)
        self.store.upsert_items([item], [])
        swept = self.store.sweep_expired()
        self.assertEqual(swept, 0)

    # ── confidence 加权 ──

    def test_low_confidence_marks_needs_review_not_expired(self):
        """低置信度（<0.5）标 needs_review，不直接 expired。"""
        item = self._make_item("next_step", "补充单元测试",
                               confidence=0.3)
        # 人工设置 recorded_at 为很久以前，确保触发 TTL
        item.recorded_at = "2025-01-01T00:00:00"
        self.store.upsert_items([item], [])
        self.store.sweep_expired()
        active = self.store.list_items("p")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].review_status, "needs_review",
                         "低置信度应标 needs_review 而非 expired")

    def test_high_confidence_slower_expiry_needs_review(self):
        """高置信度（>=0.8）到期标 needs_review，不直接 expired。"""
        item = self._make_item("next_step", "发布v2.0版本",
                               confidence=0.9)
        item.recorded_at = "2025-01-01T00:00:00"
        self.store.upsert_items([item], [])
        self.store.sweep_expired()
        active = self.store.list_items("p")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].review_status, "needs_review",
                         "高置信度到期也应标 needs_review 而非直接 expired")

    def test_mid_confidence_can_expire(self):
        """中等置信度（0.5-0.8）可直接 expired。"""
        item = self._make_item("next_step", "旧任务已过期",
                               confidence=0.6)
        item.recorded_at = "2025-01-01T00:00:00"
        self.store.upsert_items([item], [])
        swept = self.store.sweep_expired()
        self.assertGreater(swept, 0, "中等置信度超期应可直接 expired")
        active = self.store.list_items("p")
        self.assertEqual(len(active), 0)

    # ── 召回过滤 ──

    def test_forgotten_not_in_list_items(self):
        """forgotten 记忆默认不被 list_items 返回。"""
        item = self._make_item("owner", "张三负责前端")
        self.store.upsert_items([item], [])
        self.store.forget_item(item.memory_id, "测试遗忘")
        active = self.store.list_items("p")
        self.assertEqual(len(active), 0)

    def test_corrected_not_in_list_items(self):
        """corrected 记忆默认不被 list_items 返回。"""
        item = self._make_item("decision", "用Java")
        self.store.upsert_items([item], [])
        self.store.correct_item(item.memory_id, "用Python", "纠正测试")
        active = self.store.list_items("p")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].current_value, "用Python")

    def test_include_expired_returns_expired(self):
        """include_expired=True 可查回过期记忆。"""
        item = self._make_item("next_step", "过期任务", confidence=0.6)
        item.recorded_at = "2025-01-01T00:00:00"
        self.store.upsert_items([item], [])
        self.store.sweep_expired()
        all_items = self.store.list_items("p", include_expired=True)
        self.assertGreater(len(all_items), 0)

    # ── correct_item 双向关联 ──

    def test_correct_item_bidirectional_linking(self):
        """correct 后新旧 item 有双向关联。"""
        item = self._make_item("owner", "张三负责API")
        self.store.upsert_items([item], [])
        new_item = self.store.correct_item(item.memory_id, "李四负责API", "换人")
        self.assertIsNotNone(new_item)

        # 新条目 → supersedes 旧条目
        self.assertIn(item.memory_id, new_item.supersedes)

        # 新条目 metadata 记录 corrects_item_id
        self.assertEqual(
            new_item.metadata.get("corrects_item_id"), item.memory_id)

        # 旧条目在 history 中，metadata 记录 corrected_by_item_id
        history = self.store.list_history("p")
        old_in_history = [h for h in history if h.memory_id == item.memory_id]
        self.assertEqual(len(old_in_history), 1)
        self.assertEqual(
            old_in_history[0].metadata.get("corrected_by_item_id"),
            new_item.memory_id)

    # ── upsert 不误伤 ──

    def test_same_content_no_superseded(self):
        """重复 upsert 相同内容不应产生 superseded。"""
        item1 = self._make_item("decision", "用Python做后端",
                                decision_strength="confirmed")
        item2 = self._make_item("decision", "用Python做后端",
                                decision_strength="confirmed")
        self.store.upsert_items([item1], [])
        self.store.upsert_items([item2], [])
        history = self.store.list_history("p")
        self.assertEqual(len(history), 0,
                         "相同内容 upsert 不应产生 superseded 历史")


if __name__ == "__main__":
    unittest.main()
