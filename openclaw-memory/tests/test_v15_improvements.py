"""Tests for V1.5 improvements: 3-layer dedup, debounce, and prompt grounding."""

import sys
import unittest
from pathlib import Path

# Add src directory to Python path for imports
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from memory.engine import MemoryEngine
from memory.extractor import LLMExtractor, RuleBasedExtractor
from memory.llm_provider import FakeLLMProvider
from memory.schema import MemoryItem, SourceRef
from memory.store import MemoryStore


class TestThreeLayerDedup(unittest.TestCase):
    """Tests for V1.5 3-layer deduplication logic."""

    def setUp(self):
        """Create a temporary store for each test."""
        self.temp_dir = TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp_dir.name))

    def tearDown(self):
        """Clean up temporary directory."""
        self.temp_dir.cleanup()

    def _make_item(self, key, current_value, owner=None, confidence=0.8,
                   message_id="msg_test", state_type="task"):
        """Helper to create a MemoryItem with minimal fields."""
        return MemoryItem(
            project_id="test",
            state_type=state_type,
            key=key,
            current_value=current_value,
            rationale="Test rationale",
            owner=owner,
            status="active",
            confidence=confidence,
            source_refs=[
                SourceRef(
                    type="message",
                    chat_id="chat_test",
                    message_id=message_id,
                    excerpt=current_value[:50],
                    created_at=datetime.now().isoformat(),
                )
            ],
        )

    def test_layer2_identical_content_dedup(self):
        """Test Layer 2: identical content should be deduplicated."""
        item1 = self._make_item("api-docs", "张三负责 API 文档", owner="张三",
                                message_id="msg_001")
        item2 = self._make_item("api-docs", "张三负责 API 文档", owner="张三",
                                message_id="msg_002")  # 不同 message_id

        result1 = self.store.upsert_items([item1])
        self.assertEqual(len(result1), 1)

        result2 = self.store.upsert_items([item2])
        self.assertEqual(len(result2), 1)

        # source_refs 应合并（不同 message_id）
        self.assertEqual(len(result2[0].source_refs), 2)

    def test_layer2_identical_content_source_refs_dedup(self):
        """P0: Identical content merge should not duplicate source_refs with same message_id."""
        # 同一个 source_ref 被重复 upsert 时不应产生重复
        item1 = self._make_item("api-docs", "张三负责 API 文档", owner="张三",
                                message_id="msg_001")
        item2 = self._make_item("api-docs", "张三负责 API 文档", owner="张三",
                                message_id="msg_001")  # 相同 message_id

        self.store.upsert_items([item1])
        result = self.store.upsert_items([item2])

        # 相同 message_id 的 source_refs 应去重
        message_ids = [ref.message_id for ref in result[0].source_refs]
        self.assertEqual(len(message_ids), len(set(message_ids)),
                         "Duplicate message_ids in source_refs should be deduplicated")

    def test_layer3_similar_content_merge(self):
        """Test Layer 3: similar content (>90% similarity) should be merged."""
        item1 = self._make_item("api-docs", "张三负责 API 文档开发工作", owner="张三")
        item2 = self._make_item("api-docs", "张三负责 API 文档开发工作。", owner="张三")

        result1 = self.store.upsert_items([item1])
        result2 = self.store.upsert_items([item2])

        # Similar content should be merged (not create new version)
        self.assertEqual(len(result2), 1)
        sim = self.store._compute_text_similarity(
            item1.current_value, item2.current_value
        )
        self.assertGreater(sim, 0.8)

    def test_layer3_negation_polarity_not_merged(self):
        """P0: '张三负责 API 文档' and '张三不负责 API 文档' must NOT be merged."""
        item1 = self._make_item("api-docs", "张三负责 API 文档", owner="张三",
                                message_id="msg_001")
        # "不"字导致否定极性变化
        item2 = self._make_item("api-docs", "张三不负责 API 文档", owner="张三",
                                message_id="msg_002")

        result1 = self.store.upsert_items([item1])
        self.assertEqual(len(result1), 1)

        result2 = self.store.upsert_items([item2])

        # 否定极性变化应导致 supersede 而非 merge
        self.assertEqual(len(result2), 1)
        self.assertEqual(result2[0].current_value, "张三不负责 API 文档")
        self.assertEqual(result2[0].version, 2)

        # 旧版本应在 history 中
        history = self.store.list_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].current_value, "张三负责 API 文档")

    def test_owner_change_supersedes(self):
        """P0: '张三负责' -> '李四负责' 不能 semantic merge, 应该 supersede."""
        item1 = self._make_item("current_owner", "张三", owner="张三", state_type="owner",
                                message_id="msg_001")
        item2 = self._make_item("current_owner", "李四", owner="李四", state_type="owner",
                                message_id="msg_002")

        result1 = self.store.upsert_items([item1])
        result2 = self.store.upsert_items([item2])

        self.assertEqual(len(result2), 1)
        self.assertEqual(result2[0].current_value, "李四")
        self.assertEqual(result2[0].version, 2)

        history = self.store.list_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].current_value, "张三")

    def test_different_content_creates_new_version(self):
        """Test that different content creates a new version."""
        item1 = self._make_item("api-docs", "张三负责", owner="张三")
        item2 = self._make_item("api-docs", "李四负责", owner="李四")

        result1 = self.store.upsert_items([item1])
        result2 = self.store.upsert_items([item2])

        self.assertEqual(len(result2), 1)
        self.assertEqual(result2[0].version, 2)
        self.assertEqual(result2[0].current_value, "李四负责")

        history = self.store.list_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].current_value, "张三负责")

    def test_text_similarity_bigrams(self):
        """Test the text similarity computation."""
        sim1 = self.store._compute_text_similarity("完全相同", "完全相同")
        self.assertEqual(sim1, 1.0)

        sim2 = self.store._compute_text_similarity("苹果", "香蕉")
        self.assertLess(sim2, 0.5)

        sim3 = self.store._compute_text_similarity("张三负责 API", "张三负责开发")
        self.assertGreater(sim3, 0.3)


