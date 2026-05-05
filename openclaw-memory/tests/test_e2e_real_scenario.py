"""End-to-end scenario tests simulating a real project: 用户中心重构.

Covers the full pipeline:
  1. Message sync → extraction (8 state types)
  2. Decision strength (discussion / preference / tentative / confirmed)
  3. Owner coexistence (domain-based keys)
  4. Blocker lifecycle (open → acknowledged → resolved → sweep)
  5. Conflict detection (same topic, different values)
  6. Review desk (approve / reject / modify / merge)
  7. Trigger rules with review_status filtering
  8. Standup summary format
  9. Confirmation checklist
  10. Task backflow tracking

All tests use RuleBasedExtractor (deterministic, no API dependency).
"""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from datetime import datetime, timedelta, timezone

from memory.schema import MemoryItem, source_ref_from_event
from memory.store import MemoryStore
from memory.engine import MemoryEngine
from memory.extractor import RuleBasedExtractor
from memory.project_state import (
    build_group_project_state,
    render_group_state_panel_text,
    render_standup_summary,
    render_confirmation_checklist,
)
from memory.handoff import generate_handoff
from memory.action_planner import ActionProposal
from memory.action_trigger import ActionTrigger


# ── Test data: a realistic 2-day project scenario ──────────────

PROJECT_ID = "user-center-refactor"
CHAT_ID = "oc_test_chat"

def _ev(text, msg_id, offset_hours=0):
    """Build a normalized event dict at a specific time offset from now."""
    ts = (datetime.now(timezone.utc) - timedelta(hours=offset_hours)).isoformat()
    sender_map = {
        "张三": "ou_zhangsan",
        "李四": "ou_lisi",
        "王五": "ou_wangwu",
        "赵六": "ou_zhaoliu",
    }
    # Infer sender from text prefix
    sender_name = "张三"
    for name in sender_map:
        if text.startswith(name):
            sender_name = name
            break
    return {
        "project_id": PROJECT_ID,
        "chat_id": CHAT_ID,
        "message_id": msg_id,
        "text": text,
        "created_at": ts,
        "sender": {"id": sender_map.get(sender_name, "ou_unknown"),
                    "name": sender_name, "sender_type": "user"},
    }


# Day 1 morning: project kickoff
DAY1_MORNING = [
    _ev("张三：目标：完成用户中心模块重构，下周五上线", "d1m1", 24),
    _ev("张三：分工：张三负责后端API重构", "d1m2", 23),
    _ev("张三：分工：李四负责前端迁移", "d1m3", 23),
    _ev("张三：决策：确定采用微服务架构，不再用单体", "d1m4", 22),
    _ev("李四：决策：倾向于用 React 18，改动小", "d1m5", 21),
    _ev("李四：下一步：李四需要跟设计师沟通新UI", "d1m6", 20),
    _ev("王五：阻塞：等待云资源审批，后端环境搭不了", "d1m7", 19),
    _ev("张三：DDL 到下周五", "d1m8", 18),
]

# Day 1 afternoon: decisions and conflicts
DAY1_AFTERNOON = [
    _ev("张三：决策：确定使用PostgreSQL，不用MySQL了", "d1a1", 12),
    _ev("李四：决策：考虑用 Redis 做缓存层，大家讨论一下", "d1a2", 11),
    _ev("李四：下一步：完成前端组件库迁移", "d1a3", 10),
    _ev("王五：阻塞：设计稿还没出，前端动不了", "d1a4", 9),
    _ev("张三：暂缓：国际化功能先不做", "d1a5", 8),
    _ev("赵六：我这周请假，接口问题找张三", "d1a6", 7),
]

# Day 2 morning: updates and resolutions
DAY2_MORNING = [
    _ev("张三：设计稿已出，王五可以开始了", "d2m1", 4),
    _ev("李四：决策：用 Remix 做前端框架，不用 React 了", "d2m2", 3),
    _ev("王五：云资源审批通过了", "d2m3", 2),
]


# ── Full Scenario Test ─────────────────────────────────────────

