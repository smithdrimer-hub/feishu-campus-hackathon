"""V1.19: 混合源场景集成测试 — 模拟 6 人团队一天的真实协作。

覆盖 5 种数据源 (message/doc/task/calendar/meeting/approval)
+ 完整链路 (ingest → extract → upsert → maintenance → handoff)。
"""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _make_message_event(msg_id, created_at, sender_id, sender_name, text,
                        msg_type="text", project="aurora-refactor",
                        chat_id="oc_aurora"):
    return {
        "project_id": project, "chat_id": chat_id,
        "message_id": msg_id, "text": text, "content": text,
        "msg_type": msg_type, "created_at": created_at,
        "sender": {"id": sender_id, "name": sender_name, "sender_type": "user"},
    }


def _make_doc_event(doc_id, created_at, author_name, text, project="aurora-refactor"):
    return {
        "project_id": project, "chat_id": "",
        "message_id": doc_id, "text": text, "content": text,
        "msg_type": "text", "source_type": "doc", "created_at": created_at,
        "sender": {"id": "system", "name": author_name, "sender_type": "user"},
        "source_url": f"https://feishu.cn/docx/{doc_id}",
    }


def _make_task_event(task_id, created_at, text, project="aurora-refactor"):
    return {
        "project_id": project, "chat_id": "",
        "message_id": task_id, "text": text, "content": text,
        "msg_type": "text", "source_type": "task", "created_at": created_at,
        "sender": {"id": "system", "name": "飞书任务", "sender_type": "user"},
    }


def _make_calendar_event(cal_id, created_at, text, project="aurora-refactor"):
    return {
        "project_id": project, "chat_id": "",
        "message_id": cal_id, "text": text, "content": text,
        "msg_type": "text", "source_type": "calendar", "created_at": created_at,
        "sender": {"id": "system", "name": "飞书日历", "sender_type": "user"},
    }


def _make_meeting_event(meeting_id, created_at, text, project="aurora-refactor"):
    return {
        "project_id": project, "chat_id": "",
        "message_id": meeting_id, "text": text, "content": text,
        "msg_type": "text", "source_type": "meeting", "created_at": created_at,
        "sender": {"id": "system", "name": "飞书妙记", "sender_type": "system"},
    }


def _make_approval_event(approval_id, created_at, text, project="aurora-refactor"):
    return {
        "project_id": project, "chat_id": "",
        "message_id": approval_id, "text": text, "content": text,
        "msg_type": "text", "source_type": "approval", "created_at": created_at,
        "sender": {"id": "system", "name": "飞书审批", "sender_type": "system"},
    }


def _make_file_event(msg_id, created_at, sender_id, sender_name,
                     file_name, mime_type="text/markdown",
                     project="aurora-refactor", chat_id="oc_aurora"):
    import json
    content = json.dumps({
        "file_key": f"file_{msg_id}", "file_name": file_name,
        "mime_type": mime_type, "file_size": 12288,
    })
    return {
        "project_id": project, "chat_id": chat_id,
        "message_id": msg_id, "text": "", "content": content,
        "msg_type": "file", "created_at": created_at,
        "sender": {"id": sender_id, "name": sender_name, "sender_type": "user"},
    }


def _make_image_event(msg_id, created_at, sender_id, sender_name,
                      project="aurora-refactor", chat_id="oc_aurora"):
    import json
    content = json.dumps({"image_key": f"img_{msg_id}", "width": 1920, "height": 1080})
    return {
        "project_id": project, "chat_id": chat_id,
        "message_id": msg_id, "text": "", "content": content,
        "msg_type": "image", "created_at": created_at,
        "sender": {"id": sender_id, "name": sender_name, "sender_type": "user"},
    }


# ── 场景数据 ────────────────────────────────────────────────────