class TestDebounceLogic(unittest.TestCase):
    """Tests for V1.5 debounce logic."""

    def setUp(self):
        """Create engine with short debounce for testing."""
        self.temp_dir = TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp_dir.name))
        self.extractor = RuleBasedExtractor()
        self.engine = MemoryEngine(self.store, self.extractor, debounce_seconds=5)

    def tearDown(self):
        """Clean up temporary directory."""
        self.temp_dir.cleanup()

    def test_first_process_immediate(self):
        """Test that first processing happens immediately."""
        should_process, reason = self.engine._should_process_now(None)
        self.assertTrue(should_process)
        self.assertEqual(reason, "首次处理")

    def test_within_debounce_window_delayed(self):
        """Test that processing within debounce window is delayed."""
        self.engine._set_last_process_time(None, datetime.now())
        should_process, reason = self.engine._should_process_now(None)
        self.assertFalse(should_process)
        self.assertIn("debounce", reason)

    def test_after_debounce_window_allowed(self):
        """Test that processing after debounce window is allowed."""
        last_time = datetime.now() - timedelta(seconds=10)
        self.engine._set_last_process_time(None, last_time)
        should_process, reason = self.engine._should_process_now(None)
        self.assertTrue(should_process)
        self.assertIn("已", reason)

    def test_debounce_prevented_events_not_marked_processed(self):
        """P0: Debounce 窗口内 ingest 的事件不应被标记为已处理."""
        events = [
            {
                "project_id": "test",
                "chat_id": "chat_test",
                "message_id": "msg_001",
                "text": "下一步：完成 API 文档",
                "created_at": datetime.now().isoformat(),
            }
        ]

        # 首次调用：应处理
        result1 = self.engine.ingest_events(events, debounce=True)
        self.assertEqual(len(result1), 1)

        # 立即调用：应被 debounce 跳过
        events2 = [
            {
                "project_id": "test",
                "chat_id": "chat_test",
                "message_id": "msg_002",
                "text": "下一步：编写测试",
                "created_at": datetime.now().isoformat(),
            }
        ]
        result2 = self.engine.ingest_events(events2, debounce=True)

        # Debounce 跳过后，结果应与第一次相同（msg_002 未被处理）
        self.assertEqual(len(result1), len(result2))

        # msg_002 未被标记为 processed
        processed_ids = self.store.processed_event_ids()
        self.assertNotIn("msg_002", processed_ids,
                         "Debounce 跳过的事件不应被标记为 processed")

    def test_debounce_window_expired_events_are_processed(self):
        """P0: Debounce 窗口过后，积压的事件应被正常处理."""
        # 首次调用来设置 last_process_time
        event1 = {
            "project_id": "test",
            "chat_id": "chat_test",
            "message_id": "msg_001",
            "text": "下一步：完成 API 文档",
            "created_at": datetime.now().isoformat(),
        }
        self.engine.ingest_events([event1], debounce=False)

        # 手动把 last_process_time 设为 debounce 窗口之前
        old_time = datetime.now() - timedelta(seconds=10)
        self.engine._set_last_process_time(None, old_time)

        # 现在 ingest 一条新事件（debounce=False 强制处理）
        event2 = {
            "project_id": "test",
            "chat_id": "chat_test",
            "message_id": "msg_002",
            "text": "下一步：编写测试",
            "created_at": datetime.now().isoformat(),
        }
        result = self.engine.ingest_events([event2], debounce=False)

        # msg_002 应被处理
        processed_ids = self.store.processed_event_ids()
        self.assertIn("msg_002", processed_ids,
                      "非 debounce 调用应处理所有未处理事件")

    def test_debounce_disabled_processes_all(self):
        """P0: debounce=False 时应正常处理所有事件."""
        event1 = {
            "project_id": "test",
            "chat_id": "chat_test",
            "message_id": "msg_001",
            "text": "下一步：完成 API 文档",
            "created_at": datetime.now().isoformat(),
        }
        event2 = {
            "project_id": "test",
            "chat_id": "chat_test",
            "message_id": "msg_002",
            "text": "下一步：编写测试",
            "created_at": datetime.now().isoformat(),
        }

        self.engine.ingest_events([event1], debounce=False)
        # 即使上一次处理是 0 秒前，debounce=False 也应处理
        self.engine.ingest_events([event2], debounce=False)

        processed_ids = self.store.processed_event_ids()
        self.assertIn("msg_002", processed_ids)


