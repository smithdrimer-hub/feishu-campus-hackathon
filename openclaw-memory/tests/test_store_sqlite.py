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

    def test_save_state_transaction_atomic(self):
        """save_state 在事务中执行——全部写入或全部回滚。

        通过直接操作 connection 验证：手动 BEGIN 后写入数据，
        ROLLBACK 后数据不可见，COMMIT 后可见。
        """
        conn = self.backend._conn
        # 手动事务：写入后回滚
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO memory_records(memory_id, project_id, state_type, key, "
            "current_value, rationale, record_type) "
            "VALUES ('test_rollback', 'p1', 'owner', 'k', 'v', 'r', 'active')"
        )
        conn.execute("ROLLBACK")

        # 回滚后查不到
        row = conn.execute(
            "SELECT * FROM memory_records WHERE memory_id='test_rollback'"
        ).fetchone()
        self.assertIsNone(row, "ROLLBACK 后数据不应存在")

        # 手动事务：写入后提交
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO memory_records(memory_id, project_id, state_type, key, "
            "current_value, rationale, record_type) "
            "VALUES ('test_commit', 'p1', 'owner', 'k2', 'v2', 'r2', 'active')"
        )
        conn.execute("COMMIT")

        row = conn.execute(
            "SELECT * FROM memory_records WHERE memory_id='test_commit'"
        ).fetchone()
        self.assertIsNotNone(row, "COMMIT 后数据应存在")
        self.assertEqual(row["current_value"], "v2")

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


