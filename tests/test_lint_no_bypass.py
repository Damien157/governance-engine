"""Unit tests for scripts/lint_no_bypass.py (no-bypass connector lint)."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINT_PATH = ROOT / "scripts" / "lint_no_bypass.py"


def _load_lint_module():
    spec = importlib.util.spec_from_file_location("lint_no_bypass", LINT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lint_no_bypass"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestLintNoBypass(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lint = _load_lint_module()

    def test_self_check_passes(self):
        self.assertEqual(self.lint.self_check(), 0)

    def test_allowlist(self):
        self.assertTrue(self.lint.is_allowlisted("tests/test_foo.py"))
        self.assertTrue(self.lint.is_allowlisted("src/governed_stack/connectors.py"))
        self.assertTrue(self.lint.is_allowlisted("src/governed_stack/action_bus.py"))
        self.assertTrue(self.lint.is_allowlisted("docs/AGENT_MANDATES.md"))
        self.assertFalse(self.lint.is_allowlisted("src/governed_stack/mail.py"))
        self.assertFalse(self.lint.is_allowlisted("scripts/governed_mail.py"))

    def test_detects_forbidden_call(self):
        text = "def go():\n    send_message(to='x')\n"
        hits = self.lint._scan_text("src/governed_stack/evil.py", text)
        hits += self.lint._scan_ast("src/governed_stack/evil.py", text)
        self.assertTrue(any("send_message" in h for h in hits), hits)

    def test_detects_gmail_chain(self):
        text = 'x = gmail.users().messages().send(userId="me", body={})\n'
        hits = self.lint._scan_text("src/governed_stack/evil.py", text)
        self.assertTrue(any("gmail.users().messages().send" in h for h in hits), hits)

    def test_clean_file_ok(self):
        text = "def go():\n    return GovernedActionBus()\n"
        self.assertEqual(self.lint._scan_text("src/governed_stack/clean.py", text), [])
        self.assertEqual(self.lint._scan_ast("src/governed_stack/clean.py", text), [])

    def test_repo_scan_clean(self):
        """Current repo must pass the linter (no real bypasses)."""
        paths = self.lint.iter_scan_files(ROOT)
        violations = self.lint.lint_paths(paths)
        self.assertEqual(violations, [], violations)

    def test_temp_bad_file_caught_when_not_allowlisted(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bypass.py"
            bad.write_text("send_message(x)\n", encoding="utf-8")
            # Simulate scanning as if under src/governed_stack
            text = bad.read_text(encoding="utf-8")
            hits = self.lint._scan_text("src/governed_stack/bypass.py", text)
            self.assertTrue(hits)


if __name__ == "__main__":
    unittest.main()
