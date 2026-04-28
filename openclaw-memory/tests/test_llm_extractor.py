"""Tests for V1.1 LLM extraction, schema validation, and fallback."""

import json
import sys
import unittest
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.engine import MemoryEngine  # noqa: E402
from memory.extractor import LLMExtractor, RuleBasedExtractor  # noqa: E402
from memory.llm_provider import LLMProvider  # noqa: E402
from memory.store import MemoryStore  # noqa: E402


class StaticProvider(LLMProvider):
    """Test provider that returns a fixed response."""

    def __init__(self, response: str) -> None:
        """Create a provider with a fixed response string."""
        self.response = response

    def generate(self, prompt: str) -> str:
        """Return the fixed response and ignore the prompt."""
        return self.response


def fresh_test_dir(name: str) -> Path:
    """Return a unique project-local temp directory for tests."""
    path = ROOT / ".test-tmp" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def raw_event() -> dict:
    """Build a raw event that can anchor valid LLM candidates."""
    return {
        "project_id": "demo",
        "chat_id": "oc_llm",
        "message_id": "om_llm_1",
        "text": "关键决策：采用 LLM 结构化提取。",
        "created_at": "2026-04-25T11:00:00+08:00",
    }


def valid_payload() -> dict:
    """Return a valid strict JSON payload for LLM extraction tests."""
    return {
        "candidates": [
            {
                "project_id": "demo",
                "state_type": "decision",
                "key": "extractor_strategy",
                "current_value": "采用 LLM 结构化提取",
                "rationale": "消息明确记录了提取策略决策。",
                "owner": None,
                "status": "active",
                "confidence": 0.91,
                "detected_at": "2026-04-25T11:00:00+08:00",
                "source_refs": [
                    {
                        "type": "message",
                        "chat_id": "oc_llm",
                        "message_id": "om_llm_1",
                        "excerpt": "关键决策：采用 LLM 结构化提取。",
                        "created_at": "2026-04-25T11:00:00+08:00",
                    }
                ],
            }
        ]
    }


class LLMExtractorTest(unittest.TestCase):
    """Verify trusted LLM extraction behavior."""

    def test_valid_llm_output_can_write_memory_state(self) -> None:
        """A valid LLM payload should become active memory state."""
        store = MemoryStore(fresh_test_dir("llm_valid"))
        extractor = LLMExtractor(StaticProvider(json.dumps(valid_payload(), ensure_ascii=False)))
        items = MemoryEngine(store, extractor=extractor).ingest_events([raw_event()])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].state_type, "decision")
        self.assertEqual(items[0].source_refs[0].message_id, "om_llm_1")
        self.assertEqual(store.list_items("demo")[0].current_value, "采用 LLM 结构化提取")

    def test_non_json_output_falls_back_to_rules(self) -> None:
        """Free-form LLM text should be rejected and rule fallback should run."""
        extractor = LLMExtractor(StaticProvider("这里是自由摘要，不是 JSON"), fallback=RuleBasedExtractor())
        items = extractor.extract([raw_event()])
        self.assertTrue(items)
        self.assertTrue(any(item.state_type == "decision" for item in items))

    def test_missing_field_output_is_rejected(self) -> None:
        """A candidate missing required fields should be discarded via fallback."""
        payload = valid_payload()
        del payload["candidates"][0]["rationale"]
        extractor = LLMExtractor(StaticProvider(json.dumps(payload, ensure_ascii=False)), fallback=RuleBasedExtractor())
        items = extractor.extract([raw_event()])
        self.assertTrue(any(item.state_type == "decision" for item in items))
        self.assertFalse(any(item.key == "extractor_strategy" for item in items))

    def test_candidate_without_source_refs_cannot_write(self) -> None:
        """A candidate without evidence anchors should not be written to memory state."""
        payload = valid_payload()
        payload["candidates"][0]["source_refs"] = []
        store = MemoryStore(fresh_test_dir("llm_no_refs"))
        extractor = LLMExtractor(StaticProvider(json.dumps(payload, ensure_ascii=False)), fallback=RuleBasedExtractor())
        items = MemoryEngine(store, extractor=extractor).ingest_events([raw_event()])
        self.assertFalse(any(item.key == "extractor_strategy" for item in items))

    def test_unknown_source_message_id_cannot_write(self) -> None:
        """A candidate referencing a non-input message_id should be rejected."""
        payload = valid_payload()
        payload["candidates"][0]["source_refs"][0]["message_id"] = "om_missing"
        store = MemoryStore(fresh_test_dir("llm_bad_ref"))
        extractor = LLMExtractor(StaticProvider(json.dumps(payload, ensure_ascii=False)), fallback=RuleBasedExtractor())
        items = MemoryEngine(store, extractor=extractor).ingest_events([raw_event()])
        self.assertFalse(any(item.key == "extractor_strategy" for item in items))


if __name__ == "__main__":
    unittest.main()
