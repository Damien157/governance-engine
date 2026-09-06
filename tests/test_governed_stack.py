"""Tests for the unified GovernedStack composition layer."""

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
from governed_stack import GovernedStack  # noqa: E402


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


class TestGovernedStack(unittest.IsolatedAsyncioTestCase):
    """Share one RSA key — keygen is slow (same pattern as hais tests)."""

    @classmethod
    def setUpClass(cls):
        cls._t0 = time.time()
        cls.shared_crypto = CryptoEngine(private_key_path=None)

    @classmethod
    def tearDownClass(cls):
        elapsed = time.time() - cls._t0
        print(f"\n  [TestGovernedStack] class total: {elapsed:.2f}s")

    def make_stack(self, **cfg_overrides) -> GovernedStack:
        db_path = tmp_db()
        self.addCleanup(lambda: os.path.exists(db_path) and os.remove(db_path))
        key_path = db_path + ".pem"
        self.addCleanup(lambda: os.path.exists(key_path) and os.remove(key_path))
        cfg = {
            "db_path": db_path,
            "signing_key_path": key_path,
            "log_level": 40,
        }
        cfg.update(cfg_overrides)
        stack = GovernedStack(config=cfg, crypto=self.shared_crypto)
        self.addCleanup(lambda s=stack: _close_stack_storage(s))
        return stack

    async def test_benign_query_allows(self):
        stack = self.make_stack()
        token = stack.issue_token("alice", "user")
        env = await stack.govern(
            {"action": "query", "payload": {"text": "hello there"}},
            token,
        )
        self.assertEqual(env["decision"], "ALLOW")
        self.assertIn("hais", env)
        self.assertIn("haven2", env)
        self.assertIsNotNone(env["hais"]["cap"])
        self.assertGreaterEqual(env["hais"]["cap"], 0.25)

    async def test_pii_email_blocks(self):
        stack = self.make_stack()
        token = stack.issue_token("alice", "user")
        env = await stack.govern(
            {
                "action": "query",
                "payload": {"text": "reach me at bob@example.com"},
            },
            token,
        )
        self.assertEqual(env["decision"], "BLOCK")
        self.assertTrue(
            any("pii" in r.lower() or "email" in r.lower() for r in env["reasons"])
            or env["decision"] == "BLOCK"
        )

    async def test_hais_override_blocks_clean_text(self):
        """High difficulty + stress/anomaly/drift → cap < 0.25 → not ALLOW."""
        stack = self.make_stack()
        token = stack.issue_token("alice", "user")
        env = await stack.govern(
            {
                "action": "query",
                "payload": {"text": "perfectly clean text"},
                "difficulty": 1.0,
                "telemetry": {"stress": 1.0, "anomaly": 1.0, "drift": 1.0},
            },
            token,
        )
        self.assertNotEqual(env["decision"], "ALLOW")
        self.assertLess(env["hais"]["cap"], 0.25)
        self.assertIn("hais_capability_cap", env["reasons"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