class TestPromptGrounding(unittest.TestCase):
    """Tests for V1.5 prompt grounding improvements."""

    def test_extract_mentions_from_at_list(self):
        """Test extracting mentions from at_list field."""
        extractor = LLMExtractor(FakeLLMProvider())
        events = [
            {
                "text": "@张三 你负责 API 文档",
                "at_list": [
                    {"user_id": "u123", "user_name": "张三"},
                    {"user_id": "u456", "user_name": "李四"},
                ],
            }
        ]
        mentions = extractor._extract_mentions(events)
        self.assertIn("u123", mentions)
        self.assertEqual(mentions["u123"], "张三")
        self.assertIn("u456", mentions)
        self.assertEqual(mentions["u456"], "李四")

    def test_extract_mentions_from_text_pattern(self):
        """Test extracting mentions from @name pattern in text."""
        extractor = LLMExtractor(FakeLLMProvider())
        events = [
            {"text": "@张三 @李四 你们好", "at_list": []},
            {"text": "请@王五处理", "at_list": []},
        ]
        mentions = extractor._extract_mentions(events)
        self.assertTrue(len(mentions) > 0)

    def test_build_prompt_contains_grounding_rules(self):
        """Test that built prompt contains grounding rules."""
        extractor = LLMExtractor(FakeLLMProvider())
        events = [{"text": "测试消息", "at_list": []}]
        # _build_prompt 现在需要 author_map 和 time_ref 参数
        prompt = extractor._build_prompt(events)

        self.assertIn("代词解析规则", prompt)
        self.assertIn("时间解析规则", prompt)
        self.assertIn("隐式语义识别规则", prompt)
        self.assertIn("上下文信息", prompt)
        # P0: 新增内容检查
        self.assertIn("消息发送者映射", prompt,
                      "Prompt 应包含 author 映射信息")
        self.assertIn("消息时间范围", prompt,
                      "Prompt 应包含时间参考信息")
        self.assertIn("ambiguous", prompt,
                      "Prompt 应包含指代不明的处理规则")

    def test_build_author_map_user(self):
        """P0: _build_author_map 应从 user sender 提取 name."""
        extractor = LLMExtractor(FakeLLMProvider())
        events = [
            {
                "text": "我明天完成 API 文档",
                "sender": {
                    "id": "ou_user123",
                    "id_type": "open_id",
                    "name": "张三",
                    "sender_type": "user",
                },
                "created_at": "2026-04-25T10:00:00+08:00",
            }
        ]
        author_map = extractor._build_author_map(events)
        self.assertIn("ou_user123", author_map)
        self.assertEqual(author_map["ou_user123"], "张三")

    def test_build_author_map_app(self):
        """P0: _build_author_map 应从 app sender 提取 bot 标记."""
        extractor = LLMExtractor(FakeLLMProvider())
        events = [
            {
                "text": "系统通知",
                "sender": {
                    "id": "cli_axxx",
                    "id_type": "app_id",
                    "sender_type": "app",
                },
                "created_at": "2026-04-25T10:00:00+08:00",
            }
        ]
        author_map = extractor._build_author_map(events)
        self.assertIn("cli_axxx", author_map)
        self.assertIn("bot", author_map["cli_axxx"])

    def test_build_time_reference(self):
        """P0: _build_time_reference 应返回最早和最晚消息时间."""
        extractor = LLMExtractor(FakeLLMProvider())
        events = [
            {
                "text": "消息1",
                "created_at": "2026-04-25T10:00:00+08:00",
            },
            {
                "text": "消息2",
                "created_at": "2026-04-25T11:00:00+08:00",
            },
        ]
        time_ref = extractor._build_time_reference(events)
        self.assertEqual(time_ref["min_time"], "2026-04-25T10:00:00+08:00")
        self.assertEqual(time_ref["max_time"], "2026-04-25T11:00:00+08:00")

    def test_build_time_reference_single_event(self):
        """P0: 单条消息时 min/max 应相同."""
        extractor = LLMExtractor(FakeLLMProvider())
        events = [
            {
                "text": "消息1",
                "created_at": "2026-04-25T10:00:00+08:00",
            },
        ]
        time_ref = extractor._build_time_reference(events)
        self.assertEqual(time_ref["min_time"], time_ref["max_time"])

    def test_build_time_reference_empty(self):
        """P0: 没有 created_at 时应返回"未知"."""
        extractor = LLMExtractor(FakeLLMProvider())
        events = [{"text": "消息1"}]  # no created_at
        time_ref = extractor._build_time_reference(events)
        self.assertEqual(time_ref["min_time"], "未知")
        self.assertEqual(time_ref["max_time"], "未知")

    def test_prompt_contains_author_at_message_level(self):
        """P0: Prompt 中消息列表应带有 sender name 上下文以便 LLM 解析 '我'."""
        extractor = LLMExtractor(FakeLLMProvider())
        events = [
            {
                "text": "我明天完成 API 文档",
                "sender": {
                    "id": "ou_user123",
                    "id_type": "open_id",
                    "name": "张三",
                    "sender_type": "user",
                },
                "created_at": "2026-04-25T10:00:00+08:00",
            }
        ]
        author_map = extractor._build_author_map(events)
        time_ref = extractor._build_time_reference(events)
        prompt = extractor._build_prompt(events, author_map, time_ref)

        self.assertIn("张三", prompt,
                      "Prompt 应包含发送者姓名供 LLM 解析'我'")
        self.assertIn("2026-04-25T10:00:00+08:00", prompt,
                      "Prompt 应包含消息 created_at 供 LLM 解析相对时间")


