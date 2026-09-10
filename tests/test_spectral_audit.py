"""Tests for Haven2 spectral / zeta audit attachment."""

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
from governed_stack import GovernedAlgorithm, GovernedStack  # noqa: E402
from governed_stack.quantum_line import FIELD_KEYS, LINE_LENGTH  # noqa: E402
from governed_stack.spectral_audit import (  # noqa: E402
    SPECTRUM_KEYS,
    attach_spectrum,
    build_spectrum,
)


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


class TestBuildSpectrum(unittest.TestCase):
    def test_from_envelope_nested_zeta(self):
        env = {
            "haven2": {
                "realm": "NORMAL",
                "p_hat": 0.01,
                "open": True,
                "zeta": {"Z_E": 0.1, "Z_R": 0.0, "Z_C": 0.2, "Z_H": 0.3, "sigma": 2.0},
            }
        }
        spec = build_spectrum(env)
        self.assertTrue(spec["available"])
        self.assertEqual(spec["source"], "envelope_haven2_zeta")
        self.assertAlmostEqual(spec["Z_E"], 0.1)
        self.assertAlmostEqual(spec["Z_H"], 0.3)
        self.assertIn("finite Dirichlet", " ".join(spec["notes"]))

    def test_minimal_residual_when_no_engine(self):
        env = {"haven2": {"p_hat": 0.5, "c": 0.1, "open": False}}
        spec = build_spectrum(env, engine=None)
        self.assertTrue(spec["available"])
        self.assertEqual(spec["source"], "minimal_residual_snapshot")
        for k in SPECTRUM_KEYS:
            self.assertIsNotNone(spec[k])
        self.assertAlmostEqual(spec["Z_R"], 0.0)

    def test_unavailable_explicit_nulls(self):
        spec = build_spectrum({"haven2": {}}, engine=None)
        self.assertFalse(spec["available"])
        for k in SPECTRUM_KEYS:
            self.assertIsNone(spec[k])
        self.assertIn("reason", spec)

    def test_attach_spectrum_mutates(self):
        result: dict = {"haven2": {"p_hat": 0.25, "c": 0.05}}
        out = attach_spectrum(result)
        self.assertIs(out, result)
        self.assertIn("spectrum", result)
        self.assertTrue(result["spectrum"]["available"])



class TestSilentFallbackFixes(unittest.TestCase):
    """FIX 1–4: sigma, complete spectrum, malformed c, stack zeta_error."""

    def test_fix1_sigma_from_nested_zeta(self):
        env = {
            "haven2": {
                "realm": "NORMAL",
                "p_hat": 0.01,
                "open": True,
                "zeta": {
                    "Z_E": 0.1,
                    "Z_R": 0.0,
                    "Z_C": 0.2,
                    "Z_H": 0.3,
                    "sigma": 5.0,
                },
            }
        }
        spec = build_spectrum(env, sigma=2.0)
        self.assertTrue(spec["available"])
        self.assertAlmostEqual(spec["sigma"], 5.0)
        self.assertNotAlmostEqual(spec["sigma"], 2.0)

    def test_fix2_incomplete_envelope_no_fallback(self):
        # Missing Z_H only; no p_hat / no engine → cannot fall through to minimal.
        env = {
            "haven2": {
                "open": True,
                "zeta": {"Z_E": 0.1, "Z_R": 0.0, "Z_C": 0.2},  # no Z_H
            }
        }
        spec = build_spectrum(env, engine=None)
        self.assertFalse(spec["available"])
        self.assertTrue(spec.get("partial"))
        self.assertAlmostEqual(spec["Z_E"], 0.1)
        self.assertAlmostEqual(spec["Z_R"], 0.0)
        self.assertAlmostEqual(spec["Z_C"], 0.2)
        self.assertIsNone(spec["Z_H"])
        self.assertIn("incomplete", spec.get("reason", "").lower())
        self.assertIn("Z_E", spec.get("partial_keys", []))

    def test_fix3_malformed_c_fails_minimal(self):
        env = {"haven2": {"p_hat": 0.5, "c": "not-a-float"}}
        spec = build_spectrum(env, engine=None)
        self.assertFalse(spec["available"])
        self.assertTrue(spec.get("partial"))
        self.assertIn("reason", spec)
        for k in SPECTRUM_KEYS:
            self.assertIsNone(spec[k])

    def test_fix3_absent_c_still_minimal(self):
        env = {"haven2": {"p_hat": 0.5}}  # c absent → default [0.0]
        spec = build_spectrum(env, engine=None)
        self.assertTrue(spec["available"])
        self.assertEqual(spec["source"], "minimal_residual_snapshot")
        for k in SPECTRUM_KEYS:
            self.assertIsNotNone(spec[k])


class TestFix4StackZetaError(unittest.IsolatedAsyncioTestCase):
    """FIX 4: zeta_summaries failure surfaces zeta_error + notes, decision intact."""

    @classmethod
    def setUpClass(cls):
        cls.shared_crypto = CryptoEngine(private_key_path=None)

    async def test_zeta_summaries_error_surfaced(self):
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

        def _boom(*_a, **_k):
            raise RuntimeError("zeta boom")

        stack.haven2.zeta_summaries = _boom  # type: ignore[method-assign]
        token = stack.issue_token("alice", "user")
        env = await stack.govern(
            {"action": "query", "payload": {"text": "hello there"}},
            token,
        )
        self.assertEqual(env["decision"], "ALLOW")
        haven2 = env["haven2"]
        self.assertNotIn("zeta", haven2)
        self.assertIn("zeta_error", haven2)
        self.assertIn("zeta boom", haven2["zeta_error"])
        notes = env.get("notes") or []
        self.assertTrue(
            any("haven2_zeta_summaries_error" in str(n) for n in notes),
            msg=f"notes missing zeta error: {notes}",
        )


class TestSpectrumOnAlgorithmGate(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls._t0 = time.time()
        cls.shared_crypto = CryptoEngine(private_key_path=None)

    @classmethod
    def tearDownClass(cls):
        elapsed = time.time() - cls._t0
        print(f"\n  [TestSpectrumOnAlgorithmGate] class total: {elapsed:.2f}s")

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

    def test_check_sync_includes_spectrum_and_quantum(self):
        algo = self.make_algo()
        result = algo.check_sync(
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
        # QUANTUM still present (do not break quantum tests / contract).
        self.assertIn("quantum", result)
        self.assertIn("quantum_line", result)
        self.assertEqual(len(result["quantum_line"]), LINE_LENGTH)
        for key in FIELD_KEYS:
            self.assertIn(key, result["quantum"])

        self.assertIn("spectrum", result)
        spec = result["spectrum"]
        self.assertTrue(spec["available"])
        self.assertIn(spec["source"], (
            "envelope_haven2_zeta",
            "haven2_engine_zeta_summaries",
            "minimal_residual_snapshot",
        ))
        for k in SPECTRUM_KEYS:
            self.assertIn(k, spec)
            self.assertIsNotNone(spec[k])
        self.assertIn("notes", spec)
        # Envelope should carry haven2 zeta from stack when step succeeded.
        haven2 = result.get("haven2") or {}
        self.assertIn("p_hat", haven2)
        if "zeta" in haven2:
            self.assertAlmostEqual(spec["Z_E"], float(haven2["zeta"]["Z_E"]), places=8)

    async def test_check_async_includes_spectrum(self):
        algo = self.make_algo()
        result = await algo.check(
            purpose="sort_index",
            summary="Build secondary index",
            time_cost=1.5,
            energy_cost=0.2,
        )
        self.assertIn("spectrum", result)
        self.assertIn("quantum_line", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
