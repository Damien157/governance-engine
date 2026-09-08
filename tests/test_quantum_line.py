"""Tests for QUANTUM line encode/decode and Algorithm Audit attachment."""

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
from governed_stack.quantum_line import (  # noqa: E402
    FIELD_KEYS,
    LINE_LENGTH,
    N_FIELDS,
    build_quantum_state,
    decode_quantum_line,
    encode_quantum_line,
    quantum_from_hais_envelope,
)
from hais_unified_kernel import SovereignKernel  # noqa: E402


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


class TestQuantumLineCodec(unittest.TestCase):
    def test_roundtrip_fixed_width(self):
        state = build_quantum_state(
            x=0.25,
            S=0.4,
            tau=1.7,
            r_prime=0.68,
            cap=0.22,
            delta_r_prime=-0.05,
            T1=0.0,
            T2=0.0,
            T3=0.0,
            I=0.9,
        )
        line = encode_quantum_line(state)
        self.assertEqual(len(line), LINE_LENGTH)
        self.assertEqual(len(line), N_FIELDS * 15)
        self.assertNotIn(" ", line)
        self.assertNotIn(",", line)
        decoded = decode_quantum_line(line)
        for key in FIELD_KEYS:
            self.assertAlmostEqual(decoded[key], state[key], places=8)

    def test_decode_rejects_bad_length(self):
        with self.assertRaises(ValueError):
            decode_quantum_line("too-short")

    def test_from_kernel_metrics(self):
        k = SovereignKernel()
        metrics = k.evaluate_state(0.3, {"stress": 0.1, "anomaly": 0.0, "drift": 0.0})
        state = build_quantum_state(kernel_metrics=metrics)
        self.assertAlmostEqual(state["x"], metrics["risk_metric"], places=8)
        self.assertAlmostEqual(state["S"], metrics["S"], places=8)
        self.assertAlmostEqual(state["tau"], metrics["tau"], places=8)
        self.assertAlmostEqual(state["r_prime"], metrics["r_prime"], places=8)
        self.assertAlmostEqual(state["cap"], metrics["capability_cap"], places=8)
        self.assertAlmostEqual(state["delta_r_prime"], metrics["delta_r_prime"], places=8)
        self.assertAlmostEqual(state["I"], metrics["instability"], places=8)
        self.assertFalse(state["t_thresholds_available"])
        self.assertEqual(state["T1"], 0.0)
        line = encode_quantum_line(state)
        back = decode_quantum_line(line)
        self.assertAlmostEqual(back["I"], metrics["instability"], places=8)

    def test_hais_envelope_reconstructs_tau(self):
        # Envelope without tau (older shape) still builds a line.
        hais = {"risk": 0.2, "S": 0.3, "r_prime": 0.5, "cap": 0.4, "instability": 0.8}
        state = quantum_from_hais_envelope(hais)
        self.assertGreater(state["tau"], 1.0)
        self.assertEqual(state["delta_r_prime"], 0.0)  # no prev
        self.assertEqual(len(encode_quantum_line(state)), LINE_LENGTH)


class TestQuantumOnAlgorithmGate(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls._t0 = time.time()
        cls.shared_crypto = CryptoEngine(private_key_path=None)

    @classmethod
    def tearDownClass(cls):
        elapsed = time.time() - cls._t0
        print(f"\n  [TestQuantumOnAlgorithmGate] class total: {elapsed:.2f}s")

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

    def test_check_sync_includes_quantum(self):
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
        self.assertIn("quantum", result)
        self.assertIn("quantum_line", result)
        q = result["quantum"]
        for key in FIELD_KEYS:
            self.assertIn(key, q)
        line = result["quantum_line"]
        self.assertEqual(len(line), LINE_LENGTH)
        decoded = decode_quantum_line(line)
        self.assertAlmostEqual(decoded["S"], q["S"], places=8)
        # Live envelope should carry HAIS fields used for Audit.
        hais = result.get("hais") or {}
        self.assertIsNotNone(hais.get("cap"))
        self.assertIsNotNone(hais.get("S"))

    async def test_check_async_includes_quantum(self):
        algo = self.make_algo()
        result = await algo.check(
            purpose="sort_index",
            summary="Build secondary index",
            time_cost=1.5,
            energy_cost=0.2,
        )
        self.assertIn("quantum_line", result)
        self.assertEqual(len(result["quantum_line"]), LINE_LENGTH)


if __name__ == "__main__":
    unittest.main(verbosity=2)
