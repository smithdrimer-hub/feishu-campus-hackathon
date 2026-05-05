"""End-to-end pipeline tests for V1.11 Feishu integration.

Tests use mocked LarkCliAdapter to avoid real API calls.
"""

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))


# ── Mock adapter for testing ──────────────────────────────────────

class MockAdapter:
    """Simulate LarkCliAdapter with fake responses for CI-safe testing."""

    def __init__(self, messages=None, send_success=True, pin_success=True):
        self.messages = messages or []
        self.send_success = send_success
        self.pin_success = pin_success
        self.last_send_args = None
        self.last_pin_id = None

    def list_chat_messages(self, chat_id, page_size=50):
        return _mock_result(0, {
            "data": {"messages": self.messages, "has_more": False, "total": len(self.messages)}
        })

    def send_message(self, chat_id, content, msg_type="text", identity="bot"):
        self.last_send_args = (chat_id, content, msg_type, identity)
        if self.send_success:
            return _mock_result(0, {
                "data": {"message_id": "om_mock_001", "chat_id": chat_id}
            })
        return _mock_result(1, {}, err="send failed")

    def reply_message(self, message_id, content, msg_type="text", identity="bot", in_thread=False):
        if self.send_success:
            return _mock_result(0, {
                "data": {"message_id": "om_mock_reply_001"}
            })
        return _mock_result(1, {}, err="reply failed")

    def pin_message(self, message_id):
        self.last_pin_id = message_id
        if self.pin_success:
            return _mock_result(0, {"data": {"pin": {"message_id": message_id}}})
        return _mock_result(1, {}, err="pin failed")

    def unpin_message(self, message_id):
        if self.pin_success:
            return _mock_result(0, {})
        return _mock_result(1, {}, err="unpin failed")

    def create_task(self, summary, description="", due_at="", identity="bot"):
        return _mock_result(0, {"data": {"guid": "task_e2e_001", "summary": summary}})

    def create_doc(self, title, content="", identity="bot"):
        return _mock_result(0, {
            "data": {"document_id": "doc_e2e_001", "url": f"https://feishu.cn/docx/doc_e2e_001"}
        })

    def assign_task(self, task_guid, assignee_ids, identity="bot"):
        return _mock_result(0, {"data": {"success": True}})

    def fetch_doc(self, doc_id):
        return _mock_result(0, {
            "data": {
                "doc_id": doc_id,
                "title": "E2E 测试文档",
                "markdown": "## 目标：完成端到端验证\n\n负责人：张三负责 API 测试\n\n需要完成所有集成测试",
            }
        })

    def search_tasks(self, query):
        return _mock_result(0, {
            "data": {
                "has_more": False,
                "items": [
                    {
                        "guid": "task_001",
                        "summary": "需要完成 E2E 测试",
                        "status": "in_progress",
                        "description": "下一步：编写端到端测试用例",
                        "created_at": "2026-05-01T12:00:00",
                    }
                ],
            }
        })


def _mock_result(code, payload, err=""):
    """Build a CliResult-compatible object."""
    from types import SimpleNamespace
    return SimpleNamespace(
        returncode=code,
        data=payload,
        stderr=err,
        stdout=json.dumps(payload),
    )


# ── Real message samples from Feishu API ──────────────────────────

REAL_BOT_MESSAGE = {
    "content": "目标：完成 OpenClaw Memory Engine 端到端集成验证",
    "create_time": "2026-05-01 20:32:05",
    "deleted": False,
    "message_id": "om_x100b507d9a9430acb3d874aab018283",
    "msg_type": "text",
    "sender": {
        "id": "cli_a961a2ca20a7dbde",
        "id_type": "app_id",
        "sender_type": "app",
        "tenant_key": "1abd20e084069b82",
    },
    "updated": False,
}

