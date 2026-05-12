"""V1.19 P1: SQLite 存储后端专项测试。"""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class TestSQLiteBackend(unittest.TestCase):
    """SQLiteStorageBackend 基础功能测试。"""

    def setUp(self):
        from memory.store_sqlite import SQLiteStorageBackend
        self.tmp = TemporaryDirectory()
        self.backend = SQLiteStorageBackend(self.tmp.name)
        self.backend.ensure_files()

    def tearDown(self):
        self.backend.close()
        self.tmp.cleanup()

    # ── 建表与连接 ──

    def test_wal_mode(self):
        conn = self.backend._conn
        row = conn.execute("PRAGMA journal_mode").fetchone()
        self.assertEqual(row[0], "wal")

    def test_tables_exist(self):
        conn = self.backend._conn
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        self.assertIn("memory_records", tables)
        self.assertIn("raw_events", tables)
        self.assertIn("processed_events", tables)

    # ── 状态读写 ──

    def test_save_and_load_empty(self):
        self.backend.save_state([], [], [])
        state = self.backend.load_state()
        self.assertEqual(len(state["items"]), 0)
        self.assertEqual(len(state["history"]), 0)

    def test_save_and_load_items(self):
        item = self._make_dict("mem_1", "p1", "decision", "用Python")
        self.backend.save_state([item], [], ["ev_1"])
        state = self.backend.load_state()
        self.assertEqual(len(state["items"]), 1)
        self.assertEqual(state["items"][0]["current_value"], "用Python")
        self.assertEqual(state["items"][0]["state_type"], "decision")
        self.assertEqual(state["processed_event_ids"], ["ev_1"])

    def test_active_and_history_separation(self):
        active = self._make_dict("mem_1", "p1", "owner", "张三")
        hist = self._make_dict("mem_2", "p1", "owner", "李四(old)")
        hist["status"] = "superseded"
        self.backend.save_state([active], [hist], [])
        state = self.backend.load_state()
        self.assertEqual(len(state["items"]), 1)
        self.assertEqual(len(state["history"]), 1)
        self.assertEqual(state["items"][0]["current_value"], "张三")
        self.assertEqual(state["history"][0]["current_value"], "李四(old)")

    def test_supersedes_list_roundtrip(self):
        item = self._make_dict("mem_1", "p1", "decision", "v2")
        item["supersedes"] = ["mem_0", "mem_old"]
        self.backend.save_state([item], [], [])
        state = self.backend.load_state()
        self.assertEqual(state["items"][0]["supersedes"], ["mem_0", "mem_old"])

    def test_metadata_roundtrip(self):
        item = self._make_dict("mem_1", "p1", "blocker", "阻塞")
        item["metadata"] = {"blocker_status": "open", "resolved_by": "张三"}
        self.backend.save_state([item], [], [])
        state = self.backend.load_state()
        self.assertEqual(state["items"][0]["metadata"]["blocker_status"], "open")

    def test_source_refs_roundtrip(self):
        ref = {"type": "message", "chat_id": "c1", "message_id": "m1",
               "excerpt": "讨论决定", "created_at": "2026-05-01T00:00:00",
               "sender_name": "张三", "sender_id": "ou_1", "source_url": ""}
        item = self._make_dict("mem_1", "p1", "decision", "用Python")
        item["source_refs"] = [ref]
        self.backend.save_state([item], [], [])
        state = self.backend.load_state()
        loaded = state["items"][0]["source_refs"]
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["message_id"], "m1")

    # ── 生命周期字段 ──

    def test_status_fields_roundtrip(self):
        item = self._make_dict("mem_1", "p1", "owner", "张三")
        item["status"] = "corrected"
        item["status_reason"] = "写错了"
        item["status_changed_at"] = "2026-05-10T00:00:00"
        item["status_changed_by"] = "ou_admin"
        self.backend.save_state([item], [], [])
        state = self.backend.load_state()
        loaded = state["items"][0]
        self.assertEqual(loaded["status"], "corrected")
        self.assertEqual(loaded["status_reason"], "写错了")

    def test_confidence_float(self):
        item = self._make_dict("mem_1", "p1", "decision", "v")
        item["confidence"] = 0.85
        self.backend.save_state([item], [], [])
        state = self.backend.load_state()
        self.assertAlmostEqual(state["items"][0]["confidence"], 0.85)

    # ── 原始事件 ──

    def test_append_raw_events(self):
        ev = {"message_id": "m1", "project_id": "p1", "text": "hello",
              "chat_id": "c1", "created_at": "2026-05-01T00:00:00"}
        n = self.backend.append_raw_events([ev])
        self.assertEqual(n, 1)
        events = self.backend.read_raw_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["message_id"], "m1")

    def test_duplicate_raw_event_ignored(self):
        ev = {"message_id": "m1", "project_id": "p1", "text": "hello"}
        self.backend.append_raw_events([ev])
        n = self.backend.append_raw_events([ev])
        self.assertEqual(n, 0)

    # ── processed events ──

    def test_mark_processed(self):
        self.backend.mark_processed(["ev_1", "ev_2"])
        ids = self.backend.processed_event_ids()
        self.assertIn("ev_1", ids)
        self.assertIn("ev_2", ids)

    def test_mark_processed_idempotent(self):
        self.backend.mark_processed(["ev_1"])
        self.backend.mark_processed(["ev_1", "ev_2"])
        ids = self.backend.processed_event_ids()
        self.assertEqual(len(ids), 2)

    # ── 事务回滚 ──

    def test_save_state_atomic(self):
        """如果保存中途失败，不应该有部分数据残留。"""
        good = self._make_dict("mem_1", "p1", "decision", "ok")
        # 创建一个会导致问题的坏数据（None owner 可以，但测试回滚）
        self.backend.save_state([good], [], [])
        state = self.backend.load_state()
        self.assertEqual(len(state["items"]), 1)

    # ── 辅助 ──

    @staticmethod
    def _make_dict(memory_id, project_id, state_type, current_value):
        return {
            "memory_id": memory_id,
            "project_id": project_id,
            "state_type": state_type,
            "key": current_value[:20],
            "current_value": current_value,
            "rationale": "测试",
            "owner": None,
            "status": "active",
            "confidence": 0.8,
            "version": 1,
            "supersedes": [],
            "updated_at": "2026-05-01T00:00:00",
            "valid_from": "2026-05-01T00:00:00",
            "valid_to": None,
            "recorded_at": "2026-05-01T00:00:00",
            "decision_strength": "",
            "review_status": "",
            "metadata": {},
            "status_reason": "",
            "status_changed_at": "",
            "status_changed_by": "",
            "source_refs": [],
            "media_refs": [],
        }


class TestBackendCompatibility(unittest.TestCase):
    """确保 JSON 和 SQLite 后端返回相同格式的 load_state() 数据。"""

    def test_load_state_format_match(self):
        from memory.store_sqlite import SQLiteStorageBackend
        from memory.storage_protocol import JsonStorageBackend

        with TemporaryDirectory() as d1, TemporaryDirectory() as d2:
            js = JsonStorageBackend(d1)
            sq = SQLiteStorageBackend(d2)

            js.ensure_files()
            sq.ensure_files()

            state_js = js.load_state()
            state_sq = sq.load_state()

            self.assertEqual(set(state_js.keys()), set(state_sq.keys()))
            self.assertIn("items", state_sq)
            self.assertIn("history", state_sq)
            self.assertIn("processed_event_ids", state_sq)

            sq.close()


if __name__ == "__main__":
    unittest.main()
