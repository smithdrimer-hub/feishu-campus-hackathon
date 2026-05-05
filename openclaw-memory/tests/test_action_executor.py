"""Tests for ActionExecutor bridging PlannedAction -> adapter calls."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.action_planner import PlannedAction
from memory.action_executor import ActionExecutor, ExecutionResult


class MockAdapter:
    """Simulate LarkCliAdapter with write-method tracking for CI-safe testing."""

    def __init__(self, send_success=True, task_success=True,
                 doc_success=True, assign_success=True):
        self.send_success = send_success
        self.task_success = task_success
        self.doc_success = doc_success
        self.assign_success = assign_success

        # Track last call args per method
        self.last_send_args = None
        self.last_task_args = None
        self.last_doc_args = None
        self.last_assign_args = None

    def send_message(self, chat_id, content, msg_type="text", identity="bot"):
        self.last_send_args = (chat_id, content, msg_type, identity)
        if self.send_success:
            return _mock_result(0, {"data": {"message_id": "om_mock_001"}})
        return _mock_result(1, {}, err="send failed")

    def create_task(self, summary, description="", due_at="", identity="bot"):
        self.last_task_args = (summary, description, due_at, identity)
        if self.task_success:
            return _mock_result(0, {"data": {"guid": "task_mock_001"}})
        return _mock_result(1, {}, err="task create failed")

    def create_doc(self, title, content="", identity="bot"):
        self.last_doc_args = (title, content, identity)
        if self.doc_success:
            return _mock_result(0, {"data": {
                "document_id": "doc_mock_001",
                "url": "https://feishu.cn/docx/doc_mock_001",
            }})
        return _mock_result(1, {}, err="doc create failed")

    def assign_task(self, task_guid, assignee_ids, identity="bot"):
        self.last_assign_args = (task_guid, assignee_ids, identity)
        if self.assign_success:
            return _mock_result(0, {"data": {"success": True}})
        return _mock_result(1, {}, err="assign failed")


def _mock_result(returncode, data, err=""):
    """Build a CliResult-compatible object for testing."""
    from adapters.lark_cli_adapter import CliResult
    return CliResult(
        args=[], returncode=returncode, stdout="", stderr=err, data=data,
    )


def _make_action(action_type, title="test", requires_confirmation=True):
    return PlannedAction(
        action_type=action_type,
        title=title,
        reason="test reason",
        command_hint="test hint",
        requires_confirmation=requires_confirmation,
    )


class TestActionExecutor(unittest.TestCase):

    def setUp(self):
        self.adapter = MockAdapter()

    # ── create_task ─────────────────────────────────────────────

    def test_create_task_success(self):
        executor = ActionExecutor(self.adapter, auto_confirm=True)
        action = _make_action("create_task", "拟创建任务：修复登录Bug", requires_confirmation=False)
        result = executor.execute(action, {
            "task_description": "用户无法登录", "task_due_at": "2026-05-10T00:00:00Z",
        })
        self.assertTrue(result.success)
        self.assertEqual(result.output_data["task_guid"], "task_mock_001")
        # Verify prefix was stripped
        summary, desc, due, identity = self.adapter.last_task_args
        self.assertEqual(summary, "修复登录Bug")
        self.assertEqual(desc, "用户无法登录")

    def test_create_task_strips_prefix(self):
        executor = ActionExecutor(self.adapter, auto_confirm=True)
        action = _make_action("create_task", "拟创建任务：优化数据库查询", requires_confirmation=False)
        executor.execute(action, {})
        summary, _, _, _ = self.adapter.last_task_args
        self.assertEqual(summary, "优化数据库查询")

    # ── send_message ────────────────────────────────────────────

    def test_send_message_success(self):
        executor = ActionExecutor(self.adapter, auto_confirm=True)
        action = _make_action("send_message", "拟发送阻塞同步：API超时需修复", requires_confirmation=False)
        result = executor.execute(action, {"chat_id": "oc_test"})
        self.assertTrue(result.success)
        chat_id, content, msg_type, identity = self.adapter.last_send_args
        self.assertEqual(chat_id, "oc_test")
        self.assertIn("API超时需修复", content)

    def test_send_message_at_mention_replacement(self):
        executor = ActionExecutor(self.adapter, auto_confirm=True)
        action = _make_action("send_message", "请 @张三 处理阻塞问题", requires_confirmation=False)
        result = executor.execute(action, {
            "chat_id": "oc_test",
            "owner_map": {"张三": "ou_zhangsan"},
        })
        self.assertTrue(result.success)
        _, content, _, _ = self.adapter.last_send_args
        self.assertIn('<at user_id="ou_zhangsan">张三</at>', content)

    def test_send_message_missing_chat_id(self):
        executor = ActionExecutor(self.adapter, auto_confirm=True)
        action = _make_action("send_message", "test message", requires_confirmation=False)
        result = executor.execute(action, {})
        self.assertFalse(result.success)
        self.assertIn("chat_id", result.error)

    # ── create_doc ──────────────────────────────────────────────

    def test_create_doc_success(self):
        executor = ActionExecutor(self.adapter, auto_confirm=True)
        action = _make_action("create_doc", "交接说明文档", requires_confirmation=False)
        result = executor.execute(action, {"doc_content": "# 项目状态"})
        self.assertTrue(result.success)
        self.assertEqual(result.output_data["doc_token"], "doc_mock_001")
        title, content, identity = self.adapter.last_doc_args
        self.assertEqual(title, "交接说明文档")
        self.assertEqual(content, "# 项目状态")

    # ── assign_task ─────────────────────────────────────────────

    def test_assign_task_success(self):
        executor = ActionExecutor(self.adapter, auto_confirm=True)
        action = _make_action("assign_task", "分配任务", requires_confirmation=False)
        result = executor.execute(action, {
            "task_guid": "task_001",
            "assignee_ids": ["ou_aaa", "ou_bbb"],
        })
        self.assertTrue(result.success)
        guid, ids, identity = self.adapter.last_assign_args
        self.assertEqual(guid, "task_001")
        self.assertEqual(ids, ["ou_aaa", "ou_bbb"])

    def test_assign_task_missing_guid(self):
        executor = ActionExecutor(self.adapter, auto_confirm=True)
        action = _make_action("assign_task", "分配任务", requires_confirmation=False)
        result = executor.execute(action, {"assignee_ids": ["ou_aaa"]})
        self.assertFalse(result.success)

    def test_assign_task_missing_assignee_ids(self):
        executor = ActionExecutor(self.adapter, auto_confirm=True)
        action = _make_action("assign_task", "分配任务", requires_confirmation=False)
        result = executor.execute(action, {"task_guid": "task_001"})
        self.assertFalse(result.success)

    # ── confirmation gate ───────────────────────────────────────

    def test_requires_confirmation_blocks(self):
        executor = ActionExecutor(self.adapter, auto_confirm=False)
        action = _make_action("create_task", "拟创建任务：测试", requires_confirmation=True)
        result = executor.execute(action, {})
        self.assertFalse(result.success)
        self.assertIn("confirmation", result.error)
        # Verify adapter was NOT called
        self.assertIsNone(self.adapter.last_task_args)

    def test_auto_confirm_bypasses(self):
        executor = ActionExecutor(self.adapter, auto_confirm=True)
        action = _make_action("create_task", "拟创建任务：测试", requires_confirmation=True)
        result = executor.execute(action, {})
        self.assertTrue(result.success)
        self.assertIsNotNone(self.adapter.last_task_args)

    # ── unknown action type ─────────────────────────────────────

    def test_unknown_action_type(self):
        executor = ActionExecutor(self.adapter, auto_confirm=True)
        action = _make_action("garbage_type", "test", requires_confirmation=False)
        result = executor.execute(action, {})
        # sync_more_context and unknown types return success without adapter call
        self.assertTrue(result.success)

    # ── execute_plan ────────────────────────────────────────────

    def test_execute_plan_empty(self):
        executor = ActionExecutor(self.adapter, auto_confirm=True)
        results = executor.execute_plan([], {})
        self.assertEqual(results, [])

    def test_execute_plan_continues_on_blocked(self):
        executor = ActionExecutor(self.adapter, auto_confirm=False)
        action1 = _make_action("create_task", "task 1", requires_confirmation=True)
        action2 = _make_action("create_task", "task 2", requires_confirmation=False)
        results = executor.execute_plan([action1, action2], {})
        self.assertEqual(len(results), 2)
        self.assertFalse(results[0].success)  # blocked
        self.assertTrue(results[1].success)   # executed

    # ── adapter failure ─────────────────────────────────────────

    def test_create_task_adapter_failure(self):
        adapter = MockAdapter(task_success=False)
        executor = ActionExecutor(adapter, auto_confirm=True)
        action = _make_action("create_task", "test", requires_confirmation=False)
        result = executor.execute(action, {})
        self.assertFalse(result.success)


if __name__ == "__main__":
    unittest.main()