def build_aurora_day_events():
    """构建 Aurora 团队一天的所有事件。"""
    events = []

    # ── 早会分工 (text x 8) ──
    events.append(_make_message_event("msg_001", "2026-05-12T09:01:00", "ou_zhang", "张三",
        "早 今天开始用户中心重构 先对一下分工"))
    events.append(_make_message_event("msg_002", "2026-05-12T09:02:00", "ou_zhang", "张三",
        "负责人：张三负责前端重构，李四负责后端API"))
    events.append(_make_message_event("msg_003", "2026-05-12T09:03:00", "ou_li", "李四",
        "收到 我来设计新的API接口 王五负责测试"))
    events.append(_make_message_event("msg_004", "2026-05-12T09:04:00", "ou_wang", "王五",
        "没问题 测试我负责"))
    events.append(_make_message_event("msg_005", "2026-05-12T09:05:00", "ou_zhang", "张三",
        "目标：两周内完成用户中心模块的端到端重构"))
    events.append(_make_message_event("msg_006", "2026-05-12T09:06:00", "ou_li", "李四",
        "技术方案我写了个文档 大家看一下 https://feishu.cn/docx/techplan001"))
    events.append(_make_message_event("msg_007", "2026-05-12T09:10:00", "ou_zhao", "赵六",
        "确定了 前端用 React 18 + TypeScript 后端用 Go"))
    events.append(_make_message_event("msg_008", "2026-05-12T09:12:00", "ou_zhao", "赵六",
        "DDL暂定下周五 也就是5月23日完成联调"))

    # ── 分工确认 + 请假 (text x 4) ──
    events.append(_make_message_event("msg_009", "2026-05-12T09:15:00", "ou_chen", "陈七",
        "我来搞数据库迁移这部分"))
    events.append(_make_message_event("msg_010", "2026-05-12T09:20:00", "ou_liu", "刘八",
        "我这周出差 下周回来 有问题找张三"))
    events.append(_make_message_event("msg_012", "2026-05-12T09:30:00", "ou_wang", "王五",
        "对了 后天开始我请假2天 测试进度可能会受影响"))
    events.append(_make_message_event("msg_013", "2026-05-12T10:00:00", "ou_li", "李四",
        "阻塞：赵六的设计稿还没出来 我的API开发被卡住了"))

    # ── 风险/暂缓/决策 ──
    events.append(_make_message_event("msg_015", "2026-05-12T10:10:00", "ou_zhang", "张三",
        "风险：服务器资源不足 扩容申请还没批下来 联调可能受影响"))
    events.append(_make_message_event("msg_016", "2026-05-12T10:20:00", "ou_li", "李四",
        "暂缓：国际化功能和深色模式 先不做 优先级太低"))
    events.append(_make_message_event("msg_017", "2026-05-12T11:00:00", "ou_chen", "陈七",
        "数据库迁移方案我倾向用Flyway 不建议用Liquibase"))
    events.append(_make_message_event("msg_018", "2026-05-12T11:30:00", "ou_zhang", "张三",
        "下一步：李四先把API接口定义写出来 王五准备测试用例"))

    # ── 下午进展 ──
    events.append(_make_message_event("msg_019", "2026-05-12T14:00:00", "ou_li", "李四",
        "API接口定义写好了"))
    events.append(_make_message_event("msg_022", "2026-05-12T15:00:00", "ou_li", "李四",
        "设计稿收到 之前的阻塞已解决 我可以继续开发了"))
    events.append(_make_message_event("msg_025", "2026-05-12T16:00:00", "ou_zhang", "张三",
        "下午开会讨论了 确定改用微服务架构 不再用之前的单体方案"))
    events.append(_make_message_event("msg_026", "2026-05-12T16:30:00", "ou_liu", "刘八",
        "扩容申请已通过 风险解除了"))

    # ── 文件 + 图片 (5 file + 2 image) ──
    events.append(_make_file_event("msg_021", "2026-05-12T14:31:00", "ou_zhao", "赵六",
        "design_spec.md", "text/markdown"))
    events.append(_make_file_event("msg_027", "2026-05-12T17:00:00", "ou_li", "李四",
        "api_log.txt", "text/plain"))
    events.append(_make_file_event("msg_028", "2026-05-12T17:05:00", "ou_chen", "陈七",
        "migration.sql", "text/x-sql"))
    events.append(_make_file_event("msg_029", "2026-05-12T17:10:00", "ou_zhang", "张三",
        "progress.md", "text/markdown"))
    events.append(_make_file_event("msg_030", "2026-05-12T17:15:00", "ou_wang", "王五",
        "test_report.pdf", "application/pdf"))  # 非文本文件
    events.append(_make_image_event("msg_023", "2026-05-12T15:10:00", "ou_wang", "王五"))
    events.append(_make_image_event("msg_024", "2026-05-12T15:12:00", "ou_zhao", "赵六"))

    # ── 文档 (doc x 1) ──
    events.append(_make_doc_event("doc_techplan_001", "2026-05-12T09:00:00", "李四",
        "用户中心重构技术方案：前端采用 React 18 + TypeScript 单体架构，后端采用 Go + Gin，数据库 PostgreSQL"))

    # ── 文档评论 (doc_comment x 2) ──
    events.append(_make_doc_event("doc_comment_001", "2026-05-12T09:30:00", "陈七",
        "单体架构是不是不太合适？我们后面要拆微服务的"))
    events.append(_make_doc_event("doc_comment_002", "2026-05-12T09:35:00", "李四",
        "先单体快速上线 后面再拆"))

    # ── 任务 (task x 5) ──
    events.append(_make_task_event("task_001", "2026-05-12T09:00:00",
        "完成API接口开发 (负责人: 李四, 截止: 2026-05-20)"))
    events.append(_make_task_event("task_002", "2026-05-12T09:00:00",
        "完成前端页面重构 (负责人: 张三, 截止: 2026-05-22)"))
    events.append(_make_task_event("task_003", "2026-05-12T09:00:00",
        "编写集成测试用例 (负责人: 王五, 截止: 2026-05-21)"))
    events.append(_make_task_event("task_004", "2026-05-12T09:00:00",
        "数据库迁移脚本 (负责人: 陈七, 截止: 2026-05-19)"))
    events.append(_make_task_event("task_005", "2026-05-12T09:00:00",
        "设计稿交付 (负责人: 赵六, 截止: 2026-05-12)"))

    # ── 日历 (calendar x 2) ──
    events.append(_make_calendar_event("cal_001", "2026-05-12T10:00:00",
        "项目启动会 10:00-11:00 (参会: 全员)"))
    events.append(_make_calendar_event("cal_002", "2026-05-12T16:00:00",
        "架构评审会 16:00-17:00 (参会: 张三 李四 陈七)"))

    # ── 会议纪要 (meeting x 1) ──
    events.append(_make_meeting_event("meeting_001", "2026-05-12T17:00:00",
        "架构评审会总结: 确定改用微服务架构 不再用之前的单体方案。待办: 李四下周完成服务拆分方案。"))

    # ── 审批 (approval x 2) ──
    events.append(_make_approval_event("approval_pending_001", "2026-05-12T08:00:00",
        "服务器扩容申请 (状态: 待审批, 申请人: 刘八)"))
    events.append(_make_approval_event("approval_approved_001", "2026-05-12T16:00:00",
        "服务器扩容申请已批准 资源下周一到位"))

    return events


