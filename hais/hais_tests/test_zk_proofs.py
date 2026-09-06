"""
tests/test_zk_proofs.py

Two tiers, split because ZK proof generation/verification is genuinely
slow in this environment (~1-3s each, no GMP-accelerated bignum
available) while the underlying primitives (bit proofs, group setup) are
fast:

  - Primitive-level tests (group setup, bit proofs, forged-proof
    rejection): fast, always run.
  - Full range-proof / engine-integration tests: marked slow, skipped by
    default. Run them explicitly with:
        ZK_RUN_SLOW=1 python -m unittest tests.test_zk_proofs -v
    or, under pytest, register the marker in pytest.ini (included
    alongside this file) and run:
        pytest -m slow tests/test_zk_proofs.py

RSA keygen is shared per-class the same way as test_certified_engine.py,
via the crypto= injection point.
"""

import asyncio
import json
import os
import time
import unittest

import zk_enhanced_governance as zk
from zk_enhanced_governance import ZKEnhancedGovernanceEngine, ClaimType
from certified_governance import CertifiedGovernanceEngine, CryptoEngine

import tests.test_certified_engine as base  # tmp_db(), SharedCryptoTestCase

SLOW = unittest.skipUnless(
    os.environ.get("ZK_RUN_SLOW") == "1",
    "slow ZK proof test — set ZK_RUN_SLOW=1 to run (also markable as pytest.mark.slow)",
)

try:
    import pytest

    def slow(fn):
        return pytest.mark.slow(SLOW(fn))
except ImportError:
    slow = SLOW

class TestGroupSetup(unittest.TestCase):
    """Fast — no proofs, just checking the algebra is well-formed."""

    def test_generators_have_correct_order(self):
        self.assertEqual(pow(zk.G, zk.Q, zk.P), 1)
        self.assertEqual(pow(zk.H, zk.Q, zk.P), 1)

    def test_generators_distinct(self):
        self.assertNotEqual(zk.G, zk.H)
        self.assertNotIn(zk.G, (0, 1))
        self.assertNotIn(zk.H, (0, 1))

class TestBitProofFast(unittest.TestCase):
    """
    Bit proofs are cheap (a handful of modexps each) — these run every
    time, including forgery attempts, since soundness is the whole point.
    """

    def test_bit_zero_verifies(self):
        r = zk._rand_scalar()
        C = zk.commit(0, r)
        self.assertTrue(zk.verify_bit(C, zk.prove_bit(0, r)))

    def test_bit_one_verifies(self):
        r = zk._rand_scalar()
        C = zk.commit(1, r)
        self.assertTrue(zk.verify_bit(C, zk.prove_bit(1, r)))

    def test_non_bit_value_refused(self):
        with self.assertRaises(ValueError):
            zk.prove_bit(5, zk._rand_scalar())

    def test_tampered_proof_rejected(self):
        r = zk._rand_scalar()
        C = zk.commit(0, r)
        p = zk.prove_bit(0, r)
        tampered = zk.BitProof(a0=(p.a0 + 1) % zk.P, a1=p.a1, e0=p.e0, e1=p.e1, z0=p.z0, z1=p.z1)
        self.assertFalse(zk.verify_bit(C, tampered))

    def test_proof_rejected_against_wrong_commitment(self):
        r = zk._rand_scalar()
        C = zk.commit(0, r)
        p = zk.prove_bit(0, r)
        self.assertFalse(zk.verify_bit((C + 1) % zk.P, p))

