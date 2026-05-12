"""Regression & improvement tests for V1.19 P1 orchestrator hardening.

Each test case demonstrates a concrete scenario where the new version
produces a better result than the original.  All tests are self-contained
(no dependency on external data files).
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.schema import MemoryItem
from memory.orchestrator import orchestrate, OrchestratedPlan, UnblockAction


# ── helpers ─────────────────────────────────────────────────────

def _mk(**kw):
    """Shorthand MemoryItem constructor with safe defaults."""
    defaults = dict(
        project_id="test", state_type="blocker", key="k",
        current_value="v", rationale="r", owner=None, status="active",
        confidence=0.7, source_refs=[],
    )
    defaults.update(kw)
    return MemoryItem(**defaults)


OLD = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
MID = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
NEW = datetime.now(timezone.utc).isoformat()


# ── Test Cases ───────────────────────────────────────────────────

class OrchestratorSortFixTests(unittest.TestCase):
    """The original sort key was ``(has_resolver desc, downstream_count desc)``.
    This meant a blocker WITH a resolver but 0 downstream tasks would rank
    ABOVE a blocker WITHOUT a resolver but 5 downstream tasks — violating
    the documented priority rule "下游影响最大".
    """

    def test_high_impact_unassigned_ranks_above_low_impact_assigned(self):
        """5-downstream unassigned blocker should beat 0-downstream assigned blocker."""
        items = [
            # A: has resolver, 0 downstream
            _mk(state_type="blocker", key="a",
                current_value="一个小问题",
                owner="张三",
                metadata={"blocker_status": "open", "blocked_owner": "张三",
                          "dependency_owner": "李四"}),
            _mk(state_type="owner", key="o1", current_value="李四", owner="李四"),
            # B: NO resolver, 5 downstream
            _mk(state_type="blocker", key="b",
                current_value="核心架构阻塞",
                owner="王五",
                metadata={"blocker_status": "open", "blocked_owner": "王五"}),
            _mk(state_type="owner", key="o2", current_value="王五", owner="王五"),
            # 5 downstream tasks for 王五
            *[_mk(state_type="next_step", key=f"ns{i}", current_value=f"任务{i}",
                  owner="王五") for i in range(5)],
        ]
        plan = orchestrate("test", items)

        # B (high impact, unassigned) should come before A (low impact, assigned)
        actions = plan.actions
        idx_a = next(i for i, a in enumerate(actions) if "小问题" in a.evidence_msg)
        idx_b = next(i for i, a in enumerate(actions) if "架构阻塞" in a.evidence_msg)

        self.assertLess(idx_b, idx_a,
                        "高影响+无resolver的阻塞应该排在低影响+有resolver之前。"
                        f" 实际: B@P{actions[idx_b].priority}, A@P{actions[idx_a].priority}")


class UnavailableEscalationTests(unittest.TestCase):
    """When a blocked person is on leave AND no resolver is known, the old
    orchestrator would emit a useless '自行推动' action.  The new one
    escalates to '团队决策'.
    """

    def test_blocked_on_leave_no_resolver_escalates(self):
        items = [
            _mk(state_type="blocker", key="b", current_value="外部SDK没文档",
                owner="小杨",
                metadata={"blocker_status": "open", "blocked_owner": "小杨"}),
            _mk(state_type="member_status", key="ms",
                current_value="小杨请假", owner="小杨"),
        ]
        plan = orchestrate("test", items)

        self.assertGreater(len(plan.actions), 0, "应该有至少一条action")
        action = plan.actions[0]
        self.assertEqual(action.assignee, "团队决策",
                         f"被阻塞人请假+无resolver → 应升级为团队决策，实际: {action.assignee}")
        self.assertIn("团队决策", action.assignee)
        self.assertNotIn("自行推动", action.action,
                         "不应该生成无用的'自行推动'建议")


class BlockerAgeTests(unittest.TestCase):
    """Old orchestrator had no concept of blocker age.  New one surfaces
    stale blockers in reasons and blocker_summary.
    """

    def test_stale_blocker_appears_in_summary(self):
        items = [
            _mk(state_type="blocker", key="b1", current_value="老阻塞7天",
                owner="张三", recorded_at=OLD,
                metadata={"blocker_status": "open", "blocked_owner": "张三"}),
            _mk(state_type="blocker", key="b2", current_value="新阻塞1天",
                owner="李四", recorded_at=NEW,
                metadata={"blocker_status": "open", "blocked_owner": "李四"}),
        ]
        plan = orchestrate("test", items)

        self.assertIn("stale_blockers_gt_3d", plan.blocker_summary)
        self.assertGreaterEqual(plan.blocker_summary["stale_blockers_gt_3d"], 1,
                                "7天前的阻塞应该被统计为 stale_blockers_gt_3d")

    def test_stale_blocker_weight_affects_sort(self):
        """A stale blocker (7 days) with same downstream as a fresh one
        should rank higher due to age weight.
        """
        items = [
            _mk(state_type="blocker", key="fresh", current_value="新阻塞",
                owner="张三", recorded_at=NEW,
                metadata={"blocker_status": "open", "blocked_owner": "张三",
                          "dependency_owner": "李四"}),
            _mk(state_type="blocker", key="stale", current_value="老阻塞",
                owner="王五", recorded_at=OLD,
                metadata={"blocker_status": "open", "blocked_owner": "王五",
                          "dependency_owner": "李四"}),
            _mk(state_type="owner", key="o1", current_value="李四", owner="李四"),
            _mk(state_type="owner", key="o2", current_value="张三", owner="张三"),
            _mk(state_type="owner", key="o3", current_value="王五", owner="王五"),
        ]
        plan = orchestrate("test", items)

        actions = plan.actions
        idx_fresh = next(i for i, a in enumerate(actions) if "新阻塞" in a.evidence_msg)
        idx_stale = next(i for i, a in enumerate(actions) if "老阻塞" in a.evidence_msg)
        self.assertLess(idx_stale, idx_fresh,
                        f"老阻塞(7天)应排在新阻塞(1天)前面。"
                        f" 实际: stale@P{actions[idx_stale].priority}, fresh@P{actions[idx_fresh].priority}")


class BackwardCompatibilityTests(unittest.TestCase):
    """Ensure the new orchestrator output format doesn't break
    existing consumers (demo_movie.py).
    """

    def test_plan_has_all_original_fields(self):
        plan = orchestrate("test", [])
        self.assertIsInstance(plan, OrchestratedPlan)
        self.assertIsInstance(plan.actions, list)
        self.assertIsInstance(plan.dependency_chains, list)
        self.assertIsInstance(plan.team_status_summary, dict)
        self.assertIsInstance(plan.generated_reason, str)
        self.assertIsInstance(plan.blocker_summary, dict)

    def test_unblock_action_has_all_fields(self):
        items = [
            _mk(state_type="blocker", key="b", current_value="测试阻塞",
                owner="张三",
                metadata={"blocker_status": "open", "blocked_owner": "张三",
                          "dependency_owner": "李四"}),
            _mk(state_type="owner", key="o", current_value="李四", owner="李四"),
        ]
        plan = orchestrate("test", items)
        for a in plan.actions:
            self.assertIsInstance(a.priority, int)
            self.assertIsInstance(a.assignee, str)
            self.assertIsInstance(a.action, str)
            self.assertIsInstance(a.unblocks, list)
            self.assertIsInstance(a.reason, str)
            self.assertIsInstance(a.evidence_msg, str)

    def test_dependency_chain_has_age_and_urgency(self):
        items = [
            _mk(state_type="blocker", key="b", current_value="测试", owner="张三",
                recorded_at=OLD,
                metadata={"blocker_status": "open", "blocked_owner": "张三"}),
        ]
        plan = orchestrate("test", items)
        for chain in plan.dependency_chains:
            self.assertIn("age_days", chain)
            self.assertIn("deadline_urgency", chain)


class EmptyEdgeCases(unittest.TestCase):
    """Edge cases that should not crash."""

    def test_no_items(self):
        plan = orchestrate("test", [])
        self.assertEqual(len(plan.actions), 0)
        self.assertEqual(plan.blocker_summary["total_active_blockers"], 0)

    def test_no_blockers_only_next_steps(self):
        items = [
            _mk(state_type="next_step", key="n", current_value="正常任务",
                owner="张三"),
        ]
        plan = orchestrate("test", items)
        self.assertGreaterEqual(len(plan.actions), 1)
        self.assertEqual(plan.blocker_summary["total_active_blockers"], 0)

    def test_all_resolvers_unavailable(self):
        """When all resolvers are on leave, actions should still be generated
        with substitutes or fallbacks.
        """
        items = [
            _mk(state_type="blocker", key="b", current_value="阻塞",
                owner="张三",
                metadata={"blocker_status": "open", "blocked_owner": "张三",
                          "dependency_owner": "李四"}),
            _mk(state_type="member_status", key="ms",
                current_value="李四请假", owner="李四"),
            _mk(state_type="owner", key="o1", current_value="李四", owner="李四"),
            _mk(state_type="owner", key="o2", current_value="张三", owner="张三"),
            _mk(state_type="owner", key="o3", current_value="王五", owner="王五"),
        ]
        plan = orchestrate("test", items)
        self.assertGreater(len(plan.actions), 0)
        # Should substitute, not leave unassigned
        action = plan.actions[0]
        self.assertIn("代替", action.action,
                      f"resolver请假应有替代方案，实际: {action.action}")


if __name__ == "__main__":
    unittest.main()
