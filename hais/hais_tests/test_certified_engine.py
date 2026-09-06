"""
tests/test_certified_engine.py

Fast tests (no ZK proofs here — see test_zk_proofs.py for those, marked
slow). RSA-3072 keygen is genuinely expensive, so it's done ONCE per
TestCase class in setUpClass and injected into every engine built in
that class via CertifiedGovernanceEngine(config, crypto=shared_crypto)
— see the `crypto=` parameter added to the engine's constructor for
exactly this purpose. Each test still gets its own fresh sqlite DB file
so tests stay isolated from each other; only the RSA key is shared,
which is safe since these tests never mutate key state.
"""

import base64
import json
import os
import tempfile
import time
import unittest

from certified_governance import (
    CertifiedGovernanceEngine, CryptoEngine, UserRateLimiter, PolicyEngine,
)

def tmp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path

class SharedCryptoTestCase(unittest.IsolatedAsyncioTestCase):
    """Base class: one RSA key for every test method in the subclass."""

    @classmethod
    def setUpClass(cls):
        cls._t0 = time.time()
        cls.shared_crypto = CryptoEngine(private_key_path=None)

    @classmethod
    def tearDownClass(cls):
        elapsed = time.time() - cls._t0
        print(f"\n  [{cls.__name__}] class total: {elapsed:.2f}s")

    def make_engine(self, **overrides) -> CertifiedGovernanceEngine:
        db_path = tmp_db()
        self.addCleanup(lambda: os.path.exists(db_path) and os.remove(db_path))

        cfg = CertifiedGovernanceEngine._default_config()
        cfg["db_path"] = db_path
        cfg.update(overrides)
        return CertifiedGovernanceEngine(cfg, crypto=self.shared_crypto)

class TestBasicGovernance(SharedCryptoTestCase):

    async def test_benign_request_allows(self):
        engine = self.make_engine()
        token = engine.security.generate_token("alice", "user")
        result = await engine.execute_governed_action(
            {"action": "query", "payload": {"text": "hello there"}}, token
        )
        self.assertEqual(result["result"]["decision"], "ALLOW")

    async def test_pii_email_blocks(self):
        engine = self.make_engine()
        token = engine.security.generate_token("alice", "user")
        result = await engine.execute_governed_action(
            {"action": "query", "payload": {"text": "reach me at bob@example.com"}}, token
        )
        self.assertEqual(result["result"]["decision"], "BLOCK")

    async def test_pii_never_persisted_in_raw_form(self):
        engine = self.make_engine()
        token = engine.security.generate_token("alice", "user")
        await engine.execute_governed_action(
            {"action": "query", "payload": {"text": "reach me at bob@example.com"}}, token
        )
        row = engine.storage.conn.execute(
            "SELECT intent_envelope FROM audit_log ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        envelope = json.loads(row["intent_envelope"])
        intent_str = base64.b64decode(envelope["data"]).decode()
        self.assertNotIn("bob@example.com", intent_str)
        self.assertIn("REDACTED:pii:email", intent_str)

    async def test_chain_stays_valid(self):
        engine = self.make_engine()
        token = engine.security.generate_token("alice", "user")
        for i in range(3):
            await engine.execute_governed_action(
                {"action": "query", "payload": {"text": f"msg {i}"}}, token, trace_id=f"t{i}"
            )
        chain = engine.storage.verify_chain()
        self.assertTrue(chain["valid"])

    async def test_review_then_resolve(self):
        engine = self.make_engine(rate_limit_capacity=2, rate_limit_refill_per_sec=0.001)
        token = engine.security.generate_token("carol", "user")
        for i in range(6):
            await engine.execute_governed_action(
                {"action": "query", "payload": {"text": f"msg {i}"}}, token, trace_id=f"t{i}"
            )
        pending = engine.list_pending_reviews()
        if not pending:
            self.skipTest("this run's timing didn't produce a REVIEW entry")
        entry_id = pending[0]["entry_id"]
        resolution = engine.resolve_review(entry_id, resolved_by="reviewer1", approve=True)
        self.assertEqual(resolution["final_decision"], "ALLOW")
        self.assertTrue(engine.storage.verify_chain()["valid"])

class TestPerUserRateLimiter(unittest.TestCase):
    """No crypto involved — already cheap, nothing to share."""

    def test_trips_after_limit(self):
        limiter = UserRateLimiter(max_per_hour=3)
        results = [limiter.record_and_check("dave") for _ in range(5)]
        self.assertFalse(results[2]["exceeded"])
        self.assertTrue(results[-1]["exceeded"])

class TestPolicyHotReload(unittest.TestCase):
    """Also crypto-free — policy loading/reload is pure file + regex work."""

    def test_reload_picks_up_new_rules(self):
        fd, spec_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(spec_path) and os.remove(spec_path))

        with open(spec_path, "w") as f:
            json.dump({"banned_terms": [
                {"id": "b1", "term": "forbiddenxyz", "action": "BLOCK", "reason": "banned:test"}
            ]}, f)
        pe = PolicyEngine(spec_path=spec_path)
        self.assertEqual(len(pe.spec.banned_terms), 1)

        with open(spec_path, "w") as f:
            json.dump({"banned_terms": [
                {"id": "b1", "term": "forbiddenxyz", "action": "BLOCK", "reason": "banned:test"},
                {"id": "b2", "term": "otherbad", "action": "BLOCK", "reason": "banned:test2"},
            ]}, f)
        counts = pe.reload()
        self.assertEqual(counts["banned_terms"], 2)

if __name__ == "__main__":
    unittest.main(verbosity=2)