class TestAuthorMapEdgeCases(unittest.TestCase):
    """V1.6: Edge cases for _build_author_map."""

    def setUp(self):
        self.extractor = LLMExtractor(FakeLLMProvider())

    def test_empty_sender_id_skipped(self):
        """V1.6: sender.id 为空字符串时应跳过."""
        events = [{"text": "测试", "sender": {"id": "", "sender_type": "system"}}]
        result = self.extractor._build_author_map(events)
        self.assertEqual(len(result), 0, "空 sender.id 不应进入 author_map")

    def test_whitespace_sender_id_skipped(self):
        """V1.6: sender.id 为空白字符串时应跳过."""
        events = [{"text": "测试", "sender": {"id": "  ", "sender_type": "system"}}]
        result = self.extractor._build_author_map(events)
        self.assertEqual(len(result), 0, "空白 sender.id 不应进入 author_map")

    def test_system_sender_skipped(self):
        """V1.6: sender_type=system 时应跳过."""
        events = [{"text": "沈哲熙 removed member", "sender": {"id": "sys_001", "sender_type": "system"}}]
        result = self.extractor._build_author_map(events)
        self.assertEqual(len(result), 0, "system sender 不应进入 author_map")

    def test_webhook_sender_skipped(self):
        """V1.6: sender_type=webhook 时应跳过."""
        events = [{"text": "webhook 通知", "sender": {"id": "wh_001", "sender_type": "webhook"}}]
        result = self.extractor._build_author_map(events)
        self.assertEqual(len(result), 0, "webhook sender 不应进入 author_map")

    def test_anonymous_sender_skipped(self):
        """V1.6: sender_type=anonymous 时应跳过."""
        events = [{"text": "匿名消息", "sender": {"id": "anon_001", "sender_type": "anonymous"}}]
        result = self.extractor._build_author_map(events)
        self.assertEqual(len(result), 0, "anonymous sender 不应进入 author_map")

    def test_user_sender_normal(self):
        """V1.6: user sender 正常提取 name."""
        events = [{"text": "你好", "sender": {"id": "ou_123", "sender_type": "user", "name": "张三"}}]
        result = self.extractor._build_author_map(events)
        self.assertEqual(result.get("ou_123"), "张三")

    def test_app_sender_normal(self):
        """V1.6: app sender 标记为 bot."""
        events = [{"text": "通知", "sender": {"id": "cli_axxx", "sender_type": "app"}}]
        result = self.extractor._build_author_map(events)
        self.assertIn("bot", result.get("cli_axxx", ""))


class TestNegationSafeWords(unittest.TestCase):
    """V1.6: 否定极性豁免词测试."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp_dir.name))

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_item(self, current_value, message_id="msg_001", key="test-key"):
        return MemoryItem(
            project_id="test", state_type="task", key=key,
            current_value=current_value, rationale="test",
            owner=None, status="active", confidence=0.8,
            source_refs=[SourceRef("message", "chat", message_id, current_value[:50], "2026-04-28T10:00:00")],
        )

    def test_negation_true_change_detected(self):
        """V1.6: "张三负责" -> "张三不负责" 仍检测为真否定变化."""
        old = self._make_item("张三负责", "msg_001")
        new = self._make_item("张三不负责", "msg_002")
        self.assertTrue(self.store._has_negation_polarity_change(old.current_value, new.current_value),
                        "真实否定变化应被检测")

    def test_buguan_false_positive_avoided(self):
        """V1.6: "不管怎样"不应触发否定极性变化."""
        old = self._make_item("不管怎样先做", "msg_001")
        new = self._make_item("好的就这么做", "msg_002")
        self.assertFalse(self.store._has_negation_polarity_change(old.current_value, new.current_value),
                         "不管怎样不应判为否定极性")

    def test_bucuo_false_positive_avoided(self):
        """V1.6: "不错的方案"不应触发否定极性变化."""
        old = self._make_item("不错的方案", "msg_001")
        new = self._make_item("很好的方案", "msg_002")
        self.assertFalse(self.store._has_negation_polarity_change(old.current_value, new.current_value),
                         "不错不应判为否定极性")

    def test_both_have_safe_word_no_change(self):
        """V1.6: 新旧都有"不错"且极性一致."""
        old = self._make_item("不错的方案，采用", "msg_001")
        new = self._make_item("不错的方案，就这个", "msg_002")
        self.assertFalse(self.store._has_negation_polarity_change(old.current_value, new.current_value))


class TestAmbiguousPostProcessing(unittest.TestCase):
    """V1.6: 低置信度 ambiguous 候选后处理测试."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp_dir.name))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_ambiguous_low_confidence_dropped(self):
        """V1.6: ambiguous + confidence<=0.3 的候选应被丢弃."""
        from memory.llm_provider import LLMProvider
        class TestProvider(LLMProvider):
            def generate(self, prompt):
                import json
                return json.dumps({
                    "candidates": [
                        {"project_id": "test", "state_type": "decision", "key": "test",
                         "current_value": "someone said [ambiguous: 无法确定谁]",
                         "rationale": "test", "owner": None, "status": "active",
                         "confidence": 0.25,
                         "detected_at": "2026-04-28T10:00:00",
                         "source_refs": [{"type": "message", "chat_id": "chat", "message_id": "msg_001",
                                          "excerpt": "他说要改方案", "created_at": "2026-04-28T10:00:00"}]}
                    ]
                })
        extractor = LLMExtractor(TestProvider())
        events = [{"project_id": "test", "chat_id": "chat", "message_id": "msg_001",
                    "text": "他说要改方案", "created_at": "2026-04-28T10:00:00"}]
        items = extractor.extract(events)
        self.assertEqual(len(items), 0, "ambiguous+低置信度的候选应被丢弃")
        self.assertEqual(len(extractor._dropped_candidates), 1, "丢弃的候选应记录在 _dropped_candidates")

    def test_ambiguous_high_confidence_not_dropped(self):
        """V1.6: ambiguous 但 confidence>0.3 应保留."""
        from memory.llm_provider import LLMProvider
        class TestProvider(LLMProvider):
            def generate(self, prompt):
                import json
                return json.dumps({
                    "candidates": [
                        {"project_id": "test", "state_type": "decision", "key": "test",
                         "current_value": "张三说要改方案 [ambiguous: 不确定他指谁]",
                         "rationale": "test", "owner": None, "status": "active",
                         "confidence": 0.45,
                         "detected_at": "2026-04-28T10:00:00",
                         "source_refs": [{"type": "message", "chat_id": "chat", "message_id": "msg_001",
                                          "excerpt": "他说要改方案", "created_at": "2026-04-28T10:00:00"}]}
                    ]
                })
        extractor = LLMExtractor(TestProvider())
        events = [{"project_id": "test", "chat_id": "chat", "message_id": "msg_001",
                    "text": "他说要改方案", "created_at": "2026-04-28T10:00:00"}]
        items = extractor.extract(events)
        self.assertEqual(len(items), 1, "ambiguous 但高置信度的候选应保留")

    def test_clear_candidate_not_dropped(self):
        """V1.6: 无 ambiguous 标记的正常候选应通过."""
        from memory.llm_provider import LLMProvider
        class TestProvider(LLMProvider):
            def generate(self, prompt):
                import json
                return json.dumps({
                    "candidates": [
                        {"project_id": "test", "state_type": "decision", "key": "test",
                         "current_value": "采用方案A",
                         "rationale": "test", "owner": None, "status": "active",
                         "confidence": 0.85,
                         "detected_at": "2026-04-28T10:00:00",
                         "source_refs": [{"type": "message", "chat_id": "chat", "message_id": "msg_001",
                                          "excerpt": "采用方案A", "created_at": "2026-04-28T10:00:00"}]}
                    ]
                })
        extractor = LLMExtractor(TestProvider())
        events = [{"project_id": "test", "chat_id": "chat", "message_id": "msg_001",
                    "text": "采用方案A", "created_at": "2026-04-28T10:00:00"}]
        items = extractor.extract(events)
        self.assertEqual(len(items), 1, "正常候选应通过")


