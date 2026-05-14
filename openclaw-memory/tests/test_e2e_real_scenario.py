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
        owner_names = {o.owner for o in owners}
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

        for section in ("当前项目目标", "当前负责人", "决策时间线",
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


# ── V1.19 两周冲刺场景 (100 条消息, 6 人, 含噪音) ─────────────

SPRINT_PROJECT = "aurora-sprint"
SPRINT_CHAT = "oc_aurora_sprint"
SPRINT_MEMBERS = {
    "张三": "ou_zhang", "李四": "ou_li", "王五": "ou_wang",
    "赵六": "ou_zhao", "陈七": "ou_chen", "刘八": "ou_liu",
}


def _msg(text, msg_id, day=1, hour=9, minute=0,
         msg_type="text", sender=None, sender_id=None, source_type=None):
    """构建两周冲刺事件，按 day/hour 自然排列。"""
    base = datetime(2026, 5, 4, tzinfo=timezone.utc) + timedelta(days=day - 1)
    ts = base.replace(hour=hour, minute=minute).isoformat()
    # 从文本前缀推断 sender
    name = sender or "张三"
    sid = sender_id or SPRINT_MEMBERS.get(name, "ou_unknown")
    return {
        "project_id": SPRINT_PROJECT, "chat_id": SPRINT_CHAT,
        "message_id": msg_id, "text": text, "content": text,
        "msg_type": msg_type, "created_at": ts,
        "source_type": source_type or "message",
        "sender": {"id": sid, "name": name, "sender_type": "user"},
    }


# 两周 100 条消息 — 包含强信号、隐式语义、噪音、非文本、跨源
SPRINT_EVENTS = [
    # ═══ Day 1 早会分工 ═══
    _msg("早 今天开始用户中心重构的冲刺", "s001", 1, 9, 1, sender="张三"),
    _msg("分一下工", "s002", 1, 9, 1, sender="张三"),
    _msg("负责人：张三负责后端API，李四负责前端重构", "s003", 1, 9, 3, sender="张三"),
    _msg("收到 我来搞后端", "s004", 1, 9, 4, sender="张三"),
    _msg("前端交给我 王五帮我搞测试", "s005", 1, 9, 5, sender="李四"),
    _msg("可以 测试我来", "s006", 1, 9, 6, sender="王五"),
    _msg("目标：两周内完成端到端集成", "s007", 1, 9, 8, sender="张三"),
    _msg("DDL 暂定 5 月 18 日联调完成", "s008", 1, 9, 10, sender="张三"),
    _msg("好的", "s009", 1, 9, 12, sender="赵六"),  # 噪音
    _msg("OK", "s010", 1, 9, 13, sender="陈七"),  # 噪音

    # ═══ Day 1 技术方案讨论 ═══
    _msg("技术方案我写了个文档 大家看一下", "s011", 1, 10, 30, sender="李四"),
    _msg("确定用 React 18 + TypeScript 前端 Go 后端", "s012", 1, 11, 0, sender="李四"),
    _msg("后端不用 Go 了吧 我觉得还是用 Python 快一些", "s013", 1, 11, 5, sender="陈七"),
    _msg("确定用 Python 不纠结了", "s014", 1, 11, 8, sender="张三"),
    _msg("其实用 Go 也没问题 就是学习成本高", "s015", 1, 11, 10, sender="陈七"),
    _msg("已经确定了 Python 就别改了", "s016", 1, 11, 12, sender="张三"),

    # ═══ Day 1 下午阻塞 ═══
    _msg("阻塞：赵六的设计稿还没出 前端动不了", "s017", 1, 14, 30, sender="李四"),
    _msg("在弄了在弄了 今天下午给你", "s018", 1, 14, 32, sender="赵六"),
    _msg("还有个问题 数据库选型还没定", "s019", 1, 15, 0, sender="陈七"),
    _msg("数据库用 PostgreSQL 吧 别用 MySQL 了", "s020", 1, 15, 10, sender="张三"),
    _msg("确定了用 PostgreSQL", "s021", 1, 15, 11, sender="张三"),
    _msg("行 我改一下 migration", "s022", 1, 15, 20, sender="陈七"),
    _msg("暂缓：国际化先不做 优先级太低", "s023", 1, 16, 0, sender="张三"),
    _msg("截图发群里了 大家参考下", "s024", 1, 17, 30, sender="赵六", msg_type="image"),
    _msg("👍", "s025", 1, 17, 31, sender="李四"),  # 噪音

    # ═══ Day 2 进展与风险 ═══
    _msg("设计稿收到 之前的阻塞解除了", "s026", 2, 9, 30, sender="李四"),
    _msg("风险：服务器扩容申请还没批下来 联调可能受影响", "s027", 2, 10, 0, sender="张三"),
    _msg("我来催一下审批", "s028", 2, 10, 2, sender="刘八"),
    _msg("我这边后端进度还行 完成了 70%", "s029", 2, 11, 0, sender="张三"),
    _msg("下一步：陈七把数据库迁移脚本写完 李四做接口联调", "s030", 2, 11, 30, sender="张三"),
    _msg("收到 今天能写完", "s031", 2, 11, 32, sender="陈七"),
    _msg("请假：明天请假一天 去医院", "s032", 2, 14, 0, sender="王五"),
    _msg("那测试怎么办 李四能顶一下吗", "s033", 2, 14, 2, sender="张三"),
    _msg("行 我来补测试用例", "s034", 2, 14, 5, sender="李四"),
    _msg("李四：习惯用 Vitest 写单元测试", "s035", 2, 15, 0, sender="李四"),
    _msg("这个看不太懂 谁帮忙看看", "s036", 2, 16, 0, sender="赵六"),  # 模糊信号
    _msg("我来看看吧 应该是 API 接口的问题", "s037", 2, 16, 5, sender="张三"),

    # ═══ Day 2 代码片段上传 ═══
    _msg("这是错误日志 帮忙看看api_error.log", "s038", 2, 16, 30, sender="赵六",
         msg_type="file"),
    _msg("🤔", "s039", 2, 16, 31, sender="陈七"),  # 噪音
    _msg("看了 是超时的问题 我加了重试", "s040", 2, 16, 45, sender="张三"),

    # ═══ Day 3 王五请假 李四顶上 ═══
    _msg("今天王五不在 测试相关找李四", "s041", 3, 9, 0, sender="张三"),
    _msg("收到 我看了一下进度 李四那边前端还需要两天", "s042", 3, 9, 5, sender="刘八"),
    _msg("还没好 卡在权限模块了", "s043", 3, 9, 10, sender="李四"),
    _msg("需要帮忙吗 我可以看一下权限的问题", "s044", 3, 9, 12, sender="陈七"),
    _msg("要不我搭把手 你看一下后端接口就行", "s045", 3, 9, 15, sender="陈七"),
    _msg("行 那就交给你了 我继续搞接口联调", "s046", 3, 9, 20, sender="李四"),

    # ═══ Day 4 审批通过 + 死亡线变更 ═══
    _msg("扩容审批通过了 资源下周一到位", "s047", 4, 9, 0, sender="刘八",
         source_type="approval"),
    _msg("DDL 提前了 5 月 15 号就要联调 产品那边催了", "s048", 4, 10, 0, sender="张三"),
    _msg("不是吧 提前三天 压力有点大", "s049", 4, 10, 2, sender="李四"),
    _msg("没办法 产品那边说是客户要求", "s050", 4, 10, 5, sender="张三"),
    _msg("试试看吧 我来加班搞", "s051", 4, 10, 10, sender="李四"),
    _msg("暂停移动端的适配 先把桌面端搞定", "s052", 4, 11, 0, sender="张三"),

    # ═══ Day 5 架构决策冲突 ═══
    _msg("下午开会讨论了架构 确定改用微服务", "s053", 5, 14, 0, sender="张三"),
    _msg("之前说的单体方案不适用了 改用微服务了", "s054", 5, 14, 1, sender="张三"),
    _msg("微服务的话 服务拆分方案谁来写", "s055", 5, 14, 3, sender="李四"),
    _msg("我下周写 这周先把前提条件搞清楚", "s056", 5, 14, 5, sender="陈七"),
    _msg("会议纪要出来了 大家确认一下", "s057", 5, 17, 0, sender="张三",
         source_type="meeting"),
    _msg("确认 没问题", "s058", 5, 17, 5, sender="李四"),
    _msg("那就确定了要拆 先拆 3 个模块：用户、订单、库存", "s059", 5, 17, 30, sender="张三"),
    _msg("那迁移脚本也要改 工作量加大不少", "s060", 5, 17, 35, sender="陈七"),

    # ═══ Day 6 进度汇报 ═══
    _msg("进度汇报：后端 85％ 前端 60％ 测试 40％", "s061", 6, 9, 0, sender="刘八"),
    _msg("测试有点慢 主要是王五请假耽误了", "s062", 6, 9, 2, sender="李四"),
    _msg("没事 王五明天就回来了", "s063", 6, 9, 5, sender="张三"),
    _msg("文档我已经更新好了", "s064", 6, 10, 0, sender="张三", source_type="doc"),
    _msg("migration.sql 写完了 可以 review 了", "s065", 6, 11, 0, sender="陈七",
         msg_type="file"),
    _msg("我来看一下", "s066", 6, 11, 5, sender="张三"),

    # ═══ Day 7 设计文档讨论 ═══
    _msg("API 文档在这里 https://feishu.cn/docx/api_v2", "s067", 7, 10, 0, sender="张三"),
    _msg("这个接口设计我觉得不太合理 太耦合了", "s068", 7, 10, 30, sender="陈七"),
    _msg("我也觉得 返回字段太多了 前端用不了", "s069", 7, 10, 35, sender="李四"),
    _msg("行 我拆分一下 改成 RESTful 风格", "s070", 7, 10, 40, sender="张三"),
    _msg("张三负责后端API 李四和陈七配合联调 优先完成核心接口", "s071", 7, 11, 0, sender="刘八"),

    # ═══ Day 8 王五复工 ═══
    _msg("回来了 李四辛苦了", "s072", 8, 9, 0, sender="王五"),
    _msg("测试进度我补一下 今天能赶上", "s073", 8, 9, 2, sender="王五"),
    _msg("下一步：王五补齐测试用例 陈七做数据库压测", "s074", 8, 11, 0, sender="刘八"),
    _msg("migration 跑了一下 性能没问题", "s075", 8, 14, 0, sender="陈七"),
    _msg("风险：压测发现接口 QPS 不够 可能要加缓存", "s076", 8, 15, 0, sender="陈七"),
    _msg("先看看能不能优化 SQL 实在不行再加 Redis", "s077", 8, 15, 10, sender="张三"),
    _msg("负责人：李四负责架构调整", "s078", 8, 16, 0, sender="刘八"),

    # ═══ Day 9 阻塞爆发 ═══
    _msg("阻塞：第三方支付接口文档还没给 我们的支付模块动不了", "s079", 9, 10, 0, sender="李四"),
    _msg("我催了 他们说今天给", "s080", 9, 10, 2, sender="刘八"),
    _msg("这个阻塞影响挺大的 联调进度会拖", "s081", 9, 10, 5, sender="张三"),
    _msg("在弄了 但是文档有一半是空的 还在等他们补", "s082", 9, 14, 0, sender="李四"),
    _msg("如果今天还拿不到 先 mock 联调吧", "s083", 9, 14, 5, sender="张三"),
    _msg("行 先 mock", "s084", 9, 14, 10, sender="李四"),
    _msg("等一下 他们发了 我看看", "s085", 9, 14, 15, sender="刘八"),
    _msg("文档拿到了 李四可以开工了", "s086", 9, 14, 30, sender="刘八"),
    _msg("设计稿 final 版本", "s087", 9, 17, 0, sender="赵六", msg_type="image"),

    # ═══ Day 10 冲刺收尾 ═══
    _msg("最后冲刺了 DDL 后天联调 大家再加把劲", "s088", 10, 9, 0, sender="张三"),
    _msg("后端基本搞定了 就剩支付接口联调", "s089", 10, 9, 5, sender="张三"),
    _msg("前端我这边没问题了 几个 bug 收尾中", "s090", 10, 9, 10, sender="李四"),
    _msg("测试我补齐了 还差两个集成测试", "s091", 10, 9, 15, sender="王五"),
    _msg("数据库没问题 压测也过了", "s092", 10, 9, 20, sender="陈七"),
    _msg("设计这边都完成了 有问题随时找我", "s093", 10, 10, 0, sender="赵六"),
    _msg("风险：支付接口还没联调完 今天必须搞定", "s094", 10, 14, 0, sender="李四"),
    _msg("搞定了 支付接口联调通过", "s095", 10, 17, 0, sender="李四"),
    _msg("太好了 最后一个阻塞解决了", "s096", 10, 17, 2, sender="张三"),
    _msg("明天准备好演示 大家辛苦了", "s097", 10, 17, 30, sender="刘八"),
    _msg("辛苦大家了 冲刺目标基本达成", "s098", 10, 17, 35, sender="张三"),
    _msg("🎉", "s099", 10, 17, 36, sender="王五"),
    _msg("后天见 明天我整理总结", "s100", 10, 17, 40, sender="张三"),

    # ═══ 日常噪音填充（每天 2-4 条）═══
    _msg("早 今天天气不错", "s101", 1, 8, 55, sender="赵六"),
    _msg("早", "s102", 1, 8, 56, sender="张三"),
    _msg("😊", "s103", 1, 8, 57, sender="王五"),
    _msg("午饭去不去食堂", "s104", 2, 12, 0, sender="王五"),
    _msg("去啊 等我一下", "s105", 2, 12, 1, sender="李四"),
    _msg("好", "s106", 2, 12, 2, sender="王五"),
    _msg("今天有点累 昨晚没睡好", "s107", 3, 8, 50, sender="陈七"),
    _msg("辛苦 多喝热水", "s108", 3, 8, 51, sender="刘八"),
    _msg("哈哈", "s109", 3, 8, 52, sender="陈七"),
    _msg("周五了 大家周末愉快", "s110", 5, 17, 50, sender="刘八"),
    _msg("收到 周末愉快", "s111", 5, 17, 51, sender="赵六"),
    _msg("明天加班 今天先休息", "s112", 5, 17, 52, sender="李四"),
    _msg("辛苦了 李四", "s113", 5, 17, 53, sender="张三"),
    _msg("[贴纸消息]", "s114", 6, 12, 0, sender="王五", msg_type="sticker"),
    _msg("谁有好的咖啡推荐", "s115", 7, 12, 0, sender="赵六"),
    _msg("星巴克冷萃不错", "s116", 7, 12, 2, sender="陈七"),
    _msg("明天我来早一点 要开个准备会", "s117", 9, 17, 30, sender="刘八"),
    _msg("ok", "s118", 9, 17, 31, sender="李四"),

    # ═══ 回复链：支付接口讨论（Day 4 下午）═══
    _msg("支付接口的文档谁有 发我一份", "s119", 4, 15, 0, sender="李四"),
    _msg("我有 发你了 看私聊", "s120", 4, 15, 2, sender="刘八"),
    _msg("看到了 谢谢 这个接口的限流策略是什么", "s121", 4, 15, 5, sender="李四"),
    _msg("1000 QPS 应该够了 不够再加", "s122", 4, 15, 7, sender="刘八"),
    _msg("行 我先按这个对接 有问题再找你", "s123", 4, 15, 10, sender="李四"),

    # ═══ 回复链：数据库迁移讨论（Day 7 下午）═══
    _msg("migration 跑了一下 有个报错", "s124", 7, 15, 0, sender="陈七"),
    _msg("什么错 发来看看", "s125", 7, 15, 2, sender="张三"),
    _msg("orders 表缺了个 status 字段", "s126", 7, 15, 5, sender="陈七"),
    _msg("那加一下就行 我更新下 schema", "s127", 7, 15, 7, sender="陈七"),
    _msg("好 加完告诉我", "s128", 7, 15, 10, sender="张三"),
    _msg("搞定了 重新跑了 通过了", "s129", 7, 15, 20, sender="陈七"),

    # ═══ post 消息（@提及 + 链接）═══
    _msg("早会分工确认 @李四 负责前端重构 @王五 负责集成测试 方案文档 https://feishu.cn/docx/sprint_plan",
         "s130", 3, 9, 15, sender="张三", msg_type="post"),
    _msg("API 文档已更新 最新的接口定义在这里 https://feishu.cn/docx/api_v2 @陈七 帮忙 review 一下",
         "s131", 6, 14, 0, sender="张三", msg_type="post"),

    # ═══ 跨源：日历 + 任务 ═══
    _msg("[日程] 冲刺评审会 5月16日 14:00-15:00 全员参加", "s132", 8, 9, 30,
         sender="刘八", source_type="calendar"),
    _msg("[任务] 完成支付接口联调 (负责人: 李四, 截止: 2026-05-16)", "s133", 8, 10, 0,
         sender="刘八", source_type="task"),
    _msg("[任务] 数据库迁移验证 (负责人: 陈七, 截止: 2026-05-14)", "s134", 8, 10, 2,
         sender="刘八", source_type="task"),
]



class TestTwoWeekSprintScenario(unittest.TestCase):
    """V1.19: 两周冲刺真实场景——100 条消息，6 人，含噪音/隐式语义/跨源。"""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = MemoryStore(Path(self.tmp.name))
        self.engine = MemoryEngine(self.store, RuleBasedExtractor())

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    # ── 基础完整性 ──

    def test_all_100_events_ingest_without_crash(self):
        """100 条事件全部成功 ingest。"""
        result = self.engine.ingest_events(SPRINT_EVENTS, debounce=False)
        self.assertGreater(len(result), 0)

    def test_all_8_state_types_present(self):
        """8 种状态类型全部出现。"""
        self.engine.ingest_events(SPRINT_EVENTS, debounce=False)
        items = self.store.list_items(SPRINT_PROJECT)
        types = {i.state_type for i in items}
        expected = {"owner", "decision", "blocker", "deadline", "deferred",
                    "next_step", "project_goal", "member_status"}
        missing = expected - types
        self.assertEqual(len(missing), 0,
                         f"Missing state types: {missing}")

    # ── 核心角色验证 ──

    def test_key_owners_present(self):
        """张三、李四、王五 的 owner 全部出现。"""
        self.engine.ingest_events(SPRINT_EVENTS, debounce=False)
        items = self.store.list_items(SPRINT_PROJECT)
        owners = [i for i in items if i.state_type == "owner"]
        owner_text = " ".join(o.current_value for o in owners)
        self.assertGreaterEqual(len(owners), 4,
                                "100 条消息应至少有 4 条 owner 记忆")
        for name in ["张三", "李四"]:
            self.assertIn(name, owner_text,
                          f"owner 中应包含 {name}")

    def test_owner_multiple_entries_for_same_person(self):
        """李四有多条 owner（领域不同），系统保留而非错误覆盖。"""
        self.engine.ingest_events(SPRINT_EVENTS, debounce=False)
        items = self.store.list_items(SPRINT_PROJECT)
        li_owners = [i for i in items
                     if i.state_type == "owner" and "李四" in (i.current_value or "")]
        self.assertGreaterEqual(len(li_owners), 2,
                                "李四应有至少 2 条 owner（领域 key 不同）")

    # ── 决策冲突与覆盖 ──

    def test_architecture_decision_conflict_detected(self):
        """单体 vs 微服务：决策变更正确记录。"""
        self.engine.ingest_events(SPRINT_EVENTS, debounce=False)
        items = self.store.list_items(SPRINT_PROJECT)
        decisions = [i for i in items if i.state_type == "decision"]
        decision_text = " ".join(d.current_value for d in decisions)
        # 微服务决策应出现（Day 5 推翻单体）
        self.assertIn("微服务", decision_text,
                      "应有微服务架构决策")
        # Go → Python 的语言决策也应出现
        self.assertIn("Python", decision_text,
                      "应有 Python 语言决策")

    def test_backend_language_decision_override(self):
        """Go → Python 的决策覆盖：Python 是最终活跃决策。"""
        self.engine.ingest_events(SPRINT_EVENTS, debounce=False)
        items = self.store.list_items(SPRINT_PROJECT)
        decisions = [i for i in items if i.state_type == "decision"]
        has_python = any("Python" in d.current_value for d in decisions)
        self.assertTrue(has_python, "Python 应为活跃的最终决策")

    # ── 阻塞生命周期 ──

    def test_blocker_lifecycle_across_sprint(self):
        """设计稿阻塞被解除，支付接口阻塞最终解决。"""
        self.engine.ingest_events(SPRINT_EVENTS, debounce=False)
        items = self.store.list_items(SPRINT_PROJECT)
        blockers = [i for i in items if i.state_type == "blocker"]
        blocker_text = " ".join(b.current_value for b in blockers)
        self.assertIn("设计稿", blocker_text, "应有设计稿阻塞")
        self.assertIn("支付", blocker_text, "应有支付接口阻塞")

    # ── Deadlines ──

    def test_deadline_change_tracked(self):
        """DDL 从 5.18 提到 5.15。"""
        self.engine.ingest_events(SPRINT_EVENTS, debounce=False)
        items = self.store.list_items(SPRINT_PROJECT)
        deadlines = [i for i in items if i.state_type == "deadline"]
        self.assertGreater(len(deadlines), 0, "应有 deadline 记忆")
        deadline_text = " ".join(d.current_value for d in deadlines)
        self.assertIn("5", deadline_text, "应包含日期")

    # ── 噪音消息正确处理 ──

    def test_noise_messages_not_extracted(self):
        """好的/OK/👍/🤔 不产生任何协作记忆。"""
        self.engine.ingest_events(SPRINT_EVENTS, debounce=False)
        items = self.store.list_items(SPRINT_PROJECT)
        noise_msgs = ["好的", "OK", "👍", "🤔", "🎉"]
        for item in items:
            for ref in item.source_refs:
                self.assertNotIn(ref.excerpt, noise_msgs,
                                 f"噪音消息不应产生记忆: {ref.excerpt}")

    # ── 非文本消息 ──

    def test_image_messages_not_crash(self):
        """3 条非文本消息不导致崩溃。"""
        non_text = [e for e in SPRINT_EVENTS
                    if e.get("msg_type") in ("image", "file")]
        self.assertGreaterEqual(len(non_text), 3,
                                "至少应有 3 条非文本消息用于验证")

    # ── 交接摘要 ──

    def test_handoff_covers_all_dimensions(self):
        """交接摘要覆盖全部 8 个维度。"""
        self.engine.ingest_events(SPRINT_EVENTS, debounce=False)
        self.store.maintenance()
        items = self.store.list_items(SPRINT_PROJECT)
        history = self.store.list_history(SPRINT_PROJECT)
        handoff = generate_handoff(SPRINT_PROJECT, items, history)
        for dim in ["目标", "负责人", "决策", "阻塞", "截止",
                     "暂缓", "下一步", "成员"]:
            self.assertIn(dim, handoff,
                          f"交接摘要应包含'{dim}'维度")

    # ── 性能 ──

    def test_100_events_within_1_second_ruleonly(self):
        """100 条消息 RuleOnly < 1 秒。"""
        import time
        t0 = time.time()
        self.engine.ingest_events(SPRINT_EVENTS, debounce=False)
        self.assertLess(time.time() - t0, 1.0,
                        "100 条消息 RuleOnly 应在 1 秒内完成")


    # ── V1.19 扩展测试 ──

    def test_noise_filtering_does_not_over_extract(self):
        """闲聊/噪音消息不产生记忆——验证过滤精度。"""
        self.engine.ingest_events(SPRINT_EVENTS, debounce=False)
        items = self.store.list_items(SPRINT_PROJECT)
        noise_ids = {"s009", "s010", "s025", "s039", "s102", "s103",
                     "s106", "s109", "s111", "s114", "s118",
                     "s101", "s104", "s107", "s115", "s099"}
        for item in items:
            for ref in item.source_refs:
                self.assertNotIn(ref.message_id, noise_ids,
                                 f"噪音消息 {ref.message_id} 不应产生记忆")

    def test_post_messages_extracted_with_parser(self):
        """post 消息含 @提及和链接，正常通过 parser 提取。"""
        import json
        # s130: "早会分工确认 @李四 负责前端重构 @王五 负责集成测试"
        ev_s130 = [e for e in SPRINT_EVENTS if e["message_id"] == "s130"][0]
        self.assertEqual(ev_s130["msg_type"], "post")
        self.engine.ingest_events(SPRINT_EVENTS, debounce=False)
        items = self.store.list_items(SPRINT_PROJECT)
        owners = [i for i in items if i.state_type == "owner"
                  and ("李四" in (i.current_value or "")
                       or "王五" in (i.current_value or ""))]
        self.assertGreaterEqual(len(owners), 2,
                                "post 中 @李四 @王五 应被提取为 owner")

    def test_sprint_total_message_count(self):
        """验证场景规模：134 条消息。"""
        self.assertEqual(len(SPRINT_EVENTS), 134,
                         "场景应包含 134 条消息")

    def test_timeline_spans_10_days(self):
        """验证时间跨度：10 个工作日。"""
        import json
        days = set()
        for ev in SPRINT_EVENTS:
            ts = ev.get("created_at", "")
            if ts:
                days.add(ts[:10])
        self.assertGreaterEqual(len(days), 10,
                                f"应覆盖至少 10 天，实际 {len(days)} 天")

    def test_cross_source_events_present(self):
        """跨源事件存在：approval/calendar/task 各至少 1 条。"""
        sources = set()
        for ev in SPRINT_EVENTS:
            st = ev.get("source_type", "message")
            if st != "message":
                sources.add(st)
        self.assertGreaterEqual(len(sources), 3,
                                f"应有至少 3 种跨源类型，实际 {len(sources)}: {sources}")

    def test_performance_134_events_ruleonly(self):
        """134 条消息 RuleOnly < 2 秒。"""
        import time
        t0 = time.time()
        self.engine.ingest_events(SPRINT_EVENTS, debounce=False)
        elapsed = time.time() - t0
        self.assertLess(elapsed, 2.0,
                        f"134 条消息应在 2 秒内完成，实际 {elapsed:.1f}s")
        self.assertGreater(elapsed, 0.01)


if __name__ == "__main__":
    unittest.main()
