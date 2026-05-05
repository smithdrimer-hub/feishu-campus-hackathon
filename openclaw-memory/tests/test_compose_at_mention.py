"""Tests for compose_at_mention XML tag helper."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adapters.lark_cli_adapter import compose_at_mention


class TestComposeAtMention(unittest.TestCase):
    def test_basic_mention(self):
        result = compose_at_mention("ou_487c123", "ZhangSan")
        self.assertEqual(result, '<at user_id="ou_487c123">ZhangSan</at>')

    def test_empty_display_name_falls_back_to_open_id(self):
        result = compose_at_mention("ou_487c123")
        self.assertEqual(result, '<at user_id="ou_487c123">ou_487c123</at>')

    def test_mention_embedded_in_message(self):
        tag = compose_at_mention("ou_xxx", "LiSi")
        content = f"Hello {tag}, please check the task"
        self.assertIn('<at user_id="ou_xxx">LiSi</at>', content)

    def test_chinese_name(self):
        result = compose_at_mention("ou_abc", "张三")
        self.assertEqual(result, '<at user_id="ou_abc">张三</at>')


if __name__ == "__main__":
    unittest.main()
