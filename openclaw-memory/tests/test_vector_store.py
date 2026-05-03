"""Tests for vector_store.py — VectorStore with ChromaDB + FakeEmbeddingProvider."""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.embedding_provider import FakeEmbeddingProvider
from memory.schema import MemoryItem, SourceRef


def _make_item(
    memory_id: str = "mem_test_001",
    project_id: str = "proj_test",
    state_type: str = "decision",
    key: str = "test_key",
    current_value: str = "采用 React 框架开发前端",
    rationale: str = "团队投票决定使用 React",
    owner: str = "张三",
    excerpts: list[str] | None = None,
) -> MemoryItem:
    """Create a test MemoryItem with optional custom excerpts."""
    refs = []
    for i, exc in enumerate(excerpts or [current_value]):
        refs.append(SourceRef(
            type="message",
            chat_id="chat_test",
            message_id=f"msg_{memory_id}_{i}",
            excerpt=exc,
            created_at="2026-05-01T10:00:00",
            sender_name=owner,
            sender_id=f"ou_{owner}",
            source_url=f"https://app.feishu.cn/client/messages/chat_test/msg_{memory_id}_{i}",
        ))
    return MemoryItem(
        project_id=project_id,
        state_type=state_type,
        key=key,
        current_value=current_value,
        rationale=rationale,
        owner=owner,
        status="active",
        confidence=0.9,
        source_refs=refs,
        memory_id=memory_id,
    )


