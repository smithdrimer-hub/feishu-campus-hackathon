"""Tests for V1.9 project state aggregation (Dev Spec Morph A)."""

import sys
import unittest
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.project_state import build_group_project_state, render_group_state_panel_text
from memory.schema import MemoryItem, SourceRef


def make_item(
    state_type: str,
    current_value: str,
    project_id: str = "demo",
    owner: str | None = None,
    key: str | None = None,
    status: str = "active",
    memory_id: str | None = None,
) -> MemoryItem:
    """Helper to create a MemoryItem with minimal fields for testing."""
    return MemoryItem(
        project_id=project_id,
        state_type=state_type,
        key=key or f"{state_type}_{hash(current_value) % 10000}",
        current_value=current_value,
        rationale="测试用记忆",
        owner=owner,
        status=status,
        confidence=0.8,
        source_refs=[
            SourceRef(
                type="message",
                chat_id="chat_test",
                message_id=f"msg_{hash(current_value) % 10000}",
                excerpt=current_value[:50],
                created_at="2026-04-28T10:00:00",
            )
        ],
        memory_id=memory_id or f"mem_{hash(current_value)}",
    )


class TestBuildGroupProjectState(unittest.TestCase):
    """V1.9: build_group_project_state 测试."""

    def test_empty_items(self):
        """无记忆时应优雅降级（不抛错，返回空列表）。"""
        result = build_group_project_state("demo", [])
        self.assertEqual(result["project_id"], "demo")
        self.assertEqual(len(result["owners"]), 0)
        self.assertEqual(len(result["recent_decisions"]), 0)
        self.assertEqual(len(result["risks"]), 0)

    def test_owners_aggregation(self):
        """owner 类型记忆应聚合为负责人列表。"""
        items = [
            make_item("owner", "张三", owner="张三"),
            make_item("owner", "李四", owner="李四"),
        ]
        result = build_group_project_state("demo", items)
        self.assertEqual(len(result["owners"]), 2)
        user_ids = [o["user_id"] for o in result["owners"]]
        self.assertIn("张三", user_ids)
        self.assertIn("李四", user_ids)

    def test_owner_dedup(self):
        """同一个人重复出现应去重。"""
        items = [
            make_item("owner", "张三", owner="张三", memory_id="mem_1"),
            make_item("owner", "张三", owner="张三", memory_id="mem_2"),
        ]
        result = build_group_project_state("demo", items)
        self.assertEqual(len(result["owners"]), 1)

    def test_decisions_classified(self):
        """决策应区分 recent（已定）和 open（待定）。"""
        items = [
            make_item("decision", "采用方案 A 进行开发", memory_id="mem_a"),
            make_item("decision", "是否接入多 Agent 演示", memory_id="mem_b"),
            make_item("decision", "考虑改用方案 B", memory_id="mem_c"),
        ]
        result = build_group_project_state("demo", items)
        # "是否"含 open 关键词，"考虑"含 open 关键词，"采用"不含
        recent = [d for d in result["recent_decisions"]]
        open_d = [d for d in result["open_decisions"]]
        self.assertGreaterEqual(len(recent), 1)
        self.assertGreaterEqual(len(open_d), 1)
        # recent 应包含"方案 A"
        recent_titles = [d["title"] for d in recent]
        self.assertTrue(any("方案 A" in t for t in recent_titles))

    def test_risks_aggregation(self):
        """blocker 类型应聚合为风险列表。"""
        items = [
            make_item("blocker", "测试数据还没准备好"),
            make_item("blocker", "后端接口被阻塞"),
        ]
        result = build_group_project_state("demo", items)
        self.assertEqual(len(result["risks"]), 2)

    def test_risks_severity(self):
        """含"严重"的阻塞应标记为高 severity。"""
        items = [
            make_item("blocker", "严重：数据库挂了"),
            make_item("blocker", "小问题：样式需要调整"),
        ]
        result = build_group_project_state("demo", items)
        severities = [r["severity"] for r in result["risks"]]
        self.assertEqual(severities, ["high", "medium"])

    def test_next_actions_with_owner(self):
        """有 owner 的 next_step 应出现在下一步。"""
        items = [
            make_item("next_step", "完成测试", owner="张三"),
            make_item("next_step", "写文档"),  # 无 owner
        ]
        result = build_group_project_state("demo", items)
        self.assertEqual(len(result["next_actions"]), 1)
        self.assertIn("张三", result["next_actions"][0]["owner"])

    def test_active_tasks(self):
        """next_step 和有 owner 的记忆应作为任务列出。"""
        items = [
            make_item("next_step", "完成去重模块", owner="张三"),
            make_item("next_step", "修复 bug #123"),
        ]
        result = build_group_project_state("demo", items)
        self.assertEqual(len(result["active_tasks"]), 2)

    def test_project_title_from_goal(self):
        """project_goal 类型应提取为项目标题。"""
        items = [
            make_item("project_goal", "完成 V1.9 开发"),
        ]
        result = build_group_project_state("demo", items)
        self.assertIn("V1.9", result["project_title"])

    def test_project_title_fallback(self):
        """无 goal 时用 project_id 作为标题。"""
        result = build_group_project_state("demo", [])
        self.assertIn("demo", result["project_title"])


class TestRenderGroupStatePanel(unittest.TestCase):
    """V1.9: render_group_state_panel_text 测试."""

    def test_empty_state_renders_gracefully(self):
        """空状态不应报错，且有引导文案。"""
        state = build_group_project_state("demo", [])
        text = render_group_state_panel_text(state)
        self.assertIn("项目状态", text)
        self.assertIn("暂无提取", text)

    def test_full_state_renders_sections(self):
        """完整状态应包含各区块。"""
        items = [
            make_item("owner", "张三", owner="张三"),
            make_item("owner", "李四", owner="李四"),
            make_item("decision", "采用方案 A"),
            make_item("blocker", "测试数据阻塞"),
            make_item("next_step", "完成测试", owner="张三"),
        ]
        state = build_group_project_state("demo", items)
        text = render_group_state_panel_text(state)
        self.assertIn("负责人", text)
        self.assertIn("最近决策", text)
        self.assertIn("风险与阻塞", text)
        self.assertIn("下一步", text)
        self.assertNotIn("待定决策", text)  # 没有 open decision

    def test_no_section_hides(self):
        """无数据的区块应自动隐藏。"""
        items = [make_item("owner", "张三", owner="张三")]
        state = build_group_project_state("demo", items)
        text = render_group_state_panel_text(state)
        self.assertIn("负责人", text)
        self.assertNotIn("风险与阻塞", text)
        self.assertNotIn("下一步", text)


if __name__ == "__main__":
    unittest.main()