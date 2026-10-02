"""Tests for GovernedTool + bus tool content binding (0.7)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from certified_governance_unified import CryptoEngine
from governed_stack import GovernedActionBus, GovernedStack, GovernedTool, SendBlocked
from governed_stack.connectors import (
    ContentBindingError,
    MockToolInvoker,
    bus_tool_side_effect,
)


def tmp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


class TestGovernedTool(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared_crypto = CryptoEngine(private_key_path=None)

    def make_tool(self) -> GovernedTool:
        db = tmp_db()
        self.addCleanup(lambda: os.path.exists(db) and os.remove(db))
        key = db + ".pem"
        self.addCleanup(lambda: os.path.exists(key) and os.remove(key))
        stack = GovernedStack(
            config={"db_path": db, "signing_key_path": key, "log_level": 40},
            crypto=self.shared_crypto,
        )
        return GovernedTool(stack=stack)

    def make_bus(self) -> GovernedActionBus:
        tool = self.make_tool()
        return GovernedActionBus(stack=tool.stack, tool=tool)

    async def test_check_echoes_intent_envelope(self):
        gt = self.make_tool()
        intent = {
            "action": "run_algorithm",
            "payload": {"purpose": "unit test of governed tool path"},
        }
        out = await gt.check(intent=intent)
        self.assertIn(out["decision"], ("ALLOW", "REVIEW", "BLOCK"))
        if out["ok"]:
            self.assertEqual(out["action"], "run_algorithm")
            self.assertIsInstance(out["intent"], dict)
            self.assertEqual(out["intent"]["action"], "run_algorithm")
            self.assertTrue(out["intent_sha256"])

    async def test_bus_tool_side_effect_uses_envelope(self):
        bus = self.make_bus()
        inv = MockToolInvoker()
        intent = {
            "action": "run_algorithm",
            "payload": {"purpose": "benign algorithm check for bus tool"},
        }
        # May ALLOW or REVIEW depending on stack policy — only assert binding on ALLOW
        try:
            out = await bus.execute(
                "tool",
                intent=intent,
                side_effect=bus_tool_side_effect(inv),
            )
        except SendBlocked as exc:
            self.assertFalse(exc.result.get("ok"))
            self.assertEqual(inv.calls, [])
            return
        self.assertTrue(out.get("executed"))
        self.assertEqual(len(inv.calls), 1)
        self.assertEqual(inv.calls[0]["action"], "run_algorithm")
        self.assertEqual(inv.calls[0]["intent_sha256"], out["intent_sha256"])

    def test_factory_rejects_closed_over_intent(self):
        inv = MockToolInvoker()
        with self.assertRaises(TypeError):
            bus_tool_side_effect(inv, intent={"action": "x"})  # type: ignore[call-arg]

    async def test_sha_mismatch_refuses(self):
        inv = MockToolInvoker()
        se = bus_tool_side_effect(inv)
        with self.assertRaises(ContentBindingError):
            se(
                {
                    "action": "run_algorithm",
                    "intent": {"action": "run_algorithm"},
                    "intent_sha256": "0" * 64,
                }
            )
        self.assertEqual(inv.calls, [])


if __name__ == "__main__":
    unittest.main()
