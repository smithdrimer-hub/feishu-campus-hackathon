"""Tests for embedding_provider.py — FakeEmbeddingProvider and interface."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.embedding_provider import EmbeddingProvider, FakeEmbeddingProvider


class TestFakeEmbeddingProvider(unittest.TestCase):
    """Test the FakeEmbeddingProvider used in all non-API tests."""

    def setUp(self):
        self.provider = FakeEmbeddingProvider(dimension=128)

    def test_embed_single_returns_correct_dimension(self):
        vec = self.provider.embed_single("hello world")
        self.assertEqual(len(vec), 128)

    def test_embed_batch_returns_correct_count(self):
        texts = ["hello", "world", "test"]
        results = self.provider.embed(texts)
        self.assertEqual(len(results), 3)
        for vec in results:
            self.assertEqual(len(vec), 128)

    def test_deterministic_same_text_same_vector(self):
        vec1 = self.provider.embed_single("同一段文字")
        vec2 = self.provider.embed_single("同一段文字")
        self.assertEqual(vec1, vec2)

    def test_different_text_different_vector(self):
        vec1 = self.provider.embed_single("文字 A")
        vec2 = self.provider.embed_single("文字 B")
        self.assertNotEqual(vec1, vec2)

    def test_empty_list_returns_empty(self):
        results = self.provider.embed([])
        self.assertEqual(results, [])

    def test_dimension_property(self):
        p64 = FakeEmbeddingProvider(dimension=64)
        self.assertEqual(p64.dimension, 64)
        vec = p64.embed_single("test")
        self.assertEqual(len(vec), 64)

    def test_values_in_range(self):
        vec = self.provider.embed_single("range check")
        for val in vec:
            self.assertGreaterEqual(val, -1.0)
            self.assertLessEqual(val, 1.0)

    def test_base_class_raises(self):
        base = EmbeddingProvider()
        with self.assertRaises(NotImplementedError):
            base.embed(["test"])
        with self.assertRaises(NotImplementedError):
            _ = base.dimension


if __name__ == "__main__":
    unittest.main()