class TestTimeReferenceSorting(unittest.TestCase):
    """V1.6: _build_time_reference 跨时区排序测试."""

    def setUp(self):
        self.extractor = LLMExtractor(FakeLLMProvider())

    def test_cross_timezone_sorting(self):
        """V1.6: 跨时区时间正确排序."""
        events = [
            {"text": "晚八点", "created_at": "2026-04-28T20:00:00+08:00"},
            {"text": "早八点", "created_at": "2026-04-28T08:00:00+08:00"},
        ]
        time_ref = self.extractor._build_time_reference(events)
        self.assertEqual(time_ref["min_time"], "2026-04-28T08:00:00+08:00")
        self.assertEqual(time_ref["max_time"], "2026-04-28T20:00:00+08:00")

    def test_z_suffix(self):
        """V1.6: Z 后缀时间应正确排序."""
        events = [
            {"text": "消息1", "created_at": "2026-04-28T12:00:00Z"},
            {"text": "消息2", "created_at": "2026-04-28T10:00:00Z"},
        ]
        time_ref = self.extractor._build_time_reference(events)
        self.assertEqual(time_ref["min_time"], "2026-04-28T10:00:00Z")


class TestLayer3FieldProtection(unittest.TestCase):
    """V1.6: Layer 3 关键字段保护测试."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp_dir.name))

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_item(self, current_value, owner=None, status="active", message_id="msg_001", key="test"):
        return MemoryItem(
            project_id="test", state_type="task", key=key,
            current_value=current_value, rationale="test",
            owner=owner, status=status, confidence=0.8,
            source_refs=[SourceRef("message", "chat", message_id, current_value[:50], "2026-04-28T10:00:00")],
        )

    def test_owner_different_prevents_merge(self):
        """V1.6: owner 不同时不应 semantic merge.
        注意：内容必须不同以走 Layer 3 Layer 2 用哈希去重会提前拦住)。"""
        old = self._make_item("张三负责 API 文档开发", owner="张三", message_id="msg_001")
        new = self._make_item("李四负责 API 文档开发", owner="李四", message_id="msg_002")
        self.store.upsert_items([old])
        result = self.store.upsert_items([new])
        self.assertEqual(result[0].owner, "李四", "owner 不同应 supersede")
        self.assertEqual(result[0].version, 2)

    def test_ddl_date_change_not_merged(self):
        """V1.6: DDL 改日期但文本相似的不要 merge."""
        old = self._make_item("DDL 从周五改到下周三完成交付", message_id="msg_001", key="deadline")
        new = self._make_item("DDL 从周三改到周五完成交付", message_id="msg_002", key="deadline")
        self.store.upsert_items([old])
        result = self.store.upsert_items([new])
        self.assertEqual(result[0].version, 2, "DDL 日期变更应 supersede 而非 merge")


class TestIntegration(unittest.TestCase):
    """V1.6: 端到端集成测试."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp_dir.name))
        self.engine = MemoryEngine(self.store, RuleBasedExtractor())

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_full_scenario(self):
        """V1.6: 4 条飞书消息端到端流程."""
        events = [
            {"project_id": "demo", "chat_id": "chat_01", "message_id": "msg_001",
             "text": "目标：完成 V1.6 优化", "created_at": "2026-04-28T10:00:00"},
            {"project_id": "demo", "chat_id": "chat_01", "message_id": "msg_002",
             "text": "负责人：张三负责测试模块", "created_at": "2026-04-28T10:01:00"},
            {"project_id": "demo", "chat_id": "chat_01", "message_id": "msg_003",
             "text": "决策：先修复否定极性问题", "created_at": "2026-04-28T10:02:00"},
            {"project_id": "demo", "chat_id": "chat_01", "message_id": "msg_004",
             "text": "阻塞：测试数据还没准备好", "created_at": "2026-04-28T10:03:00"},
        ]
        result = self.engine.ingest_events(events)
        items = self.store.list_items("demo")
        # 应提取到: goal, owner, decision, blocker
        state_types = {item.state_type for item in items}
        self.assertIn("project_goal", state_types)
        self.assertIn("owner", state_types)
        self.assertIn("decision", state_types)
        self.assertIn("blocker", state_types)
        # processed_event_ids 应包含 4 条
        processed = self.store.processed_event_ids()
        self.assertEqual(len(processed), 4)
        # source_refs 都有 message_id
        for item in items:
            self.assertTrue(len(item.source_refs) > 0)
            self.assertEqual(item.source_refs[0].chat_id, "chat_01")