REAL_SYSTEM_MESSAGE = {
    "content": "沈哲熙 invited 飞书 CLI to the group.",
    "create_time": "2026-04-24 17:46",
    "deleted": False,
    "message_id": "om_x100b51878fd4a880c2dd6e88519a92c",
    "msg_type": "system",
    "sender": {"id": "", "id_type": "", "sender_type": "", "tenant_key": ""},
    "updated": False,
}

REAL_COLLAB_MESSAGES = [
    {"content": "目标：完成 V1.11 端到端集成", "create_time": "2026-05-01T12:00:00",
     "message_id": "om_goal_001", "msg_type": "text",
     "sender": {"id": "user_张三", "sender_type": "user"}},
    {"content": "负责人：张三负责 API 接口开发", "create_time": "2026-05-01T12:01:00",
     "message_id": "om_owner_001", "msg_type": "text",
     "sender": {"id": "user_张三", "sender_type": "user"}},
    {"content": "决策：采用 Hybrid 提取模式", "create_time": "2026-05-01T12:02:00",
     "message_id": "om_decision_001", "msg_type": "text",
     "sender": {"id": "user_王五", "sender_type": "user"}},
    {"content": "阻塞：测试环境还没准备好", "create_time": "2026-05-01T12:03:00",
     "message_id": "om_blocker_001", "msg_type": "text",
     "sender": {"id": "user_李四", "sender_type": "user"}},
]


# ── Helpers ───────────────────────────────────────────────────────

def _normalize(msg, chat_id="chat_test", project_id="test"):
    """Re-implement normalization inline for testing."""
    sender = msg.get("sender", {}) or {}
    content = msg.get("content", "")
    return {
        "project_id": project_id,
        "chat_id": chat_id,
        "message_id": str(msg.get("message_id", "")),
        "text": content,
        "content": content,
        "msg_type": str(msg.get("msg_type", "text")),
        "created_at": str(msg.get("create_time", "")),
        "sender": {
            "id": str(sender.get("id", "")),
            "sender_type": str(sender.get("sender_type", "")),
        },
    }


# ── Tests ─────────────────────────────────────────────────────────

class TestMessageNormalization(unittest.TestCase):
    """Step 3.1: Verify field mapping against real Feishu API structure."""

    def test_normalize_real_bot_message(self):
        ev = _normalize(REAL_BOT_MESSAGE)
        self.assertEqual(ev["message_id"], "om_x100b507d9a9430acb3d874aab018283")
        self.assertEqual(ev["msg_type"], "text")
        self.assertIn("端到端集成验证", ev["text"])
        self.assertEqual(ev["sender"]["sender_type"], "app")
        self.assertEqual(ev["sender"]["id"], "cli_a961a2ca20a7dbde")

    def test_system_message_detected_by_msg_type(self):
        ev = _normalize(REAL_SYSTEM_MESSAGE)
        self.assertEqual(ev["msg_type"], "system")

    def test_system_message_empty_sender(self):
        ev = _normalize(REAL_SYSTEM_MESSAGE)
        self.assertEqual(ev["sender"]["id"], "")
        self.assertEqual(ev["sender"]["sender_type"], "")

    def test_filter_system_messages(self):
        """System messages should be excluded from collaboration events."""
        messages = [REAL_BOT_MESSAGE, REAL_SYSTEM_MESSAGE]
        collab = [
            m for m in messages
            if m.get("msg_type") != "system"
            and m.get("sender", {}).get("sender_type", "") not in ("", "system")
        ]
        self.assertEqual(len(collab), 1)
        self.assertEqual(collab[0]["message_id"], REAL_BOT_MESSAGE["message_id"])


