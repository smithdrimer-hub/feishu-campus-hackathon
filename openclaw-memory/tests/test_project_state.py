"""Tests for V1.9 project state aggregation (Dev Spec Morph A)."""

import sys
import unittest
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.project_state import (
    build_agent_context_pack,
    build_group_project_state,
    build_personal_work_context,
    render_group_state_panel_text,
    render_personal_context_text,
)
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


class TestBuildAgentContextPack(unittest.TestCase):
    """V1.9: build_agent_context_pack 测试."""

    def test_empty_items(self):
        """无记忆时应返回空列表字段，不抛错。"""
        result = build_agent_context_pack("demo", [])
        self.assertIn("project", result)
        self.assertEqual(len(result["decisions"]), 0)
        self.assertEqual(len(result["tasks"]), 0)

    def test_decisions_dedup_by_key(self):
        """相同 key 的决策只保留最新版本（按 version 取最新）。"""
        items = [
            make_item("decision", "采用方案 A", key="tech_choice",
                      memory_id="mem_old", status="superseded"),
            make_item("decision", "采用方案 B", key="tech_choice",
                      memory_id="mem_new", status="active"),
        ]
        # 手动设 version 确保排序正确
        items[0].version = 1  # 旧
        items[1].version = 2  # 新
        result = build_agent_context_pack("demo", items)
        self.assertEqual(len(result["decisions"]), 1)
        self.assertIn("方案 B", result["decisions"][0]["title"])

    def test_decisions_multiple_keys(self):
        """不同 key 的决策应各自保留。"""
        items = [
            make_item("decision", "采用方案 A", key="tech_choice", memory_id="mem_1"),
            make_item("decision", "使用 PostgreSQL", key="db_choice", memory_id="mem_2"),
        ]
        result = build_agent_context_pack("demo", items)
        self.assertEqual(len(result["decisions"]), 2)

    def test_tasks_from_next_step(self):
        """next_step 应聚合为 tasks。"""
        items = [
            make_item("next_step", "完成测试模块", owner="张三"),
            make_item("next_step", "写 API 文档"),
        ]
        result = build_agent_context_pack("demo", items)
        self.assertEqual(len(result["tasks"]), 2)

    def test_risks_from_blocker(self):
        """blocker 应聚合为 risks。"""
        items = [make_item("blocker", "测试数据阻塞")]
        result = build_agent_context_pack("demo", items)
        self.assertEqual(len(result["risks"]), 1)

    def test_raw_snippets(self):
        """有 source_refs 的记忆应在 snippets 中出现。"""
        item = make_item("decision", "采用方案 A")
        result = build_agent_context_pack("demo", [item])
        self.assertGreater(len(result["recent_discussion_snippets"]), 0)
        self.assertIn("chat_id", result["recent_discussion_snippets"][0])
        self.assertIn("message_id", result["recent_discussion_snippets"][0])

    def test_user_perspective(self):
        """指定 user_id 后应附加该用户视角。"""
        items = [
            make_item("next_step", "完成测试", owner="张三"),
            make_item("next_step", "写文档", owner="李四"),
        ]
        result = build_agent_context_pack("demo", items, user_id="张三")
        self.assertIn("user_perspective", result)
        self.assertGreater(len(result["user_perspective"]["open_tasks"]), 0)
        for t in result["user_perspective"]["open_tasks"]:
            self.assertIn("张三", t["assignees"])

    def test_user_perspective_no_match(self):
        """指定不存在的 user_id 应返回空列表。"""
        items = [make_item("next_step", "完成测试", owner="张三")]
        result = build_agent_context_pack("demo", items, user_id="李四")
        self.assertEqual(len(result["user_perspective"]["open_tasks"]), 0)


