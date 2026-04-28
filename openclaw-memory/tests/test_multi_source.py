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


if __name__ == "__main__":
    unittest.main()