class TestBiTemporal(unittest.TestCase):
    """V1.6: Bi-temporal valid_from/valid_to/as_of 测试."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp_dir.name))

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_item(self, current_value, owner=None, key="owner", message_id="msg_001",
                   valid_from="", state_type="owner", source_time=""):
        if not source_time:
            source_time = "2026-04-28T10:00:00"
        return MemoryItem(
            project_id="demo", state_type=state_type, key=key,
            current_value=current_value, rationale="test",
            owner=owner, status="active", confidence=0.8,
            source_refs=[SourceRef("message", "chat", message_id, current_value[:50],
                                    source_time)],
            valid_from=valid_from,
        )

    def test_owner_change_as_of(self):
        """V1.6: 负责人 A→B，as_of 查询正确返回各自时段有效项."""
        from datetime import timezone
        # 所有时间使用 UTC 避免跨时区比较问题
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        t1 = now_utc.isoformat()
        t2 = (now_utc).isoformat()  # 同一时刻，李四会 supersede 张三

        item_a = self._make_item("张三", owner="张三", message_id="msg_a",
                                 valid_from=t1, source_time=t1)
        item_b = self._make_item("李四", owner="李四", message_id="msg_b", key="owner",
                                 source_time=t2)

        self.store.upsert_items([item_a])
        self.store.upsert_items([item_b])

        # as_of=现在: 李四有效（张三被 supersede 了）
        as_of_now = self.store.list_items("demo", as_of=now_utc.isoformat())
        owners_now = [i for i in as_of_now if i.state_type == "owner"]
        self.assertEqual(len(owners_now), 1, "as_of=now 应有一条 owner")
        self.assertIn("李四", owners_now[0].current_value)

    def test_decision_override_as_of(self):
        """V1.6: 决策被同 key 的值 supersede 后，as_of 查询正确."""
        from datetime import timezone
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        t1 = now_utc.isoformat()

        item_old = self._make_item("使用 React", key="tech_choice", message_id="msg_a",
                                   valid_from=t1, state_type="decision")
        item_new = self._make_item("改为 Vue", key="tech_choice", message_id="msg_b",
                                   source_time=now_utc.isoformat(), state_type="decision")

        self.store.upsert_items([item_old])
        self.store.upsert_items([item_new])

        # as_of after now: Vue 有效
        as_of_now = self.store.list_items("demo", as_of=now_utc.isoformat())
        decisions_now = [i for i in as_of_now if i.state_type == "decision"]
        self.assertEqual(len(decisions_now), 1)
        self.assertIn("Vue", decisions_now[0].current_value)

    def test_old_data_loading(self):
        """V1.6: 旧 memory_state.json 缺少 valid_from/valid_to 时仍能加载."""
        import json
        from datetime import timezone
        old_payload = {
            "items": [{
                "project_id": "demo", "state_type": "owner", "key": "owner",
                "current_value": "张三", "rationale": "test",
                "owner": "张三", "status": "active", "confidence": 0.8,
                "source_refs": [{"type": "message", "chat_id": "chat",
                                  "message_id": "msg_001", "excerpt": "张三负责",
                                  "created_at": "2026-04-28T10:00:00"}],
                "version": 1, "supersedes": [], "updated_at": "2026-04-28T10:00:00",
                "memory_id": "mem_old_001",
            }],
            "history": [],
            "processed_event_ids": ["msg_001"],
            "updated_at": "2026-04-28T10:00:00",
        }
        self.store.memory_state_path.write_text(json.dumps(old_payload, ensure_ascii=False), encoding="utf-8")
        items = self.store.list_items("demo")
        self.assertEqual(len(items), 1, "旧格式数据应能被加载")
        self.assertEqual(items[0].current_value, "张三")
        # as_of 查询也应能工作（旧数据 valid_from="" 视为始终有效）
        as_of_items = self.store.list_items("demo", as_of=datetime.now(timezone.utc).isoformat())
        self.assertEqual(len(as_of_items), 1, "旧数据 valid_from='' 应通过 as_of 查询")


class TestMemberStatus(unittest.TestCase):
    """V1.6: 成员状态提取测试."""

    def setUp(self):
        self.extractor = RuleBasedExtractor()

    def test_member_status_leave(self):
        """V1.6: 请假消息应提取为 member_status."""
        from memory.schema import source_ref_from_event
        event = {"project_id": "test", "chat_id": "chat", "message_id": "msg_001",
                 "text": "我这周请假，有事找李四", "created_at": "2026-04-28T10:00:00"}
        items = self.extractor._extract_member_status(event, event["text"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].state_type, "member_status")
        self.assertIn("请假", items[0].current_value)

    def test_member_status_preference(self):
        """V1.6: 工作偏好应提取为 member_status."""
        event = {"project_id": "test", "chat_id": "chat", "message_id": "msg_002",
                 "text": "我习惯用 Figma 做设计", "created_at": "2026-04-28T10:00:00"}
        items = self.extractor._extract_member_status(event, event["text"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].state_type, "member_status")
        self.assertIn("Figma", items[0].current_value)

    def test_member_status_absence(self):
        """V1.6: 出差消息应提取为 member_status."""
        event = {"project_id": "test", "chat_id": "chat", "message_id": "msg_003",
                 "text": "我明天不在，出差去上海", "created_at": "2026-04-28T10:00:00"}
        items = self.extractor._extract_member_status(event, event["text"])
        self.assertEqual(len(items), 1)
        self.assertIn("出差", items[0].current_value)

    def test_member_status_no_false_positive(self):
        """V1.6: 不含关键词的消息不应触发."""
        event = {"project_id": "test", "chat_id": "chat", "message_id": "msg_004",
                 "text": "这个接口什么时候上线", "created_at": "2026-04-28T10:00:00"}
        items = self.extractor._extract_member_status(event, event["text"])
        self.assertEqual(len(items), 0)

    def test_member_status_in_engine(self):
        """V1.6: 端到端：member_status 通过 engine 被提取."""
        from memory.engine import MemoryEngine
        from tempfile import TemporaryDirectory
        tmp = TemporaryDirectory()
        store = MemoryStore(Path(tmp.name))
        engine = MemoryEngine(store, RuleBasedExtractor())
        events = [{"project_id": "test", "chat_id": "chat", "message_id": "msg_005",
                    "text": "我这周请假", "created_at": "2026-04-28T10:00:00"}]
        engine.ingest_events(events)
        items = store.list_items("test")
        member_status = [i for i in items if i.state_type == "member_status"]
        self.assertEqual(len(member_status), 1)


class TestDeadlineExtraction(unittest.TestCase):
    """V1.8: 截止时间提取测试."""

    def setUp(self):
        self.extractor = RuleBasedExtractor()

    def test_deadline_ddl(self):
        """V1.8: DDL 关键词应提取为 deadline."""
        event = {"project_id": "test", "chat_id": "chat", "message_id": "msg_001",
                 "text": "DDL 到下周五", "created_at": "2026-04-28T10:00:00"}
        items = self.extractor._extract_deadline(event, event["text"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].state_type, "deadline")

    def test_deadline_change(self):
        """V1.8: 截止时间变更."""
        event = {"project_id": "test", "chat_id": "chat", "message_id": "msg_002",
                 "text": "截止时间改到下周三", "created_at": "2026-04-28T10:00:00"}
        items = self.extractor._extract_deadline(event, event["text"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].state_type, "deadline")

    def test_deadline_postpone(self):
        """V1.8: 延期."""
        event = {"project_id": "test", "chat_id": "chat", "message_id": "msg_003",
                 "text": "延期到明天", "created_at": "2026-04-28T10:00:00"}
        items = self.extractor._extract_deadline(event, event["text"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].state_type, "deadline")

    def test_deadline_no_false_positive(self):
        """V1.8: 普通消息不应误触发."""
        event = {"project_id": "test", "chat_id": "chat", "message_id": "msg_004",
                 "text": "好的收到", "created_at": "2026-04-28T10:00:00"}
        items = self.extractor._extract_deadline(event, event["text"])
        self.assertEqual(len(items), 0)


# ── V1.12 FIX-1: 跨时区时间比较测试 ───────────────────────────

class TestCrossTimezoneComparison(unittest.TestCase):
    """V1.12: 验证 _compare_iso_time 跨时区正确性."""

    def setUp(self):
        from memory.store import MemoryStore
        self.store = MemoryStore("data")

    def test_same_instant_different_timezone_equal(self):
        """同一时刻不同时区表示应判为相等."""
        self.assertEqual(
            self.store._compare_iso_time(
                "2026-04-28T01:00:00+00:00",
                "2026-04-28T09:00:00+08:00",
            ), 0)

    def test_earlier_instant_less(self):
        """较早的时刻應小于较晚的时刻."""
        self.assertEqual(
            self.store._compare_iso_time(
                "2026-04-27T23:00:00+00:00",
                "2026-04-28T01:00:00+00:00",
            ), -1)

    def test_z_suffix_vs_offset_equal(self):
        """Z 后缀和 +00:00 应等价."""
        self.assertEqual(
            self.store._compare_iso_time(
                "2026-04-28T01:00:00Z",
                "2026-04-28T01:00:00+00:00",
            ), 0)

    def test_different_instant_greater(self):
        """较晚的时刻應大于较早的时刻."""
        self.assertEqual(
            self.store._compare_iso_time(
                "2026-04-28T10:00:00+08:00",
                "2026-04-28T01:00:00+00:00",
            ), 1)

    def test_as_of_filters_correctly_cross_tz(self):
        """as_of 在跨时区时应正确过滤."""
        from memory.schema import MemoryItem, SourceRef
        with __import__('tempfile').TemporaryDirectory() as td:
            from memory.store import MemoryStore as MS
            s = MS(__import__('pathlib').Path(td))
            # Item valid from UTC 01:00 (which is Beijing 09:00)
            item = MemoryItem(
                project_id="t", state_type="decision", key="k",
                current_value="v", rationale="r", owner=None,
                status="active", confidence=0.8,
                source_refs=[SourceRef("msg", "c", "m", "e", "2026-04-28T01:00:00Z")],
                valid_from="2026-04-28T01:00:00Z",
            )
            s.save_state([item], [], [])
            # Query at Beijing 08:00 (= UTC 00:00) — before valid_from
            before = s.list_items("t", as_of="2026-04-28T00:00:00Z")
            self.assertEqual(len(before), 0, "as_of before valid_from should return 0")
            # Query at Beijing 10:00 (= UTC 02:00) — after valid_from
            after = s.list_items("t", as_of="2026-04-28T02:00:00Z")
            self.assertEqual(len(after), 1, "as_of after valid_from should return 1")


# ── V1.12 FIX-7b: Layer 4 跨 key 覆盖边界测试 ─────────────────

class TestLayer4CrossKey(unittest.TestCase):
    def test_same_topic_decision_supersedes(self):
        from memory.store import MemoryStore
        from memory.schema import MemoryItem, SourceRef
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as td:
            s = MemoryStore(Path(td))
            d1 = MemoryItem(project_id="t", state_type="decision", key="d1",
                current_value="采用 React 作为前端框架", rationale="", owner=None,
                status="active", confidence=0.8,
                source_refs=[SourceRef("msg","c","m1","","")])
            d2 = MemoryItem(project_id="t", state_type="decision", key="d2",
                current_value="改为使用 Vue 替代 React", rationale="", owner=None,
                status="active", confidence=0.8,
                source_refs=[SourceRef("msg","c","m2","","")])
            s.upsert_items([d1]); s.upsert_items([d2])
            self.assertEqual(len(s.list_items("t")), 1, "shared topic -> old superseded")
            self.assertEqual(len(s.list_history("t")), 1)

    def test_different_topic_no_supersede(self):
        from memory.store import MemoryStore
        from memory.schema import MemoryItem, SourceRef
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as td:
            s = MemoryStore(Path(td))
            d1 = MemoryItem(project_id="t", state_type="decision", key="d1",
                current_value="确定使用 Docker 部署", rationale="", owner=None,
                status="active", confidence=0.8,
                source_refs=[SourceRef("msg","c","m1","","")])
            d2 = MemoryItem(project_id="t", state_type="decision", key="d2",
                current_value="采用 React 作为前端框架", rationale="", owner=None,
                status="active", confidence=0.8,
                source_refs=[SourceRef("msg","c","m2","","")])
            s.upsert_items([d1]); s.upsert_items([d2])
            self.assertEqual(len(s.list_items("t")), 2, "different topics -> both active")

    def test_deadline_same_date_supersedes(self):
        from memory.store import MemoryStore
        from memory.schema import MemoryItem, SourceRef
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as td:
            s = MemoryStore(Path(td))
            d1 = MemoryItem(project_id="t", state_type="deadline", key="dl1",
                current_value="DDL 下周五 完成前端", rationale="", owner=None,
                status="active", confidence=0.7,
                source_refs=[SourceRef("msg","c","m1","","")])
            d2 = MemoryItem(project_id="t", state_type="deadline", key="dl2",
                current_value="DDL 改到下周三 延期交付", rationale="", owner=None,
                status="active", confidence=0.7,
                source_refs=[SourceRef("msg","c","m2","","")])
            s.upsert_items([d1]); s.upsert_items([d2])
            self.assertEqual(len(s.list_items("t")), 1)

    def test_deadline_different_date_no_supersede(self):
        from memory.store import MemoryStore
        from memory.schema import MemoryItem, SourceRef
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as td:
            s = MemoryStore(Path(td))
            d1 = MemoryItem(project_id="t", state_type="deadline", key="dl1",
                current_value="DDL 下周五 前端完成", rationale="", owner=None,
                status="active", confidence=0.7,
                source_refs=[SourceRef("msg","c","m1","","")])
            d2 = MemoryItem(project_id="t", state_type="deadline", key="dl2",
                current_value="截止日期 下周一 后端交付", rationale="", owner=None,
                status="active", confidence=0.7,
                source_refs=[SourceRef("msg","c","m2","","")])
            s.upsert_items([d1]); s.upsert_items([d2])
            self.assertEqual(len(s.list_items("t")), 2, "different dates -> both active")


if __name__ == "__main__":
    unittest.main()
if __name__ == "__main__":
    unittest.main()
