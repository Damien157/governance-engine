"""Tests for the GovernedAlgorithm run/deploy adapter."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
from governed_stack import (  # noqa: E402
    AlgorithmBlocked,
    GovernedAlgorithm,
    GovernedStack,
    IntentValidationError,
)
from governed_stack.algorithm import intent_for_scan  # noqa: E402
from governed_stack.contracts import (  # noqa: E402
    ALGORITHM_SCAN_FORBIDDEN,
    validate_algorithm_scan,
)


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


class TestGovernedAlgorithm(unittest.IsolatedAsyncioTestCase):
    """Share one RSA key — keygen is slow (same pattern as mail tests)."""

    @classmethod
    def setUpClass(cls):
        cls._t0 = time.time()
        cls.shared_crypto = CryptoEngine(private_key_path=None)

    @classmethod
    def tearDownClass(cls):
        elapsed = time.time() - cls._t0
        print(f"\n  [TestGovernedAlgorithm] class total: {elapsed:.2f}s")

    def make_algo(self) -> GovernedAlgorithm:
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
        return GovernedAlgorithm(stack=stack)

    async def test_clean_run_allows(self):
        algo = self.make_algo()
        result = await algo.check(
            purpose="batch_dedupe",
            summary="Nightly anonymized id dedupe",
            time_cost="O(n log n)",
            space_cost="O(n)",
            energy_cost="low",
            speedup="~2x",
            risk_notes="read-only replica",
            security_margin="standard",
        )
        self.assertEqual(result["decision"], "ALLOW")
        self.assertTrue(result["ok"])
        self.assertFalse(result["blocked_run"])
        self.assertEqual(result["purpose"], "batch_dedupe")
        self.assertEqual(result["cost"]["time"], "O(n log n)")
        self.assertEqual(result["cost"]["speedup"], "~2x")
        self.assertEqual(result["risk"]["notes"], "read-only replica")
        self.assertIsNotNone(result.get("entry_id"))

    async def test_require_allow_passes_clean(self):
        algo = self.make_algo()
        result = await algo.require_allow(
            purpose="sort_index",
            summary="Build secondary index",
            time_cost=1.5,
            energy_cost=0.2,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["decision"], "ALLOW")

    async def test_require_allow_raises_on_block(self):
        algo = self.make_algo()
        # Force BLOCK via banned term in scanned purpose/summary (PolicyEngine).
        with self.assertRaises(AlgorithmBlocked) as ctx:
            await algo.require_allow(
                purpose="rotate_password",
                summary="Here is your password for the portal",
                risk_notes="do not do this",
            )
        self.assertIsInstance(ctx.exception, PermissionError)
        self.assertEqual(ctx.exception.result["decision"], "BLOCK")

    async def test_require_allow_raises_on_forced_non_allow(self):
        """Mock govern to REVIEW so require_allow cannot be ignored via ok."""
        algo = self.make_algo()
        fake = {
            "decision": "REVIEW",
            "reasons": ["forced_review"],
            "entry_id": "test-entry",
            "hais": {},
            "haven2": {},
        }
        with patch.object(algo.stack, "govern", new=AsyncMock(return_value=fake)):
            with self.assertRaises(AlgorithmBlocked) as ctx:
                await algo.require_allow(
                    purpose="needs_review",
                    summary="human gate",
                )
        self.assertEqual(ctx.exception.result["decision"], "REVIEW")

    def test_check_sync_allows(self):
        algo = self.make_algo()
        result = algo.check_sync(
            purpose="compress_logs",
            summary="Gzip rotated logs",
            time_cost="O(n)",
            energy_cost="low",
        )
        self.assertEqual(result["decision"], "ALLOW")
        self.assertTrue(result["ok"])

    def test_forbidden_secret_keys_rejected(self):
        for key in sorted(ALGORITHM_SCAN_FORBIDDEN):
            with self.assertRaises(IntentValidationError) as ctx:
                validate_algorithm_scan(
                    purpose="ok",
                    summary="clean",
                    **{key: "should-not-pass"},
                )
            self.assertIn(f"scan_intent_forbids:{key}", str(ctx.exception))

        # Also reject when smuggled via parse path / model_validate.
        from governed_stack.contracts import parse_intent, AlgorithmScanIntent

        with self.assertRaises(IntentValidationError) as ctx2:
            parse_intent(
                {
                    "action": "run_algorithm",
                    "purpose": "ok",
                    "summary": "clean",
                    "token": "leak",
                }
            )
        self.assertIn("scan_intent_forbids:token", str(ctx2.exception))

        from pydantic import ValidationError

        with self.assertRaises((IntentValidationError, ValidationError)) as ctx3:
            AlgorithmScanIntent.model_validate(
                {
                    "action": "run_algorithm",
                    "purpose": "ok",
                    "payload": {"subject": "ok", "text": "clean", "secret": "x"},
                }
            )
        self.assertIn("scan_intent_forbids:secret", str(ctx3.exception))

    def test_intent_for_scan_shape(self):
        intent = intent_for_scan(
            purpose="batch_dedupe",
            summary="Nightly job",
            time_cost="O(n)",
            speedup=2,
            risk_notes="no PII",
            security_margin=0.9,
        )
        self.assertEqual(intent["action"], "run_algorithm")
        self.assertEqual(intent["payload"]["subject"], "batch_dedupe")
        text = intent["payload"]["text"]
        self.assertIn("Nightly job", text)
        self.assertIn("time=O(n)", text)
        self.assertIn("speedup=2", text)
        self.assertIn("risk: no PII", text)
        self.assertIn("security_margin=0.9", text)
        for key in ALGORITHM_SCAN_FORBIDDEN:
            self.assertNotIn(key, intent)
            self.assertNotIn(key, intent.get("payload", {}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