class TestVectorStoreBasic(unittest.TestCase):
    """Test VectorStore index/search/remove operations."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory(ignore_cleanup_errors=True)
        self.provider = FakeEmbeddingProvider(dimension=128)
        try:
            from memory.vector_store import VectorStore
            self.vs = VectorStore(
                data_dir=self.temp_dir.name,
                embedding_provider=self.provider,
                similarity_threshold=-2.0,  # accept all for testing (fake vectors can have negative cosine)
            )
            self.skip_chromadb = not self.vs.available
        except Exception:
            self.skip_chromadb = True

    def tearDown(self):
        if hasattr(self, "vs") and self.vs.available:
            self.vs.close()
        if hasattr(self, "engine_vs"):
            self.engine_vs.close()
        self.temp_dir.cleanup()

    def test_index_and_search(self):
        if self.skip_chromadb:
            self.skipTest("chromadb not available")

        item = _make_item()
        self.vs.index_item(item)

        stats = self.vs.stats()
        self.assertEqual(stats["memories"], 1)
        self.assertGreaterEqual(stats["evidence"], 1)

        results = self.vs.search("React 框架", project_id="proj_test")
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0][0], "mem_test_001")

    def test_index_multiple_items(self):
        if self.skip_chromadb:
            self.skipTest("chromadb not available")

        items = [
            _make_item(memory_id="mem_001", current_value="周五前 API 必须上线",
                       rationale="产品经理要求本周交付"),
            _make_item(memory_id="mem_002", current_value="张三负责前端开发",
                       rationale="团队会议确认分工"),
            _make_item(memory_id="mem_003", current_value="被设计稿卡住了",
                       rationale="设计师还没输出最终版", state_type="blocker"),
        ]
        count = self.vs.index_items(items)
        self.assertEqual(count, 3)

        stats = self.vs.stats()
        self.assertEqual(stats["memories"], 3)

    def test_remove_item(self):
        if self.skip_chromadb:
            self.skipTest("chromadb not available")

        item = _make_item(memory_id="mem_to_remove")
        self.vs.index_item(item)
        self.assertEqual(self.vs.stats()["memories"], 1)

        self.vs.remove_item("mem_to_remove")
        self.assertEqual(self.vs.stats()["memories"], 0)

    def test_search_empty_query(self):
        if self.skip_chromadb:
            self.skipTest("chromadb not available")

        item = _make_item()
        self.vs.index_item(item)
        results = self.vs.search("")
        self.assertEqual(results, [])

    def test_search_with_project_filter(self):
        if self.skip_chromadb:
            self.skipTest("chromadb not available")

        item_a = _make_item(memory_id="mem_a", project_id="proj_A",
                            current_value="项目 A 的决策")
        item_b = _make_item(memory_id="mem_b", project_id="proj_B",
                            current_value="项目 B 的决策")
        self.vs.index_items([item_a, item_b])

        results_a = self.vs.search("决策", project_id="proj_A")
        result_ids = [r[0] for r in results_a]
        self.assertIn("mem_a", result_ids)
        self.assertNotIn("mem_b", result_ids)

    def test_search_evidence(self):
        if self.skip_chromadb:
            self.skipTest("chromadb not available")

        item = _make_item(
            memory_id="mem_ev",
            current_value="决定使用 Vue",
            excerpts=["前端框架我们用 Vue 吧", "Vue 的生态比较好"],
        )
        self.vs.index_item(item)

        results = self.vs.search_evidence("框架选择")
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0][0], "mem_ev")

    def test_rebuild_index(self):
        if self.skip_chromadb:
            self.skipTest("chromadb not available")

        items = [
            _make_item(memory_id="mem_r1", current_value="第一条"),
            _make_item(memory_id="mem_r2", current_value="第二条"),
        ]
        self.vs.index_items(items)
        self.assertEqual(self.vs.stats()["memories"], 2)

        new_items = [_make_item(memory_id="mem_r3", current_value="第三条")]
        count = self.vs.rebuild_index(new_items)
        self.assertEqual(count, 1)
        self.assertEqual(self.vs.stats()["memories"], 1)

    def test_upsert_updates_existing(self):
        if self.skip_chromadb:
            self.skipTest("chromadb not available")

        item_v1 = _make_item(memory_id="mem_up", current_value="版本 1")
        self.vs.index_item(item_v1)

        item_v2 = _make_item(memory_id="mem_up", current_value="版本 2 更新了")
        self.vs.index_item(item_v2)

        self.assertEqual(self.vs.stats()["memories"], 1)

    def test_graceful_when_unavailable(self):
        """If ChromaDB fails, methods return empty without raising."""
        from memory.vector_store import VectorStore
        vs = VectorStore.__new__(VectorStore)
        vs._available = False
        vs.data_dir = Path(self.temp_dir.name)
        vs.embedding_provider = self.provider
        vs.similarity_threshold = 0.5

        self.assertEqual(vs.search("test"), [])
        self.assertEqual(vs.search_evidence("test"), [])
        self.assertEqual(vs.index_items([_make_item()]), 0)
        vs.remove_item("anything")  # should not raise


class TestHybridSearch(unittest.TestCase):
    """Test the search_hybrid method in MemoryStore."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory(ignore_cleanup_errors=True)
        self.data_dir = Path(self.temp_dir.name)
        self.provider = FakeEmbeddingProvider(dimension=128)

        from memory.store import MemoryStore
        self.store = MemoryStore(self.data_dir / "store")

        try:
            from memory.vector_store import VectorStore
            self.vs = VectorStore(
                data_dir=self.data_dir / "vectors",
                embedding_provider=self.provider,
                similarity_threshold=-2.0,
            )
            self.skip_chromadb = not self.vs.available
        except Exception:
            self.skip_chromadb = True

    def tearDown(self):
        if hasattr(self, "vs") and self.vs.available:
            self.vs.close()
        if hasattr(self, "engine_vs"):
            self.engine_vs.close()
        self.temp_dir.cleanup()

    def test_hybrid_fallback_to_keyword_when_no_vector_store(self):
        """Without vector_store, search_hybrid = search_keywords."""
        from memory.engine import MemoryEngine
        from memory.extractor import RuleBasedExtractor

        engine = MemoryEngine(self.store, RuleBasedExtractor())
        events = [{
            "project_id": "test",
            "chat_id": "c1",
            "message_id": "m1",
            "text": "负责人：张三负责前端",
            "created_at": "2026-05-01T10:00:00",
        }]
        engine.ingest_events(events, debounce=False)

        results = self.store.search_hybrid("前端", project_id="test", vector_store=None)
        self.assertGreater(len(results), 0)

    def test_hybrid_with_vector_store(self):
        if self.skip_chromadb:
            self.skipTest("chromadb not available")

        from memory.engine import MemoryEngine
        from memory.extractor import RuleBasedExtractor

        engine = MemoryEngine(self.store, RuleBasedExtractor(), vector_store=self.vs)
        events = [
            {
                "project_id": "test",
                "chat_id": "c1",
                "message_id": "m1",
                "text": "负责人：张三负责前端开发",
                "created_at": "2026-05-01T10:00:00",
            },
            {
                "project_id": "test",
                "chat_id": "c1",
                "message_id": "m2",
                "text": "决策：使用 React 作为前端框架",
                "created_at": "2026-05-01T10:01:00",
            },
        ]
        engine.ingest_events(events, debounce=False)

        results = engine.search_hybrid("前端", project_id="test")
        self.assertGreater(len(results), 0)

    def test_engine_search_hybrid_method(self):
        if self.skip_chromadb:
            self.skipTest("chromadb not available")

        from memory.engine import MemoryEngine
        from memory.extractor import RuleBasedExtractor

        engine = MemoryEngine(self.store, RuleBasedExtractor(), vector_store=self.vs)
        events = [{
            "project_id": "test",
            "chat_id": "c1",
            "message_id": "m1",
            "text": "阻塞：被设计稿卡住了，设计师还没出最终版",
            "created_at": "2026-05-01T10:00:00",
        }]
        engine.ingest_events(events, debounce=False)

        kw_results = engine.search("设计", project_id="test")
        hybrid_results = engine.search_hybrid("设计", project_id="test")
        self.assertGreater(len(kw_results), 0)
        self.assertGreater(len(hybrid_results), 0)


if __name__ == "__main__":
    unittest.main()
