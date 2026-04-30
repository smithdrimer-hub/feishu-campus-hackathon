"""Tests for V1.8 multi-source ingestion: doc and task data sources."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.engine import MemoryEngine
from memory.extractor import RuleBasedExtractor
from memory.schema import source_ref_from_doc, source_ref_from_task
from memory.store import MemoryStore


class MockCliResult:
    """模拟 LarkCliAdapter 的 CliResult."""

    def __init__(self, data, returncode=0, stdout="", stderr=""):
        self.data = data
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestSourceRefBuilders(unittest.TestCase):
    """V1.8: SourceRef builder 函数测试."""

    def test_source_ref_from_doc(self):
        """source_ref_from_doc 应生成 type=doc 的 SourceRef."""
        doc_data = {"doc_id": "doc_abc123", "title": "需求文档 V2", "created_at": "2026-04-28T10:00:00"}
        ref = source_ref_from_doc(doc_data)
        self.assertEqual(ref.type, "doc")
        self.assertEqual(ref.message_id, "doc_abc123")
        self.assertIn("需求文档 V2", ref.excerpt)

    def test_source_ref_from_task(self):
        """source_ref_from_task 应生成 type=task 的 SourceRef."""
        task_data = {"guid": "task_xyz", "summary": "完成 API 文档", "created_at": "2026-04-28T10:00:00"}
        ref = source_ref_from_task(task_data)
        self.assertEqual(ref.type, "task")
        self.assertEqual(ref.message_id, "task_xyz")
        self.assertIn("API 文档", ref.excerpt)


class TestSyncDoc(unittest.TestCase):
    """V1.8: sync_doc 方法测试."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp_dir.name))
        self.mock_adapter = MagicMock()
        self.engine = MemoryEngine(self.store, RuleBasedExtractor(), adapter=self.mock_adapter)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_sync_doc_no_adapter_raises(self):
        """没有设置 adapter 时应抛出 RuntimeError."""
        engine = MemoryEngine(self.store, RuleBasedExtractor())
        with self.assertRaises(RuntimeError):
            engine.sync_doc("doc_xxx")

    def test_sync_doc_creates_event(self):
        """sync_doc 应调用 adapter.fetch_doc 并提取记忆。"""
        self.mock_adapter.fetch_doc.return_value = MockCliResult({
            "data": {
                "doc_id": "doc_test_001",
                "title": "测试文档",
                "markdown": "目标：完成 V1.8 文档接入\n负责人：张三负责实现",
            }
        })
        items = self.engine.sync_doc("doc_test_001", project_id="test")
        # sync_doc 中 ingest_events 会调用 extractor
        # RuleBasedExtractor 应提取到 goal 和 owner
        self.assertGreater(len(items), 0)
        state_types = {item.state_type for item in items}
        self.assertIn("project_goal", state_types)
        self.assertIn("owner", state_types)

    def test_sync_doc_fetch_failure(self):
        """fetch_doc 失败时应返回空列表。"""
        self.mock_adapter.fetch_doc.return_value = MockCliResult(None, returncode=1, stderr="error")
        items = self.engine.sync_doc("doc_fail", project_id="test")
        self.assertEqual(len(items), 0)