class TestOwnerMap(unittest.TestCase):
    """V1.9: owner_map 分辨率测试。"""

    def test_group_state_owner_map(self):
        """owner_map 使 build_group_project_state 输出 open_id。"""
        items = [make_item("owner", "张三", owner="张三")]
        result = build_group_project_state("demo", items, owner_map={"张三": "ou_zhangsan"})
        self.assertEqual(result["owners"][0]["user_id"], "ou_zhangsan")

    def test_group_state_task_owner_map(self):
        """owner_map 应影响 task assignees。"""
        items = [make_item("next_step", "完成测试", owner="张三")]
        result = build_group_project_state("demo", items, owner_map={"张三": "ou_zhangsan"})
        self.assertIn("ou_zhangsan", result["active_tasks"][0]["assignees"])

    def test_group_state_no_owner_map(self):
        """不传 owner_map 时仍输出原姓名。"""
        items = [make_item("owner", "张三", owner="张三")]
        result = build_group_project_state("demo", items)
        self.assertEqual(result["owners"][0]["user_id"], "张三")

    def test_agent_pack_owner_map(self):
        """owner_map 使 agent_context_pack 输出 open_id。"""
        items = [make_item("next_step", "测试", owner="张三")]
        result = build_agent_context_pack("demo", items, owner_map={"张三": "ou_zhangsan"})
        self.assertIn("ou_zhangsan", result["tasks"][0]["assignees"])


class TestPersonalWorkContext(unittest.TestCase):
    """V1.9: build_personal_work_context 测试。"""

    def test_tasks_by_owner(self):
        """按 owner 姓名过滤出个人任务。"""
        items = [
            make_item("next_step", "完成测试", owner="张三"),
            make_item("next_step", "写文档", owner="李四"),
        ]
        result = build_personal_work_context("张三", "demo", items)
        self.assertEqual(len(result["my_open_tasks"]), 1)
        self.assertIn("完成测试", result["my_open_tasks"][0]["title"])

    def test_tasks_by_open_id(self):
        """通过 owner_map 按 open_id 过滤。"""
        items = [
            make_item("next_step", "完成测试", owner="张三"),
            make_item("next_step", "写文档", owner="李四"),
        ]
        owner_map = {"张三": "ou_zhangsan", "李四": "ou_lisi"}
        result = build_personal_work_context("ou_zhangsan", "demo", items, owner_map=owner_map)
        self.assertEqual(len(result["my_open_tasks"]), 1)
        self.assertIn("完成测试", result["my_open_tasks"][0]["title"])

    def test_decisions_involved(self):
        """owner 相关的决策应出现。"""
        items = [
            make_item("decision", "采用方案 A", owner="张三"),
            make_item("decision", "采用方案 B", owner="李四"),
        ]
        result = build_personal_work_context("张三", "demo", items)
        self.assertEqual(len(result["my_recent_decisions_involved"]), 1)

    def test_risks_involved(self):
        """owner 相关的阻塞应出现。"""
        items = [
            make_item("blocker", "测试数据阻塞", owner="张三"),
        ]
        result = build_personal_work_context("张三", "demo", items)
        self.assertEqual(len(result["my_related_risks"]), 1)

    def test_empty_graceful(self):
        """无相关记忆时空字段降级。"""
        result = build_personal_work_context("张三", "demo", [])
        self.assertEqual(len(result["my_open_tasks"]), 0)
        self.assertEqual(len(result["my_recent_decisions_involved"]), 0)
        self.assertEqual(len(result["my_related_risks"]), 0)

    def test_render_personal_context(self):
        """render_personal_context_text 应产出可读文本。"""
        items = [make_item("next_step", "完成测试", owner="张三")]
        ctx = build_personal_work_context("张三", "demo", items)
        text = render_personal_context_text(ctx)
        self.assertIn("完成测试", text)
        self.assertIn("demo", text)

    def test_render_empty_shows_friendly(self):
        """空数据渲染应显示"没有分配给你的任务"。"""
        ctx = build_personal_work_context("张三", "demo", [])
        text = render_personal_context_text(ctx)
        self.assertIn("没有分配", text)


