"""Tests for OpenAIProvider with mocked API calls."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.extractor import LLMExtractor
from memory.llm_provider import OpenAIProvider


class TestOpenAIProviderMocked(unittest.TestCase):
    """Test OpenAIProvider with mocked API responses."""

    def setUp(self):
        """Set up a mocked OpenAI client."""
        self.mock_client = MagicMock()
        self.mock_response = MagicMock()
        self.mock_choice = MagicMock()
        self.mock_choice.message.content = (
            '{"candidates": [{"project_id": "test", "state_type": "decision", '
            '"key": "test", "current_value": "采用方案A", "rationale": "test", '
            '"owner": null, "status": "active", "confidence": 0.85, '
            '"detected_at": "2026-04-28T10:00:00", '
            '"source_refs": [{"type": "message", "chat_id": "chat_test", '
            '"message_id": "msg_001", "excerpt": "决策测试", '
            '"created_at": "2026-04-28T10:00:00"}]}]}'
        )
        self.mock_response.choices = [self.mock_choice]
        self.mock_client.chat.completions.create.return_value = self.mock_response

    def test_generate_returns_json(self):
        """OpenAIProvider.generate() should return the response content."""
        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o-mini")
        # Replace the client with mock
        provider.client = self.mock_client

        result = provider.generate("test prompt")
        self.assertEqual(result, self.mock_choice.message.content)

    def test_generate_correct_params(self):
        """OpenAIProvider should send correct parameters to OpenAI."""
        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o-mini")
        provider.client = self.mock_client

        provider.generate("test prompt")
        call_kwargs = self.mock_client.chat.completions.create.call_args[1]
        self.assertEqual(call_kwargs["model"], "gpt-4o-mini")
        self.assertEqual(call_kwargs["messages"][0]["content"], "test prompt")
        self.assertEqual(call_kwargs["response_format"]["type"], "json_object")

    def test_missing_api_key_raises(self):
        """Without API key and env var, should raise ValueError."""
        # Temporarily clear env var
        import os
        old_key = os.environ.pop("OPENAI_API_KEY", None)
        try:
            with self.assertRaises(ValueError):
                OpenAIProvider(api_key="")
        finally:
            if old_key is not None:
                os.environ["OPENAI_API_KEY"] = old_key

    @patch("memory.llm_provider.OpenAIProvider")
    def test_llm_extractor_with_provider(self, MockProvider):
        """LLMExtractor with OpenAIProvider should work."""
        mock_instance = MockProvider.return_value
        mock_instance.generate.return_value = (
            '{"candidates": [{"project_id": "test", "state_type": "decision", '
            '"key": "test", "current_value": "采用方案A", "rationale": "test", '
            '"owner": null, "status": "active", "confidence": 0.85, '
            '"detected_at": "2026-04-28T10:00:00", '
            '"source_refs": [{"type": "message", "chat_id": "chat_test", '
            '"message_id": "msg_001", "excerpt": "决策测试", '
            '"created_at": "2026-04-28T10:00:00"}]}]}'
        )

        from memory.extractor import LLMExtractor
        extractor = LLMExtractor(mock_instance)
        events = [
            {"project_id": "test", "chat_id": "chat_test", "message_id": "msg_001",
             "text": "决策：采用方案A", "created_at": "2026-04-28T10:00:00"}
        ]
        items = extractor.extract(events)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].state_type, "decision")

    @patch("memory.llm_provider.OpenAIProvider")
    def test_llm_extractor_fallback_on_error(self, MockProvider):
        """LLMExtractor should fall back to RuleBased on invalid JSON."""
        mock_instance = MockProvider.return_value
        mock_instance.generate.return_value = "这不是有效 JSON"

        from memory.extractor import RuleBasedExtractor
        extractor = LLMExtractor(mock_instance, fallback=RuleBasedExtractor())
        events = [
            {"project_id": "test", "chat_id": "chat_test", "message_id": "msg_001",
             "text": "决策：采用方案A", "created_at": "2026-04-28T10:00:00"}
        ]
        items = extractor.extract(events)
        # Should fall back to RuleBased and extract the decision
        self.assertGreater(len(items), 0)


if __name__ == "__main__":
    unittest.main()