class TestSyncTasks(unittest.TestCase):
    """V1.8: sync_tasks 方法测试."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp_dir.name))
        self.mock_adapter = MagicMock()
        self.engine = MemoryEngine(self.store, RuleBasedExtractor(), adapter=self.mock_adapter)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_sync_tasks_creates_events(self):
        """sync_tasks 应调用 adapter.search_tasks 并为每个任务创建事件。"""
        self.mock_adapter.search_tasks.return_value = MockCliResult({
            "data": {
                "items": [
                    {"guid": "task_001", "summary": "完成测试模块", "status": "todo"},
                    {"guid": "task_002", "summary": "修复 bug #123", "status": "in_progress"},
                ]
            }
        })
        items = self.engine.sync_tasks("test", project_id="test")
        # sync_tasks 中 ingest_events 调 extractor
        # RuleBasedExtractor 从任务文本中提取记忆
        self.assertGreater(len(items), 0)

    def test_sync_tasks_empty(self):
        """无匹配任务时应返回空列表。"""
        self.mock_adapter.search_tasks.return_value = MockCliResult({
            "data": {"items": []}
        })
        items = self.engine.sync_tasks("不存在的关键词", project_id="test")
        self.assertEqual(len(items), 0)

    def test_sync_tasks_no_adapter_raises(self):
        """没有设置 adapter 时应抛出 RuntimeError。"""
        engine = MemoryEngine(self.store, RuleBasedExtractor())
        with self.assertRaises(RuntimeError):
            engine.sync_tasks("test")

    def test_sync_tasks_fetch_failure(self):
        """search_tasks 失败时应返回空列表。"""
        self.mock_adapter.search_tasks.return_value = MockCliResult(None, returncode=1, stderr="error")
        items = self.engine.sync_tasks("test", project_id="test")
        self.assertEqual(len(items), 0)


class TestKeywordSearch(unittest.TestCase):
    """V1.9: 关键词搜索功能测试."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp_dir.name))
        # 预置一些记忆项用于搜索测试
        self._seed_data()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _seed_data(self):
        """写入几条不同内容的记忆项。"""
        from memory.schema import MemoryItem, SourceRef

        items = [
            MemoryItem(
                project_id="search_test", state_type="owner", key="owner_1",
                current_value="张三负责 API 文档开发",
                rationale="消息 msg_001 指定负责人", owner="张三",
                status="active", confidence=0.8,
                source_refs=[SourceRef("message", "chat_01", "msg_001",
                                        "张三负责 API 文档开发", "2026-04-28T10:00:00")],
            ),
            MemoryItem(
                project_id="search_test", state_type="blocker", key="blocker_1",
                current_value="测试数据还没准备好，后端接口被阻塞",
                rationale="消息 msg_002 报告阻塞", owner=None,
                status="active", confidence=0.7,
                source_refs=[SourceRef("message", "chat_01", "msg_002",
                                        "测试数据还没准备好", "2026-04-28T10:00:00")],
            ),
            MemoryItem(
                project_id="search_test", state_type="decision", key="decision_1",
                current_value="采用方案 A 进行开发",
                rationale="会上讨论确定", owner=None,
                status="active", confidence=0.85,
                source_refs=[SourceRef("message", "chat_01", "msg_003",
                                        "采用方案 A", "2026-04-28T10:00:00")],
            ),
            MemoryItem(
                project_id="other_proj", state_type="owner", key="owner_2",
                current_value="李四负责前端开发",
                rationale="任务分配", owner="李四",
                status="active", confidence=0.8,
                source_refs=[SourceRef("message", "chat_02", "msg_004",
                                        "李四负责前端", "2026-04-28T10:00:00")],
            ),
        ]
        self.store.upsert_items(items)

    def test_search_api_doc(self):
        """搜索 'API' 应返回相关项。"""
        results = self.store.search_keywords("API", project_id="search_test")
        self.assertGreater(len(results), 0)
        top_value = results[0][0].current_value
        self.assertIn("API", top_value)

    def test_search_blocker(self):
        """搜索 '阻塞' 应找到阻塞项。"""
        results = self.store.search_keywords("阻塞", project_id="search_test")
        self.assertGreater(len(results), 0)
        self.assertIn("blocker", results[0][0].state_type)

    def test_search_project_id_filter(self):
        """搜索时 project_id 过滤应排除其他项目。"""
        results = self.store.search_keywords("开发", project_id="search_test")
        for item, score in results:
            self.assertEqual(item.project_id, "search_test")

    def test_search_no_match(self):
        """无匹配时应返回空列表。"""
        results = self.store.search_keywords("zzz_not_exist", project_id="search_test")
        self.assertEqual(len(results), 0)

    def test_search_top_k(self):
        """top_k 参数应限制结果数。"""
        results = self.store.search_keywords("开发", project_id=None, top_k=1)
        self.assertLessEqual(len(results), 1)

    def test_search_score_ordering(self):
        """搜索结果应按分数降序排列。"""
        results = self.store.search_keywords("负责", project_id="search_test")
        for i in range(len(results) - 1):
            self.assertGreaterEqual(results[i][1], results[i + 1][1])

    def test_search_via_engine(self):
        """通过 MemoryEngine.search() 调用。"""
        engine = MemoryEngine(self.store, RuleBasedExtractor())
        results = engine.search("API", project_id="search_test")
        self.assertGreater(len(results), 0)

    def test_tokenize_chinese(self):
        """中文查询应拆分为单字。"""
        tokens = MemoryStore._tokenize_query("API 文档")
        self.assertIn("api", tokens)
        self.assertIn("文", tokens)
        self.assertIn("档", tokens)

    def test_tokenize_english(self):
        """英文查询应空格分词。"""
        tokens = MemoryStore._tokenize_query("hello world")
        self.assertIn("hello", tokens)
        self.assertIn("world", tokens)

    def test_tokenize_mixed(self):
        """中英文混合查询。"""
        tokens = MemoryStore._tokenize_query("张三 API")
        self.assertIn("张", tokens)
        self.assertIn("三", tokens)
        self.assertIn("api", tokens)


if __name__ == "__main__":
    unittest.main()