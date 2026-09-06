"""CLI / helpers for P1 REVIEW operator tooling (scripts/review_ops.py)."""

from __future__ import annotations

import io
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_PATHS = (
    ROOT / "src",
    ROOT,  # certified_governance_unified.py ahead of hais/
    ROOT / "hais",
    ROOT / "imprint" / "src",
    ROOT / "haven2" / "src",
    ROOT / "scripts",
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

import review_ops  # noqa: E402


def tmp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _close_stack_storage(stack) -> None:
    eng = getattr(stack, "engine", None)
    if eng is None:
        return
    storage = getattr(eng, "storage", None)
    if storage is not None and hasattr(storage, "close"):
        try:
            storage.close()
        except Exception:
            pass


class TestReviewOps(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls._t0 = time.time()
        cls.shared_crypto = CryptoEngine(private_key_path=None)

    @classmethod
    def tearDownClass(cls):
        elapsed = time.time() - cls._t0
        print(f"\n  [TestReviewOps] class total: {elapsed:.2f}s")

    def make_stack_with_review_probe(self) -> GovernedStack:
        """Temp DB + REVIEW probe policy (same pattern as test_review_path)."""
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
        stack.engine.policy_engine = PolicyEngine(
            spec=PolicySpec(
                pii_rules=list(base.pii_rules),
                banned_terms=list(base.banned_terms) + [probe],
                action_rules=list(base.action_rules),
            )
        )
        self._db_path = db_path
        self._key_path = key_path
        return stack

    async def test_list_sees_entry_then_approve_clears(self):
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
        self.assertIsNotNone(env["entry_id"])

        eng = stack.engine
        pending = eng.list_pending_reviews()
        self.assertGreaterEqual(len(pending), 1)
        entry_id = pending[0]["entry_id"]
        self.assertEqual(entry_id, env["entry_id"])

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = review_ops.cmd_list(eng, as_json=False)
        self.assertEqual(rc, 0)
        self.assertIn(entry_id, buf.getvalue())

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = review_ops.cmd_approve(
                eng, entry_id, resolved_by="damien", notes="ops approve"
            )
        self.assertEqual(rc, 0)
        self.assertIn("ALLOW", buf.getvalue())
        self.assertEqual(len(eng.list_pending_reviews()), 0)

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = review_ops.cmd_list(eng, as_json=True)
        self.assertEqual(rc, 0)
        self.assertIn('"count": 0', buf.getvalue())

    async def test_deny_path_block_resolution(self):
        stack = self.make_stack_with_review_probe()
        token = stack.issue_token("alice", "user")
        env = await stack.govern(
            {
                "action": "query",
                "payload": {"text": "deny me GOVERN_REVIEW_PROBE please"},
            },
            token,
        )
        self.assertEqual(env["decision"], "REVIEW")
        entry_id = env["entry_id"]

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = review_ops.cmd_deny(
                stack.engine, entry_id, resolved_by="damien", notes="ops deny"
            )
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("BLOCK", out)
        self.assertEqual(len(stack.engine.list_pending_reviews()), 0)

    async def test_voucher_recoverable_and_missing(self):
        stack = self.make_stack_with_review_probe()
        token = stack.issue_token("alice", "user")
        env = await stack.govern(
            {
                "action": "query",
                "payload": {"text": "voucher GOVERN_REVIEW_PROBE path"},
            },
            token,
        )
        self.assertEqual(env["decision"], "REVIEW")
        entry_id = env["entry_id"]

        intent = review_ops.recover_intent_from_entry(stack.engine, entry_id)
        self.assertIsNotNone(intent)

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = review_ops.cmd_voucher(
                stack.engine, entry_id, resolved_by="damien"
            )
        self.assertEqual(rc, 0)
        self.assertTrue(out.getvalue().strip())  # JWT printed
        self.assertIn("single-use", err.getvalue().lower())

        err2 = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err2):
            rc = review_ops.cmd_voucher(
                stack.engine, "does-not-exist-entry", resolved_by="damien"
            )
        self.assertNotEqual(rc, 0)
        self.assertIn("not recoverable", err2.getvalue().lower())

    def test_bootstrap_engine_against_temp_db(self):
        db_path = tmp_db()
        self.addCleanup(lambda: os.path.exists(db_path) and os.remove(db_path))
        key_path = db_path + ".pem"
        Path(key_path).write_bytes(self.shared_crypto.private_pem)
        self.addCleanup(lambda: os.path.exists(key_path) and os.remove(key_path))
        # Touch empty schema by constructing engine once
        eng = review_ops.bootstrap_engine(Path(db_path), key_path=Path(key_path))
        self.addCleanup(lambda: eng.storage.close())
        self.assertEqual(eng.list_pending_reviews(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
