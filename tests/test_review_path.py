"""Exercise REVIEW: inject a REVIEW policy rule, pending queue, resolve approve."""

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
    ROOT,  # certified_governance_unified.py ahead of hais/
    ROOT / "hais",
    ROOT / "imprint" / "src",
    ROOT / "haven2" / "src",
)
for p in reversed(_PATHS):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from certified_governance_unified import (  # noqa: E402
    CryptoEngine,
    PolicyEngine,
    PolicyRule,
    PolicySpec,
)
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


class TestReviewPath(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls._t0 = time.time()
        cls.shared_crypto = CryptoEngine(private_key_path=None)

    @classmethod
    def tearDownClass(cls):
        elapsed = time.time() - cls._t0
        print(f"\n  [TestReviewPath] class total: {elapsed:.2f}s")

    def make_stack_with_review_probe(self) -> GovernedStack:
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
        base = PolicyEngine._default_spec()
        probe = PolicyRule(
            id="review_probe",
            type="banned",
            pattern=r"\bGOVERN_REVIEW_PROBE\b",
            action="REVIEW",
            reason="probe:review",
        )
        # Throwaway policy on this temp-db stack only — not durable mail audit.
        stack.engine.policy_engine = PolicyEngine(
            spec=PolicySpec(
                pii_rules=list(base.pii_rules),
                banned_terms=list(base.banned_terms) + [probe],
                action_rules=list(base.action_rules),
            )
        )
        return stack

    async def test_review_then_approve(self):
        stack = self.make_stack_with_review_probe()
        token = stack.issue_token("alice", "user")
        env = await stack.govern(
            {
                "action": "query",
                "payload": {"text": "token GOVERN_REVIEW_PROBE for human eyes"},
            },
            token,
        )
        self.assertEqual(env["decision"], "REVIEW")
        self.assertTrue(
            any("probe:review" in r or "review" in r.lower() for r in (env["reasons"] or []))
            or env["decision"] == "REVIEW"
        )
        self.assertIsNotNone(env["entry_id"])

        pending = stack.engine.list_pending_reviews()
        self.assertGreaterEqual(len(pending), 1)
        entry_id = pending[0]["entry_id"]
        self.assertEqual(entry_id, env["entry_id"])

        resolution = stack.engine.resolve_review(
            entry_id, resolved_by="reviewer1", approve=True, notes="ok for test"
        )
        self.assertEqual(resolution["final_decision"], "ALLOW")
        self.assertEqual(len(stack.engine.list_pending_reviews()), 0)
        self.assertTrue(stack.engine.storage.verify_chain()["valid"])

    async def test_clean_text_still_allows_under_probe_spec(self):
        stack = self.make_stack_with_review_probe()
        token = stack.issue_token("alice", "user")
        env = await stack.govern(
            {"action": "query", "payload": {"text": "hello there"}},
            token,
        )
        self.assertEqual(env["decision"], "ALLOW")


if __name__ == "__main__":
    unittest.main(verbosity=2)