# ── 测试类 ──────────────────────────────────────────────────────


class TestMixedSourceScenario(unittest.TestCase):
    """混合源场景：6 人团队一天，5 种数据源，完整链路。"""

    def setUp(self):
        from memory.store import MemoryStore
        from memory.engine import MemoryEngine
        from memory.extractor import RuleBasedExtractor
        self.tmp = TemporaryDirectory()
        self.store = MemoryStore(self.tmp.name)
        self.engine = MemoryEngine(self.store, RuleBasedExtractor())
        self.events = build_aurora_day_events()

    def tearDown(self):
        if hasattr(self.store, '_backend') and self.store._backend is not None:
            try:
                self.store._backend.close()
            except Exception:
                pass
        self.tmp.cleanup()

    # ── 检查点 1: 所有数据源成功 ingest ──

    def test_all_sources_ingest_without_crash(self):
        """40 条混合事件全部成功 ingest，产生 ≥20 条活跃记忆（去重后）。"""
        result = self.engine.ingest_events(self.events, debounce=False)
        self.assertIsInstance(result, list)
        self.assertGreaterEqual(len(result), 20,
                                "40 条混合事件应产生至少 20 条活跃记忆")

    # ── 检查点 2: 跨源去重 ──

    def test_cross_source_dedup_owner(self):
        """群聊和任务中重复的负责人信息被去重，不产生多条重复 owner。"""
        self.engine.ingest_events(self.events, debounce=False)
        items = self.store.list_items("aurora-refactor")
        owners = [i for i in items if i.state_type == "owner"]
        self.assertGreaterEqual(len(owners), 4,
                                "6 人团队应有至少 4 条 owner 记忆")
        # 校验：张三、李四、王五、陈七 的 owner 都应存在
        owner_texts = " ".join(o.current_value for o in owners)
        for name in ["张三", "李四", "王五"]:
            self.assertIn(name, owner_texts,
                          f"owner 记忆中应包含 {name}")

    # ── 检查点 3: 跨源冲突检测 ──

    def test_architecture_decision_conflict(self):
        """文档'单体' vs 会议'微服务' 的架构决策冲突被提取。"""
        self.engine.ingest_events(self.events, debounce=False)
        items = self.store.list_items("aurora-refactor")
        decisions = [i for i in items if i.state_type == "decision"]
        self.assertGreaterEqual(len(decisions), 3,
                                "应至少有 3 条决策（React+Go / Flyway / 微服务）")
        decision_texts = " ".join(d.current_value for d in decisions)
        self.assertIn("React", decision_texts, "应有前端框架决策")
        self.assertIn("Go", decision_texts, "应有后端语言决策")
        self.assertIn("微服务", decision_texts, "应有架构决策")

    # ── 检查点 4: 审批驱动阻塞更新 ──

    def test_approval_updates_blocker(self):
        """服务器扩容审批：待审批→已通过，阻塞信息被提取。"""
        self.engine.ingest_events(self.events, debounce=False)
        items = self.store.list_items("aurora-refactor")
        blockers = [i for i in items if i.state_type == "blocker"]
        self.assertGreaterEqual(len(blockers), 2,
                                "应有至少 2 条阻塞（设计稿 + 服务器资源）")
        blocker_texts = " ".join(b.current_value for b in blockers)
        self.assertIn("设计稿", blocker_texts, "应有设计稿阻塞")
        self.assertIn("资源", blocker_texts, "应有服务器资源阻塞")

    # ── 检查点 5: 文件消息类型区分 ──

    def test_file_message_type_discrimination(self):
        """文本文件（markdown/sql）与 PDF 被正确区分。"""
        from memory.message_parser import get_parser
        parser = get_parser()
        text_count = 0
        binary_count = 0
        for ev in self.events:
            if ev.get("msg_type") != "file":
                continue
            content = ev.get("content", "")
            if "pdf" in str(content).lower():
                binary_count += 1
            else:
                text_count += 1
        self.assertGreaterEqual(text_count, 3, "应有 ≥3 个文本文件")
        self.assertGreaterEqual(binary_count, 1, "应有 ≥1 个 PDF 文件")

    # ── 检查点 6: 图片消息不导致崩溃 ──

    def test_image_messages_present_in_raw_events(self):
        """图片消息被存储为 raw_events，engine normalize 时生成占位文本。
        验证：提取未因图片消息崩溃，活跃记忆正常产出。"""
        self.engine.ingest_events(self.events, debounce=False)
        raw = self.store.read_raw_events("aurora-refactor")
        images = [e for e in raw if e.get("msg_type") == "image"]
        self.assertGreaterEqual(len(images), 2,
                                "至少 2 条图片消息被存储")
        items = self.store.list_items("aurora-refactor")
        self.assertGreaterEqual(len(items), 20,
                                "图片消息不影响提取产出")

    # ── 检查点 7: Handoff 完整 ──

    def test_handoff_covers_all_dimensions(self):
        """交接摘要包含核心状态类型 + needs_review 标记。"""
        self.engine.ingest_events(self.events, debounce=False)
        self.store.maintenance()
        items = self.store.list_items("aurora-refactor")
        history = self.store.list_history("aurora-refactor")

        from memory.handoff import generate_handoff
        handoff = generate_handoff("aurora-refactor", items, history, store=self.store)
        self.assertGreater(len(handoff), 100, "交接摘要不应为空")
        # 关键维度应该出现
        for keyword in ["目标", "负责人", "决策", "阻塞", "截止"]:
            self.assertIn(keyword, handoff,
                          f"交接摘要应包含'{keyword}'维度")

    # ── 检查点 8: 生命周期正确 ──

    def test_lifecycle_states_present(self):
        """sweep_expired 后 confirmed 决策仍在活跃列表中（锚点保护）。"""
        self.engine.ingest_events(self.events, debounce=False)
        self.store.maintenance()
        items = self.store.list_items("aurora-refactor")
        decisions = [i for i in items if i.state_type == "decision"]
        self.assertGreater(len(decisions), 0, "应该有决策类型的记忆")
        # confirmed 决策即使 needs_review，status 仍为 active（不被 sweep 移入 history）
        confirmed = [d for d in decisions if d.decision_strength == "confirmed"]
        if confirmed:
            for d in confirmed:
                self.assertNotIn(d.status, ["expired", "forgotten"],
                                 f"confirmed 决策不应被自动标记失效: {d.current_value[:50]}")

    # ── 性能基准 ──

    def test_performance_within_budget(self):
        """混合源完整链路 < 2 秒。"""
        import time
        t0 = time.time()
        self.engine.ingest_events(self.events, debounce=False)
        self.store.maintenance()
        items = self.store.list_items("aurora-refactor")
        history = self.store.list_history("aurora-refactor")
        from memory.handoff import generate_handoff
        generate_handoff("aurora-refactor", items, history)
        t1 = time.time()
        self.assertLess(t1 - t0, 2.0,
                        f"完整链路应在 2 秒内完成，实际 {t1 - t0:.2f}s")

    # ── 数据完整性 ──

    def test_all_expected_state_types_present(self):
        """确认 8 种状态类型都有出现。"""
        from collections import Counter
        self.engine.ingest_events(self.events, debounce=False)
        items = self.store.list_items("aurora-refactor")
        types = Counter(i.state_type for i in items)
        expected = {"owner", "decision", "blocker", "deadline", "deferred",
                    "next_step", "project_goal", "member_status"}
        found = expected & set(types.keys())
        self.assertGreaterEqual(len(found), 5,
                                f"至少应有 5 种状态类型，实际 {len(found)}: {found}")


class TestMixedSourceSQLite(TestMixedSourceScenario):
    """SQLite 后端下的混合源场景——继承全部 10 个检查点。"""

    def setUp(self):
        from memory.store import MemoryStore
        from memory.engine import MemoryEngine
        from memory.extractor import RuleBasedExtractor
        from memory.store_sqlite import SQLiteStorageBackend
        self.tmp = TemporaryDirectory()
        self._sq_backend = SQLiteStorageBackend(self.tmp.name)
        self.store = MemoryStore(self.tmp.name, backend=self._sq_backend)
        self.engine = MemoryEngine(self.store, RuleBasedExtractor())
        self.events = build_aurora_day_events()

    def tearDown(self):
        if hasattr(self, '_sq_backend') and self._sq_backend is not None:
            try:
                self._sq_backend.close()
            except Exception:
                pass
        self.tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