class TestRangeProofSlow(unittest.TestCase):
    """
    Full range proofs (K_BITS=18 bit-proofs each) — this is where the
    real time goes. Skipped unless ZK_RUN_SLOW=1.
    """

    @slow
    def test_honest_range_proof_verifies(self):
        value, blinding = 42, zk._rand_scalar()
        C = zk.commit(value, blinding)
        t0 = time.time()
        rp = zk.prove_range(value, blinding)
        t1 = time.time()
        ok = zk.verify_range(C, rp)
        t2 = time.time()
        print(f"\n    prove: {(t1 - t0) * 1000:.0f}ms  verify: {(t2 - t1) * 1000:.0f}ms")
        self.assertTrue(ok)

    @slow
    def test_out_of_range_value_refused(self):
        with self.assertRaises(ValueError):
            zk.prove_range(-1, zk._rand_scalar())
        with self.assertRaises(ValueError):
            zk.prove_range(1 << zk.K_BITS, zk._rand_scalar())

    @slow
    def test_forged_range_proof_rejected(self):
        value, blinding = 7, zk._rand_scalar()
        rp = zk.prove_range(value, blinding)
        wrong_C = zk.commit(999, zk._rand_scalar())
        self.assertFalse(zk.verify_range(wrong_C, rp))

class TestZKEngineIntegration(unittest.IsolatedAsyncioTestCase):
    """
    End-to-end through the engine. Slow for the same reason as above —
    each of these generates at least one real range proof.
    """

    @classmethod
    def setUpClass(cls):
        cls.shared_crypto = CryptoEngine(private_key_path=None)

    def make_engine(self, **overrides):
        db_path = base.tmp_db()
        self.addCleanup(lambda: os.path.exists(db_path) and os.remove(db_path))
        cfg = CertifiedGovernanceEngine._default_config()
        cfg["db_path"] = db_path
        cfg.update(overrides)
        return ZKEnhancedGovernanceEngine(cfg, crypto=self.shared_crypto)

    @slow
    async def test_risk_threshold_proof_sync_mode(self):
        engine = self.make_engine()
        token = engine.security.generate_token("alice", "user")
        result = await engine.execute_governed_action(
            {"action": "query", "payload": {"text": "hello there"}},
            token, generate_proofs=True,
            proof_claims=[ClaimType.RISK_THRESHOLD.value],
            proof_mode="sync",
        )
        self.assertEqual(result["attestations"]["errors"], [])
        att_id = result["attestations"]["generated"][0]
        verification = engine.verify_attestation(att_id)
        self.assertTrue(verification["valid"])
        self.assertTrue(verification["is_zero_knowledge"])

    @slow
    async def test_impossible_threshold_refused_not_faked(self):
        engine = self.make_engine()
        token = engine.security.generate_token("alice", "user")
        result = await engine.execute_governed_action(
            {"action": "query", "payload": {"text": "another message"}},
            token, generate_proofs=True,
            proof_claims=[ClaimType.RISK_THRESHOLD.value],
            risk_threshold=0.0000001,
            proof_mode="sync",
        )
        self.assertEqual(result["attestations"]["generated"], [])
        self.assertEqual(len(result["attestations"]["errors"]), 1)

    async def test_background_mode_does_not_block(self):
        """Fast on purpose — this is exactly the test that proves the
        background-mode fix works, so it should NOT be marked slow."""
        engine = self.make_engine()
        token = engine.security.generate_token("alice", "user")
        t0 = time.time()
        result = await engine.execute_governed_action(
            {"action": "query", "payload": {"text": "background check"}},
            token, generate_proofs=True,
            proof_claims=[ClaimType.RISK_THRESHOLD.value],
            # proof_mode="background" is the default
        )
        elapsed = time.time() - t0
        self.assertLess(elapsed, 1.0, f"background mode took {elapsed:.2f}s — should return immediately")
        self.assertIn("pending", result["attestations"])

    async def test_policy_redaction_attestation_is_signed_not_zk(self):
        """Fast — this is a signature, not a range proof."""
        engine = self.make_engine()
        token = engine.security.generate_token("alice", "user")
        result = await engine.execute_governed_action(
            {"action": "query", "payload": {"text": "reach me at bob@example.com"}},
            token, generate_proofs=True,
            proof_claims=[ClaimType.POLICY_REDACTION.value],
            proof_mode="sync",
        )
        att_id = result["attestations"]["generated"][0]
        v = engine.verify_attestation(att_id)
        self.assertTrue(v["valid"])
        self.assertFalse(v["is_zero_knowledge"])

if __name__ == "__main__":
    unittest.main(verbosity=2)