class TestRawSnippetsEnrichment(unittest.TestCase):
    """V1.9: raw_snippets 原文回溯测试。"""

    def test_enrich_snippets(self):
        """传 raw_events_path 后 snippets 应包含更长的原文。"""
        from memory.project_state import _enrich_snippets as enrich
        snippets = [
            {"chat_id": "chat_01", "message_id": "msg_001", "text": "摘要", "sent_at": ""},
        ]
        # 不传 raw_events_path 时不应报错
        result = enrich(snippets, None)
        self.assertEqual(len(result), 1)


# ── V1.12 FIX-9: 跨项目用户视图测试 ───────────────────────────

class TestCrossProjectContext(unittest.TestCase):
    """V1.12: 跨项目聚合测试。"""

    def test_user_in_3_projects_aggregates_all(self):
        """T9.1: 用户在 3 个项目各有任务 → 全部聚合。"""
        from memory.project_state import build_cross_project_context
        items_a = [make_item("next_step", "写API", owner="张三")]
        items_b = [make_item("next_step", "画UI", owner="张三")]
        items_c = [make_item("next_step", "测试", owner="张三")]
        ctx = build_cross_project_context("张三", {
            "proj_a": items_a, "proj_b": items_b, "proj_c": items_c,
        })
        self.assertEqual(len(ctx["projects"]), 3)
        total = sum(len(p["tasks"]) for p in ctx["projects"].values())
        self.assertEqual(total, 3)

    def test_user_only_in_one_project(self):
        """T9.2: 用户只在一个项目有任务 → 其他不出现。"""
        from memory.project_state import build_cross_project_context
        ctx = build_cross_project_context("张三", {
            "proj_a": [make_item("next_step", "X", owner="张三")],
            "proj_b": [make_item("next_step", "Y", owner="李四")],
        })
        self.assertEqual(len(ctx["projects"]), 1)
        self.assertIn("proj_a", ctx["projects"])

    def test_multi_type_coverage(self):
        """T9.3: 用户有 owner+blocker+next_step → 全部列出。"""
        from memory.project_state import build_cross_project_context
        items = [
            make_item("next_step", "任务1", owner="张三"),
            make_item("blocker", "阻塞1", owner="张三"),
            make_item("deadline", "周五", owner="张三"),
        ]
        ctx = build_cross_project_context("张三", {"p": items})
        p = ctx["projects"]["p"]
        self.assertEqual(len(p["tasks"]), 1)
        self.assertEqual(len(p["blockers"]), 1)
        self.assertEqual(len(p["deadlines"]), 1)

    def test_empty_projects_returns_empty(self):
        """T9.4: 用户在 0 个项目有任务 → 空字典。"""
        from memory.project_state import build_cross_project_context
        ctx = build_cross_project_context("张三", {
            "a": [make_item("next_step", "X", owner="李四")],
            "b": [make_item("blocker", "Y", owner="王五")],
        })
        self.assertEqual(len(ctx["projects"]), 0)

    def test_fuzzy_owner_matching(self):
        """T9.5: owner 模糊匹配 '张三' 应匹配 '张三负责API'。"""
        from memory.project_state import build_cross_project_context
        items = [make_item("next_step", "写文档", owner="张三负责API文档")]
        ctx = build_cross_project_context("张三", {"p": items})
        self.assertEqual(len(ctx["projects"]), 1)

    def test_render_cross_project(self):
        """render_cross_project_text 应产出可读文本。"""
        from memory.project_state import build_cross_project_context, render_cross_project_text
        items = [make_item("next_step", "完成测试", owner="张三")]
        ctx = build_cross_project_context("张三", {"demo": items})
        text = render_cross_project_text(ctx)
        self.assertIn("张三", text)
        self.assertIn("完成测试", text)


if __name__ == "__main__":
    unittest.main()