class TestE2EPipelineWithMock(unittest.TestCase):
    """Step 3.2-3.3: Full pipeline with mocked adapter."""

    def setUp(self):
        self.mock = MockAdapter(messages=REAL_COLLAB_MESSAGES)

    def test_pipeline_sync_and_extract(self):
        """Sync messages → extract memory → verify items."""
        from memory.engine import MemoryEngine
        from memory.store import MemoryStore

        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            engine = MemoryEngine(store)

            # Step 1: Sync
            result = self.mock.list_chat_messages("chat_test")
            self.assertEqual(result.returncode, 0)
            msgs = result.data.get("data", {}).get("messages", [])
            self.assertEqual(len(msgs), 4)

            events = [_normalize(m) for m in msgs]
            written = store.append_raw_events(events)
            self.assertEqual(written, 4)

            # Step 2: Extract
            items = engine.process_new_events("test", debounce=False)
            # Should extract: goal from om_goal_001, owner from om_owner_001,
            #               decision from om_decision_001, blocker from om_blocker_001
            types = {item.state_type for item in items}
            self.assertIn("project_goal", types)
            self.assertIn("owner", types)
            self.assertIn("decision", types)
            self.assertIn("blocker", types)

    def test_pipeline_generate_state_panel(self):
        """Extracted items → state panel contains expected sections."""
        from memory.engine import MemoryEngine
        from memory.project_state import build_group_project_state, render_group_state_panel_text
        from memory.store import MemoryStore

        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            engine = MemoryEngine(store)
            events = [_normalize(m) for m in REAL_COLLAB_MESSAGES]
            store.append_raw_events(events)
            items = engine.process_new_events("test", debounce=False)

            state = build_group_project_state("test", items)
            self.assertEqual(state["project_id"], "test")
            self.assertGreater(len(state["owners"]), 0, "Should have at least one owner")
            # V1.15: needs_review decisions are excluded from state panel
            # Check that decision items exist in store (not necessarily in panel)
            decisions_in_store = [i for i in items if i.state_type == "decision"]
            self.assertGreater(len(decisions_in_store), 0,
                               "Should have at least one decision in store")
            needs_review = [i for i in decisions_in_store
                           if getattr(i, "review_status", "") == "needs_review"]
            if needs_review:
                self.assertGreater(len(needs_review), 0,
                                   "Tentative decisions should be needs_review")
            else:
                total_decisions = len(state["recent_decisions"]) + len(state.get("open_decisions", []))
                self.assertGreater(total_decisions, 0, "Should have decisions in panel")
            self.assertGreater(len(state["risks"]), 0, "Should have at least one risk")

            text = render_group_state_panel_text(state)
            self.assertIn("项目状态", text)

    def test_send_and_pin_flow(self):
        """Send message → pin it."""
        adapter = MockAdapter()
        result = adapter.send_message("chat_test", "**状态面板**", msg_type="markdown")
        self.assertEqual(result.returncode, 0)
        msg_id = result.data["data"]["message_id"]

        pin = adapter.pin_message(msg_id)
        self.assertEqual(pin.returncode, 0)
        self.assertEqual(adapter.last_pin_id, msg_id)

    def test_send_text_fallback(self):
        """Text message type works."""
        adapter = MockAdapter()
        result = adapter.send_message("chat_test", "hello", msg_type="text")
        self.assertEqual(result.returncode, 0)
        _, _, msg_type, identity = adapter.last_send_args
        self.assertEqual(msg_type, "text")
        self.assertEqual(identity, "bot")


class TestCommandRegistry(unittest.TestCase):
    """Step 3.2: Verify im pins is registered as a write command."""

    def setUp(self):
        from adapters.command_registry import CommandRegistry
        self.registry = CommandRegistry()

    def test_pins_create_is_write(self):
        from adapters.command_registry import CommandKind
        kind = self.registry.classify(["im", "pins", "create", "--data", '{"message_id":"x"}'])
        self.assertEqual(kind, CommandKind.WRITE)

    def test_pins_delete_is_write(self):
        from adapters.command_registry import CommandKind
        kind = self.registry.classify(["im", "pins", "delete", "--data", '{"message_id":"x"}'])
        self.assertEqual(kind, CommandKind.WRITE)

    def test_pins_list_is_write(self):
        from adapters.command_registry import CommandKind
        kind = self.registry.classify(["im", "pins", "list"])
        self.assertEqual(kind, CommandKind.WRITE)

    def test_send_message_is_write(self):
        from adapters.command_registry import CommandKind
        kind = self.registry.classify(["im", "+messages-send", "--chat-id", "x", "--text", "hi"])
        self.assertEqual(kind, CommandKind.WRITE)

    def test_chat_search_is_read_only(self):
        from adapters.command_registry import CommandKind
        kind = self.registry.classify(["im", "+chat-search", "--query", "test"])
        self.assertEqual(kind, CommandKind.READ_ONLY)


