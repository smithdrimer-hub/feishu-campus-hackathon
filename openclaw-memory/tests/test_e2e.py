"""End-to-end integration test for the full Memory Engine pipeline.

Tests the complete flow without real Feishu or LLM dependencies:
  ingest → extract → upsert → handoff → action_plan → project_state

Uses RuleBasedExtractor (default) and optionally FakeLLMProvider.
"""

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.action_planner import generate_action_plan, render_action_plan
from memory.engine import MemoryEngine
from memory.extractor import LLMExtractor, RuleBasedExtractor
from memory.handoff import generate_handoff
from memory.llm_provider import FakeLLMProvider
from memory.project_state import (
    build_agent_context_pack,
    build_group_project_state,
    build_personal_work_context,
    render_group_state_panel_text,
    render_personal_context_text,
)
from memory.schema import MemoryItem, SourceRef
from memory.store import MemoryStore


class TestE2EFullPipeline(unittest.TestCase):
    """End-to-end test: ingest → extract → upsert → handoff → plan → state panel."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp_dir.name))
        self.engine = MemoryEngine(self.store, RuleBasedExtractor())

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_event(self, text, message_id, created_at="2026-04-28T10:00:00",
                    project_id="e2e-test"):
        return {
            "project_id": project_id,
            "chat_id": "chat_e2e",
            "message_id": message_id,
            "text": text,
            "created_at": created_at,
        }

    def _ingest_scenario(self, events):
        """Ingest events and return all active items."""
        items = self.engine.ingest_events(events)
        return self.store.list_items("e2e-test")

    # ========== Core Pipeline Tests ==========

    def test_pipeline_full_scenario(self):
        """Complete E2E: 7-message mixed scenario through all pipeline stages.

        Pipeline: ingest → extract → upsert → handoff → action_plan
        """
        events = [
            self._make_event("目标：完成用户中心模块开发", "msg_001",
                             "2026-04-28T09:00:00"),
            self._make_event("负责人：张三负责后端开发", "msg_002",
                             "2026-04-28T09:01:00"),
            self._make_event("决策：确定采用前后端分离方案", "msg_003",
                             "2026-04-28T09:02:00"),
            self._make_event("阻塞：等待设计稿", "msg_004",
                             "2026-04-28T09:03:00"),
            self._make_event("暂缓：移动端适配先不做", "msg_005",
                             "2026-04-28T09:04:00"),
            self._make_event("下一步：张三需要完成 API 设计", "msg_006",
                             "2026-04-28T09:05:00"),
            self._make_event("我这周请假", "msg_007",
                             "2026-04-28T09:06:00"),
        ]
        items = self._ingest_scenario(events)

        # === Assert extract ===
        self.assertGreaterEqual(len(items), 6, "Should extract >= 6 items from 7 messages")

        state_types = {item.state_type for item in items}
        for expected_type in ("project_goal", "owner", "decision", "blocker",
                              "deferred", "member_status"):
            self.assertIn(expected_type, state_types,
                          f"Should extract state_type={expected_type}")

        # === Assert upsert dedup ===
        # Re-ingest same events — should not duplicate
        self.engine.ingest_events(events)
        items_after_dup = self.store.list_items("e2e-test")
        self.assertEqual(len(items_after_dup), len(items),
                         "Re-ingesting same events should not duplicate items")

        # Source refs should not have duplicate message_ids
        for item in items_after_dup:
            message_ids = [ref.message_id for ref in item.source_refs]
            self.assertEqual(len(message_ids), len(set(message_ids)),
                             f"Item {item.key} should have unique source_ref message_ids: {message_ids}")

        # === Assert handoff ===
        handoff = generate_handoff("e2e-test", items_after_dup)
        self.assertIsInstance(handoff, str)
        self.assertGreater(len(handoff), 50, "Handoff should be substantial text")
        # Handoff should contain section titles
        for section in ("当前项目目标", "当前负责人", "当前关键决策",
                        "重要暂缓事项", "当前阻塞与风险", "建议下一步"):
            self.assertIn(section, handoff,
                          f"Handoff should contain section: {section}")

        # === Assert action plan ===
        actions = generate_action_plan("e2e-test", items_after_dup)
        self.assertGreater(len(actions), 0, "Should generate at least one action")
        plan_text = render_action_plan("e2e-test", actions)
        self.assertIn("下一步行动计划", plan_text)

    # ========== State Panel Tests ==========

    def test_pipeline_group_state_panel(self):
        """E2E: build_group_project_state after ingestion produces correct structure."""
        events = [
            self._make_event("目标：完成项目 Alpha", "gs_msg_001"),
            self._make_event("负责人：张三", "gs_msg_002"),
            self._make_event("负责人：李四", "gs_msg_003"),
            self._make_event("决策：确定使用 Python", "gs_msg_004"),
            self._make_event("阻塞：等待云资源审批", "gs_msg_005"),
            self._make_event("下一步：完成架构设计", "gs_msg_006"),
        ]
        items = self._ingest_scenario(events)

        # build_group_project_state
        state = build_group_project_state("e2e-test", items)
        self.assertIn("project_title", state)
        self.assertIn("owners", state)
        self.assertIn("recent_decisions", state)
        self.assertIn("risks", state)
        self.assertIn("active_tasks", state)

        self.assertGreater(len(state["owners"]), 0, "Should have owners")
        self.assertGreater(len(state["recent_decisions"]), 0, "Should have decisions")
        self.assertGreater(len(state["risks"]), 0, "Should have risks")

        # render_group_state_panel_text
        panel_text = render_group_state_panel_text(state)
        self.assertIn("项目状态", panel_text)
        self.assertIn("负责人", panel_text)
        self.assertIn("进行中任务", panel_text)

    def test_pipeline_agent_context_pack(self):
        """E2E: build_agent_context_pack after ingestion produces structured JSON."""
        events = [
            self._make_event("目标：完成项目 Beta", "ac_msg_001"),
            self._make_event("决策：使用 PostgreSQL", "ac_msg_002"),
            self._make_event("阻塞：缺少 DBA 支持", "ac_msg_003"),
            self._make_event("下一步：完成数据建模", "ac_msg_004"),
        ]
        items = self._ingest_scenario(events)

        ctx = build_agent_context_pack("e2e-test", items)
        self.assertIn("project", ctx)
        self.assertIn("decisions", ctx)
        self.assertIn("tasks", ctx)
        self.assertIn("risks", ctx)
        self.assertIn("recent_discussion_snippets", ctx)

        self.assertGreater(len(ctx["decisions"]), 0)
        self.assertGreater(len(ctx["risks"]), 0)

        # with user_id filter
        ctx_user = build_agent_context_pack("e2e-test", items, user_id="None")
        self.assertIn("user_perspective", ctx_user)

    def test_pipeline_personal_context(self):
        """E2E: build_personal_work_context produces user-scoped output."""
        events = [
            self._make_event("负责人：张三", "pc_msg_001"),
            self._make_event("下一步：张三需要完成测试", "pc_msg_002"),
            self._make_event("决策：采用方案 B", "pc_msg_003"),
        ]
        items = self._ingest_scenario(events)

        ctx = build_personal_work_context("张三", "e2e-test", items)
        self.assertEqual(ctx["user_id"], "张三")

        # Personal context with empty-scope user should not crash
        ctx_empty = build_personal_work_context("王五", "e2e-test", items)
        self.assertEqual(len(ctx_empty["my_open_tasks"]), 0)

        # Text rendering
        text = render_personal_context_text(ctx)
        self.assertIn("e2e-test", text)

    # ========== LLM + Rule Fallback Pipeline ==========

    def test_pipeline_llm_fallback(self):
        """E2E: LLMExtractor with fake provider + fallback to rules."""
        with TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp))
            extractor = LLMExtractor(
                FakeLLMProvider(),
                fallback=RuleBasedExtractor()
            )
            engine = MemoryEngine(store, extractor=extractor)

            events = [
                self._make_event("关键决策：采用 LLM 结构化提取方式", "llm_msg_001"),
                self._make_event("负责人：C 负责可信提取模块", "llm_msg_002"),
                self._make_event("阻塞：docs +create --dry-run 会实际创建文档", "llm_msg_003"),
            ]
            engine.ingest_events(events)
            items = store.list_items("e2e-test")

            # FakeLLMProvider returns fixed payload (scenario_01_payload)
            # But events have project_id="e2e-test", not "openclaw-memory-demo"
            # So rule fallback should also fire
            self.assertGreater(len(items), 0, "LLM + rule fallback should produce items")

    # ========== Version and History Pipeline ==========

    def test_pipeline_versioning(self):
        """E2E: owner changes should coexist (V1.15: domain-based keys)."""
        events_v1 = [
            self._make_event("负责人：张三", "ver_msg_001", "2026-04-28T09:00:00"),
        ]
        self.engine.ingest_events(events_v1, debounce=False)

        events_v2 = [
            self._make_event("负责人：李四", "ver_msg_002", "2026-04-28T10:00:00"),
        ]
        self.engine.ingest_events(events_v2, debounce=False)

        items = self.store.list_items("e2e-test")
        owners = [i for i in items if i.state_type == "owner"]
        # V1.15: multiple owners coexist with domain-based keys
        self.assertGreaterEqual(len(owners), 1,
                                f"Should have at least 1 active owner, got {len(owners)}")
        owner_names = {o.current_value for o in owners}
        # 李四 should be present (may coexist with 张三)
        self.assertTrue(any("李四" in o.current_value for o in owners),
                        f"Active owners should include 李四, got: {owner_names}")

    # ========== Empty / Edge Cases ==========

    def test_pipeline_empty_state(self):
        """E2E: empty state should not crash any pipeline stage."""
        items = self.store.list_items("e2e-test")

        # Handoff with empty state
        handoff = generate_handoff("e2e-test", items)
        self.assertIn("暂无明确状态", handoff)

        # Action plan with empty state
        actions = generate_action_plan("e2e-test", items)
        self.assertGreater(len(actions), 0)

        # State panel with empty state
        state = build_group_project_state("e2e-test", items)
        panel = render_group_state_panel_text(state)
        self.assertIn("暂无提取", panel)

        # Agent context pack with empty state
        ctx = build_agent_context_pack("e2e-test", items)
        self.assertEqual(len(ctx["decisions"]), 0)
        self.assertEqual(len(ctx["tasks"]), 0)

        # Personal context with empty state
        pc = build_personal_work_context("张三", "e2e-test", items)
        self.assertEqual(len(pc["my_open_tasks"]), 0)

    def test_pipeline_concurrent_multiple_projects(self):
        """E2E: multiple project_ids should not interfere with each other."""
        events_a = [
            self._make_event("目标：项目 A", "multi_a_001", project_id="project-a"),
            self._make_event("负责人：张三", "multi_a_002", project_id="project-a"),
        ]
        events_b = [
            self._make_event("目标：项目 B", "multi_b_001", project_id="project-b"),
            self._make_event("负责人：李四", "multi_b_002", project_id="project-b"),
        ]

        # Use debounce=False to avoid debounce window blocking second ingest
        self.engine.ingest_events(events_a, debounce=False)
        self.engine.ingest_events(events_b, debounce=False)

        items_a = self.store.list_items("project-a")
        items_b = self.store.list_items("project-b")

        types_a = {i.state_type for i in items_a}
        types_b = {i.state_type for i in items_b}
        self.assertIn("project_goal", types_a)
        self.assertIn("project_goal", types_b,
                      f"project-b items should have project_goal, got types={types_b}")

        # items count
        self.assertGreaterEqual(len(items_a), 1, "project-a should have items")
        self.assertGreaterEqual(len(items_b), 1, "project-b should have items")

        # Handoff for project-a should not mention project-b
        handoff_a = generate_handoff("project-a", items_a)
        self.assertNotIn("项目 B", handoff_a, "Project A's handoff should not leak B")


class TestE2EGoldenSetIntegration(unittest.TestCase):
    """E2E: Run golden set cases through the full pipeline.

    Validates that the golden set evaluation mechanism itself works, and that
    every golden case can be ingested without crashing.
    """

    def test_all_golden_cases_ingest_without_error(self):
        """All 150 golden set cases should ingest without errors."""
        golden_path = ROOT / "examples" / "golden_set.jsonl"
        self.assertTrue(golden_path.exists(), "Golden set file must exist")

        samples = []
        for line in golden_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            samples.append(json.loads(line))

        self.assertGreaterEqual(len(samples), 150,
                                "Golden set should have 150+ cases")

        errors = []
        for sample in samples:
            case_id = sample["case_id"]
            with self.subTest(case_id=case_id):
                try:
                    with TemporaryDirectory() as tmp:
                        store = MemoryStore(Path(tmp))
                        engine = MemoryEngine(store, RuleBasedExtractor())
                        engine.ingest_events(sample["input_events"])
                        items = store.list_items()

                        # Verify processed_event_ids exist
                        processed = store.processed_event_ids()
                        expected_ids = [e["message_id"] for e in sample["input_events"]]
                        for eid in expected_ids:
                            self.assertIn(eid, processed,
                                          f"{case_id}: {eid} should be marked processed")

                        # Verify handoff doesn't crash
                        handoff = generate_handoff("golden", items)
                        self.assertIsInstance(handoff, str)
                except Exception as e:
                    errors.append(f"{case_id}: {e}")

        if errors:
            self.fail(f"{len(errors)} cases had errors:\n" + "\n".join(errors[:5]))


if __name__ == "__main__":
    unittest.main()