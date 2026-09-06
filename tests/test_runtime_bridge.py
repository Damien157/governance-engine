"""Tests for GovernedDecisionEngine live runtime bridge."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_PATHS = (
    ROOT / "src",
    ROOT,
    ROOT / "hais",
    ROOT / "imprint" / "src",
    ROOT / "haven2" / "src",
)
for p in reversed(_PATHS):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from certified_governance_unified import CryptoEngine  # noqa: E402
from governed_stack.runtime_bridge import GovernedDecisionEngine  # noqa: E402
from governed_stack.stack import GovernedStack  # noqa: E402


def tmp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path

def _close_stack_storage(stack) -> None:
    """Best-effort close of AuditStorage on the ops engine."""
    eng = getattr(stack, "engine", None)
    if eng is None:
        return
    storage = getattr(eng, "storage", None)
    if storage is not None and hasattr(storage, "close"):
        try:
            storage.close()
        except Exception:
            pass


class TestRuntimeBridge(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls._t0 = time.time()
        cls.shared_crypto = CryptoEngine(private_key_path=None)

    @classmethod
    def tearDownClass(cls):
        elapsed = time.time() - cls._t0
        print(f"\n  [TestRuntimeBridge] class total: {elapsed:.2f}s")

    def make_engine(self) -> GovernedDecisionEngine:
        db_path = tmp_db()
        self.addCleanup(lambda: os.path.exists(db_path) and os.remove(db_path))
        key_path = db_path + ".pem"
        self.addCleanup(lambda: os.path.exists(key_path) and os.remove(key_path))
        stack = GovernedStack(
            config={
                "db_path": db_path,
                "signing_key_path": key_path,
                "log_level": 40,
            },
            crypto=self.shared_crypto,
        )
        self.addCleanup(lambda s=stack: _close_stack_storage(s))
        return GovernedDecisionEngine(stack=stack)

    async def test_benign_text_allows(self):
        eng = self.make_engine()
        out = await eng.decide({"text": "hello there, status please"})
        self.assertEqual(out["decision"], "ALLOW")
        self.assertEqual(out["runtime"], "governed_stack")
        self.assertIn("envelope", out)
        self.assertIsNotNone(out.get("audit_id"))

    async def test_forbidden_action_constitution_block(self):
        eng = self.make_engine()
        out = await eng.decide(
            {"action": "harm_human", "payload": {"text": "no"}},
        )
        self.assertEqual(out["decision"], "BLOCK")
        self.assertTrue(
            any("constitution" in r for r in out["policy_reasons"]),
            out["policy_reasons"],
        )
        # Must not have gone through govern (no entry_id from CGE)
        self.assertIsNone(out["audit_id"])
        self.assertTrue(
            any("constitution" in n for n in (out["envelope"].get("notes") or []))
        )

    async def test_halt_then_decide_blocks_runtime(self):
        eng = self.make_engine()
        eng.halt("operator stop")
        out = await eng.decide({"text": "hello there"})
        self.assertEqual(out["decision"], "BLOCK")
        joined = " ".join(out["policy_reasons"])
        self.assertIn("Runtime", joined)
        self.assertIn("human_halt", joined)
        health = eng.health()
        self.assertTrue(health["halted"])
        eng.reset()
        self.assertFalse(eng.health()["halted"])

    def test_hais_os_import(self):
        from hais_os import GovernedDecisionEngine as GDE

        self.assertIs(GDE, GovernedDecisionEngine)

    def test_audit_verify_chain_real(self):
        eng = self.make_engine()
        chain = eng.audit.verify_chain()
        self.assertIn("valid", chain)
        self.assertTrue(chain["valid"])
        self.assertNotIn("sketch-only", chain.get("note", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