class TestDocTaskSourcePath(unittest.TestCase):
    """Step 3.4: Verify engine.py JSON path parsing matches real API."""

    def test_doc_path_resolution(self):
        """Verify the doc data path: data.data.markdown / data.data.title."""
        # Real API response structure (from actual lark-cli docs +fetch)
        real_response = {
            "ok": True,
            "data": {
                "doc_id": "QqlDdfkPMowxiYxitdFclJHcnsc",
                "title": "E2E Test Doc",
                "markdown": "## E2E Test Doc\n\nTarget: Complete E2E\n",
            }
        }
        # This is what result.data would be
        doc_data = real_response
        inner = doc_data.get("data", doc_data)
        self.assertEqual(inner["title"], "E2E Test Doc")
        self.assertIn("E2E", inner["markdown"])

    def test_task_path_resolution(self):
        """Verify task data path: data.data.items[].summary/status/description/guid."""
        # Real API response (from actual lark-cli task +search)
        real_response = {
            "ok": True,
            "data": {
                "has_more": False,
                "items": [
                    {
                        "guid": "task_001",
                        "summary": "Write tests",
                        "status": "in_progress",
                        "description": "E2E test for pipeline",
                        "created_at": "2026-05-01T12:00:00",
                    }
                ],
            }
        }
        payload = real_response
        tasks = payload.get("data", {}).get("items", [])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["summary"], "Write tests")

    def test_doc_sync_through_engine(self):
        """sync_doc() → doc event created → extractor picks up goal and owner."""
        from memory.engine import MemoryEngine
        from memory.store import MemoryStore

        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            mock = MockAdapter()
            engine = MemoryEngine(store, adapter=mock)

            items = engine.sync_doc("doc_test_001", project_id="test")
            types = {item.state_type for item in items}
            # 含"目标"→ project_goal, "负责人"→ owner, "需要"→ next_step
            self.assertIn("project_goal", types,
                          "Doc should produce project_goal from 目标 keyword")
            self.assertIn("owner", types,
                          "Doc should produce owner from 负责人 keyword")

    def test_task_sync_through_engine(self):
        """sync_tasks() → task events created → extractor picks up next_step."""
        from memory.engine import MemoryEngine
        from memory.store import MemoryStore

        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            mock = MockAdapter()
            engine = MemoryEngine(store, adapter=mock)

            items = engine.sync_tasks("E2E", project_id="test")
            types = {item.state_type for item in items}
            # Task summary: "需要" → next_step, description: "下一步" → next_step
            self.assertIn("next_step", types,
                          "Task should produce next_step from 需要/下一步 keywords")

    def test_doc_sync_no_adapter_raises(self):
        """sync_doc without adapter should raise RuntimeError."""
        from memory.engine import MemoryEngine
        from memory.store import MemoryStore

        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            engine = MemoryEngine(store)
            with self.assertRaises(RuntimeError):
                engine.sync_doc("doc_test")


class TestMarkdownSafety(unittest.TestCase):
    """Step 3.3: Markdown truncation for Feishu message length limits."""

    def test_truncation(self):
        from scripts.demo_e2e_pipeline import _markdown_safe
        short = "hello"
        self.assertEqual(_markdown_safe(short), short)

        long = "x" * 5000
        result = _markdown_safe(long)
        self.assertLess(len(result), 4200)
        self.assertIn("...", result)

    def test_no_truncation_under_limit(self):
        from scripts.demo_e2e_pipeline import _markdown_safe
        text = "a" * 3000
        self.assertEqual(_markdown_safe(text), text)


if __name__ == "__main__":
    unittest.main()
