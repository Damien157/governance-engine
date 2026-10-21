"""GovernedTool (0.7) decision paths: ALLOW / BLOCK / REVIEW, sync wrappers, hashing.

No network, no real side effects: every stack uses a throwaway temp DB.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from certified_governance_unified import (  # noqa: E402
    CryptoEngine,
    PolicyEngine,
    PolicyRule,
    PolicySpec,
)
from governed_stack import GovernedStack, GovernedTool, SendBlocked  # noqa: E402
from governed_stack.tool import ToolBlocked, intent_sha256  # noqa: E402

BENIGN = {"action": "run_algorithm", "payload": {"purpose": "unit test of tool gate"}}


def _close(stack) -> None:
    eng = getattr(stack, "engine", None)
    st = getattr(eng, "storage", None)
    for name in ("close",):
        fn = getattr(st, name, None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.crypto = CryptoEngine(private_key_path=None)

    def make_tool(self, review_probe: bool = False) -> GovernedTool:
        d = tempfile.mkdtemp()
        stack = GovernedStack(
            config={"db_path": d + "/a.db", "signing_key_path": d + "/k.pem", "log_level": 40},
            crypto=self.crypto,
        )
        self.addCleanup(lambda: _close(stack))
        if review_probe:
            base = PolicyEngine._default_spec()
            stack.engine.policy_engine = PolicyEngine(
                spec=PolicySpec(
                    pii_rules=list(base.pii_rules),
                    banned_terms=list(base.banned_terms)
                    + [
                        PolicyRule(
                            id="tool_review_probe",
                            type="banned",
                            pattern=r"\bTOOL_REVIEW_PROBE\b",
                            action="REVIEW",
                            reason="probe:review",
                        )
                    ],
                    action_rules=list(base.action_rules),
                )
            )
        return GovernedTool(stack=stack)


class TestIntentHash(unittest.TestCase):
    def test_hash_is_key_order_independent(self):
        a = {"action": "x", "payload": {"a": 1, "b": 2}}
        b = {"payload": {"b": 2, "a": 1}, "action": "x"}
        self.assertEqual(intent_sha256(a), intent_sha256(b))
        self.assertEqual(len(intent_sha256(a)), 64)

    def test_hash_changes_with_args(self):
        self.assertNotEqual(
            intent_sha256({"action": "x", "payload": {"a": 1}}),
            intent_sha256({"action": "x", "payload": {"a": 2}}),
        )

    def test_toolblocked_alias(self):
        self.assertIs(ToolBlocked, SendBlocked)


class TestToolPaths(_Base):
    def test_allow_sync_echoes_and_hashes_intent(self):
        out = self.make_tool().check_sync(intent=BENIGN)
        self.assertEqual(out["decision"], "ALLOW")
        self.assertTrue(out["ok"])
        self.assertFalse(out["blocked_tool"])
        self.assertEqual(out["action"], "run_algorithm")
        self.assertEqual(out["intent_sha256"], intent_sha256(out["intent"]))
        self.assertIsNotNone(out["entry_id"])

    def test_pii_blocks_and_require_allow_raises(self):
        gt = self.make_tool()
        intent = {"action": "query", "payload": {"text": "reach me at bob@example.com"}}
        out = gt.check_sync(intent=intent)
        self.assertEqual(out["decision"], "BLOCK")
        self.assertTrue(out["blocked_tool"])
        with self.assertRaises(SendBlocked):
            gt.require_allow_sync(intent=intent)

    def test_invalid_intent_blocks_without_govern(self):
        gt = self.make_tool()
        for bad in ({"action": ""}, {"foo": 1}):
            out = gt.check_sync(intent=bad)
            self.assertEqual(out["decision"], "BLOCK")
            self.assertEqual(out["error_code"], "GOV_INTENT_INVALID")
            self.assertIsNone(out["entry_id"])
            self.assertIsNone(out["intent_sha256"])

    def test_non_dict_intent_type_error(self):
        with self.assertRaises(TypeError):
            self.make_tool().check_sync(intent=["run_algorithm"])  # type: ignore[arg-type]

    def test_review_is_not_ok(self):
        gt = self.make_tool(review_probe=True)
        intent = {"action": "query", "payload": {"text": "TOOL_REVIEW_PROBE please"}}
        out = gt.check_sync(intent=intent)
        self.assertEqual(out["decision"], "REVIEW")
        self.assertFalse(out["ok"])
        with self.assertRaises(SendBlocked):
            gt.require_allow_sync(intent=intent)

    def test_token_cached_per_user_role(self):
        gt = self.make_tool()
        self.assertEqual(gt.issue_token("a"), gt.issue_token("a"))
        self.assertNotEqual(gt.issue_token("a"), gt.issue_token("b"))

    def test_check_sync_inside_running_loop(self):
        gt = self.make_tool()

        async def inner():
            return gt.check_sync(intent=BENIGN)

        self.assertEqual(asyncio.run(inner())["decision"], "ALLOW")

    def test_require_allow_async_returns_on_allow(self):
        out = asyncio.run(self.make_tool().require_allow(intent=BENIGN))
        self.assertTrue(out["ok"])


if __name__ == "__main__":
    unittest.main()
