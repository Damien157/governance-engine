"""hais/certified_governance.py AuditStorage stays in line with the root copy:
log_decision(enqueue_review=True) flag and the additive stats()["reviewed"]."""

from __future__ import annotations

import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in reversed((ROOT, ROOT / "hais")):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import certified_governance as hais_cg  # noqa: E402
import certified_governance_unified as root_cg  # noqa: E402

COMMON = dict(
    intent={"action": "probe"},
    result="r",
    verification_score=1.0,
    risk_signal=0.5,
    anomaly_signal=0.0,
    policy_reasons=["p"],
    metadata={},
)


class TestHaisAuditReviewFlag(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.crypto = hais_cg.CryptoEngine(private_key_path=None)

    def make_storage(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(db_path) and os.remove(db_path))
        storage = hais_cg.AuditStorage(db_path, self.crypto)
        self.addCleanup(storage.conn.close)
        return storage

    def test_module_is_the_hais_copy(self):
        self.assertEqual(Path(hais_cg.__file__).resolve(), ROOT / "hais" / "certified_governance.py")

    def test_hais_enqueue_review_flag(self):
        storage = self.make_storage()
        queued = storage.log_decision(decision="REVIEW", **COMMON)  # default unchanged
        unqueued = storage.log_decision(decision="REVIEW", enqueue_review=False, **COMMON)
        storage.log_decision(decision="BLOCK", enqueue_review=True, **COMMON)  # no-op for non-REVIEW
        pending = {r["entry_id"] for r in storage.list_pending_reviews()}
        self.assertEqual(pending, {queued})
        self.assertNotIn(unqueued, pending)
        with self.assertRaises(ValueError):
            storage.resolve_review(unqueued, resolved_by="reviewer", approve=True)
        self.assertTrue(storage.verify_chain()["valid"])

    def test_hais_stats_counts_reviewed(self):
        storage = self.make_storage()
        for decision in ("ALLOW", "BLOCK", "REVIEW", "REVIEW"):
            storage.log_decision(decision=decision, enqueue_review=False, **COMMON)
        stats = storage.stats()
        self.assertEqual(
            (stats["total"], stats["allowed"], stats["blocked"], stats["reviewed"]), (4, 1, 1, 2)
        )

    def test_flag_signature_matches_root_copy(self):
        for mod in (hais_cg, root_cg):
            param = inspect.signature(mod.AuditStorage.log_decision).parameters["enqueue_review"]
            self.assertIs(param.default, True, mod.__name__)


if __name__ == "__main__":
    unittest.main()
