"""Tests for V1.1 LLM extraction, schema validation, fallback, and ADD-only strategy."""

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


class TestAddOnlyStrategy(unittest.TestCase):
    """Tests for ADD-only extraction strategy (V1.9, inspired by mem0).

    核心原则：LLM 只做 ADD，不做 UPDATE/DELETE。
    如果 LLM 输出 status=superseded，系统应强制改为 active。
    """

    def test_llm_superseded_forced_to_active(self) -> None:
        """LLM 输出 status=superseded 时，candidate_to_memory_item 应改为 active."""
        from memory.candidate import MemoryCandidate, candidate_to_memory_item
        from memory.schema import SourceRef

        candidate = MemoryCandidate(
            project_id="test",
            state_type="decision",
            key="test_key",
            current_value="旧决策",
            rationale="test",
            owner=None,
            status="superseded",  # LLM 错误输出 superseded
            confidence=0.8,
            source_refs=[
                SourceRef(type="message", chat_id="chat", message_id="msg_001",
                          excerpt="旧决策", created_at="2026-04-28T10:00:00")
            ],
            detected_at="2026-04-28T10:00:00",
        )
        item = candidate_to_memory_item(candidate)
        self.assertEqual(item.status, "active",
                         "LLM 输出的 superseded 应被强制改为 active")

    def test_llm_active_preserved(self) -> None:
        """LLM 输出 status=active 时保持不变."""
        from memory.candidate import MemoryCandidate, candidate_to_memory_item
        from memory.schema import SourceRef

        candidate = MemoryCandidate(
            project_id="test",
            state_type="decision",
            key="test_key",
            current_value="新决策",
            rationale="test",
            owner=None,
            status="active",
            confidence=0.8,
            source_refs=[
                SourceRef(type="message", chat_id="chat", message_id="msg_001",
                          excerpt="新决策", created_at="2026-04-28T10:00:00")
            ],
            detected_at="2026-04-28T10:00:00",
        )
        item = candidate_to_memory_item(candidate)
        self.assertEqual(item.status, "active")

    def test_add_only_golden_set_still_passes(self) -> None:
        """ADD-only 策略修改后，Golden Set 用 LLM 跑应仍然全通。"""
        # 这是集成验证，通过 scripts/run_golden_eval.py --llm 手动跑
        # 此处验证现有 LLM 路径没被破坏
        payload = valid_payload()
        # 即使 LLM 输出了 superseded
        payload["candidates"][0]["status"] = "superseded"
        store = MemoryStore(fresh_test_dir("add_only"))
        extractor = LLMExtractor(
            StaticProvider(json.dumps(payload, ensure_ascii=False)),
            fallback=RuleBasedExtractor()
        )
        items = MemoryEngine(store, extractor=extractor).ingest_events([raw_event()])
        # 应该仍然提取到，且 status 为 active
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].status, "active")


if __name__ == "__main__":
    unittest.main()