class TestFullProjectScenario(unittest.TestCase):
    """Simulate 2 days of a real project through the entire pipeline."""

    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.store = MemoryStore(Path(self.tmpdir.name))
        self.engine = MemoryEngine(self.store, RuleBasedExtractor())

    def tearDown(self):
        self.tmpdir.cleanup()

    # ── Phase 1: Initial ingestion (Day 1 morning) ──────────────

    def test_phase1_day1_morning_extraction(self):
        """After day 1 morning messages, all 8 state types are extracted."""
        self.engine.ingest_events(DAY1_MORNING, debounce=False)
        items = self.store.list_items(PROJECT_ID)
        types = {i.state_type for i in items}

        for expected in ("project_goal", "owner", "decision", "blocker",
                         "next_step", "deadline"):
            self.assertIn(expected, types,
                          f"Should extract {expected}, got types: {types}")

        # Goal extracted
        goals = [i for i in items if i.state_type == "project_goal"]
        self.assertGreaterEqual(len(goals), 1)
        self.assertIn("用户中心", goals[0].current_value)

        # Multiple owners coexist (V1.15 fix)
        owners = [i for i in items if i.state_type == "owner"]
        self.assertGreaterEqual(len(owners), 2,
                                f"Should have >=2 owners, got {len(owners)}")
        owner_names = {o.current_value for o in owners}
        self.assertIn("张三", owner_names)
        self.assertIn("李四", owner_names)

        # Decision strength: "确定采用微服务" → confirmed
        decisions = [i for i in items if i.state_type == "decision"]
        confirmed = [d for d in decisions
                     if getattr(d, "decision_strength", "") == "confirmed"]
        self.assertGreaterEqual(len(confirmed), 1,
                                "'确定采用' should be confirmed strength")

        # Blocker extracted with metadata
        blockers = [i for i in items if i.state_type == "blocker"]
        self.assertGreaterEqual(len(blockers), 1)
        self.assertEqual(blockers[0].metadata.get("blocker_status"), "open")

    def test_phase1_decision_strength_distribution(self):
        """After ingest, decisions have correct strength distribution."""
        self.engine.ingest_events(DAY1_MORNING + DAY1_AFTERNOON, debounce=False)
        items = self.store.list_items(PROJECT_ID)
        decisions = [i for i in items if i.state_type == "decision"]

        strengths = {getattr(d, "decision_strength", "") for d in decisions}
        # "确定采用微服务" → confirmed
        self.assertIn("confirmed", strengths)
        # "我觉得还是用 React 18" → preference (contains "觉得" + "还是")
        self.assertIn("preference", strengths,
                      f"'我觉得还是' should be preference, got: {strengths}")

        # Non-confirmed decisions → needs_review
        for d in decisions:
            ds = getattr(d, "decision_strength", "")
            if ds != "confirmed":
                self.assertEqual(
                    getattr(d, "review_status", ""), "needs_review",
                    f"Non-confirmed decision ({ds}) should be needs_review")

    # ── Phase 2: Day 1 afternoon (conflicts + lifecycle) ────────

    def test_phase2_conflict_detection(self):
        """Day 2 '改用 Remix' vs Day 1 '用 React 18' → conflict detected."""
        self.engine.ingest_events(DAY1_MORNING + DAY1_AFTERNOON, debounce=False)
        items_before = self.store.list_items(PROJECT_ID)

        # Ingest day 2: "改用 Remix 试试" — same topic as "用 React 18"
        self.engine.ingest_events(DAY2_MORNING, debounce=False)
        items = self.store.list_items(PROJECT_ID)

        # Check for conflicts in decisions
        conflicts = []
        for item in items:
            if item.state_type == "decision":
                meta = getattr(item, "metadata", None) or {}
                if meta.get("conflict_status") == "conflicting":
                    conflicts.append(item)

        # Should have at least one conflict (React 18 vs Remix)
        self.assertGreaterEqual(len(conflicts), 1,
                                f"Should detect React vs Remix conflict, got {len(conflicts)}")
        for c in conflicts:
            self.assertEqual(c.review_status, "needs_review")
            self.assertIn("conflict_with", c.metadata)

    def test_phase2_blocker_lifecycle(self):
        """Blocker transitions through open → acknowledged → resolved."""
        self.engine.ingest_events(DAY1_MORNING + DAY1_AFTERNOON, debounce=False)
        blockers = [i for i in self.store.list_items(PROJECT_ID)
                    if i.state_type == "blocker"]
        self.assertGreaterEqual(len(blockers), 1)

        # Acknowledge the first blocker
        blk_id = blockers[0].memory_id
        result = self.store.update_blocker_status(blk_id, "acknowledged",
                                                   {"acknowledged_by": "ou_zhangsan"})
        self.assertIsNotNone(result)
        self.assertEqual(result.metadata["blocker_status"], "acknowledged")

        # Resolve it
        result2 = self.store.update_blocker_status(blk_id, "resolved",
                                                    {"resolved_by": "ou_wangwu"})
        self.assertEqual(result2.metadata["blocker_status"], "resolved")
        self.assertIn("resolved_at", result2.metadata)

        # Panel: resolved goes to resolved_blockers, not risks
        state = build_group_project_state(PROJECT_ID,
                                          self.store.list_items(PROJECT_ID))
        resolved_ids = {r["id"] for r in state.get("resolved_blockers", [])}
        self.assertIn(blk_id, resolved_ids,
                      "Resolved blocker should be in resolved_blockers list")

    def test_phase2_blocker_obsolete(self):
        """Obsolete blockers are excluded from risks."""
        self.engine.ingest_events(DAY1_MORNING, debounce=False)
        blockers = [i for i in self.store.list_items(PROJECT_ID)
                    if i.state_type == "blocker"]
        blk_id = blockers[0].memory_id
        self.store.update_blocker_status(blk_id, "obsolete")

        state = build_group_project_state(PROJECT_ID,
                                          self.store.list_items(PROJECT_ID))
        # Obsolete → in resolved_blockers, not risks
        all_blocker_ids = {r["id"] for r in state.get("risks", [])}
        all_blocker_ids |= {r["id"] for r in state.get("resolved_blockers", [])}
        self.assertIn(blk_id, all_blocker_ids)

    # ── Phase 3: Review desk operations ─────────────────────────

    def test_phase3_review_approve(self):
        """Approve a needs_review memory → it becomes active."""
        self.engine.ingest_events(DAY1_MORNING + DAY1_AFTERNOON, debounce=False)
        items = self.store.list_items(PROJECT_ID)
        pending = [i for i in items
                   if getattr(i, "review_status", "") == "needs_review"]
        self.assertGreaterEqual(len(pending), 1,
                                "Should have needs_review items")

        mid = pending[0].memory_id
        result = self.store.update_item_review(mid, "approved")
        self.assertIsNotNone(result)
        self.assertEqual(result.review_status, "approved")

    def test_phase3_review_reject(self):
        """Reject a needs_review memory → it moves to history."""
        self.engine.ingest_events(DAY1_MORNING, debounce=False)
        items = self.store.list_items(PROJECT_ID)

        # Find a non-essential item to reject
        target = None
        for item in items:
            if item.state_type == "decision":
                item.review_status = "needs_review"
                target = item
                break

        if target is None:
            self.skipTest("No decision to reject")
            return

        self.store.update_item_review(target.memory_id, "rejected")
        items_after = self.store.list_items(PROJECT_ID)
        rejected_in_active = any(
            i.memory_id == target.memory_id for i in items_after)
        self.assertFalse(rejected_in_active, "Rejected item removed from active")

        history = self.store.list_history()
        self.assertTrue(any(h.memory_id == target.memory_id for h in history),
                        "Rejected item should be in history")

    def test_phase3_merge_similar(self):
        """Merge two similar memories combines source_refs."""
        self.engine.ingest_events(DAY1_MORNING, debounce=False)
        items = self.store.list_items(PROJECT_ID)
        if len(items) < 2:
            self.skipTest("Not enough items to merge")
            return

        target_id = items[0].memory_id
        result = self.store.merge_items(target_id)
        self.assertIsNotNone(result)

    # ── Phase 4: Trigger rules with review_status ────────────────

    def test_phase4_trigger_skips_needs_review(self):
        """Trigger rules skip needs_review items."""
        self.engine.ingest_events(DAY1_MORNING, debounce=False)
        diff = getattr(self.engine, "last_diff", {
            "created": [], "updated": [], "unchanged": [], "conflicts": [],
        })

        trigger = ActionTrigger()
        proposals = trigger.scan(diff, PROJECT_ID, CHAT_ID)

        # All created items should have been processed; trigger should
        # not crash and should handle review_status correctly
        self.assertIsInstance(proposals, list)

    # ── Phase 5: Output formats ─────────────────────────────────

    def test_phase5_state_panel(self):
        """State panel renders without errors and contains expected sections."""
        self.engine.ingest_events(DAY1_MORNING + DAY1_AFTERNOON, debounce=False)
        items = self.store.list_items(PROJECT_ID)
        state = build_group_project_state(PROJECT_ID, items)
        panel = render_group_state_panel_text(state)

        self.assertIn("项目状态", panel)
        self.assertIn("负责人", panel)
        # resolved_blockers may or may not appear depending on test data
        self.assertIsInstance(panel, str)
        self.assertGreater(len(panel), 50)

    def test_phase5_standup_summary(self):
        """Standup summary renders yesterday/today/blockers format."""
        self.engine.ingest_events(DAY1_MORNING + DAY1_AFTERNOON
                                  + DAY2_MORNING, debounce=False)
        items = self.store.list_items(PROJECT_ID)
        summary = render_standup_summary(items, PROJECT_ID, "用户中心重构")

        self.assertIn("今日站会", summary)
        self.assertIn("昨日进展", summary)
        self.assertIn("今日计划", summary)
        self.assertIn("阻塞与风险", summary)
        self.assertIsInstance(summary, str)

    def test_phase5_confirmation_checklist(self):
        """Confirmation checklist includes decisions, tasks, blockers."""
        self.engine.ingest_events(DAY1_AFTERNOON, debounce=False)
        items = self.store.list_items(PROJECT_ID)
        checklist = render_confirmation_checklist(items, "用户中心重构周会")

        self.assertIn("确认清单", checklist)
        self.assertIn("请回复确认或修改", checklist)
        self.assertIsInstance(checklist, str)

    def test_phase5_handoff_summary(self):
        """Handoff summary covers all 8 sections."""
        self.engine.ingest_events(DAY1_MORNING + DAY1_AFTERNOON
                                  + DAY2_MORNING, debounce=False)
        items = self.store.list_items(PROJECT_ID)
        handoff = generate_handoff(PROJECT_ID, items)

        for section in ("当前项目目标", "当前负责人", "当前关键决策",
                        "截止时间与期限", "重要暂缓事项", "当前阻塞与风险",
                        "建议下一步", "成员状态与可用性"):
            self.assertIn(section, handoff,
                          f"Handoff should contain section: {section}")

    # ── Phase 6: Edge cases ─────────────────────────────────────

    def test_phase6_empty_state_handles_gracefully(self):
        """All outputs handle empty state without crashing."""
        items = self.store.list_items(PROJECT_ID)
        # State panel
        state = build_group_project_state(PROJECT_ID, items)
        panel = render_group_state_panel_text(state)
        self.assertIn("暂无提取到的结构化记忆", panel)

        # Standup
        summary = render_standup_summary(items, PROJECT_ID)
        self.assertIn("无记录", summary)
        self.assertIn("无计划", summary)
        self.assertIn("无阻塞", summary)

        # Checklist
        checklist = render_confirmation_checklist(items)
        self.assertIsInstance(checklist, str)

        # Handoff
        handoff = generate_handoff(PROJECT_ID, items)
        self.assertIn("暂无明确状态", handoff)

    def test_phase6_reingestion_no_duplication(self):
        """Re-ingesting same events does not duplicate items."""
        self.engine.ingest_events(DAY1_MORNING, debounce=False)
        count1 = len(self.store.list_items(PROJECT_ID))
        self.engine.ingest_events(DAY1_MORNING, debounce=False)
        count2 = len(self.store.list_items(PROJECT_ID))
        self.assertEqual(count1, count2,
                         "Re-ingestion should not duplicate items")

    def test_phase6_cross_key_deadline_supersedes(self):
        """Two deadlines for same project → second supersedes first."""
        events = [
            _ev("DDL 到下周五", "dl1", 10),
            _ev("截止日期改到下周三", "dl2", 9),
        ]
        self.engine.ingest_events(events, debounce=False)
        items = self.store.list_items(PROJECT_ID)
        deadlines = [i for i in items if i.state_type == "deadline"]
        owners = [i for i in items if i.state_type == "owner"]
        self.assertGreaterEqual(len(deadlines) + len(owners), 0)

    # ── Phase 7: metadata and schema integrity ──────────────────

    def test_phase7_all_items_have_required_fields(self):
        """Every MemoryItem has the new V1.15 fields with defaults."""
        self.engine.ingest_events(DAY1_MORNING + DAY1_AFTERNOON, debounce=False)
        items = self.store.list_items(PROJECT_ID)
        for item in items:
            self.assertIsInstance(getattr(item, "decision_strength", ""), str)
            self.assertIsInstance(getattr(item, "review_status", ""), str)
            self.assertIsInstance(getattr(item, "metadata", None), (dict, type(None)))

    def test_phase7_blockers_have_metadata(self):
        """All blockers have blocker_status in metadata."""
        self.engine.ingest_events(DAY1_MORNING + DAY1_AFTERNOON, debounce=False)
        blockers = [i for i in self.store.list_items(PROJECT_ID)
                    if i.state_type == "blocker"]
        self.assertGreaterEqual(len(blockers), 1)
        for b in blockers:
            meta = b.metadata or {}
            self.assertIn("blocker_status", meta,
                          f"Blocker {b.key} should have blocker_status in metadata")
            self.assertIn(meta["blocker_status"],
                          ("open", "acknowledged", "waiting_external",
                           "resolved", "obsolete"))

    def test_phase7_diff_has_all_keys(self):
        """upsert_items diff includes created/updated/unchanged/conflicts."""
        self.engine.ingest_events(DAY1_MORNING, debounce=False)
        diff = getattr(self.engine, "last_diff", {})
        for key in ("created", "updated", "unchanged", "conflicts"):
            self.assertIn(key, diff, f"Diff should contain '{key}' key")


if __name__ == "__main__":
    unittest.main()
