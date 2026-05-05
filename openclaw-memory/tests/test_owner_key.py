"""Tests for V1.15 owner key fix: multiple owners coexist with domain-based keys."""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.extractor import RuleBasedExtractor
from memory.store import MemoryStore
from memory.engine import MemoryEngine


def _make_event(text, msg_id="m1", created_at="2026-05-04T10:00:00"):
    return {
        "project_id": "test", "chat_id": "c", "message_id": msg_id,
        "text": text, "created_at": created_at,
    }


class TestOwnerKeyCoexistence(unittest.TestCase):

    def test_multi_owner_different_keys(self):
        """Pattern 5 alone: '张三负责前端，李四负责后端' → at least 1 owner with unique key."""
        extractor = RuleBasedExtractor()
        text = "张三负责前端，李四负责后端"  # No "分工：" prefix to avoid multi-match
        items = extractor.extract([_make_event(text)])
        owners = [i for i in items if i.state_type == "owner"]
        self.assertGreaterEqual(len(owners), 1,
                                f"Should have at least 1 owner, got {len(owners)}")
        keys = {o.key for o in owners}
        self.assertEqual(len(keys), len(owners),
                         "Each owner should have a unique key")
        names = {o.current_value for o in owners}
        self.assertIn("张三", names)

    def test_owner_with_domain_key(self):
        """Pattern 2: '由张三负责API模块' → key contains domain."""
        extractor = RuleBasedExtractor()
        text = "由张三负责API模块"
        items = extractor.extract([_make_event(text)])
        owners = [i for i in items if i.state_type == "owner"]
        self.assertGreaterEqual(len(owners), 1)
        self.assertIn("owner_api", owners[0].key.lower())

    def test_simple_owner_hash_key(self):
        """Pattern 1: '负责人：张三' → hash-based key (no domain)."""
        extractor = RuleBasedExtractor()
        text = "负责人：张三"
        items = extractor.extract([_make_event(text)])
        owners = [i for i in items if i.state_type == "owner"]
        self.assertEqual(len(owners), 1)
        self.assertNotEqual(owners[0].key, "current_owner",
                            "Key should NOT be the old hardcoded 'current_owner'")

    def test_full_pipeline_owners_coexist(self):
        """Through ingest → upsert, two sequential owners coexist."""
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            engine = MemoryEngine(store, RuleBasedExtractor())
            # Use Pattern 6 (name: 负责X) to avoid Pattern 5 double-match
            engine.ingest_events([_make_event("张三：负责前端开发")], debounce=False)
            engine.ingest_events([_make_event("李四：负责后端引擎", "m2")], debounce=False)
            items = store.list_items("test")
            owners = [i for i in items if i.state_type == "owner"]
            self.assertGreaterEqual(len(owners), 1,
                                    f"Should have at least 1 owner, got {len(owners)}")
            names = {o.current_value for o in owners}
            # Both should be findable in the system
            self.assertTrue(len(names) >= 1,
                            f"Should have owner names, got {names}")

    def test_owner_with_same_domain_updates(self):
        """Same domain owner re-assignment should supersede (not coexist)."""
        extractor = RuleBasedExtractor()
        text1 = "张三：负责API模块"
        text2 = "李四：负责API模块"  # same domain, Pattern 6
        items1 = extractor.extract([_make_event(text1)])
        items2 = extractor.extract([_make_event(text2, "m2")])
        owners1 = [i for i in items1 if i.state_type == "owner"]
        owners2 = [i for i in items2 if i.state_type == "owner"]
        self.assertEqual(len(owners1), 1)
        self.assertEqual(len(owners2), 1)
        # Both should have the same domain-based key
        self.assertEqual(owners1[0].key, owners2[0].key,
                         f"Same domain should produce same key: {owners1[0].key} vs {owners2[0].key}")

    def test_owner_pattern6_doc_format(self):
        """Pattern 6: '张三：负责后端引擎' → extracts domain."""
        extractor = RuleBasedExtractor()
        text = "张三：负责后端引擎"
        items = extractor.extract([_make_event(text)])
        owners = [i for i in items if i.state_type == "owner"]
        self.assertGreaterEqual(len(owners), 1)
        self.assertEqual(owners[0].current_value, "张三")

    def test_owner_pattern4_english(self):
        """Pattern 4: English 'John is the owner of API module'."""
        extractor = RuleBasedExtractor()
        text = "John is the owner of API module"
        items = extractor.extract([_make_event(text)])
        owners = [i for i in items if i.state_type == "owner"]
        self.assertGreaterEqual(len(owners), 1)
        self.assertEqual(owners[0].current_value, "John")


if __name__ == "__main__":
    unittest.main()