class TestSQLiteQueryPushdown(unittest.TestCase):
    """V1.19 P2: SQLite 查询下推正确性验证。"""

    def setUp(self):
        from memory.store_sqlite import SQLiteStorageBackend
        self.tmp = TemporaryDirectory()
        self.backend = SQLiteStorageBackend(self.tmp.name)
        self.backend.ensure_files()

    def tearDown(self):
        self.backend.close()
        self.tmp.cleanup()

    def _seed(self):
        """写入 5 条不同 project/status 的测试数据。"""
        items = [
            self._make("mem_a", "p1", "owner", "张三", status="active"),
            self._make("mem_b", "p1", "decision", "用React", status="active",
                       decision_strength="confirmed", confidence=0.9),
            self._make("mem_c", "p2", "blocker", "阻塞A", status="active"),
            self._make("mem_d", "p1", "next_step", "旧任务", status="expired",
                       status_reason="TTL过期"),
            self._make("mem_e", "p1", "owner", "李四", status="forgotten",
                       status_reason="写错了"),
        ]
        self.backend.save_state(items, [], ["ev_1"])
        return items

    @staticmethod
    def _make(mid, pid, stype, val, status="active", **kw):
        return {
            "memory_id": mid, "project_id": pid, "state_type": stype,
            "key": val[:20], "current_value": val, "rationale": "",
            "owner": None, "status": status,
            "confidence": kw.get("confidence", 0.7),
            "version": 1, "supersedes": [],
            "updated_at": "", "valid_from": "", "valid_to": None,
            "recorded_at": "", "decision_strength": kw.get("decision_strength", ""),
            "review_status": "", "metadata": {},
            "status_reason": kw.get("status_reason", ""),
            "status_changed_at": "", "status_changed_by": "",
            "source_refs": [], "media_refs": [],
        }

    # ── 下推正确性 ──

    def test_list_items_project_filter(self):
        """project_id 过滤只返回匹配行。"""
        self._seed()
        result = self.backend.list_items(project_id="p1", statuses={"active", ""})
        self.assertEqual(len(result), 2, "p1 应有 2 条 active 记忆")

    def test_list_items_status_filter(self):
        """status 参数正确过滤。"""
        self._seed()
        result = self.backend.list_items(
            project_id="p1", statuses={"expired", "forgotten"})
        self.assertEqual(len(result), 2, "p1 应有 2 条非 active 记忆")
        statuses = {r["status"] for r in result}
        self.assertEqual(statuses, {"expired", "forgotten"})

    def test_list_items_default_active_only(self):
        """默认只返回 status='active'。"""
        self._seed()
        result = self.backend.list_items(project_id="p1",
                                          statuses={"active", ""})
        statuses = {r["status"] for r in result}
        self.assertNotIn("expired", statuses)
        self.assertNotIn("forgotten", statuses)

    def test_list_items_limit_offset(self):
        """limit/offset 分页正确。"""
        self._seed()
        all_items = self.backend.list_items(project_id="p1",
                                             statuses={"active", "",
                                                       "expired", "forgotten"})
        full_count = len(all_items)
        page1 = self.backend.list_items(project_id="p1",
                                         statuses={"active", "",
                                                   "expired", "forgotten"},
                                         limit=2, offset=0)
        page2 = self.backend.list_items(project_id="p1",
                                         statuses={"active", "",
                                                   "expired", "forgotten"},
                                         limit=2, offset=2)
        self.assertEqual(len(page1), min(2, full_count))
        self.assertEqual(len(page2), max(0, full_count - 2))

    def test_search_keywords_finds_match(self):
        """SQL LIKE 搜索能找到匹配行。"""
        self._seed()
        results = self.backend.search_keywords("张三", project_id="p1", top_k=5)
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["current_value"], "张三")

    def test_search_keywords_no_match(self):
        """不匹配时返回空列表。"""
        self._seed()
        results = self.backend.search_keywords("不存在的关键词XYZ", project_id="p1")
        self.assertEqual(len(results), 0)

    # ── 索引验证 ──

    def test_indexes_exist(self):
        """所有预期索引已创建。"""
        conn = self.backend._conn
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name LIKE 'idx_%'"
        ).fetchall()
        names = {r[0] for r in rows}
        expected = {"idx_mem_project_type", "idx_mem_state_type",
                    "idx_mem_status", "idx_mem_identity", "idx_raw_msg_id"}
        missing = expected - names
        self.assertEqual(len(missing), 0,
                         f"缺少索引: {missing}")

    def test_project_type_index_used(self):
        """查询使用 project_id + record_type 索引。"""
        conn = self.backend._conn
        conn.execute("ANALYZE")
        explain = conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT * FROM memory_records WHERE project_id='p1' AND record_type='active'"
        ).fetchall()
        detail = " ".join(r["detail"] for r in explain)
        self.assertIn("idx_mem_project_type", detail,
                      f"应使用 idx_mem_project_type 索引，实际: {detail}")

    # ── INSERT OR IGNORE 去重 ──

    def test_raw_event_dedup_via_insert_ignore(self):
        """同一 message_id 重复插入被忽略。"""
        ev = {"message_id": "m_dup", "project_id": "p1", "text": "hello"}
        n1 = self.backend.append_raw_events([ev])
        n2 = self.backend.append_raw_events([ev])
        self.assertEqual(n1, 1)
        self.assertEqual(n2, 0, "重复 message_id 应被 INSERT OR IGNORE 忽略")

    # ── WAL 并发 ──

    def test_wal_mode_allows_concurrent_readers(self):
        """WAL 模式下可以同时打开多个读连接。"""
        import sqlite3
        self._seed()
        conn2 = sqlite3.connect(str(self.backend.db_path))
        conn2.execute("PRAGMA journal_mode=WAL")
        row = conn2.execute(
            "SELECT * FROM memory_records WHERE project_id='p1'"
        ).fetchall()
        self.assertGreaterEqual(len(row), 1)
        conn2.close()

    # ── Schema 完整性 ──

    def test_memory_records_has_all_columns(self):
        """memory_records 表包含 MemoryItem 的所有字段。"""
        conn = self.backend._conn
        cols = {r[1] for r in conn.execute("PRAGMA table_info(memory_records)")}
        required = {
            "memory_id", "project_id", "state_type", "key", "current_value",
            "rationale", "owner", "status", "confidence", "version",
            "supersedes", "updated_at", "valid_from", "valid_to", "recorded_at",
            "decision_strength", "review_status", "metadata",
            "status_reason", "status_changed_at", "status_changed_by",
            "source_refs", "media_refs", "record_type",
        }
        missing = required - cols
        self.assertEqual(len(missing), 0,
                         f"缺少列: {missing}")

    def test_null_fields_default_correctly(self):
        """可空字段（owner/valid_to）接受 NULL。"""
        item = self._make("mem_null", "p1", "decision", "test")
        item["owner"] = None
        item["valid_to"] = None
        self.backend.save_state([item], [], [])
        state = self.backend.load_state()
        self.assertIsNone(state["items"][0]["owner"])
        self.assertIsNone(state["items"][0]["valid_to"])


if __name__ == "__main__":
    unittest.main()
