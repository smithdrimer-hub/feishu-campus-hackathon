"""Tests for V1 command safety rules. V1.12 FIX-2: 6 new tests for edge cases."""

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from safety.policy import SafetyError, SafetyPolicy  # noqa: E402


class SafetyPolicyTest(unittest.TestCase):
    """Verify read-only commands are allowed and writes are blocked."""

    def setUp(self) -> None:
        self.policy = SafetyPolicy()

    def test_read_only_command_allowed(self) -> None:
        decision = self.policy.assert_allowed(["im", "+chat-search", "--query", "Memory"])
        self.assertTrue(decision.allowed)

    def test_write_command_blocked_by_default(self) -> None:
        with self.assertRaises(SafetyError):
            self.policy.assert_allowed(["task", "+create", "--summary", "x"])

    def test_docs_create_dry_run_blocked(self) -> None:
        with self.assertRaises(SafetyError):
            self.policy.assert_allowed(["docs", "+create", "--title", "x", "--dry-run"])

    # ── V1.12 FIX-2: 新增 6 个边界测试 ──────────────────────────

    def test_unknown_command_blocked(self) -> None:
        """未注册命令应被拒绝（UNKNOWN → SafetyError）。"""
        with self.assertRaises(SafetyError):
            self.policy.assert_allowed(["im", "+messages-delete", "--message-id", "x"])

    def test_write_with_allow_write_passes(self) -> None:
        """写入命令带 allow_write=True 应放行。"""
        decision = self.policy.assert_allowed(
            ["im", "+messages-send", "--chat-id", "x", "--text", "hi"],
            allow_write=True,
        )
        self.assertTrue(decision.allowed)

    def test_write_without_allow_blocked(self) -> None:
        """写入命令不带 allow_write 应被拒绝。"""
        with self.assertRaises(SafetyError):
            self.policy.assert_allowed(
                ["im", "+messages-send", "--chat-id", "x", "--text", "hi"],
            )

    def test_dry_run_docs_create_blocked(self) -> None:
        """docs +create --dry-run 必须被拒绝（曾经实际创建文档）。"""
        with self.assertRaises(SafetyError):
            self.policy.assert_allowed(
                ["docs", "+create", "--dry-run", "--title", "test"],
            )

    def test_injection_like_pattern_blocked(self) -> None:
        """即使参数含特殊字符，未注册命令仍应被拒绝。"""
        with self.assertRaises(SafetyError):
            self.policy.assert_allowed(["im", "+messages-delete;", "rm", "-rf", "/"])

    def test_pins_write_classification(self) -> None:
        """im pins create 应被分类为写入命令（默认拒绝）。"""
        with self.assertRaises(SafetyError):
            self.policy.assert_allowed(["im", "pins", "create", "--data", '{"message_id":"x"}'])

    def test_adapter_run_enforces_policy(self) -> None:
        """adapter.run 应通过 SafetyPolicy 拦截写入命令。"""
        from adapters.lark_cli_adapter import LarkCliAdapter
        adapter = LarkCliAdapter(policy=self.policy)
        with self.assertRaises(SafetyError):
            adapter.run(["im", "+messages-send", "--chat-id", "x", "--text", "hi"])

    def test_adapter_run_allow_write_passes(self) -> None:
        """adapter.run with allow_write=True 应放行（不实际执行 subprocess 会失败）。"""
        from adapters.lark_cli_adapter import LarkCliAdapter
        adapter = LarkCliAdapter(policy=self.policy)
        # Policy 允许，但 subprocess 会失败（lark-cli 不在测试环境）
        # 我们只验证 policy 不抛异常
        try:
            adapter.run(
                ["im", "+messages-send", "--chat-id", "x", "--text", "hi"],
                allow_write=True,
            )
        except SafetyError:
            self.fail("allow_write=True should bypass safety policy")
        except Exception:
            pass  # subprocess 失败是预期的


class TestAdapterEncoding(unittest.TestCase):
    """V1.12 FIX-5 real: 验证 adapter subprocess 编码安全."""

    def test_run_uses_strict_encoding(self):
        """adapter.run 应优先使用 errors='strict' + UTF-8。"""
        from adapters.lark_cli_adapter import LarkCliAdapter
        import inspect
        source = inspect.getsource(LarkCliAdapter.run)
        self.assertIn('encoding="utf-8"', source)
        self.assertIn('errors="strict"', source)

    def test_run_has_gbk_fallback(self):
        """adapter.run 应有 GBK 回退（UnicodeDecodeError → GBK）。"""
        from adapters.lark_cli_adapter import LarkCliAdapter
        import inspect
        source = inspect.getsource(LarkCliAdapter.run)
        self.assertIn('UnicodeDecodeError', source,
                      "should catch UnicodeDecodeError for GBK fallback")
        self.assertIn('encoding="gbk"', source,
                      "should fall back to GBK encoding")


class TestSafetyBypassScenarios(unittest.TestCase):
    """V1.12 FIX-2 real: 测试真实绕过场景。"""

    def setUp(self):
        from safety.policy import SafetyPolicy
        self.policy = SafetyPolicy()

    def test_param_reorder_bypass_attempt(self):
        """参数重排不能绕过安全检查。"""
        from safety.policy import SafetyError
        # 尝试把 --text 放在命令前面
        with self.assertRaises(SafetyError):
            self.policy.assert_allowed(
                ["im", "+messages-send", "--text", "hi", "--chat-id", "x"]
            )

    def test_extra_spaces_no_bypass(self):
        """多余空格不能绕过前缀匹配。"""
        from safety.policy import SafetyError
        with self.assertRaises(SafetyError):
            self.policy.assert_allowed(
                ["im", "  +messages-send", "--chat-id", "x"]
            )

    def test_case_variation_no_bypass(self):
        """大小写变化不能绕过（飞书命令区分大小写）。"""
        from safety.policy import SafetyError
        # 大小写变化导致 prefix 不匹配 → UNKNOWN → 拒绝
        with self.assertRaises(SafetyError):
            self.policy.assert_allowed(
                ["IM", "+MESSAGES-SEND", "--chat-id", "x"]
            )

    def test_unknown_subcommand_blocked(self):
        """不存在子命令被拒绝。"""
        from safety.policy import SafetyError
        with self.assertRaises(SafetyError):
            self.policy.assert_allowed(["im", "+admin-delete-all"])


if __name__ == "__main__":
    unittest.main()
