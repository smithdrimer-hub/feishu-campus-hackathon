"""Tests for V1 command safety rules."""

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from safety.policy import SafetyError, SafetyPolicy  # noqa: E402


class SafetyPolicyTest(unittest.TestCase):
    """Verify read-only commands are allowed and writes are blocked."""

    def setUp(self) -> None:
        """Create a fresh safety policy for each test."""
        self.policy = SafetyPolicy()

    def test_read_only_command_allowed(self) -> None:
        """Read-only lark-cli commands should be auto-allowed."""
        decision = self.policy.assert_allowed(["im", "+chat-search", "--query", "Memory"])
        self.assertTrue(decision.allowed)

    def test_write_command_blocked_by_default(self) -> None:
        """Write commands should be blocked unless an explicit override is passed."""
        with self.assertRaises(SafetyError):
            self.policy.assert_allowed(["task", "+create", "--summary", "x"])

    def test_docs_create_dry_run_blocked(self) -> None:
        """docs +create --dry-run should be blocked because it is not a safe preview."""
        with self.assertRaises(SafetyError):
            self.policy.assert_allowed(["docs", "+create", "--title", "x", "--dry-run"])


if __name__ == "__main__":
    unittest.main()
